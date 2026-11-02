import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');
const cores = readFileSync(new URL('../ui/static/cores.js', import.meta.url), 'utf8');
function load(context, name) {
  const start = app.indexOf(`function ${name}(`);
  let end = app.indexOf('{', start) + 1, depth = 1;
  while (depth) { if (app[end] === '{') depth++; if (app[end] === '}') depth--; end++; }
  vm.runInContext(app.slice(start, end), context);
}
function tab() {
  const requests = [], stops = [];
  const context = vm.createContext({
    module: {exports:{}}, availableCores:['pru0','pru1','rtu0','rtu1'],
    coreSelection:{used:['pru0'],shown:['pru0'],focus:'pru0',pinned:false},
    currentCore:'pru0', devicePanelCore:'pru0', multiCoreMode:false, coreStates:{},
    sharedExecution:{running:false}, executionStartPending:null, executionReady:true, captureStride:null,
    stopRun:notify => stops.push(['run',notify]), stopSim:notify => stops.push(['sim',notify]),
    sendAction:request => requests.push(request), persistCoreSelection() {}, renderCorePanes() {},
    selectEditorCore() {}, syncDeviceCoreSelect() {}, updateCoresControl() {}, paintCoreState() {},
    mcFreshRegs:() => [], sourceList:{replaceChildren() {}}, initSpadState() {},
    syncGpioLoopback() {}, updateCtableForCore() {},
  });
  vm.runInContext(cores,context);
  load(context,'applyCoreSelection'); load(context,'receiveExecution');
  return {context,requests,stops};
}
const plain = value => JSON.parse(JSON.stringify(value));
const session = fields => ({running:false,owner:false,mode:null,cores:['pru0','pru1'],
  scenario:{ui:{capture_stride:2}},...fields});

test('observer and late join use backend execution cores without opening extra panes', () => {
  const t = tab();
  t.context.receiveExecution(session());
  assert.deepEqual(plain(t.context.coreSelection.used),['pru0','pru1']);
  assert.deepEqual(plain(t.context.coreSelection.shown),['pru0']);
  assert.equal(t.context.currentCore,'pru0');
  assert.equal(t.context.captureStride,2);
  assert.equal(t.requests.some(r => r.action === 'set_execution_cores'),false,'received selection must not echo');
  assert.ok(t.stops.every(([,notify]) => notify === false),'received state must not stop another tab');
});

test('local execution membership publishes once but local inspection stays local', () => {
  const t = tab();
  t.context.applyCoreSelection({used:['pru0','pru1'],shown:['pru0'],focus:'pru0'});
  assert.deepEqual(plain(t.requests.filter(r => r.action === 'set_execution_cores')),
    [{action:'set_execution_cores',cores:['pru0','pru1']}]);
  t.context.applyCoreSelection({used:['pru0','pru1'],shown:['pru1'],focus:'pru1'});
  assert.equal(t.requests.filter(r => r.action === 'set_execution_cores').length,1);
  t.context.receiveExecution(session({running:true,owner:false}));
  assert.equal(t.context.currentCore,'pru1','backend updates preserve valid local inspection');
});

test('a backend target change removes an unavailable local inspection core', () => {
  const t = tab();
  t.context.applyCoreSelection({used:['rtu1'],shown:['rtu1'],focus:'rtu1'},true);
  t.context.availableCores = ['pru0','pru1'];
  t.context.receiveExecution(session({scenario:null}));
  assert.deepEqual(plain(t.context.coreSelection.used),['pru0','pru1']);
  assert.equal(t.context.currentCore,'pru0');
  assert.equal(t.context.captureStride,null);
});

test('Reset Layout preserves shared execution cores and does not stop a run', () => {
  const t = tab(); let reset;
  Object.assign(t.context, {
    document:{getElementById() {}, addEventListener(name,fn) { if (name === 'pru-cores-reset') reset = fn; }},
    createCoresDeck:() => ({}), changeGroupClock() {},
  });
  load(t.context,'initCoresControl');
  t.context.receiveExecution(session({running:true,owner:false}));
  t.context.coreSelection.shown = ['pru0','pru1'];
  t.context.coreSelection.pinned = true;
  t.context.initCoresControl();
  t.stops.length = 0; t.requests.length = 0;
  reset();
  assert.deepEqual(plain(t.context.coreSelection.used),['pru0','pru1']);
  assert.deepEqual(plain(t.context.coreSelection.shown),['pru0']);
  assert.equal(t.context.coreSelection.pinned,false);
  assert.equal(t.stops.length,0);
  assert.equal(t.requests.some(r => r.action === 'set_execution_cores'),false);
});

test('manual CORES Use actions add and remove shared membership from either tab', () => {
  const a = tab(), b = tab();
  for (const t of [a,b]) {
    Object.assign(t.context, {
      document:{getElementById() {}, addEventListener() {}}, changeGroupClock() {},
      createCoresDeck(_root,handlers) { t.click = handlers.action; return {shake() {}}; },
    });
    load(t.context,'initCoresControl');
    t.context.initCoresControl();
  }
  for (const [origin,core,expected] of [[a,'pru1',['pru0','pru1']],
    [b,'rtu0',['pru0','pru1','rtu0']], [a,'pru1',['pru0','rtu0']],
    [b,'rtu0',['pru0']]]) {
    origin.click({type:'use',core});
    const request = origin.requests.filter(r => r.action === 'set_execution_cores').at(-1);
    assert.deepEqual(plain(request.cores),expected);
    for (const t of [a,b]) t.context.receiveExecution(session({cores:request.cores}));
    for (const t of [a,b]) assert.deepEqual(plain(t.context.coreSelection.used),expected);
  }
});

test('Use cannot change local execution membership before shared state arrives', () => {
  const t = tab();
  t.context.executionReady = false;
  Object.assign(t.context, {
    document:{getElementById() {},addEventListener() {}}, changeGroupClock() {},
    createCoresDeck(_root,handlers) { t.click = handlers.action; return {shake() {}}; },
  });
  load(t.context,'initCoresControl'); t.context.initCoresControl();
  for (const action of [{type:'use',core:'pru1'},{type:'use-all'}]) t.click(action);
  assert.deepEqual(plain(t.context.coreSelection.used),['pru0'],'offline clicks must not imply a shared change');
  assert.equal(t.requests.some(r => r.action === 'set_execution_cores'),false);
  t.context.receiveExecution(session({cores:['pru0']}));
  assert.equal(t.context.executionReady,true);
  t.click({type:'use',core:'pru1'});
  assert.deepEqual(plain(t.context.coreSelection.used),['pru0','pru1']);
});
