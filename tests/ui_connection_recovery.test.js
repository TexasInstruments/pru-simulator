import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const app = readFileSync(new URL('../ui/static/app.js',import.meta.url),'utf8');
function dashboard() {
  const sockets = [], retries = [];
  let online = false, probe;
  class Socket {
    static CONNECTING = 0; static OPEN = 1; static CLOSED = 3;
    readyState = Socket.CONNECTING;
    constructor() { sockets.push(this); }
    send() {}
  }
  const context = vm.createContext({
    WebSocket:Socket, location:{host:'localhost:8081'}, wsStatus:{}, console,
    stopRun() {}, stopSim() {}, updateCoresControl() {},
    fetch:async () => probe ? await probe() : online ? {ok:true} : Promise.reject(new Error('server offline')),
    setTimeout:fn => {retries.push(fn);},
  });
  vm.runInContext(app.slice(app.indexOf('let ws ='),app.indexOf('// ---- Core selection:')),context);
  vm.runInContext(app.slice(app.indexOf('function connect()'),app.indexOf('// ---- UI update')),context);
  context.connect();
  return {context,sockets,retries,
    online(value) {online = value;}, probe(fn) {probe = fn;},
    disconnect() {const socket=sockets.at(-1);socket.readyState=Socket.CLOSED;socket.onclose();},
    async retry() {assert.ok(retries.length,'a disconnected page must schedule recovery');await retries.shift()();},
  };
}

test('recovery checks server availability instead of failing more WebSocket attempts while offline', async () => {
  const d=dashboard();d.disconnect();
  for(let i=0;i<4;i++) {await d.retry();assert.equal(d.sockets.length,1,'offline HTTP probes must not open WebSockets');}
  d.online(true);await d.retry();
  assert.equal(d.sockets.length,2,'server recovery opens one new connection');
  assert.equal(d.retries.length,0);
});

test('an unsuccessful readiness response does not start a WebSocket handshake', async () => {
  const d=dashboard();d.disconnect();d.probe(async () => ({ok:false,status:503}));
  await d.retry();
  assert.equal(d.sockets.length,1);
  assert.equal(d.retries.length,1);
});

test('a delayed readiness response cannot replace a newer connection', async () => {
  const d=dashboard();d.disconnect();let resolve;
  d.probe(() => new Promise(done => {resolve=done;}));
  const pending=d.retry();
  assert.equal(d.sockets.length,1,'wait for the HTTP readiness result');
  d.context.connect();
  resolve({ok:true});await pending;
  assert.equal(d.sockets.length,2,'the old retry must leave the newer connecting socket alone');
});
