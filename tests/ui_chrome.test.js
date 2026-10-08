import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const frames = [];
const context = vm.createContext({ requestAnimationFrame: fn => { frames.push(fn); } });
vm.runInContext(readFileSync(new URL('../ui/static/chrome.js', import.meta.url), 'utf8'), context);
const C = context.PruChrome;
const plain = value => JSON.parse(JSON.stringify(value));

// setButtonLabel keeps the wipe's inner spans; plain buttons just get the text.
const text = { textContent: 'Run' };
const wrapped = { querySelector: selector => (selector === '.text' ? text : null), textContent: 'Run' };
C.setButtonLabel(wrapped, 'Stop');
assert.equal(text.textContent, 'Stop');
assert.equal(wrapped.textContent, 'Run', 'the wrapper is untouched');
const bare = { querySelector: () => null, textContent: 'Run' };
C.setButtonLabel(bare, 'Stop SIM');
assert.equal(bare.textContent, 'Stop SIM');
assert.doesNotThrow(() => C.setButtonLabel(null, 'x'));
// A .btn-17 button built in script gets the spans on its first label, then reuses them.
class Span {
  constructor() { this.children = []; this.className = ''; this.textContent = ''; }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...kids) { this.children = kids; }
  querySelector(selector) {
    const walk = node => node.children.flatMap(child => [child, ...walk(child)]);
    return walk(this).find(n => '.' + n.className === selector) || null;
  }
}
const made = new Span();
made.ownerDocument = { createElement: () => new Span() };
made.classList = { contains: name => name === 'btn-17' };
C.setButtonLabel(made, 'Detach');
assert.equal(made.children[0].className, 'text-container');
assert.equal(made.querySelector('.text').textContent, 'Detach');
C.setButtonLabel(made, 'Open');
assert.equal(made.children.length, 1, 'labels are rewritten in place');
assert.equal(made.querySelector('.text').textContent, 'Open');
assert.equal(context.setButtonLabel, C.setButtonLabel, 'app.js calls it as a global');

// Multi-core view: PRU0 leads, the partner is second, extras fill slots three and four.
assert.deepEqual(plain(C.mcCores('rtu0', [])), ['pru0', 'rtu0']);
assert.deepEqual(plain(C.mcCores('pru1', ['rtu1', 'rtu0'])), ['pru0', 'pru1', 'rtu0', 'rtu1'], 'extras keep core order');
assert.deepEqual(plain(C.mcExtras('rtu0', ['rtu0', 'pru1', 'pru1', 'nope'])), ['pru1'], 'partner, duplicates and unknown cores dropped');
assert.deepEqual(plain(C.MC_SLOTS), ['pru0', 'rtu0', 'x2', 'x3'], 'the first two slots keep the dual view ids');
{
  const shown = C.mcCores('pru1', ['rtu1']);
  assert.equal(C.mcSlot(shown, 'pru0'), 'pru0');
  assert.equal(C.mcSlot(shown, 'pru1'), 'rtu0', 'the partner renders into the second slot');
  assert.equal(C.mcSlot(shown, 'rtu1'), 'x2');
  assert.equal(C.mcSlot(shown, 'rtu0'), null, 'a core that is not shown has no slot');
}
assert.deepEqual([2, 3, 4].map(C.mcMode), ['mc', 'mc3', 'mc4']);

// Tabs keyboard model.
assert.equal(C.nextTabIndex('ArrowRight', 0, 3), 1);
assert.equal(C.nextTabIndex('ArrowRight', 2, 3), 0);
assert.equal(C.nextTabIndex('ArrowDown', 1, 3), 2);
assert.equal(C.nextTabIndex('ArrowLeft', 0, 3), 2);
assert.equal(C.nextTabIndex('ArrowUp', 2, 3), 1);
assert.equal(C.nextTabIndex('Home', 2, 3), 0);
assert.equal(C.nextTabIndex('End', 0, 3), 2);
assert.equal(C.nextTabIndex('Enter', 0, 3), -1);
assert.equal(C.nextTabIndex('ArrowRight', 0, 0), -1);

// Glider geometry comes from the active tab alone, for any number of tabs.
assert.deepEqual(plain(C.gliderBox({ offsetLeft: 103, offsetWidth: 88 })), { x: 103, width: 88 });
assert.equal(C.gliderBox({ offsetLeft: 0, offsetWidth: 0 }), null, 'a hidden strip has nothing to measure');
assert.equal(C.gliderBox(undefined), null);
assert.deepEqual(plain(C.gliderStyle({ x: 103, width: 88 })), { '--glider-x': '103px', '--glider-w': '88px' });
assert.equal(C.gliderStyle(null), null);
const strip = { dataset: {}, props: {}, cls: new Set(),
  style: { setProperty(name, value) { strip.props[name] = value; } },
  classList: { add: name => strip.cls.add(name) } };
assert.equal(C.placeGlider(strip, { offsetLeft: 0, offsetWidth: 0 }), false);
assert.equal(strip.cls.has('has-glider'), false, 'a strip that cannot be measured is left alone');
assert.equal(C.placeGlider(strip, { offsetLeft: 5, offsetWidth: 60 }), true);
assert.deepEqual(strip.props, { '--glider-x': '5px', '--glider-w': '60px' });
assert.ok(strip.cls.has('has-glider'));
assert.equal(strip.dataset.ready, undefined, 'the first placement is not animated');
frames.splice(0).forEach(fn => fn());
assert.equal(strip.dataset.ready, undefined, 'one frame is not enough: the placement must have painted');
frames.splice(0).forEach(fn => fn());
assert.equal(strip.dataset.ready, '1');
C.placeGlider(strip, { offsetLeft: 70, offsetWidth: 90 });
assert.equal(strip.props['--glider-x'], '70px');
assert.equal(frames.length, 0, 'later moves do not re-arm the first-paint guard');

// Fault accounting.
const bus = faults => ({ io: { device_bus: { faults } } });
assert.equal(C.faultTotal({}), 0);
assert.equal(C.faultTotal(bus(['a', 'b'])), 2);
assert.equal(C.faultTotal({ ...bus(['a']), core_faults: { pru0: {}, rtu0: {} } }), 3);
assert.equal(C.faultTotal({ core: 'pru1', fault: { opcode: 'X' } }), 1, 'falls back to the single-core fault');
assert.equal(C.faultTotal({ io: { device_bus: { events: [{}, {}], faults: [] } } }), 0, 'events are not faults');
assert.equal(C.newFaultCount(5, 2), 3);
assert.equal(C.newFaultCount(2, 2), 0);
assert.equal(C.newFaultCount(1, 4), 0);
assert.equal(C.badgeLabel(1), '1 new fault');
assert.equal(C.badgeLabel(7), '7 new faults');
const fault = { opcode: 'LBBO', address: 12, error: 'Unmapped memory' };
assert.deepEqual(plain(C.coreFaultLines({ core: 'pru0', core_faults: { pru0: fault, rtu1: fault } }, ['pru0'])),
  ['pru0 · LBBO at 12: Unmapped memory']);
assert.deepEqual(plain(C.coreFaultLines({ core: 'pru0', fault }, ['pru0', 'rtu0'])),
  ['pru0 · LBBO at 12: Unmapped memory']);

// Markup contract of the reorganised dashboard.
const html = readFileSync(new URL('../ui/static/index.html', import.meta.url), 'utf8');
const section = id => {
  const start = html.indexOf(`id="${id}"`);
  assert.ok(start >= 0, `${id} exists`);
  const next = html.indexOf('\n  <section id=', start);
  return html.slice(start, next < 0 ? undefined : next);
};
const ioPanel = html.slice(html.indexOf('id="io-panel"'), html.indexOf('id="signal-graph-panel"'));
for (const id of ['io-mux-sel', 'gpo-grid', 'gpi-grid', 'loopback-strip'])
  assert.ok(ioPanel.includes(`id="${id}"`), `Simulator I/O Pins keeps ${id}`);
for (const id of ['uart-decoder', 'uart-rx-inject', 'sd-interface', 'perif-interface',
  'i2c-attach-strip', 'i2c-attach-btn', 'device-runtime-panel', 'i2c-interface'])
  assert.ok(!ioPanel.includes(`id="${id}"`), `${id} left the Simulator I/O Pins panel`);
const ioView = section('io-view');
for (const id of ['device-runtime-panel', 'ssi-attach-button', 'tca-attach-button', 'uart-decoder',
  'uart-rx-inject', 'ssi-device-list', 'sd-interface', 'perif-interface', 'device-event-log'])
  assert.ok(ioView.includes(`id="${id}"`), `I/O & Devices holds ${id}`);
const nav = html.match(/<nav class="view-switch"[\s\S]*?<\/nav>/)[0];
assert.deepEqual([...nav.matchAll(/data-workspace-view="(\w+)"/g)].map(m => m[1]), ['simulator', 'io', 'motor']);
assert.match(nav, /I\/O &amp; Devices/);
assert.deepEqual([...html.matchAll(/data-io-tab="(\w+)"/g)].map(m => m[1]), ['devices', 'peripherals', 'events']);
assert.equal((html.match(/class="view-glider"/g) || []).length, 2, 'one glider per tab strip');
for (const id of ['btn-multicore', 'btn-step', 'btn-run', 'btn-sim', 'btn-reset', 'btn-hard-reset',
  'btn-config', 'btn-reset-layout', 'btn-help'])
  assert.match(html, new RegExp(`<button id="${id}" class="btn-17[^"]*"[^>]*><span class="text-container"><span class="text">`),
    `${id} wraps its label for the wipe`);

// Every wipe button wraps its label; panel and device buttons share the mechanism.
for (const [, attrs, inner] of html.matchAll(/<button\b([^>]*\bbtn-17\b[^>]*)>([\s\S]*?)<\/button>/g))
  assert.match(inner, /^<span class="text-container"><span class="text">/, `wipe button ${attrs.trim()} wraps its label`);
for (const id of ['btn-file', 'btn-load', 'bp-add-btn', 'btn-mem-fill', 'btn-config-save', 'btn-fill-apply',
  'tca-attach-button', 'uart-inj-btn', 'motor-start', 'motor-physics-defaults'])
  assert.match(html, new RegExp(`<button [^>]*id="${id}"[^>]*class="[^"]*btn-17|<button [^>]*class="[^"]*btn-17[^"]*"[^>]*id="${id}"`), `${id} wipes on hover`);
for (const id of ['spad', 'loopback', 'mem-btn', 'panel-toggle'])
  assert.ok(!new RegExp(`<button[^>]*(id|class)="[^"]*${id}[^"]*btn-17`).test(html), `${id} toggles keep their own hover`);

// Nothing writes textContent on a wipe button (it would destroy the label spans).
const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');
assert.ok(!/\bbtn(Run|Sim|Step|Reset|HardReset|Config|Help|Multicore)\.textContent\s*=/.test(app));

// Run, Step, Reset and the shortcuts address every shown core, not a fixed pair.
assert.match(app, /run_multicore[\s\S]{0,80}partners: mcShown\(\)\.slice\(1\)/);
assert.equal((app.match(/for \(const core of mcShown\(\)\) sendAction\(\{ action: "step", core, count: 1 \}\)/g) || []).length, 3,
  'Step button, SIM timer and ArrowRight step all shown cores');

// Multi-core markup: a panel pair per slot, and a toggle per candidate core.
for (const slot of ['pru0', 'rtu0', 'x2', 'x3'])
  for (const id of [`mc-${slot}-source-panel`, `mc-${slot}-reg-panel`, `mc-${slot}-source-list`, `mc-${slot}-reg-tbody`,
    `mc-${slot}-pc`, `mc-${slot}-bp-add-btn`, `mc-${slot}-bp-chip-list`])
    assert.ok(html.includes(`id="${id}"`), `multi-core slot ${slot} has ${id}`);
assert.deepEqual([...html.matchAll(/data-mc-extra="(\w+)"/g)].map(m => m[1]), ['rtu0', 'pru1', 'rtu1']);

// IEP clock selector: runtime-only choice sent over the WebSocket, shown from state.
const iepSelect = html.match(/<select id="iep-clock-select"[\s\S]*?<\/select>/)[0];
assert.deepEqual([...iepSelect.matchAll(/<option value="(\d+)"/g)].map(m => m[1]), ['200', '225', '250', '300', '333']);
const clockSection = app.slice(app.indexOf('// ---- IEP counter clock selector'), app.indexOf('// ---- end IEP counter clock selector'));
assert.ok(clockSection.length > 0 && !/fetch\(/.test(clockSection), 'IEP session choice does not call REST');
// Clock rendering and actions are exercised with a DOM harness in ui_controls.test.js.

// Every id the scripts look up exists (three are created at run time).
const known = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));
const dynamic = new Set(['_src-menu', '_proj-menu']);  // menus created on demand
for (const file of ['app.js', 'workspace.js', 'motor.js', 'layout.js', 'chrome.js']) {
  const source = readFileSync(new URL(`../ui/static/${file}`, import.meta.url), 'utf8');
  for (const [, id] of source.matchAll(/getElementById\(\s*['"]([\w-]+)['"]\s*\)/g))
    assert.ok(known.has(id) || dynamic.has(id), `${file} looks up #${id}, which is not in index.html`);
}
console.log('Chrome helpers, glider, fault badge and markup contract pass');

// Actual target capabilities constrain partners/extras even after a target reload.
assert.deepEqual(plain(C.mcCores('rtu1', ['pru1', 'rtu1'], ['pru0', 'rtu0', 'pru1'])),
  ['pru0', 'rtu0', 'pru1']);
assert.deepEqual(plain(C.mcExtras('rtu0', ['pru1', 'rtu1'], ['pru0', 'rtu0', 'pru1'])), ['pru1']);
