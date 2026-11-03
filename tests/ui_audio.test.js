import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../ui/static/audio.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../ui/static/index.html', import.meta.url), 'utf8');
const FULL_SCALE = 8388607;

class Element {
  constructor(id = '') {
    this.id = id; this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {};
    this.style = { setProperty() {} }; this.textContent = ''; this.className = ''; this.value = '';
    this.checked = false; this.disabled = false; this.hidden = false; this.title = '';
  }
  get classList() {
    const names = () => (this.className || '').split(' ').filter(Boolean);
    return {
      contains: name => names().includes(name),
      add: name => { if (!names().includes(name)) this.className = [...names(), name].join(' '); },
      remove: name => { this.className = names().filter(value => value !== name).join(' '); },
      toggle: (name, force) => {
        const on = force === undefined ? !names().includes(name) : force;
        this.className = on ? [...new Set([...names(), name])].join(' ') : names().filter(v => v !== name).join(' ');
        return on;
      },
    };
  }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key]; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...kids) { this.children = kids; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type, event = {}) { (this.listeners[type] || []).forEach(fn => fn({ target: this, ...event })); }
  focus() {}
}

// A canvas whose 2-D context only records what was drawn.
class Canvas extends Element {
  constructor(id, width = 480, height = 200) {
    super(id); this.width = width; this.height = height; this.fonts = []; this.fills = 0; this.texts = [];
    const canvas = this;
    this.ctx = new Proxy({}, {
      get: (target, key) => {
        if (key === 'fillRect') return () => { canvas.fills++; };
        if (key === 'fillText') return text => { canvas.texts.push(String(text)); };
        return key in target ? target[key] : () => ({ width: 10, addColorStop() {} });
      },
      set: (target, key, value) => { if (key === 'font') canvas.fonts.push(value); target[key] = value; return true; },
    });
  }
  getContext() { return this.ctx; }
}

const IDS = ['audio-view', 'audio-left-clip', 'audio-right-clip', 'audio-swap', 'audio-routing',
  'audio-progress-fill', 'audio-progress-text', 'audio-speed', 'audio-load-pru0', 'audio-load-pru1',
  'audio-mode', 'audio-notice', 'audio-overrun', 'audio-error', 'audio-observer', 'audio-run',
  'audio-stop', 'audio-reset', 'audio-booth', 'audio-title-left', 'audio-title-right'];

function page({ clips = [{ name: 'hello.wav', ms: 250 }, { name: 'chirp.wav', ms: 250 }] } = {}) {
  const elements = new Map(IDS.map(id => [id, new Element(id)]));
  elements.set('audio-left-clip', new Element('audio-left-clip'));
  elements.set('audio-canvas-left', new Canvas('audio-canvas-left'));
  elements.set('audio-canvas-right', new Canvas('audio-canvas-right'));
  const listeners = {};
  const sent = [];
  const timers = new Map();
  const net = { open: true };
  let nextTimer = 1;
  const document = {
    fullscreenElement: null,
    getElementById: id => elements.get(id) || null,
    createElement: () => new Element(),
    addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    dispatch: (type, event = {}) => (listeners[type] || []).forEach(fn => fn({ type, ...event })),
  };
  const context = vm.createContext({
    document, console, Float64Array, Math, Number, Array, JSON, Object, Error,
    window: { addEventListener() {} },
    // Outer-realm copy, comparable with deepEqual. Like the real one it drops a message and returns false when closed.
    sendAction: obj => { if (!net.open) return false; sent.push(JSON.parse(JSON.stringify(obj))); return true; },
    fetch: async () => ({ ok: true, json: async () => ({ clips }) }),
    requestAnimationFrame: fn => fn(),
    setInterval: (fn, ms) => { timers.set(nextTimer, { fn, ms }); return nextTimer++; },
    clearInterval: id => { timers.delete(id); },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
  });
  vm.runInContext(source, context);
  return { context, elements, document, sent, timers, net, audio: context.PruAudio,
    el: id => elements.get(id) };
}

const sine = (n, bin = 32, amplitude = FULL_SCALE) =>
  Array.from({ length: n }, (_, i) => amplitude * Math.sin(2 * Math.PI * bin * i / 256));
const flush = () => new Promise(resolve => setImmediate(resolve));
const luma = ([r, g, b]) => 0.2126 * r + 0.7152 * g + 0.0722 * b;

test('fft of a bin-centred sine peaks at that bin', () => {
  const { audio } = page();
  const column = audio.spectrumColumn(sine(256));
  assert.equal(column.length, 129);
  let peak = 0;
  column.forEach((value, k) => { if (value > column[peak]) peak = k; });
  assert.equal(peak, 32);
  assert.ok(column[32] - column[80] >= 60, `peak ${column[32]} vs bin 80 ${column[80]}`);
});

test('full-scale sine reads about 0 dBFS', () => {
  const { audio } = page();
  const column = audio.spectrumColumn(sine(256));
  assert.ok(Math.abs(column[32]) <= 1, `got ${column[32]} dBFS`);
  assert.equal(audio.spectrumColumn(new Array(256).fill(0))[10], -140, 'silence is floored at -140');
});

test('fft and hann follow the textbook definitions', () => {
  const { audio } = page();
  const re = new Float64Array([1, 0, 0, 0, 0, 0, 0, 0]);
  const im = new Float64Array(8);
  audio.fft(re, im);
  assert.deepEqual(Array.from(re), [1, 1, 1, 1, 1, 1, 1, 1], 'impulse -> flat spectrum');
  const w = audio.hann(256);
  assert.equal(w.length, 256);
  assert.ok(Math.abs(w[0]) < 1e-12 && Math.abs(w[128] - 1) < 1e-12);
});

test('colormap clamps and is monotonic in luminance', () => {
  const { audio } = page();
  assert.deepEqual(Array.from(audio.colormap(-200)), Array.from(audio.colormap(-90)));
  assert.equal(audio.colormap(-90).length, 3);
  assert.deepEqual(Array.from(audio.colormap(20)), Array.from(audio.colormap(0)));
  const steps = Array.from({ length: 10 }, (_, i) => luma(audio.colormap(-90 + i * 10)));   // -90 ... 0 dBFS
  for (let i = 1; i < steps.length; i++) assert.ok(steps[i] > steps[i - 1], `step ${i}: ${steps}`);
  assert.equal(Math.max(...steps), luma(audio.colormap(0)), '0 dBFS is the brightest');
});

test('append is idempotent and rejects gaps', () => {
  const { audio, elements } = page();
  const spec = new audio.Spectrogram(elements.get('audio-canvas-left'), {});
  const block = sine(256);
  spec.append(0, block);
  spec.append(0, block);
  assert.equal(spec.columns, 1);
  assert.throws(() => spec.append(300, block), /gap/i);
  assert.equal(spec.columns, 1, 'a rejected append changes nothing');
  spec.append(128, sine(256));          // overlaps 128 held samples, adds 128 new ones
  assert.equal(spec.columns, 2);
  spec.reset();
  assert.equal(spec.columns, 0);
  spec.append(0, block);
  assert.equal(spec.columns, 1);
});

test('spectrogram draws, and its text scales with canvas height', () => {
  const { audio } = page();
  const small = new Canvas('s', 600, 100);
  const big = new Canvas('b', 1800, 800);
  for (const canvas of [small, big]) {
    const spec = new audio.Spectrogram(canvas, {});
    spec.append(0, sine(1024));
    spec.draw();
    assert.ok(canvas.fills > 129, 'columns and colour bar were painted');
    assert.ok(canvas.texts.some(text => /8/.test(text)), 'frequency axis is labelled');
  }
  const px = canvas => Math.max(...canvas.fonts.map(font => parseFloat(font)));
  assert.ok(px(big) >= 2 * px(small), `font ${px(small)}px -> ${px(big)}px`);
});

test('spectrogram text is capped by canvas width as well as height', () => {
  const { audio } = page();
  const draw = (width, height) => {
    const canvas = new Canvas('c', width, height);
    const spec = new audio.Spectrogram(canvas, {});
    spec.append(0, sine(1024));
    spec.draw();
    return Math.max(...canvas.fonts.map(font => parseFloat(font)));
  };
  // Tall and narrow: the height alone would give 48 px; the width cap (3.5 %) gives 25 px.
  const narrow = draw(700, 1000);
  assert.ok(narrow <= 25, `700x1000 font ${narrow}px, expected <= 25`);
  assert.ok(narrow >= 24, `700x1000 font ${narrow}px, expected the cap to bind at about 24.5`);
  // Wide enough that the height governs again: unchanged from before the cap.
  assert.equal(draw(1800, 800), 48);
  assert.equal(draw(1000, 270), 19);
});

test('booth button toggles class and Esc leaves', () => {
  const { elements, document } = page();
  const view = elements.get('audio-view');
  elements.get('audio-booth').fire('click');
  assert.ok(view.classList.contains('audio-booth'));
  document.dispatch('keydown', { key: 'a' });
  assert.ok(view.classList.contains('audio-booth'), 'other keys do nothing');
  document.dispatch('keydown', { key: 'Escape' });
  assert.ok(!view.classList.contains('audio-booth'));
  elements.get('audio-booth').fire('click');
  elements.get('audio-booth').fire('click');
  assert.ok(!view.classList.contains('audio-booth'), 'the button toggles');
});

test('booth requests fullscreen and fullscreenchange leaves', () => {
  const { elements, document } = page();
  const view = elements.get('audio-view');
  let requested = 0;
  view.requestFullscreen = () => { requested++; document.fullscreenElement = view; return Promise.resolve(); };
  elements.get('audio-booth').fire('click');
  assert.equal(requested, 1);
  document.dispatch('fullscreenchange');
  assert.ok(view.classList.contains('audio-booth'), 'entering fullscreen keeps booth mode');
  document.fullscreenElement = null;          // the browser handled Esc itself
  document.dispatch('fullscreenchange');
  assert.ok(!view.classList.contains('audio-booth'));
});

test('history replay after reload', () => {
  const live = page();
  const reloaded = page();
  const samples = sine(1500);
  const chunks = [[0, 300], [300, 500], [800, 700]];
  for (const [start, length] of chunks)
    for (const ch of [0, 1])
      live.audio.handleMessage({ type: 'pdm_pcm', ch, start, samples: samples.slice(start, start + length) });
  for (const ch of [0, 1])
    reloaded.audio.handleMessage({ type: 'pdm_pcm', ch, start: 0, samples });
  const columns = page_ => Array.from(page_.audio.view.spectrograms, spec => spec.columns);
  assert.deepEqual(columns(live), [10, 10]);
  assert.deepEqual(columns(reloaded), columns(live));
  reloaded.audio.handleMessage({ type: 'pdm_pcm', ch: 0, start: 0, samples });
  assert.deepEqual(columns(reloaded), [10, 10], 'a repeated history changes nothing');
});

test('opening the view loads the clip list and asks for history', async () => {
  const { elements, document, sent } = page();
  document.dispatch('pru-workspace-view-changed', { detail: { view: 'simulator' } });
  assert.deepEqual(sent, [], 'other views do not touch the demo');
  document.dispatch('pru-workspace-view-changed', { detail: { view: 'audio', tab: 'devices' } });
  await flush();
  assert.deepEqual(sent, [{ action: 'pdm_history' }]);
  const left = elements.get('audio-left-clip');
  assert.deepEqual(left.children.map(option => option.value), ['hello.wav', 'chirp.wav']);
  assert.equal(left.value, 'hello.wav');
  assert.equal(elements.get('audio-right-clip').value, 'chirp.wav', 'Right defaults to the second clip');
});

function runSetup(extra) {
  const p = page(extra);
  p.document.dispatch('pru-workspace-view-changed', { detail: { view: 'audio' } });
  return p;
}
const status = (over = {}) => ({ type: 'pdm_status', audio_ms: 88, clip_ms: 250, speed_ms_per_s: 2.4,
  load: { pru0: 0.21, pru1: 0.05 }, mode: 'run-ahead', overrun: null, done: false, notices: [],
  clips: { left: 'hello.wav', right: 'chirp.wav' }, running: false, breakpoint: null, swap: false, ...over });

test('Run sets up, then re-sends pdm_run while running and not done', async () => {
  const { audio, elements, sent } = runSetup();
  await flush();
  sent.length = 0;
  elements.get('audio-swap').checked = true;
  elements.get('audio-swap').fire('change');
  elements.get('audio-run').fire('click');
  assert.deepEqual(sent, [{ action: 'pdm_setup', clip_left: 'hello.wav', clip_right: 'chirp.wav', swap: true }]);
  audio.handleMessage({ type: 'pdm_notice', text: 'PRU clocks set to 200 MHz for the PDM demo' });
  audio.handleMessage({ type: 'state', core: 'pru0' });                 // clock change side effect: ignored
  assert.equal(sent.length, 1);
  audio.handleMessage(status({ audio_ms: 0, swap: true }));              // setup reply
  assert.equal(sent.length, 2);
  assert.equal(sent[1].action, 'pdm_run');
  const first = sent[1].run_id;
  audio.handleMessage(status({ running: true, run_id: first, swap: true }));
  assert.equal(sent.length, 3);
  assert.equal(sent[2].action, 'pdm_run');
  assert.notEqual(sent[2].run_id, first);
  assert.ok(elements.get('audio-run').disabled && !elements.get('audio-stop').disabled);
  audio.handleMessage(status({ running: false, done: true, audio_ms: 250, run_id: sent[2].run_id, swap: true }));
  assert.equal(sent.length, 3, 'a finished run is not re-sent');
  assert.ok(!elements.get('audio-run').disabled && elements.get('audio-stop').disabled);
  assert.match(elements.get('audio-notice').textContent, /200 MHz/);
  assert.equal(elements.get('audio-speed').textContent, '2.4 ms audio / s');
  assert.equal(elements.get('audio-progress-fill').style.width, '100%');
  assert.match(elements.get('audio-load-pru0').textContent, /PRU0 21 ?%/);
  assert.match(elements.get('audio-mode').textContent, /run-ahead/);
});

test('Stop does not wait for a reply and a late status does not restart the run', async () => {
  const { audio, elements, sent } = runSetup();
  await flush();
  elements.get('audio-run').fire('click');
  audio.handleMessage(status({ audio_ms: 0 }));
  const run = sent.at(-1);
  assert.equal(run.action, 'pdm_run');
  sent.length = 0;
  elements.get('audio-stop').fire('click');
  assert.deepEqual(sent, [{ action: 'pdm_stop' }]);
  assert.ok(!elements.get('audio-run').disabled, 'Run is usable at once; no reply is awaited');
  audio.handleMessage(status({ running: true, run_id: run.run_id }));   // in-flight chunk lands
  assert.deepEqual(sent, [{ action: 'pdm_stop' }]);
  sent.length = 0;
  elements.get('audio-run').fire('click');                             // same clips: continue, no new setup
  assert.equal(sent[0].action, 'pdm_run');
});

test('Run after a finished run or a changed clip sets up again; Reset clears the picture', async () => {
  const { audio, elements, sent } = runSetup();
  await flush();
  audio.handleMessage(status({ done: true, audio_ms: 250 }));
  audio.handleMessage({ type: 'pdm_pcm', ch: 0, start: 0, samples: sine(512) });
  assert.equal(audio.view.spectrograms[0].columns, 3);
  sent.length = 0;
  elements.get('audio-run').fire('click');
  assert.equal(sent[0].action, 'pdm_setup', 'a finished run restarts from a clean setup');
  assert.equal(audio.view.spectrograms[0].columns, 0, 'the old picture is dropped');
  audio.handleMessage(status({ audio_ms: 0 }));
  audio.handleMessage(status({ running: true, run_id: sent.at(-1).run_id }));
  elements.get('audio-stop').fire('click');
  audio.handleMessage({ type: 'pdm_pcm', ch: 0, start: 0, samples: sine(512) });
  sent.length = 0;
  elements.get('audio-reset').fire('click');
  assert.deepEqual(sent.map(m => m.action), ['pdm_reset']);
  assert.equal(audio.view.spectrograms[0].columns, 0);
});

test('an observer tab shows the history and says it is not the running tab', async () => {
  const { audio, elements, sent, timers } = runSetup();
  await flush();
  assert.deepEqual(sent, [{ action: 'pdm_history' }]);
  audio.handleMessage({ type: 'pdm_pcm', ch: 0, start: 0, samples: sine(512) });
  audio.handleMessage(status({ running: true }));
  assert.equal(audio.view.spectrograms[0].columns, 3);
  assert.match(elements.get('audio-observer').textContent, /another tab/i);
  assert.equal(elements.get('audio-observer').hidden, false);
  assert.ok(elements.get('audio-run').disabled, 'an observer cannot start a second run');
  assert.equal(sent.filter(m => m.action === 'pdm_run').length, 0);
  assert.equal(timers.size, 1, 'the snapshot is refreshed while the other tab runs');
  [...timers.values()][0].fn();
  assert.equal(sent.at(-1).action, 'pdm_history');
  audio.handleMessage(status({ running: false, done: true }));
  assert.equal(timers.size, 0, 'polling stops when the run ends');
  assert.equal(elements.get('audio-observer').hidden, true);
});

test('a changed setup in another tab rebuilds the observer picture', async () => {
  const { audio, sent } = runSetup();
  await flush();
  audio.handleMessage({ type: 'pdm_pcm', ch: 0, start: 0, samples: sine(512) });
  audio.handleMessage(status({ audio_ms: 32, running: true }));
  sent.length = 0;
  audio.handleMessage(status({ audio_ms: 4, running: true, clips: { left: 'chirp.wav', right: 'hello.wav' } }));
  assert.equal(audio.view.spectrograms[0].columns, 0);
  assert.deepEqual(sent, [{ action: 'pdm_history' }]);
});

test('server errors land in the view error line and end the run', async () => {
  const { audio, elements, sent } = runSetup();
  await flush();
  elements.get('audio-run').fire('click');
  assert.equal(audio.handleMessage({ type: 'error', tag: 'pdm', errors: ['unknown clip'] }), true);
  assert.equal(elements.get('audio-error').textContent, 'unknown clip');
  assert.equal(elements.get('audio-error').hidden, false);
  assert.ok(!elements.get('audio-run').disabled);
  // The ownership error carries no tag; while a request is pending it belongs to the view.
  sent.length = 0;
  elements.get('audio-run').fire('click');
  assert.equal(audio.handleMessage({ type: 'error', errors: ['Execution stopped or controlled by another tab'] }), true);
  assert.match(elements.get('audio-error').textContent, /another tab/);
  assert.ok(!elements.get('audio-run').disabled);
  // With nothing pending the error is not ours.
  assert.equal(audio.handleMessage({ type: 'error', errors: ['assembly failed'] }), false);
  assert.equal(audio.handleMessage({ type: 'state' }), false);
});

test('overrun and a mid-run notice are shown, a missed chunk triggers a history refresh', async () => {
  const { audio, elements, sent } = runSetup();
  await flush();
  audio.handleMessage(status({ overrun: 'PCM ring 0 overrun: 3 blocks lost' }));
  assert.match(elements.get('audio-overrun').textContent, /overrun/i);
  assert.equal(elements.get('audio-overrun').hidden, false);
  audio.handleMessage(status({ overrun: null, notices: ['lockstep: wiring changed'] }));
  assert.equal(elements.get('audio-overrun').hidden, true);
  assert.match(elements.get('audio-notice').textContent, /lockstep/);
  sent.length = 0;
  audio.handleMessage({ type: 'pdm_pcm', ch: 0, start: 5000, samples: sine(256) });   // gap
  assert.deepEqual(sent, [{ action: 'pdm_history' }]);
});

test('the notices of one setup are all shown, a later one replaces them', async () => {
  const { audio, elements } = runSetup();
  await flush();
  elements.get('audio-run').fire('click');
  const clock = 'PRU clocks set to 200 MHz for the PDM demo';
  const programs = 'Audio Run loaded pdm_gen.asm on PRU1 and pdm_rx.asm on PRU0 (Simulator-view programs replaced)';
  audio.handleMessage({ type: 'pdm_notice', text: clock });
  audio.handleMessage({ type: 'pdm_notice', text: programs });
  assert.equal(elements.get('audio-notice').textContent, `${clock} · ${programs}`);
  audio.handleMessage(status({ audio_ms: 0 }));                // setup reply: loop starts
  audio.handleMessage({ type: 'pdm_notice', text: 'Run-ahead stopped mid-run: x' });
  assert.equal(elements.get('audio-notice').textContent, 'Run-ahead stopped mid-run: x');
});

const NOT_SET_UP = { type: 'error', tag: 'pdm', errors: ['PDM demo is not set up; send pdm_setup first'] };

test('pdm_history before any setup shows no error and keeps Run enabled', async () => {
  const { audio, elements } = runSetup();
  await flush();
  assert.equal(audio.handleMessage(NOT_SET_UP), true, 'the view consumes the reply');
  assert.equal(elements.get('audio-error').textContent, '', 'a passive history reply is not an error');
  assert.ok(elements.get('audio-error').hidden);
  assert.ok(!elements.get('audio-run').disabled);
});

test('a Run clicked before the history reply arrives still starts the loop', async () => {
  const { audio, elements, sent } = runSetup();
  await flush();
  assert.deepEqual(sent, [{ action: 'pdm_history' }]);
  elements.get('audio-run').fire('click');                   // before the history reply
  assert.equal(sent.at(-1).action, 'pdm_setup');
  audio.handleMessage(NOT_SET_UP);                            // reply to the history, in order
  assert.ok(elements.get('audio-error').hidden, 'no error line');
  assert.ok(elements.get('audio-run').disabled, 'the setup is still pending');
  audio.handleMessage(status({ audio_ms: 0 }));               // reply to the setup
  assert.equal(sent.at(-1).action, 'pdm_run', 'the loop starts');
  assert.ok(!elements.get('audio-stop').disabled);
});

test('an orphaned demo makes the next Run set up again', async () => {
  const { audio, elements, sent } = runSetup();
  await flush();
  audio.handleMessage(status({ done: false, audio_ms: 40 }));          // a demo existed
  audio.handleMessage({ type: 'pdm_pcm', ch: 0, start: 0, samples: sine(512) });
  audio.handleMessage(NOT_SET_UP);                                     // a clock change orphaned it
  assert.ok(elements.get('audio-error').hidden);
  sent.length = 0;
  elements.get('audio-run').fire('click');
  assert.equal(sent[0].action, 'pdm_setup', 'a stale status no longer skips the setup');
  // The same when the not-set-up error answers a Run: it is shown, and the Run after it sets up.
  audio.handleMessage(status({ audio_ms: 0 }));
  audio.handleMessage(NOT_SET_UP);                                     // answers the pdm_run just sent
  assert.match(elements.get('audio-error').textContent, /not set up/);
  assert.ok(!elements.get('audio-run').disabled);
  sent.length = 0;
  elements.get('audio-run').fire('click');
  assert.equal(sent[0].action, 'pdm_setup');
});

test('Run and Reset while the socket is closed set no pending state', async () => {
  const { audio, elements, sent, net } = runSetup();
  await flush();
  net.open = false;
  elements.get('audio-run').fire('click');
  assert.deepEqual(sent, [{ action: 'pdm_history' }], 'nothing was sent');
  assert.match(elements.get('audio-error').textContent, /Not connected/);
  assert.ok(!elements.get('audio-run').disabled, 'Run is not stuck disabled');
  audio.handleMessage(status({ done: false }));
  elements.get('audio-error').textContent = '';
  elements.get('audio-run').fire('click');                  // set up, so this goes straight to pdm_run
  assert.ok(!elements.get('audio-run').disabled);
  elements.get('audio-reset').fire('click');
  assert.ok(!elements.get('audio-reset').disabled);
  net.open = true;
  elements.get('audio-run').fire('click');
  assert.equal(sent.at(-1).action, 'pdm_run', 'Run works again once the socket is back');
});

test('a reconnect forgets requests of the old socket', async () => {
  const { audio, elements, sent } = runSetup();
  await flush();
  elements.get('audio-run').fire('click');                  // setup pending
  assert.ok(elements.get('audio-run').disabled);
  audio.disconnected();
  assert.ok(!elements.get('audio-run').disabled);
  elements.get('audio-run').fire('click');
  audio.connected();                                        // the reply never came
  assert.ok(!elements.get('audio-run').disabled);
  assert.equal(sent.at(-1).action, 'pdm_history', 'the picture is requested again');
});

test('any error answering a pending request ends it and stops the loop', async () => {
  const { audio, elements, sent } = runSetup();
  await flush();
  elements.get('audio-run').fire('click');
  audio.handleMessage(status({ audio_ms: 0 }));
  audio.handleMessage(status({ running: true, run_id: sent.at(-1).run_id }));   // loop is going
  assert.ok(elements.get('audio-run').disabled);
  const before = sent.length;
  assert.equal(audio.handleMessage({ type: 'error', errors: ['core must be one of [pru0]'] }), true);
  assert.equal(elements.get('audio-error').textContent, 'core must be one of [pru0]');
  assert.ok(!elements.get('audio-run').disabled && elements.get('audio-stop').disabled, 'loop ended');
  audio.handleMessage(status({ running: true, run_id: sent.at(-1).run_id }));   // late chunk reply
  assert.equal(sent.length, before, 'no further pdm_run');
  // A pending reset is answered by an untagged error too.
  audio.handleMessage(status({ done: false }));
  elements.get('audio-reset').fire('click');
  assert.equal(sent.at(-1).action, 'pdm_reset');
  assert.equal(audio.handleMessage({ type: 'error', errors: ['Execution stopped or controlled by another tab'] }), true);
  assert.ok(!elements.get('audio-reset').disabled);
});

test('wiring: nav button, section, script and message routing exist', () => {
  assert.match(html, /data-workspace-view="audio"[^>]*aria-controls="audio-view"/);
  assert.match(html, /<section id="audio-view" class="workspace-view"[^>]*hidden/);
  assert.match(html, /<script src="\/static\/audio\.js[^"]*"><\/script>/);
  for (const id of IDS.concat(['audio-canvas-left', 'audio-canvas-right']))
    assert.ok(html.includes(`id="${id}"`), `index.html has #${id}`);
  assert.match(html, /PRU1 firmware synthesises 2 PDM microphones · PRU0 firmware decodes them · every cycle simulated/);
  assert.match(html, /Left mic/);
  assert.match(html, /Right mic/);
  assert.match(html, /Gen L → PRU0 SD ch0 · rising edge/);
  const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');
  assert.match(app, /PruAudio/);
  assert.doesNotMatch(app, /executes up to `max_steps` instructions\n/, 'stale run comment is fixed');
});
