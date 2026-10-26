import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');
const layout = readFileSync(new URL('../ui/static/layout.js', import.meta.url), 'utf8');
function load(context, source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} must exist`);
  let end = source.indexOf('{', start) + 1;
  let depth = 1;
  while (depth) {
    if (source[end] === '{') depth++;
    if (source[end] === '}') depth--;
    end++;
  }
  vm.runInContext(source.slice(start, end), context);
}

function graph() {
  let channels = [];
  let exported;
  const paths = [];
  const ctx = new Proxy({
    moveTo: (x, y) => paths.push(['move', x, y]),
    lineTo: (x, y) => paths.push(['line', x, y]),
  }, { get: (target, key) => target[key] ?? (() => {}) });
  const canvas = { style: {}, offsetWidth: 1000, offsetHeight: 140, getContext: () => ctx };
  const context = vm.createContext({
    signalGraph: { recording: true, windowSize: 100, buf: [], head: 0, fill: 0, memChannels: [] },
    document: { getElementById: () => canvas, createElement: () => ({ click() {} }) },
    Blob, URL: { createObjectURL: blob => { exported = blob; return 'blob:trace'; }, revokeObjectURL() {} },
    graphThemeColor: () => '', _graphUpdateStepLabel() {},
    _graphUpdateLegend: value => { channels = value; },
    GRAPH_GPO_COLORS: Array(20).fill('red'), GRAPH_GPI_COLORS: Array(20).fill('blue'),
    GRAPH_PERIF_COLORS: [], GRAPH_PERIF_OE_COLORS: [], GRAPH_PERIF_CLK_COLORS: [],
  });
  for (const name of ['graphPushSample', 'graphGetSamples', 'graphHandleCapture', 'drawDigitalGraph', 'exportGraphCSV']) load(context, app, name);
  // Identity helpers, when present, are production functions too.
  for (const name of ['graphCoreNames', 'graphCoreSample']) if (app.includes(`function ${name}(`)) load(context, app, name);
  return { context, paths, channels: () => Array.from(channels, ch => ({ label: ch.label, data: Array.from(ch.data) })), exported: () => exported };
}

function capture(context, core, partners, rows) {
  context.graphHandleCapture({ core, partners, samples: rows });
}

test('capture channels retain peer identity across executed core changes', () => {
  const g = graph();
  capture(g.context, 'pru0', ['pru1'], [[0,0,0,0,0,0,0,0], [1,0,0,0,0,0,1,0]]);
  capture(g.context, 'pru0', ['rtu0'], [[2,0,0,0,0,0,0,0], [3,0,0,0,0,0,0,0]]);
  g.context.drawDigitalGraph();
  assert.deepEqual(g.channels(), [{ label: 'PRU1 GPO 0', data: [0,1,null,null] }]);
});

test('a core keeps its own history when changing from lead to peer', () => {
  const g = graph();
  capture(g.context, 'pru0', ['pru1'], [[0,0,0,0,0,0,0,0], [1,1,0,0,0,0,0,0]]);
  capture(g.context, 'pru1', ['pru0'], [[2,0,0,0,0,0,1,0], [3,0,0,0,0,0,0,0]]);
  g.context.drawDigitalGraph();
  assert.deepEqual(g.channels(), [{ label: 'PRU0 GPO 0', data: [0,1,1,0] }]);
});

test('missing core samples neither invent transitions nor connect across gaps', () => {
  const g = graph();
  capture(g.context, 'pru0', ['pru1'], [[0,0,0,0,0,0,1,0], [1,0,0,0,0,0,1,0]]);
  capture(g.context, 'pru0', ['rtu0'], [[2,0,0,0,0,0,0,0]]);
  capture(g.context, 'pru0', ['pru1'], [[3,0,0,0,0,0,0,0], [4,0,0,0,0,0,1,0]]);
  g.context.drawDigitalGraph();
  assert.deepEqual(g.channels(), [{ label: 'PRU1 GPO 0', data: [1,1,null,0,1] }]);
  assert.ok(g.paths.some(([op, x]) => op === 'move' && x === 750), 'resumed capture begins a separate waveform segment');
  const flat = graph();
  capture(flat.context, 'pru0', ['pru1'], [[0,0,0,0,0,0,1,0], [1,0,0,0,0,0,1,0]]);
  capture(flat.context, 'pru0', ['rtu0'], [[2,0,0,0,0,0,0,0]]);
  flat.context.drawDigitalGraph();
  assert.deepEqual(flat.channels(), [], 'a departing high core must not fall to zero');
});

test('CSV preserves every captured core by name and leaves absent samples blank', async () => {
  const g = graph();
  capture(g.context, 'pru0', ['pru1'], [[0,0,0,0,0,0,1,0]]);
  capture(g.context, 'pru1', ['rtu0'], [[1,0,0,0,0,0,1,0]]);
  g.context.exportGraphCSV();
  const [headers, ...rows] = (await g.exported().text()).split('\n').map(row => row.split(','));
  const column = name => { assert.ok(headers.includes(name), `missing ${name}`); return headers.indexOf(name); };
  assert.deepEqual(rows.map(row => row[column('pru0_gpo0')]), ['0','']);
  assert.deepEqual(rows.map(row => row[column('pru1_gpo0')]), ['1','0']);
  assert.deepEqual(rows.map(row => row[column('rtu0_gpo0')]), ['','1']);
});

test('peripheral lead changes preserve separate traces and CSV columns', async () => {
  const g = graph();
  g.context.graphHandleCapture({ core: 'pru0', partners: ['pru1'], mode: 'perif',
    samples: [[0,0,0,0,1,0,0,0], [1,0,0,1,1,0,0,0]] });
  g.context.graphHandleCapture({ core: 'pru1', partners: ['pru0'], mode: 'perif',
    samples: [[2,0,0,0,1,0,0,0], [3,0,0,1,1,0,0,0]] });
  g.context.drawDigitalGraph();
  assert.deepEqual(g.channels().filter(ch => ch.label.endsWith('_out')), [
    { label: 'PRU0 perif0_out', data: [0,1,null,null] },
    { label: 'PRU1 perif0_out', data: [null,null,0,1] },
  ]);
  g.context.exportGraphCSV();
  const [headers, ...rows] = (await g.exported().text()).split('\n').map(row => row.split(','));
  assert.ok(headers.includes('pru0_perif0_out'));
  assert.ok(headers.includes('pru1_perif0_out'));
  assert.deepEqual(rows.map(row => row[headers.indexOf('pru0_perif0_out')]), ['0','1','','']);
  assert.deepEqual(rows.map(row => row[headers.indexOf('pru1_perif0_out')]), ['','','0','1']);
});

function inspector() {
  const requests = [];
  let memoryRefreshes = 0;
  const noop = () => {};
  const context = vm.createContext({
    currentCore: 'pru0', devicePanelCore: 'pru0', availableCores: ['pru0','pru1'],
    coreSelection: { used: ['pru0','pru1'], shown: ['pru0'], focus: 'pru0', pinned: false }, coreStates: {},
    signalGraph: { recording: true, windowSize: 100, buf: [], head: 0, fill: 0, memChannels: [{ addr: 0, count: 1, format: 'uint8' }] },
    GRAPH_MEM_BPS: { uint8: 1 }, cntCycles: {}, cntStalls: {}, cntInstrs: {}, cntIpc: {}, cntPc: {}, btnXfrShift: {},
    document: { querySelectorAll: () => [], getElementById: () => null }, window: {},
    stopRun: noop, stopSim: noop, persistCoreSelection: noop, renderCorePanes: noop,
    selectEditorCore: noop, syncDeviceCoreSelect: noop, updateCoresControl: noop, sendAction: command => requests.push(command),
    updateRegisters: noop, updateSpad: noop, updateSource: noop, updatePins: noop, updateSDPanel: noop,
    updatePerifPanel: noop, updateI2CPanel: noop, updateDevicePanel: noop, drawGraph: noop,
    memAutoOnStateChange: () => { memoryRefreshes++; },
    mcFreshRegs: () => [], sourceList: { replaceChildren: noop }, initSpadState: noop,
    syncGpioLoopback: noop, updateCtableForCore: noop, mcBreakpoints: {}, updateMCRegisters: noop, updateMCSource: noop,
  });
  vm.runInContext(readFileSync(new URL('../ui/static/cores.js', import.meta.url), 'utf8'), context);
  for (const name of ['graphPushSample', 'graphSample', 'updateUI', 'paintCoreState', 'applyCoreSelection', 'updateCoreState']) load(context, app, name);
  const state = core => ({ core, pc: 0, cycles: 0, stall_cycles: 0, instruction_count: 42,
    ipc: 0, registers: [], carry: 0, instructions: [], io: { gpo_pins: [1], gpi_pins: [0] } });
  context.updateCoreState(state('pru0'));
  context.updateCoreState(state('pru1'));
  return { context, requests, memoryRefreshes: () => memoryRefreshes };
}

test('pin, focus, and shown-core changes repaint cached states without acquiring samples or memory', () => {
  const i = inspector();
  const initialRequests = i.requests.filter(r => r.action === 'read_memory').length;
  const initialRefreshes = i.memoryRefreshes();
  for (const action of [{ type: 'pin' }, { type: 'pin' }, { type: 'focus', core: 'pru1' }, { type: 'show', core: 'pru0' }]) {
    i.context.applyCoreSelection(i.context.PruCores.transition(i.context.coreSelection, action, i.context.availableCores));
  }
  assert.equal(i.context.signalGraph.fill, 1);
  assert.equal(i.requests.filter(r => r.action === 'read_memory').length, initialRequests);
  assert.equal(i.memoryRefreshes(), initialRefreshes);
  assert.equal(i.context.cntInstrs.textContent, 42, 'cached focus state still paints counters');
  i.context.updateCoreState({ ...i.context.coreStates.pru1, instruction_count: 43 });
  assert.equal(i.context.signalGraph.fill, 2, 'a new state still records');
  assert.equal(i.context.signalGraph.buf[1].core, 'pru1');
  i.context.updateCoreState({ ...i.context.coreStates.pru1, captured: true });
  assert.equal(i.context.signalGraph.fill, 2, 'closing capture states do not duplicate capture data');
});

test('selection state replies repaint authoritative values without acquiring signal or memory samples', () => {
  const i = inspector();
  const samples = i.context.signalGraph.fill;
  const memoryRequests = () => i.requests.filter(r => r.action === 'read_memory').length;
  const reads = memoryRequests();
  const refreshes = i.memoryRefreshes();
  i.context.applyCoreSelection(i.context.PruCores.transition(i.context.coreSelection,
    { type: 'pin' }, i.context.availableCores));
  const stateRequests = i.requests.filter(r => r.action === 'get_state');
  assert.ok(stateRequests.length > 0, 'selection refreshes server state');
  for (const request of stateRequests) {
    i.context.updateCoreState({ ...i.context.coreStates[request.core], cycles: 99, view_only: true });
  }
  assert.equal(i.context.cntCycles.textContent, 99, 'view replies still update displayed state');
  assert.equal(i.context.signalGraph.fill, samples);
  assert.equal(memoryRequests(), reads);
  assert.equal(i.memoryRefreshes(), refreshes);
  i.context.updateCoreState({ ...i.context.coreStates.pru0, instruction_count: 43, view_only: false });
  assert.equal(i.context.signalGraph.fill, samples + 1, 'execution replies still acquire samples');
  assert.equal(memoryRequests(), reads + 1);
  assert.equal(i.memoryRefreshes(), refreshes + 1);
});

test('nested button and link content do not start panel dragging; plain titles still do', () => {
  class Element {
    constructor() { this.dataset = {}; this.style = {}; this.children = []; this.listeners = {}; this.classList = { contains: () => true }; }
    appendChild(child) { this.children.push(child); }
    querySelector(selector) { return selector === '[data-panel-id]' ? heading : {}; }
    addEventListener(event, handler) { this.listeners[event] = handler; }
  }
  const heading = new Element();
  const context = vm.createContext({ document: { createElement: () => new Element() }, localStorage: {}, panel: new Element() });
  vm.runInContext(layout, context);
  vm.runInContext('PANEL_REGISTRY.editor = panel;', context);
  let dragged = 0;
  let prevented = 0;
  context.startDrag = () => { dragged++; };
  context.renderNode({ type: 'leaf', panelId: 'editor' });
  for (const ancestor of ['button', 'a']) {
    heading.listeners.mousedown({ target: { tagName: 'SPAN', closest: selector => selector.split(',').map(s => s.trim()).includes(ancestor) ? {} : null }, preventDefault: () => { prevented++; } });
  }
  assert.equal(dragged, 0);
  assert.equal(prevented, 0);
  heading.listeners.mousedown({ target: { tagName: 'SPAN', closest: () => null }, preventDefault: () => { prevented++; } });
  assert.equal(dragged, 1);
  assert.equal(prevented, 1);
});
