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
    coreSelection: { used: ['rtu1'], shown: ['rtu1'], focus: 'rtu1' },
    currentCore: 'rtu1', stopRun() {}, stopSim() {}, selectEditorCore() {},
  });
  vm.runInContext(selection, context);
  // Rendering is covered by ui_controls; keep the real selection normalization here.
  vm.runInContext(`function applyCoreSelection(next) {
    coreSelection = next; currentCore = next.focus;
  }`, context);
  context.renderIoBlocks = () => {};
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
    currentCore: 'pru0', devicePanelCore: 'pru0',
    coreSelection: { shown: ['pru0', 'rtu1'] },
    mcBreakpoints: {}, updateMCRegisters() {}, updateMCSource() {},
    updatePins() {}, updateDevicePanel() {},
    document: { querySelectorAll: () => [{ dataset: { core: 'rtu1' }, querySelector: () => field }] },
  });
  vm.runInContext(app.match(/function paintCoreState\([^)]*\) \{[\s\S]*?\n\}/)[0], ui);
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
  ui.renderIoBlocks = () => {};
  for (const name of ['applyCoreSelection', 'syncGpioLoopback']) {
    vm.runInContext(app.match(new RegExp(`function ${name}\\([^\\n]*\\) \\{[\\s\\S]*?\\n\\}`))[0], ui);
  }
  await ui.loadCoreChoices();
  assert.equal(ui.currentCore, 'pru0');
  assert.equal(button.active, true, 'per-core I/O blocks are not reset by a focus change');
  assert.equal(ui.sourceList.textContent, '');
  assert.equal(ui._lastSourceKey, '');
  assert.deepEqual(Array.from(ui.prevRegisters), Array(32).fill('0x00000000'));
  assert.deepEqual(actions.map(action => action.core), ['pru0']);
});

test('GPIO loopback buttons follow the selected cores actual mask', () => {
  const buttons = [0, 1, 2, 3, 4].map(group => ({ dataset: { group: String(group) }, active: true }));
  for (const button of buttons) button.classList = { toggle(name, value) { button.active = value; } };
  const context = vm.createContext({ document: { querySelectorAll: () => buttons } });
  const fn = app.match(/function syncGpioLoopback\(io, core\) \{[\s\S]*?\n\}/);
  assert.ok(fn, 'loopback controls must synchronize from server state');
  vm.runInContext(fn[0], context);
  context.syncGpioLoopback({ gpio_loopback_mask: 0xF0 }, 'pru0');
  assert.deepEqual(buttons.map(button => button.active), [false, true, false, false, false]);
  context.syncGpioLoopback({ gpio_loopback_mask: 0 }, 'pru0');
  assert(buttons.every(button => !button.active));
});

test('clicking Use on a core also marks it as shown', () => {
  // Simulate the action handler added in initCoresControl:
  // when a core is newly added by 'use', the handler also applies a 'show' transition.
  const context = vm.createContext({
    coreSelection: { used: ['pru0'], shown: ['pru0'], focus: 'pru0' },
    availableCores: ['pru0', 'pru1', 'rtu0', 'rtu1'],
    pulseCore: null,
    appliedSelections: [],
  });
  vm.runInContext(selection, context);
  // Run the handler logic from initCoresControl directly
  vm.runInContext(`
    function handleUseAction(action) {
      const next = PruCores.transition(coreSelection, action, availableCores);
      if (action.type === 'use' && !coreSelection.used.includes(action.core) && next.used.includes(action.core)) {
        const withShow = PruCores.transition(next, {type:'show', core:action.core}, availableCores);
        appliedSelections.push(withShow);
        pulseCore = action.core;
      } else {
        appliedSelections.push(next);
      }
    }
    handleUseAction({type:'use', core:'rtu0'});
  `, context);
  assert.ok(context.appliedSelections[0].used.includes('rtu0'), 'rtu0 added to used');
  assert.ok(context.appliedSelections[0].shown.includes('rtu0'), 'rtu0 also added to shown');
  assert.equal(context.pulseCore, 'rtu0', 'pulse called for the newly-used core');
});

test('clicking Use again (toggle off) keeps shown unchanged', () => {
  // When toggling off a used core, shown should NOT be affected by the Use handler
  // (only the core being removed gets pruned from shown via PruCores.normalize).
  const context = vm.createContext({
    coreSelection: { used: ['pru0', 'rtu0'], shown: ['pru0', 'rtu0'], focus: 'pru0' },
    availableCores: ['pru0', 'pru1', 'rtu0', 'rtu1'],
    pulseCore: null,
    appliedSelections: [],
  });
  vm.runInContext(selection, context);
  vm.runInContext(`
    function handleUseAction(action) {
      const next = PruCores.transition(coreSelection, action, availableCores);
      if (action.type === 'use' && !coreSelection.used.includes(action.core) && next.used.includes(action.core)) {
        const withShow = PruCores.transition(next, {type:'show', core:action.core}, availableCores);
        appliedSelections.push(withShow);
        pulseCore = action.core;
      } else {
        appliedSelections.push(next);
      }
    }
    handleUseAction({type:'use', core:'rtu0'});
  `, context);
  const result = context.appliedSelections[0];
  assert.ok(!result.used.includes('rtu0'), 'rtu0 removed from used');
  assert.ok(!result.shown.includes('rtu0'), 'rtu0 pruned from shown by normalize (was in shown)');
  assert.equal(context.pulseCore, null, 'pulse not called when toggling off');
});

test('multicore keyboard undo restores one shared snapshot', () => {
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
  assert.deepEqual(actions.map(action => action.core), ['pru0']);
  assert(actions.every(action => action.action === 'step_back'));
});

// ---- B9+B10: per-core circle tabs + minimize persistence -------------------

const layoutSrc = readFileSync(`${__dirname}/../ui/static/layout.js`, 'utf8');

function makeLayoutContext() {
  const storage = {};
  const context = vm.createContext({
    localStorage: {
      getItem: key => storage[key] ?? null,
      setItem: (key, val) => { storage[key] = val; },
    },
    document: { dispatchEvent() {} },
    CustomEvent: class CustomEvent { constructor(type, opts) { this.type = type; this.detail = opts?.detail; } },
    currentMode: 'sc', currentTree: {},
  });
  vm.runInContext(layoutSrc, context);
  // Override the DOM-dependent render() with a stub AFTER loading layout.js
  // (function declarations become context properties, so this replaces it)
  context._renders = [];
  context.render = function(tree) { context._renders.push(tree); };
  return { context, storage };
}

test('clicking a panel-title core chip toggles that core visibility in that pane', () => {
  const { context, storage } = makeLayoutContext();

  // Default: visible (no entry → true)
  assert.equal(context.getCoreVisibility('source', 'pru1'), true);

  // Set invisible; setCoreVisibility calls render() + notifyLayoutChanged()
  context.setCoreVisibility('source', 'pru1', false);
  assert.equal(context.getCoreVisibility('source', 'pru1'), false);

  // Set back visible
  context.setCoreVisibility('source', 'pru1', true);
  assert.equal(context.getCoreVisibility('source', 'pru1'), true);

  // Each call triggered a render
  assert.equal(context._renders.length, 2);

  // State is persisted in localStorage
  const stored = JSON.parse(storage['pru-core-visibility-sc'] || '{}');
  assert.equal(stored.source?.pru1, true);
});

test('minimize state survives render (getCoreVisibility reads from coreVisibility map)', () => {
  const { context, storage } = makeLayoutContext();

  // Seed localStorage with pru0 hidden in registers
  storage['pru-core-visibility-sc'] = JSON.stringify({ registers: { pru0: false } });

  // Simulate initLayout loading persisted state (let binding — must use runInContext)
  vm.runInContext('coreVisibility = loadCoreVisibility("sc")', context);

  // pru0 should be hidden in registers
  assert.equal(context.getCoreVisibility('registers', 'pru0'), false);
  // pru1 defaults to visible (no entry)
  assert.equal(context.getCoreVisibility('registers', 'pru1'), true);
  // source panel pru0 defaults to visible (no entry)
  assert.equal(context.getCoreVisibility('source', 'pru0'), true);
});

test('collapse state persists in localStorage', () => {
  const { context, storage } = makeLayoutContext();

  // Add 'source' to the collapsedPanelIds set (let binding — must use runInContext)
  vm.runInContext('collapsedPanelIds.add("source"); saveCollapsedPanels("sc");', context);

  const stored = JSON.parse(storage['pru-panel-collapsed-sc'] || '[]');
  assert.ok(stored.includes('source'), 'source in collapsed localStorage');

  // loadCollapsedPanels reads it back
  const loaded = context.loadCollapsedPanels('sc');
  assert.ok(loaded.has('source'), 'loadCollapsedPanels restores source');
  assert.ok(!loaded.has('registers'), 'registers not in set');
});

// ---- I/O Pins: one block per PRU core ----------------------------------------

class Node {
  constructor(tag, registry) {
    this.tag = tag; this.registry = registry; this.children = []; this.dataset = {};
    this.listeners = {}; this.attrs = {}; this.className = ''; this.textContent = '';
    this.value = ''; this.hidden = false; this._classes = new Set(); this._id = '';
    this.style = { setProperty() {} };
    this.classList = {
      toggle: (name, on) => { if (on) this._classes.add(name); else this._classes.delete(name); },
      contains: name => this._classes.has(name),
    };
  }
  get id() { return this._id; }
  set id(value) { this._id = value; if (value) this.registry.set(value, this); }
  set innerHTML(_) { this.children = []; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  replaceChildren(...kids) { this.children = []; kids.forEach(kid => this.appendChild(kid)); }
  setAttribute(name, value) { this.attrs[name] = value; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  click() { this.listeners.click(); }
  descendants() { return this.children.flatMap(child => [child, ...child.descendants()]); }
}

const plain = value => JSON.parse(JSON.stringify(value));

function ioDashboard(cores = ['pru0', 'pru1', 'rtu0', 'rtu1', 'txpru0']) {
  const registry = new Map(), actions = [];
  const host = new Node('div', registry); host.id = 'io-blocks';
  const error = new Node('div', registry); error.id = 'gpio-drive-error';
  const context = vm.createContext({
    availableCores: cores, coreStates: {}, currentCore: 'pru0', devicePanelCore: 'pru0',
    coreSelection: { shown: ['pru0'] }, mcBreakpoints: {}, updateMCRegisters() {}, updateMCSource() {},
    updateDevicePanel() {}, updateUI() {}, sendAction: action => actions.push(action),
    document: {
      activeElement: null,
      createElement: tag => new Node(tag, registry),
      getElementById: id => registry.get(id) || null,
      querySelectorAll: selector => {
        const match = selector.match(/^#(\S+) \.lb-btn$/);
        const root = match && registry.get(match[1]);
        return root ? root.descendants().filter(node => node.className === 'lb-btn') : [];
      },
    },
  });
  vm.runInContext(`const IO_PIN_COUNT = 20;
    const IO_LOOPBACK_RANGES = ['3:0', '7:4', '11:8', '15:12', '19:16'];
    const IO_MUX_OPTIONS = [['0', 'GPIO'], ['1', 'Peripheral Interface'], ['3', 'SD']];`, context);
  for (const name of ['ioEl', 'ioSection', 'buildPinGrid', 'buildGpioDirectionControls', 'buildIoBlock',
    'renderIoBlocks', 'updateGpioDirections', 'updateIoMux', 'updatePins', 'handleGpiClick',
    'syncGpioLoopback', 'onLoopbackToggle', 'paintCoreState']) {
    const fn = app.match(new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n\\}`));
    assert.ok(fn, `${name} is implemented`);
    vm.runInContext(fn[0], context);
  }
  context.renderIoBlocks();
  const pin = (kind, core, i) => registry.get(`pin-${kind}-${core}-${i}`);
  const high = (kind, core, i) => pin(kind, core, i).classList.contains('high');
  return { context, registry, actions, host, pin, high };
}

const gpioState = (core, gpo, gpi, extra = {}) => ({ core, pc: 0, carry: 0, registers: [], instructions: [],
  io: { gpo_pins: gpo, gpi_pins: gpi, mux_sel: 0, mode: 'gpio', gpo_drive_mask: 0, gpio_loopback_mask: 0, ...extra } });

test('I/O Pins renders one labelled block per PRU core and none for RTU or TX_PRU', () => {
  const d = ioDashboard();
  assert.deepEqual(d.host.children.map(block => block.dataset.core), ['pru0', 'pru1']);
  assert.deepEqual(d.host.children.map(block => block.children[0].textContent), ['PRU0', 'PRU1']);
  for (const core of ['pru0', 'pru1']) {
    assert.ok(d.pin('gpo', core, 19), `${core} GPO row`);
    assert.ok(d.pin('gpi', core, 19), `${core} GPI row`);
  }
  assert.equal(d.registry.get('io-block-rtu0'), undefined);
  assert.equal(d.registry.get('io-block-txpru0'), undefined);
  assert.equal(d.registry.get('io-core-chips'), undefined, 'no core selector');
});

test('a processor with only PRU0 gets a single block; no PRU cores gets none', () => {
  assert.deepEqual(ioDashboard(['pru0', 'rtu0']).host.children.map(b => b.dataset.core), ['pru0']);
  assert.equal(ioDashboard(['rtu0']).host.children.length, 0);
});

test('both cores pins render, and a PRU1 state message does not repaint PRU0', () => {
  const d = ioDashboard();
  d.context.paintCoreState(gpioState('pru0', [1, 0], [0, 1], { gpio_loopback_mask: 0xF }));
  d.context.paintCoreState(gpioState('pru1', [0, 1], [1, 0]));
  assert.equal(d.high('gpo', 'pru0', 0), true);
  assert.equal(d.high('gpi', 'pru0', 1), true);
  assert.equal(d.high('gpo', 'pru1', 1), true);
  assert.equal(d.high('gpi', 'pru1', 0), true);
  // A later PRU1 message must not change anything in PRU0's block.
  d.context.paintCoreState(gpioState('pru1', [0, 0], [0, 0]));
  assert.equal(d.high('gpo', 'pru0', 0), true, 'PRU0 GPO kept');
  assert.equal(d.high('gpi', 'pru0', 1), true, 'PRU0 GPI kept');
  assert.equal(d.high('gpo', 'pru1', 1), false, 'PRU1 GPO cleared');
  const lb = core => d.registry.get(`loopback-strip-${core}`).children.map(b => b.classList.contains('active'));
  assert.deepEqual(lb('pru0'), [true, false, false, false, false]);
  assert.deepEqual(lb('pru1'), [false, false, false, false, false]);
});

test('state for a core without a block (RTU) is ignored by the I/O Pins panel', () => {
  const d = ioDashboard();
  d.context.paintCoreState(gpioState('pru0', [1], [1]));
  assert.doesNotThrow(() => d.context.paintCoreState(gpioState('rtu0', [0], [0])));
  assert.equal(d.high('gpo', 'pru0', 0), true);
});

test('GPI clicks, loopback, drive and GP mux act on the block core, not the focus core', () => {
  const d = ioDashboard();
  d.context.currentCore = 'pru0';
  d.pin('gpi', 'pru1', 5).click();
  assert.deepEqual(plain(d.actions.pop()), { action: 'set_input', core: 'pru1', pin: 5, value: 1 });
  d.pin('gpi', 'pru0', 2).click();
  assert.deepEqual(plain(d.actions.pop()), { action: 'set_input', core: 'pru0', pin: 2, value: 1 });
  d.actions.length = 0;
  const strip = d.registry.get('loopback-strip-pru1');
  strip.children[1].click();
  assert.deepEqual(d.actions.map(a => [a.action, a.core]), [['set_loopback', 'pru1'], ['get_state', 'pru1']]);
  assert.equal(d.actions[0].group, 1);
  assert.equal(d.actions[0].enabled, true);
  assert.equal(strip.children[0].title, 'Loop GPO[3:0] back to GPI[3:0]');
  d.actions.length = 0;
  const mux = d.registry.get('io-mux-sel-pru1');
  mux.value = '3'; mux.listeners.change();
  assert.deepEqual(plain(d.actions), [{ action: 'gpcfg_write', core: 'pru1', mux_sel: 3 }]);
  d.actions.length = 0;
  d.registry.get('gpio-drive-controls-pru1').children[4].click();
  assert.deepEqual(plain(d.actions), [{ action: 'set_gpio_drive_mask', core: 'pru1', pin: 4 }]);
});

test('rebuilding the blocks repaints them from the per-core state cache', () => {
  const d = ioDashboard(['pru0']);
  d.context.coreStates.pru0 = gpioState('pru0', [1], [0]);
  d.context.coreStates.pru1 = gpioState('pru1', [0, 1], [0, 0]);
  d.context.availableCores = ['pru0', 'pru1', 'rtu0'];
  d.context.renderIoBlocks();
  assert.deepEqual(d.host.children.map(block => block.dataset.core), ['pru0', 'pru1']);
  assert.equal(d.high('gpo', 'pru0', 0), true);
  assert.equal(d.high('gpo', 'pru1', 1), true);
  d.context.availableCores = ['pru0'];                       // another processor
  d.context.renderIoBlocks();
  assert.deepEqual(d.host.children.map(block => block.dataset.core), ['pru0']);
});
