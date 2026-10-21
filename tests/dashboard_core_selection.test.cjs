const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');

const app = readFileSync(`${__dirname}/../ui/static/app.js`, 'utf8');

function dashboard(cores) {
  function select(value) {
    return {
      value, options: [],
      replaceChildren(...options) { this.options = options; this.value = options[0].value; },
    };
  }
  const context = vm.createContext({
    fetch: async () => ({ ok: true, json: async () => cores }),
    Option: function(text, value) { this.text = text; this.value = value; },
    coreSelect: select('pru0'), mcPartnerSelect: select('rtu0'), mcLoadCore: select('pru0'),
    currentCore: 'pru0', mcPartner: 'rtu0', applyMCPartnerLabels() {},
    prevRegisters: [], _lastSourceKey: '', sourceList: { innerHTML: '' },
    initSpadState() {},
    document: { querySelectorAll: () => [], getElementById: () => null },
  });
  // Run the production selector function with the DOM and HTTP boundaries supplied.
  const fn = app.match(/async function loadCoreChoices\(\) \{[\s\S]*?\n\}/);
  if (fn) vm.runInContext(fn[0], context);
  return context;
}

test('RTU1 can be selected and kept as the current, partner and load target', async () => {
  const ui = dashboard(['pru0', 'rtu0', 'pru1', 'rtu1']);
  assert.equal(typeof ui.loadCoreChoices, 'function', 'Dashboard must refresh target core choices');
  await ui.loadCoreChoices();
  for (const select of [ui.coreSelect, ui.mcPartnerSelect, ui.mcLoadCore]) {
    assert(select.options.some(option => option.value === 'rtu1'));
    select.value = 'rtu1';
  }
  await ui.loadCoreChoices();
  assert.equal(ui.currentCore, 'rtu1');
  assert.equal(ui.mcPartner, 'rtu1');
  assert.equal(ui.mcLoadCore.value, 'rtu1');
  assert(!ui.mcPartnerSelect.options.some(option => option.value === 'pru0'));
});

test('a target without RTU1 removes it and replaces stale selections', async () => {
  const ui = dashboard(['pru0', 'rtu0', 'pru1']);
  assert.equal(typeof ui.loadCoreChoices, 'function', 'Dashboard must refresh target core choices');
  for (const select of [ui.coreSelect, ui.mcPartnerSelect, ui.mcLoadCore]) select.value = 'rtu1';
  await ui.loadCoreChoices();
  assert.equal(ui.currentCore, 'pru0');
  assert.equal(ui.mcPartner, 'rtu0');
  assert.equal(ui.mcLoadCore.value, 'pru0');
  for (const select of [ui.coreSelect, ui.mcPartnerSelect, ui.mcLoadCore]) {
    assert(!select.options.some(option => option.value === 'rtu1'));
  }
});


test('target replacement clears loopback display and stale source caches without touching removed cores', async () => {
  const ui = dashboard(['pru0', 'rtu0', 'pru1']);
  ui.currentCore = 'rtu1'; ui.coreSelect.value = 'rtu1';
  ui._lastSourceKey = 'old'; ui.sourceList.innerHTML = 'old source';
  const button = { active: true, classList: { remove() { button.active = false; } } };
  ui.document.querySelectorAll = () => [button];
  ui.sendAction = () => assert.fail('target refresh must not send actions to removed cores');
  await ui.loadCoreChoices();
  assert.equal(ui.currentCore, 'pru0');
  assert.equal(button.active, false);
  assert.equal(ui.sourceList.innerHTML, '');
  assert.equal(ui._lastSourceKey, '');
  assert.deepEqual(Array.from(ui.prevRegisters), Array(32).fill('0x00000000'));
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
  assert.equal(actions[0].core, 'pru0');
  context.multiCoreMode = false;
  context.onLoopbackToggle(button);
  assert.equal(actions[1].core, 'pru1');
});

test('multicore keyboard undo runs in reverse step order', () => {
  const actions = []; let onKey;
  const context = vm.createContext({
    document: { addEventListener(type, listener) { onKey = listener; } },
    multiCoreMode: true, mcPartner: 'rtu1', currentCore: 'pru0',
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
