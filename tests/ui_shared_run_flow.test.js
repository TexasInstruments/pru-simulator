import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');

function element() {
  const listeners = {}, classes = new Set();
  return {
    dataset: {}, children: [], style: { setProperty() {} },
    classList: { add: name => classes.add(name), remove: name => classes.delete(name),
      toggle(name, on) { if (on) classes.add(name); else classes.delete(name); } },
    addEventListener: (name, fn) => { listeners[name] = fn; },
    click: () => listeners.click?.(), setAttribute() {},
    replaceChildren() { this.children = []; }, appendChild(child) { this.children.push(child); },
  };
}

// Each tab has its own real connection, execution, state and button handlers.
// Network delivery and timers are controlled to expose ordering between tabs.
function dashboard() {
  const messages = [], timers = new Map(), elements = new Map();
  let timerId = 0;
  const getElement = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  class Socket {
    static OPEN = 1;
    readyState = Socket.OPEN;
    send(text) { messages.push(JSON.parse(text)); }
  }
  const context = vm.createContext({
    WebSocket: Socket, location: {host:'localhost:8081'}, window: {}, console,
    document: {getElementById:getElement, createElement:element},
    wsStatus: {}, btnStep:element(), btnRun:Object.assign(element(),{textContent:'Run'}),
    btnSim:Object.assign(element(),{textContent:'SIM'}),
    statusBadge:element(), simIntervalInput:{value:'1.0'},
    availableCores:['pru0'], coreSelection:{used:['pru0'],shown:['pru0'],focus:'pru0'},
    coreStates:{}, coresDeck:{update() {}}, clockState:{}, clockChanging:false,
    multiCoreMode:false, signalGraph:{recording:false},
    executionCores:() => context.coreSelection.used,
    setButtonLabel:(button,text) => { button.textContent = text; },
    paintCoreState() {}, showAvailableCores() {}, showIepClock() {}, updateSsiDemo() {}, maybeRefreshCtable() {}, syncEditorCoreSelect() {},
    setTimeout() {},
    setInterval:fn => { timers.set(++timerId,fn); return timerId; },
    clearInterval:id => timers.delete(id),
  });
  function load(start, end) {
    vm.runInContext(app.slice(app.indexOf(start),app.indexOf(end,app.indexOf(start))),context);
  }
  load('let ws =','// ---- Core selection:');
  load('function connect()','// ---- UI update');
  load('function startRun()','// ---- GPI pin click');
  load('function updateCoreState(','// ---- Init');
  load('btnStep.addEventListener(','btnReset.addEventListener(');
  context.connect();
  const socket = vm.runInContext('ws',context);
  return {
    context, messages,
    tick() { for (const fn of [...timers.values()]) fn(); },
    receive(fields) { socket.onmessage({data:JSON.stringify(fields)}); },
    session(fields) { this.receive({type:'execution',...fields}); },
    state(fields) { this.receive({type:'state',core:'pru0',...fields}); },
    starts:() => messages.filter(message => message.action === 'start_execution'),
    chunks:() => messages.filter(message => ['run','run_multicore'].includes(message.action)),
  };
}

function acknowledgeStart(owner, observers = [], mode = 'run') {
  owner.session({running:true,owner:true,mode,request_id:owner.starts().at(-1).request_id});
  for (const observer of observers) observer.session({running:true,owner:false,mode});
}

test('two frontend tabs can stop each other and take turns owning Run', () => {
  const a = dashboard(), b = dashboard();
  a.context.btnRun.click(); acknowledgeStart(a,[b]); a.tick();
  b.context.startRun(); b.tick();
  assert.equal(b.chunks().length,0,'an observer cannot add a second execution timer');
  b.context.btnRun.click();
  assert.equal(b.messages.at(-1).action,'stop_execution');
  for (const tab of [a,b]) tab.session({running:false,owner:false,mode:null});
  a.tick(); b.tick();
  assert.equal(a.chunks().length,1,'the observer Stop releases the controlling timer');
  b.context.btnRun.click(); acknowledgeStart(b,[a]); b.tick();
  assert.equal(b.chunks().length,1,'the former observer can start the next run');
  a.context.btnRun.click();
  assert.equal(a.messages.at(-1).action,'stop_execution');
  for (const tab of [a,b]) tab.session({running:false,owner:false,mode:null});
  b.tick();
  assert.equal(b.chunks().length,1);
});

for (const stopState of [{at_breakpoint:true},{halted:true}]) {
  test(`a late ${Object.keys(stopState)[0]} reply cannot cancel a pending restart`, () => {
    const d = dashboard();
    d.context.startRun(); acknowledgeStart(d); d.tick();
    const first = d.chunks()[0];
    d.context.stopRun();
    // Reset is ordered after the previous chunk and before this new start.
    if (stopState.halted) d.context.sendAction({action:'reset',core:'pru0'});
    d.context.startRun();
    d.state({...stopState,run_id:first.run_id,
      execution:{type:'execution',running:true,owner:true,mode:'run'}});
    d.session({running:false,owner:false,mode:null});
    if (stopState.halted) d.state({halted:false,
      execution:{type:'execution',running:false,owner:false,mode:null}});
    acknowledgeStart(d); d.tick();
    assert.equal(d.chunks().length,2,'the new start acknowledgement must retain its timer');
    assert.equal(d.messages.filter(message => message.action === 'stop_execution').length,1,
      'an old core state must not send a stop for the new execution');
  });
}

test('backend execution completion stops Run after a halted core state', () => {
  const d = dashboard();
  d.context.startRun(); acknowledgeStart(d); d.tick();
  d.state({halted:true,run_id:d.chunks()[0].run_id,
    execution:{type:'execution',running:true,owner:true,mode:'run'}});
  d.session({running:false,owner:false,mode:null});
  d.tick();
  assert.equal(d.chunks().length,1);
  assert.equal(d.context.btnRun.textContent,'Run');
});

test('an observer SIM button shows that it stops the shared simulation', () => {
  const a = dashboard(), b = dashboard();
  a.context.btnSim.click(); acknowledgeStart(a,[b],'sim');
  assert.equal(b.context.btnSim.textContent,'Stop SIM');
  b.context.btnSim.click();
  assert.equal(b.messages.at(-1).action,'stop_execution');
  for (const tab of [a,b]) tab.session({running:false,owner:false,mode:null});
  assert.equal(a.context.btnSim.textContent,'SIM');
  assert.equal(b.context.btnSim.textContent,'SIM');
});


test('loading an example cannot cancel Run requested before the load reply arrives', async () => {
  const d = dashboard();
  Object.assign(d.context, {scenarioGeneration:0, PruCores:{normalize:selection => selection},
    applyCoreSelection(next) { d.context.coreSelection = next; d.context.multiCoreMode = next.used.length > 1; },
    selectEditorCore() {}, syncDeviceCoreSelect() {}, fetch:async () => ({ok:false})});
  d.context.document.querySelector = () => null;
  const start = app.indexOf('async function applyScenario(');
  vm.runInContext(app.slice(start, app.indexOf('function buildMCRegTable(',start)),d.context);
  d.context.sendAction({action:'scenario_load',name:'SSI multi-core (emulator + reader)'});
  d.context.btnRun.click();
  await d.context.applyScenario({cores:['pru0','pru1'],lead:'pru0',firmware:{},ui:{}});
  acknowledgeStart(d); d.tick();
  assert.equal(d.chunks().length,1);
  assert.equal(d.chunks()[0].action,'run_multicore');
  assert.equal(d.messages.some(message => message.action === 'stop_execution'),false);
});

test('Step in an observer stops the shared run before advancing once', () => {
  const a = dashboard(), b = dashboard();
  a.context.btnRun.click(); acknowledgeStart(a,[b]); a.tick();
  b.context.btnStep.click();
  assert.deepEqual(b.messages.map(message => message.action),['stop_execution','step']);
  for (const tab of [a,b]) tab.session({running:false,owner:false,mode:null});
  a.tick(); b.tick();
  assert.equal(a.chunks().length,1);
  assert.equal(b.chunks().length,0);
});

test('a delayed target update cannot cancel a new pending Run', () => {
  const d = dashboard();
  d.context.availableCores = ['pru0','pru1','rtu0','rtu1'];
  d.context.PruCores = {ORDER:['pru0','pru1','rtu0','rtu1'],normalize:selection => selection};
  d.context.applyCoreSelection = selection => { d.context.coreSelection = selection; };
  const start = app.indexOf('function showAvailableCores(');
  vm.runInContext(app.slice(start,app.indexOf('function syncDeviceCoreSelect(',start)),d.context);
  d.context.btnRun.click();
  d.state({available_cores:['pru0','pru1'],execution:{running:false,owner:false,mode:null}});
  acknowledgeStart(d); d.tick();
  assert.equal(d.chunks().length,1);
  assert.equal(d.context.btnRun.textContent,'Stop');
});

test('core controls stay busy until shared state arrives and become busy on disconnect', () => {
  const d = dashboard(); let busy;
  d.context.coresDeck.update = state => { busy = state.busy; };
  d.context.updateCoresControl();
  assert.equal(busy,true,'a connecting page must not offer unsendable core changes');
  d.session({running:false,owner:false,mode:null});
  assert.equal(busy,false);
  const socket = vm.runInContext('ws',d.context);
  socket.readyState = 3;
  socket.onclose();
  assert.equal(busy,true,'disconnect must disable execution-core changes immediately');
});

test('disconnect during SIM clears its timer and running status while waiting for recovery', () => {
  const d = dashboard();
  d.context.btnSim.click();acknowledgeStart(d,[],'sim');
  const socket=vm.runInContext('ws',d.context);socket.readyState=3;socket.onclose();
  assert.equal(vm.runInContext('simRunning',d.context),false);
  assert.equal(vm.runInContext('simTimer',d.context),null);
  assert.equal(d.context.btnSim.textContent,'SIM');
  assert.equal(d.context.statusBadge.textContent,'HALTED');
});
