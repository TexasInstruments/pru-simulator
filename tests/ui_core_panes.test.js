import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Runs the real renderCorePanes / applyCoreSelection / layout.js visibility code
// against a small fake DOM that models detaching and re-attaching nodes.
const read = name => readFileSync(new URL(`../ui/static/${name}`, import.meta.url), 'utf8');
const app = read('app.js');
function load(context, name) {
  const start = app.indexOf(`function ${name}(`);
  let end = app.indexOf('{', start) + 1, depth = 1;
  while (depth) { if (app[end] === '{') depth++; if (app[end] === '}') depth--; end++; }
  vm.runInContext(app.slice(start, end), context);
}
const plain = value => JSON.parse(JSON.stringify(value));

class Node {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.dataset = {}; this.listeners = {}; this.attrs = {};
    this.parentNode = null; this.className = ''; this.id = ''; this.textContent = ''; this.hidden = false;
    this.style = { setProperty() {} };
  }
  set innerHTML(html) { this.children = []; }
  detach() {
    if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this);
    this.parentNode = null;
  }
  appendChild(child) { child.detach(); child.parentNode = this; this.children.push(child); return child; }
  removeChild(child) { child.detach(); return child; }
  prepend(child) { child.detach(); child.parentNode = this; this.children.unshift(child); }
  append(...kids) { kids.forEach(k => this.appendChild(k)); }
  replaceChildren(...kids) {
    this.children.forEach(c => { c.parentNode = null; });
    this.children = []; kids.forEach(k => this.appendChild(k));
  }
  remove() { this.detach(); }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  click() { this.listeners.click({ stopPropagation() {} }); }
  descendants() { return this.children.flatMap(c => [c, ...c.descendants()]); }
  matches(selector) {
    return selector.startsWith('.') ? this.className.split(' ').includes(selector.slice(1)) : false;
  }
  querySelectorAll(selector) { return this.descendants().filter(n => n.matches(selector)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function dashboard({ used, shown, focus }) {
  const sourcePanes = new Node(); sourcePanes.id = 'source-panes';
  const regBody = new Node(); regBody.id = 'reg-body';
  const sourceFocusPane = new Node('section'); sourceFocusPane.id = 'source-focus-pane';
  const registerFocusPane = new Node('section'); registerFocusPane.id = 'register-focus-pane';
  sourcePanes.appendChild(sourceFocusPane); regBody.appendChild(registerFocusPane);
  const fixed = { 'source-panes': sourcePanes, 'reg-body': regBody,
    'source-core-chips': new Node(), 'register-core-chips': new Node(), 'carry-row': new Node() };
  const root = new Node(); root.append(sourcePanes, regBody);
  const chipHosts = [fixed['source-core-chips'], fixed['register-core-chips']];
  const events = {};
  const storage = {};
  const requests = [];
  const context = vm.createContext({
    document: {
      // Like the browser, only elements still attached to the document are found.
      getElementById: id => fixed[id] || root.descendants().find(n => n.id === id) || null,
      createElement: tag => new Node(tag),
      addEventListener: (type, fn) => { events[type] = fn; },
      dispatchEvent: event => { events[event.type]?.(event); },
    },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init?.detail; } },
    localStorage: { getItem: k => storage[k] ?? null, setItem: (k, v) => { storage[k] = String(v); },
      removeItem: k => { delete storage[k]; } },
    sourceFocusPane, registerFocusPane,
    availableCores: ['pru0', 'pru1', 'rtu0', 'rtu1'],
    coreSelection: { used, shown, focus }, currentCore: focus, devicePanelCore: focus, ioCore: used[0],
    multiCoreMode: used.length > 1, executionReady: true, coreStates: {},
    mcPrevRegs: {}, mcLastSourceKey: {}, _lastSourceKey: '', sourceList: new Node(),
    stopRun() {}, stopSim() {}, sendAction: r => requests.push(r), selectEditorCore() {},
    mcFreshRegs: () => [], initSpadState() {}, syncGpioLoopback() {}, updateCtableForCore() {},
    persistCoreSelection() {}, syncDeviceCoreSelect() {}, paintCoreState() {}, updateCoresControl() {},
    buildMCRegTable() {}, updatePins() {},
    currentTree: {}, currentMode: 'sc',
  });
  vm.runInContext(read('cores.js'), context);
  vm.runInContext(read('layout.js'), context);
  context.render = () => {};   // layout.js tiling is not under test here
  for (const name of ['applyCoreSelection', 'setPaneCoreVisible', 'paneHeading', 'renderCorePanes']) {
    load(context, name);
  }
  // initCoresControl registers this listener in the app.
  events['pru-layout-changed'] = () => context.renderCorePanes();
  return { context, sourcePanes, regBody, sourceFocusPane, registerFocusPane, chipHosts, requests, events };
}
const headings = pane => pane.querySelectorAll('.core-pane-heading');
const paneCores = host => host.children.map(c => c.dataset.core);
const chipState = host => host.children.map(c =>
  `${c.textContent}:${c.className.split(' ').includes('visible') ? 'on' : 'off'}`);

test('hiding then showing the only visible core restores its panes', () => {
  const d = dashboard({ used: ['pru0'], shown: ['pru0'], focus: 'pru0' });
  d.context.renderCorePanes();
  assert.deepEqual(paneCores(d.sourcePanes), ['pru0']);
  d.chipHosts[0].children[0].click();                      // hide PRU0 in Source
  assert.deepEqual(paneCores(d.sourcePanes), []);
  assert.deepEqual(paneCores(d.regBody), ['pru0'], 'Registers is untouched');
  d.chipHosts[0].children[0].click();                      // show it again
  assert.deepEqual(paneCores(d.sourcePanes), ['pru0']);
  assert.doesNotThrow(() => d.context.renderCorePanes());
  assert.doesNotThrow(() => d.events['pru-layout-changed']());
  // Use RTU0 afterwards still gets a Source chip and pane
  d.context.applyCoreSelection({ used: ['pru0', 'rtu0'], shown: ['pru0', 'rtu0'], focus: 'pru0' });
  assert.deepEqual(chipState(d.chipHosts[0]), ['PRU0:on', 'RTU0:on']);
  assert.deepEqual(paneCores(d.sourcePanes).sort(), ['pru0', 'rtu0']);
});

test('a layout change or Reset Layout re-renders after the only visible pane was hidden', () => {
  const d = dashboard({ used: ['pru0'], shown: ['pru0'], focus: 'pru0' });
  d.context.renderCorePanes();
  d.chipHosts[1].children[0].click();                      // hide in Registers
  assert.deepEqual(paneCores(d.regBody), []);
  assert.doesNotThrow(() => d.events['pru-layout-changed']());
  vm.runInContext('coreVisibility = {}', d.context);       // what resetLayout() does
  assert.doesNotThrow(() => d.events['pru-layout-changed']());
  assert.deepEqual(paneCores(d.regBody), ['pru0']);
});

test('hiding the focus core moves focus through applyCoreSelection', () => {
  const d = dashboard({ used: ['pru0', 'pru1'], shown: ['pru0', 'pru1'], focus: 'pru0' });
  d.context.renderCorePanes();
  d.context._lastSourceKey = 'stale';
  d.context.sourceList.children = [new Node()];
  d.chipHosts[0].children[0].click();                      // hide PRU0 (focus) in Source
  assert.equal(d.context.currentCore, 'pru1');
  assert.equal(d.context.coreSelection.focus, 'pru1', 'selection model follows the focus');
  assert.equal(d.context.devicePanelCore, 'pru1');
  assert.equal(d.context._lastSourceKey, '', 'source cache reset');
  assert.equal(d.context.sourceList.children.length, 0, 'source list cleared');
  assert.ok(d.requests.some(r => r.action === 'get_state' && r.core === 'pru1'));
  assert.deepEqual(paneCores(d.sourcePanes), ['pru1']);
  assert.deepEqual(paneCores(d.regBody).sort(), ['pru0', 'pru1'], 'Registers still shows both');
  assert.equal(d.registerFocusPane.dataset.core, 'pru1');
});

test('pane X hides the core in that panel group only and keeps the chip', () => {
  const d = dashboard({ used: ['pru0', 'pru1'], shown: ['pru0', 'pru1'], focus: 'pru0' });
  d.context.renderCorePanes();
  headings(d.sourceFocusPane)[0].children.at(-1).click();  // X on PRU0 in Source
  assert.equal(d.context.getCoreVisibility('source', 'pru0'), false);
  assert.equal(d.context.getCoreVisibility('registers', 'pru0'), true, 'Registers keeps the core');
  assert.deepEqual(plain(d.context.coreSelection.shown), ['pru0', 'pru1'], 'core stays shown');
  assert.deepEqual(chipState(d.chipHosts[0]), ['PRU0:off', 'PRU1:on'], 'Source chip stays, unfilled');
  assert.deepEqual(chipState(d.chipHosts[1]), ['PRU0:on', 'PRU1:on']);
  assert.deepEqual(paneCores(d.sourcePanes), ['pru1']);
  // X on a non-focus pane
  const extra = d.regBody.children.find(c => c.dataset.core === 'pru1');
  headings(extra)[0].children.at(-1).click();
  assert.equal(d.context.getCoreVisibility('registers', 'pru1'), false);
  assert.equal(d.context.getCoreVisibility('source', 'pru1'), true);
});

test('a lone visible pane keeps its core label while several cores are shown', () => {
  const d = dashboard({ used: ['pru0', 'pru1'], shown: ['pru0', 'pru1'], focus: 'pru0' });
  d.context.renderCorePanes();
  d.chipHosts[0].children[1].click();                      // hide PRU1 in Source
  assert.equal(d.sourcePanes.dataset.count, '1');
  assert.equal(d.sourcePanes.dataset.multi, '1', 'CSS keeps the heading');
  assert.equal(headings(d.sourceFocusPane)[0].children[0].textContent, 'PRU0');
  // single-core session: no label, unchanged behaviour
  const single = dashboard({ used: ['pru0'], shown: ['pru0'], focus: 'pru0' });
  single.context.renderCorePanes();
  assert.equal(single.sourcePanes.dataset.multi, '0');
  assert.match(read('cores.css'),
    /\.core-panes\[data-count="1"\]:not\(\[data-multi="1"\]\) \.core-pane-heading \{ display:none/);
});

test('Use reveals a core that was hidden in Source and Registers', () => {
  const d = dashboard({ used: ['pru0'], shown: ['pru0'], focus: 'pru0' });
  let action;
  d.context.setClockDirect = () => {};
  d.context.createCoresDeck = (_root, handlers) => { action = handlers.action; return { shake() {}, pulse() {} }; };
  load(d.context, 'initCoresControl');
  d.context.initCoresControl();
  d.context.setCoreVisibility('source', 'rtu0', false);
  d.context.setCoreVisibility('registers', 'rtu0', false);
  action({ type: 'use', core: 'rtu0' });
  assert.equal(d.context.getCoreVisibility('source', 'rtu0'), true);
  assert.equal(d.context.getCoreVisibility('registers', 'rtu0'), true);
  assert.deepEqual(paneCores(d.sourcePanes).sort(), ['pru0', 'rtu0']);
  assert.deepEqual(paneCores(d.regBody).sort(), ['pru0', 'rtu0']);
});
