const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');

const app = readFileSync(`${__dirname}/../ui/static/app.js`, 'utf8');
const selection = readFileSync(`${__dirname}/../ui/static/cores.js`, 'utf8');

function dashboard(cores) {
  const context = vm.createContext({
    fetch: async () => ({ ok: true, json: async () => cores }),
    availableCores: ['pru0', 'pru1', 'rtu0', 'rtu1'],
    coreSelection: { used: ['rtu1'], shown: ['rtu1'], focus: 'rtu1', pinned: false },
    currentCore: 'rtu1', stopRun() {}, stopSim() {},
  });
  vm.runInContext(selection, context);
  // Rendering is covered by ui_controls; keep the real selection normalization here.
  vm.runInContext(`function applyCoreSelection(next) {
    coreSelection = next; currentCore = next.focus;
  }`, context);
  for (const name of ['loadCoreChoices', 'showAvailableCores']) {
    const fn = app.match(new RegExp(`(?:async )?function ${name}\\(\\) \\{[\\s\\S]*?\\n\\}|function ${name}\\(cores\\) \\{[\\s\\S]*?\\n\\}`));
    assert.ok(fn, `${name} is implemented`);
    vm.runInContext(fn[0], context);
  }
  return context;
}

test('refreshing target cores keeps a valid RTU1 deck selection', async () => {
  const ui = dashboard(['pru0', 'rtu0', 'pru1', 'rtu1']);
  await ui.loadCoreChoices();
  assert.equal(ui.currentCore, 'rtu1');
  assert.deepEqual(Array.from(ui.coreSelection.used), ['rtu1']);
  assert.deepEqual(Array.from(ui.availableCores), ['pru0', 'pru1', 'rtu0', 'rtu1']);
});

test('a target without RTU1 prunes it from the deck and repairs focus', async () => {
  const ui = dashboard(['pru0', 'rtu0', 'pru1']);
  await ui.loadCoreChoices();
  assert.equal(ui.currentCore, 'pru0');
  assert.deepEqual(Array.from(ui.coreSelection.used), ['pru0']);
  assert.deepEqual(Array.from(ui.coreSelection.shown), ['pru0']);
  assert(!ui.availableCores.includes('rtu1'));
});

test('failed target discovery preserves the current selection', async () => {
  const ui = dashboard([]);
  ui.fetch = async () => ({ ok: false, status: 503 });
  await assert.rejects(ui.loadCoreChoices(), /503/);
  assert.equal(ui.currentCore, 'rtu1');
});

test('each visible register pane shows its own CRC state', () => {
  const field = { dataset: { paneReadout: 'carry' }, textContent: '' };
  const ui = vm.createContext({
    currentCore: 'pru0', devicePanelCore: 'pru0', coreSelection: { shown: ['pru0', 'rtu1'] },
    mcBreakpoints: {}, updateMCRegisters() {}, updateMCSource() {},
    document: { querySelectorAll: () => [{ dataset: { core: 'rtu1' }, querySelector: () => field }] },
  });
  vm.runInContext(app.match(/function paintCoreState\(state\) \{[\s\S]*?\n\}/)[0], ui);
  ui.paintCoreState({ core: 'rtu1', carry: false, registers: [], instructions: [],
    crc: { mode: 'CRC32', value: '1234ABCD' } });
  assert.match(field.textContent, /Carry 0.*CRC32 1234ABCD/);
});


test('target replacement clears stale inspectors without requesting a removed core', async () => {
  const ui = dashboard(['pru0', 'rtu0', 'pru1']);
  const actions = [];
  const button = { active: true, dataset: { group: '0' },
    classList: { toggle(name, value) { button.active = value; } } };
  Object.assign(ui, {
    _lastSourceKey: 'old', sourceList: { textContent: 'old source', replaceChildren() { this.textContent = ''; } },
    devicePanelCore: 'rtu1', coreStates: {},
    document: { querySelectorAll: () => [button] },
    mcFreshRegs: () => Array(32).fill('0x00000000'), initSpadState() {}, updateCtableForCore() {},
    persistCoreSelection() {}, renderCorePanes() {}, syncDeviceCoreSelect() {},
    paintCoreState() {}, updateCoresControl() {}, sendAction(action) { actions.push(action); },
  });
  for (const name of ['applyCoreSelection', 'syncGpioLoopback']) {
    vm.runInContext(app.match(new RegExp(`function ${name}\\([^\\n]*\\) \\{[\\s\\S]*?\\n\\}`))[0], ui);
  }
  await ui.loadCoreChoices();
  assert.equal(ui.currentCore, 'pru0');
  assert.equal(button.active, false);
  assert.equal(ui.sourceList.textContent, '');
  assert.equal(ui._lastSourceKey, '');
  assert.deepEqual(Array.from(ui.prevRegisters), Array(32).fill('0x00000000'));
  assert.deepEqual(actions.map(action => action.core), ['pru0']);
});

test('GPIO loopback buttons follow the selected cores actual mask', () => {
  const buttons = [0, 1, 2, 3, 4].map(group => ({ dataset: { group: String(group) }, active: true }));
  for (const button of buttons) button.classList = { toggle(name, value) { button.active = value; } };
  const context = vm.createContext({ document: { querySelectorAll: () => buttons } });
  const fn = app.match(/function syncGpioLoopback\(io\) \{[\s\S]*?\n\}/);
  assert.ok(fn, 'loopback controls must synchronize from server state');
  vm.runInContext(fn[0], context);
  context.syncGpioLoopback({ gpio_loopback_mask: 0xF0 });
  assert.deepEqual(buttons.map(button => button.active), [false, true, false, false, false]);
  context.syncGpioLoopback({ gpio_loopback_mask: 0 });
  assert(buttons.every(button => !button.active));
});

test('loopback clicks target the core displayed by the pins panel', () => {
  const actions = [];
  const context = vm.createContext({
    multiCoreMode: true, currentCore: 'pru1',
    sendAction(action) { actions.push(action); },
  });
  vm.runInContext(app.match(/function onLoopbackToggle\(btn\) \{[\s\S]*?\n\}/)[0], context);
  const button = { dataset: { group: '1' }, classList: {
    contains() { return false; }, toggle() {},
  } };
  context.onLoopbackToggle(button);
  assert.deepEqual(actions.map(action => [action.action, action.core]), [
    ['set_loopback', 'pru1'], ['get_state', 'pru1'],
  ]);
  actions.length = 0;
  context.multiCoreMode = false;
  context.onLoopbackToggle(button);
  assert(actions.every(action => action.core === 'pru1'));
  assert.equal(actions.length, 2);
});

test('multicore keyboard undo runs in reverse step order', () => {
  const actions = []; let onKey;
  const context = vm.createContext({
    document: { addEventListener(type, listener) { onKey = listener; } },
    multiCoreMode: true, executionCores: () => ['pru0', 'rtu1'], currentCore: 'pru0',
    helpModal: { style: { display: 'none' } }, stopRun() {}, stopSim() {},
    sendAction(action) { actions.push(action); },
  });
  const start = app.indexOf('document.addEventListener("keydown"', app.indexOf('// Keyboard shortcuts'));
  const end = app.indexOf('\n});', start) + 4;
  vm.runInContext(app.slice(start, end), context);
  onKey({ key: 'ArrowLeft', target: { tagName: 'BODY' }, preventDefault() {} });
  assert.deepEqual(actions.map(action => action.core), ['rtu1', 'pru0']);
  assert(actions.every(action => action.action === 'step_back'));
});
