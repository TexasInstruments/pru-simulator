import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../ui/static/index.html', import.meta.url), 'utf8');
class Element {
  constructor() { this.children = []; this.listeners = {}; this.style = {}; this.dataset = {};
    this.textContent = ''; this.value = ''; this.classes = new Set(); this.attributes = {}; }
  get classList() { return { contains: name => this.classes.has(name), add: name => this.classes.add(name), remove: name => this.classes.delete(name),
    toggle: (name, on) => { if (on) this.classes.add(name); else this.classes.delete(name); } }; }
  get innerHTML() { return this.markup || ''; }
  set innerHTML(value) { this.markup = value; this.children = []; }
  appendChild(child) { this.children.push(child); }
  replaceChildren() { this.children = []; this.markup = ""; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = value; }
  querySelector() { return this.label || null; }
  querySelectorAll() { return []; }
}
function dashboard() {
  // Resolve only IDs actually shipped in the HTML, so absent controls cannot pass.
  const elements = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element()]));
  for (const match of html.matchAll(/<input[^>]*id="([^"]+)"[^>]*value="([^"]*)"/g)) {
    elements.get(match[1]).defaultValue = match[2];
    elements.get(match[1]).value = match[2];
  }
  const get = id => elements.get(id) || null;
  const sent = [];
  const context = vm.createContext({ document: { getElementById: get,
    createElement: () => new Element(), querySelectorAll: () => [] }, window: {}, console,
    currentCore: 'pru0', multiCoreMode: false, activeTab: -1, tabs: [], activeProjectManifest: null,
    asmSource: get('asm-source'), sourceList: get('source-list'), btnLoad: get('btn-load'),
    coreSelect: get('core-select'), devicePanelCore: 'pru0', _lastSourceKey: '',
    availableCores:['pru0','pru1'], coreSelection:{used:['pru0'],shown:['pru0'],focus:'pru0',pinned:false}, coreStates:{},
    persistCoreSelection() {}, renderCorePanes() {}, paintCoreState() {}, updateCoresControl() {}, mcFreshRegs:()=>Array(32).fill('0x00000000'),
    clientBreakpoints: new Set(), signalGraph: { recording: false, windowSize: 128, buf: [], head: 0, fill: 0 },
    stopRun() {}, stopSim() {}, clearErrors() {}, renderTabs() {},
    renderBpBar() {}, initSpadState() {}, syncDeviceCoreSelect() {}, updateCtableForCore() {},
    highlightAsm: text => text, escapeHtml: text => text,
    sendAction: action => sent.push(action),
  });
  const run = code => vm.runInContext(code, context);
  const section = (start, end) => run(app.slice(app.indexOf(start), app.indexOf(end, app.indexOf(start))));
  const loadFunction = name => {
    const start = app.indexOf(`function ${name}(`);
    const open = app.indexOf('{', start); let depth = 1, end = open + 1;
    while (depth) { if (app[end] === '{') depth++; if (app[end] === '}') depth--; end++; }
    run(app.slice(start, end));
  };
  return { get, sent, context, run, section, loadFunction };
}

test('#53: reloading unchanged assembly restores the source listing every time', async () => {
  const d = dashboard();
  d.loadFunction('updateSource');
  d.section('btnLoad.addEventListener', '// ---- Signal graph controls');
  d.context.asmSource.value = 'ldi r0, 1\nhalt';
  const instructions = [{ addr: 0, text: 'ldi r0, 1' }, { addr: 1, text: 'halt' }];
  for (let load = 0; load < 3; load++) {
    await d.get('btn-load').listeners.click();
    assert.equal(d.sent.at(-1).action, 'load');
    d.context.updateSource(instructions, 0, {});
    assert.equal(d.get('source-list').children.length, 2);
  }
});

test('#53: switching cores with identical assembly keeps the source visible', () => {
  const d = dashboard();
  d.loadFunction('updateSource');
  d.run(readFileSync(new URL('../ui/static/cores.js', import.meta.url), 'utf8'));
  d.loadFunction('applyCoreSelection');
  const instructions = [{ addr: 0, text: 'halt' }];
  d.context.updateSource(instructions, 0, {});
  d.context.applyCoreSelection({used:['pru1'],shown:['pru1'],focus:'pru1',pinned:false});
  d.context.updateSource(instructions, 0, {});
  assert.equal(d.get('source-list').children.length, 1);
});

test('#55: UART Record and Signal Graph REC control the same capture state', () => {
  const d = dashboard();
  d.run(readFileSync(new URL('../ui/static/chrome.js', import.meta.url), 'utf8'));
  d.section('function graphSetRecording', 'document.getElementById("graph-clear-btn")');
  d.section('// ---- UART decoder controls', '/* ───────────── UART RX Inject');
  for (const name of ['graphPushSample', 'graphGetSamples', 'graphHandleCapture', 'decodeUARTBits', 'decodeUART']) d.loadFunction(name);
  const uart = d.get('uart-record-btn');
  assert.ok(uart, 'Record must be available next to UART Decode');
  uart.listeners.click();
  assert.equal(d.context.signalGraph.recording, true);
  assert.equal(d.get('graph-rec-btn').classes.has('rec-on'), true);
  assert.equal(uart.attributes['aria-pressed'], 'true');
  // Idle, START, 0x55 LSB first, STOP: four samples per bit.
  const bits = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1].flatMap(bit => [bit, bit, bit, bit]);
  d.context.graphHandleCapture({ mode: 'gpio', core: 'pru0',
    samples: bits.map((bit, step) => [step, bit, 0, 0, 0, 0]) });
  assert.deepEqual(Array.from(d.context.decodeUART(d.context.graphGetSamples()).bytes), [85]);
  d.get('graph-rec-btn').listeners.click();
  assert.equal(d.context.signalGraph.recording, false);
  assert.equal(uart.attributes['aria-pressed'], 'false');
  uart.listeners.click();
  // A full single-shot peripheral capture automatically releases both buttons.
  Object.assign(d.context.signalGraph, { windowSize: 2, buf: [], head: 0, fill: 0 });
  d.context.graphHandleCapture({ mode: 'perif', core: 'pru0', samples: [
    [0, 0, 0, 0, 0, 0], [1, 0, 0, 1, 1, 1], [2, 0, 0, 0, 1, 0],
  ] });
  assert.equal(uart.attributes['aria-pressed'], 'false');
});

test('#56: UART Reset inputs restores the form and clears feedback without a server restart', () => {
  const d = dashboard();
  d.section('/* ───────────── UART RX Inject', '// ---- GP Mux mode selector');
  const reset = d.get('uart-inj-reset-btn');
  assert.ok(reset, 'the injection form must have a local reset control');
  d.get('uart-inj-mode-ascii').listeners.click();
  d.get('uart-inj-payload').value = 'bad draft';
  d.get('uart-inj-baud').value = 'invalid';
  d.get('uart-inj-frames').value = '99';
  d.get('uart-inj-pin').value = '3';
  d.get('uart-inj-btn').listeners.click();
  assert.notEqual(d.get('uart-inj-status').textContent, '');
  reset.listeners.click();
  assert.equal(d.get('uart-inj-status').textContent, '');
  assert.equal(d.get('uart-inj-status').style.display, 'none');
  assert.equal(d.get('uart-inj-baud').value, '4.00');
  assert.equal(d.get('uart-inj-frames').value, '1');
  assert.equal(d.get('uart-inj-pin').value, '0');
  assert.equal(d.get('uart-inj-mode-hex').classes.has('active'), true);
  assert.equal(d.get('uart-inj-byte-count').style.display, 'none');
  assert.deepEqual(Array.from(d.context.window._uartInjectGetBytes()), [72,101,108,108,111,87,111,114,108,100,33]);
  assert.equal(d.sent.length, 0, 'resetting a form must not reset or modify running firmware');
  // Reset also clears the success/arming feedback after a valid injection.
  d.get('uart-inj-btn').listeners.click();
  assert.equal(d.sent.at(-1).action, 'uart_inject');
  reset.listeners.click();
  assert.equal(d.get('uart-inj-status').textContent, '');
});
