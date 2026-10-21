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
