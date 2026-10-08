import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');

// Drive the real connection handlers and Run timer with withheld network replies.
function dashboard() {
  const timers = new Map();
  const messages = [];
  let nextTimer = 0;
  class Socket {
    static OPEN = 1;
    readyState = Socket.OPEN;
    send(text) { messages.push(JSON.parse(text)); }
  }
  const button = { classList: { add() {}, remove() {} } };
  const context = vm.createContext({
    WebSocket: Socket, location: { host: 'localhost:8081' }, window: {}, console,
    wsStatus: {}, btnRun: button, multiCoreMode: false,
    signalGraph: { recording: false }, mcShown: () => ['pru0', 'rtu0', 'pru1'],
    setButtonLabel: (element, text) => { element.textContent = text; },
    stopSim() {}, setTimeout() {}, showIepClock() {}, showAvailableCores() {},
    updateMCUI() {},
    updateUI(state) { if (state.halted || state.at_breakpoint) context.stopRun(); },
    setInterval: fn => { const id = ++nextTimer; timers.set(id, fn); return id; },
    clearInterval: id => timers.delete(id),
  });
  vm.runInContext(app.slice(app.indexOf('let ws ='), app.indexOf('// ---- Multi-core state')), context);
  vm.runInContext(app.slice(app.indexOf('function connect()'), app.indexOf('// ---- UI update')), context);
  vm.runInContext(app.slice(app.indexOf('function startRun()'), app.indexOf('function startSim()')), context);
  context.connect();
  const socket = () => vm.runInContext('ws', context);
  return {
    context, messages, button, socket,
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
  d.state({ run_id: d.messages[0].run_id, halted: true });
  d.tick(50);
  assert.equal(d.messages.length, 1, 'HALT must stop every timer');
});
