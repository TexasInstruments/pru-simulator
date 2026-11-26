import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');

// Drive the real connection handlers and Run timer with withheld network replies.
function dashboard({autoSessions = true} = {}) {
  const timers = new Map();
  const messages = [];
  const sessionCommands = [];
  let nextTimer = 0;
  class Socket {
    static OPEN = 1;
    readyState = Socket.OPEN;
    send(text) {
      const command = JSON.parse(text);
      if (['start_execution', 'stop_execution'].includes(command.action)) {
        sessionCommands.push(command);
        if (autoSessions) this.onmessage({data:JSON.stringify({type:'execution', running:command.action === 'start_execution',
          owner:command.action === 'start_execution', mode:command.mode || null, request_id:command.request_id})});
      } else messages.push(command);
    }
  }
  const button = { classList: { add() {}, remove() {} } };
  const context = vm.createContext({
    WebSocket: Socket, location: { host: 'localhost:8081' }, window: {}, console,
    wsStatus: {}, btnRun: button, multiCoreMode: false, clockChanging:false,
    executionCores: () => context.multiCoreMode ? ['pru0','rtu0','pru1'] : ['pru0'],
    updateCoresControl() {}, updateCoreState(state) { context.updateUI(state); },
    signalGraph: { recording: false }, mcShown: () => ['pru0', 'rtu0', 'pru1'],
    setButtonLabel: (element, text) => { element.textContent = text; },
    stopSim() {}, setTimeout() {}, showIepClock() {}, showAvailableCores() {},
    updateMCUI() {}, updateSsiDemo() {}, maybeRefreshCtable() {},
    updateUI() {},
    setInterval: fn => { const id = ++nextTimer; timers.set(id, fn); return id; },
    clearInterval: id => timers.delete(id),
  });
  vm.runInContext(app.slice(app.indexOf('let ws ='), app.indexOf('// ---- Core selection:')), context);
  vm.runInContext(app.slice(app.indexOf('function connect()'), app.indexOf('// ---- UI update')), context);
  vm.runInContext(app.slice(app.indexOf('function startRun()'), app.indexOf('function startSim()')), context);
  context.connect();
  const socket = () => vm.runInContext('ws', context);
  return {
    context, messages, sessionCommands, button, socket,
    session(fields) { socket().onmessage({data:JSON.stringify({type:'execution', ...fields})}); },
    tick(count = 1) { for (let i = 0; i < count; i++) for (const fn of [...timers.values()]) fn(); },
    state(fields = {}) { socket().onmessage({ data: JSON.stringify({ type: 'state', core: 'pru0', ...fields }) }); },
  };
}

test('Run waits for its chunk reply even while other states arrive', () => {
  const d = dashboard();
  d.context.startRun();
  d.tick(100);
  assert.equal(d.messages.length, 1, 'a slow server must not receive a run queue');
  const first = d.messages[0];
  assert.equal(first.action, 'run');
  assert.equal(first.max_steps, 1000);
  assert.ok(Number.isInteger(first.run_id));
  d.state(); // A GPIO/config/get_state response is not a chunk completion.
  d.state({ run_id: first.run_id + 1 });
  d.tick(100);
  assert.equal(d.messages.length, 1);
  d.state({ run_id: first.run_id });
  d.tick(100);
  assert.equal(d.messages.length, 2);
  assert.notEqual(d.messages[1].run_id, first.run_id);
});

test('an observing tab shows shared execution without starting a second timer', () => {
  const d = dashboard();
  d.session({running:true, owner:false, mode:'run'});
  d.context.startRun();
  d.tick(50);
  assert.equal(d.messages.length, 0);
  assert.equal(d.sessionCommands.length, 0);
});

test('a shared stop releases the controlling tabs timer without sending another stop', () => {
  const d = dashboard();
  d.context.startRun();
  d.tick();
  assert.equal(d.messages.length, 1);
  d.session({running:false, owner:false, mode:null});
  d.tick(50);
  assert.equal(d.messages.length, 1);
  assert.deepEqual(d.sessionCommands.map(command => command.action), ['start_execution']);
});

test('a delayed stop from the previous run cannot cancel a newly requested run', () => {
  const d = dashboard({autoSessions:false});
  d.context.startRun();
  d.session({running:true,owner:true,mode:'run',request_id:d.sessionCommands[0].request_id});
  d.tick();
  d.context.stopRun();
  d.context.startRun();
  const restart = d.sessionCommands.at(-1);
  d.session({running:false,owner:false,mode:null}); // reply to the earlier stop
  d.tick();
  assert.equal(d.messages.length, 1, 'wait for the new start acknowledgement');
  d.session({running:true,owner:true,mode:'run',request_id:restart.request_id});
  d.state({run_id:d.messages[0].run_id});
  d.tick();
  assert.equal(d.messages.length, 2, 'the restarted run must still have a timer');
});

test('Stop leaves at most one chunk in flight and Start waits for it', () => {
  const d = dashboard();
  d.context.startRun();
  d.tick();
  const first = d.messages[0];
  d.context.stopRun();
  assert.equal(d.button.textContent, 'Run');
  d.tick(100);
  assert.equal(d.messages.length, 1);
  d.context.startRun();
  d.tick(100);
  assert.equal(d.messages.length, 1, 'restart must not add a second pending chunk');
  d.state({ run_id: first.run_id });
  d.tick();
  assert.equal(d.messages.length, 2);
  d.context.stopRun();
  d.state({ run_id: d.messages[1].run_id });
  d.tick(100);
  assert.equal(d.messages.length, 2, 'a late reply must not restart a stopped run');
});

test('multicore Run waits through intermediate core states and preserves capture', () => {
  const d = dashboard();
  d.context.multiCoreMode = true;
  d.context.signalGraph.recording = true;
  d.context.startRun();
  d.tick(50);
  assert.equal(d.messages.length, 1);
  const first = d.messages[0];
  assert.equal(first.action, 'run_multicore');
  assert.deepEqual(first.partners, ['rtu0', 'pru1']);
  assert.equal(first.capture, true);
  d.state({ core: 'pru0' });
  d.state({ core: 'rtu0' });
  d.tick(50);
  assert.equal(d.messages.length, 1);
  d.state({ core: 'pru1', run_id: first.run_id });
  d.tick();
  assert.equal(d.messages.length, 2);
});

test('disconnect clears a pending chunk so a new connection can run', () => {
  const d = dashboard();
  d.context.startRun();
  d.tick();
  const first = d.messages[0];
  d.socket().readyState = 3;
  d.socket().onclose();
  assert.equal(d.button.textContent, 'Run');
  d.context.connect();
  d.context.startRun();
  d.tick();
  assert.equal(d.messages.length, 2);
  assert.notEqual(d.messages[1].run_id, first.run_id);
  d.state({ run_id: first.run_id });
  d.tick(50);
  assert.equal(d.messages.length, 2, 'an old reply must not release the new request');
});

test('Run does not send to a closed socket or duplicate a running timer', () => {
  const d = dashboard();
  d.socket().readyState = 3;
  d.context.startRun();
  d.tick(50);
  assert.equal(d.messages.length, 0);
  d.socket().readyState = 1;
  d.context.startRun();
  d.tick(50);
  assert.equal(d.messages.length, 1);
  d.state({ run_id: d.messages[0].run_id, halted: true,
    execution: {running:false,owner:false,mode:null} });
  d.tick(50);
  assert.equal(d.messages.length, 1, 'HALT must stop every timer');
});

test('Run executes used cores even when hidden and does not force PRU0 as lead', () => {
  const d = dashboard();
  d.context.multiCoreMode = true;
  d.context.executionCores = () => ['pru1', 'rtu0'];
  d.context.startRun();
  d.tick();
  assert.equal(d.messages[0].core, 'pru1');
  assert.deepEqual(d.messages[0].partners, ['rtu0']);
});

test('Space on a focused button keeps its native action instead of setting a breakpoint', () => {
  const sent = [], listeners = {};
  const context = vm.createContext({document:{addEventListener:(name,fn)=>listeners[name]=fn},
    sourceList:{querySelector:()=>({id:'src-line-1'})},currentCore:'pru1',sendAction:a=>sent.push(a)});
  const start = app.indexOf('// Keyboard shortcuts');
  vm.runInContext(app.slice(start,app.indexOf('// ---- UART decoder controls',start)),context);
  let prevented = false;
  listeners.keydown({target:{tagName:'BUTTON'},key:' ',preventDefault:()=>{prevented=true;}});
  assert.equal(prevented,false);
  assert.deepEqual(sent,[]);
});

test('multicore Step while disconnected leaves no pending request to block reconnect', () => {
  const context=vm.createContext({clockChanging:false,runPending:null,runSequence:0,
    ws:{readyState:3},WebSocket:{OPEN:1},executionCores:()=>['pru0','pru1'],sendAction(){}});
  const start=app.indexOf('function requestSelectedStep(');
  vm.runInContext(app.slice(start,app.indexOf('// ---- Init',start)),context);
  context.requestSelectedStep();
  assert.equal(context.runPending,null);
});
