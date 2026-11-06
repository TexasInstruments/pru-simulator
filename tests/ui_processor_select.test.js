import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Runs the real processor-selector code from app.js against a fake <select>,
// fetch and confirm().
const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');

class Select {
  constructor() { this.options = []; this.hidden = true; this.disabled = false; this.dataset = {};
    this.listeners = {}; this._value = ''; }
  get value() { return this._value; }
  set value(v) { this._value = v; }
  replaceChildren(...options) { this.options = options; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
}

function dashboard({ initial = 'AM243x', choices = ['AM243x', 'AM261x', 'AM263x'], confirms = true } = {}) {
  const select = new Select();
  const requests = [], errors = [], confirmations = [], calls = [];
  const responses = [];
  const context = vm.createContext({
    document: {
      getElementById: id => id === 'processor-select' ? select : null,
      createElement: () => ({ value: '', textContent: '' }),
    },
    fetch: async (url, init) => {
      requests.push({ url, method: init?.method || 'GET', body: init?.body && JSON.parse(init.body) });
      if (!init) return { ok: true, json: async () => ({ processor: initial, choices }) };
      return responses.shift();
    },
    confirm: message => { confirmations.push(message); return confirms; },
    showErrors: list => errors.push(...list),
    loadCoreChoices: async () => { calls.push('loadCoreChoices'); },
    refreshAfterReload: async () => { calls.push('refreshAfterReload'); },
    updateCoresControl: () => { calls.push('updateCoresControl'); },
    running: false, simRunning: false, sharedExecution: { running: false },
    processorChanging: false,
  });
  for (const name of ['syncProcessorEnabled', 'showProcessor', 'loadProcessor', 'setProcessor']) {
    const start = app.indexOf(`function ${name}(`);
    assert.ok(start >= 0, `${name} is implemented`);
    const asyncStart = app.slice(start - 6, start) === 'async ' ? start - 6 : start;
    let end = app.indexOf('{', start) + 1, depth = 1;
    while (depth) { if (app[end] === '{') depth++; if (app[end] === '}') depth--; end++; }
    vm.runInContext(app.slice(asyncStart, end), context);
  }
  // The change listener registered at load time in app.js.
  select.addEventListener('change', event => context.setProcessor(event.target.value));
  return { context, select, requests, errors, confirmations, calls, responses };
}

test('the selector is populated from GET /config/processor and shown', async () => {
  const d = dashboard();
  await d.context.loadProcessor();
  assert.deepEqual(d.requests.map(r => [r.method, r.url]), [['GET', '/config/processor']]);
  assert.deepEqual(d.select.options.map(o => o.value), ['AM243x', 'AM261x', 'AM263x']);
  assert.equal(d.select.value, 'AM243x');
  assert.equal(d.select.hidden, false);
});

test('a websocket processor name updates the selection without changing the choices', async () => {
  const d = dashboard();
  await d.context.loadProcessor();
  d.context.showProcessor('AM261x');
  assert.equal(d.select.value, 'AM261x');
  assert.equal(d.select.dataset.current, 'AM261x');
  assert.equal(d.select.options.length, 3);
});

test('changing the selection confirms, PUTs, then rebuilds cores and views', async () => {
  const d = dashboard();
  await d.context.loadProcessor();
  d.responses.push({ ok: true, json: async () => ({ ok: true, processor: 'AM261x', choices: ['AM243x', 'AM261x', 'AM263x'] }) });
  d.select.value = 'AM261x';
  await d.select.listeners.change({ target: d.select });
  assert.match(d.confirmations[0], /AM261x.*reloads the simulator and clears loaded programs/);
  const put = d.requests.at(-1);
  assert.deepEqual([put.method, put.url, put.body], ['PUT', '/config/processor', { processor: 'AM261x' }]);
  assert.equal(d.select.value, 'AM261x');
  assert.equal(d.select.disabled, false, 'enabled again after the request');
  assert.ok(d.calls.indexOf('loadCoreChoices') < d.calls.indexOf('refreshAfterReload'),
    'the available cores are reloaded before the views are refreshed');
  assert.deepEqual(d.errors, []);
});

test('a refused confirmation reverts the selection and sends nothing', async () => {
  const d = dashboard({ confirms: false });
  await d.context.loadProcessor();
  d.select.value = 'AM263x';
  await d.select.listeners.change({ target: d.select });
  assert.equal(d.select.value, 'AM243x');
  assert.equal(d.requests.filter(r => r.method === 'PUT').length, 0);
});

test('a server error reverts the selection and shows the error', async () => {
  const d = dashboard();
  await d.context.loadProcessor();
  d.responses.push({ ok: false, json: async () => ({ error: 'bad config' }) });
  d.select.value = 'AM263x';
  await d.select.listeners.change({ target: d.select });
  assert.equal(d.select.value, 'AM243x', 'reverted');
  assert.equal(d.select.disabled, false);
  assert.match(d.errors.join('\n'), /bad config/);
  assert.ok(!d.calls.includes('refreshAfterReload'), 'a failed switch does not wipe the views');
});

test('a network failure reverts the selection too', async () => {
  const d = dashboard();
  await d.context.loadProcessor();
  d.context.fetch = async () => { throw new Error('offline'); };
  d.select.value = 'AM261x';
  await d.select.listeners.change({ target: d.select });
  assert.equal(d.select.value, 'AM243x');
  assert.match(d.errors.join('\n'), /offline/);
});

test('the selector is disabled while the simulator runs and ignores changes then', async () => {
  const d = dashboard();
  await d.context.loadProcessor();
  d.context.simRunning = true;
  d.context.showProcessor('AM243x');
  assert.equal(d.select.disabled, true);
  d.select.value = 'AM261x';
  await d.select.listeners.change({ target: d.select });
  assert.equal(d.select.value, 'AM243x');
  assert.equal(d.requests.filter(r => r.method === 'PUT').length, 0);
  assert.equal(d.confirmations.length, 0);
});

test('the selector is disabled for the duration of the request', async () => {
  const d = dashboard();
  await d.context.loadProcessor();
  let release;
  d.context.fetch = (url, init) => new Promise(resolve => {
    release = () => resolve({ ok: true, json: async () => ({ ok: true, processor: 'AM261x', choices: ['AM243x', 'AM261x'] }) });
  });
  d.select.value = 'AM261x';
  const pending = d.select.listeners.change({ target: d.select });
  assert.equal(vm.runInContext('processorChanging', d.context), true);
  d.context.showProcessor('AM243x');
  assert.equal(d.select.disabled, true, 'a refresh during the request keeps it disabled');
  release();
  await pending;
  assert.equal(vm.runInContext('processorChanging', d.context), false);
});

test('a follow-up failure after a successful switch keeps the new processor shown', async () => {
  const d = dashboard();
  await d.context.loadProcessor();
  d.context.loadCoreChoices = async () => { throw new Error('cores unavailable'); };
  d.responses.push({ ok: true, json: async () => ({ ok: true, processor: 'AM263x',
    choices: ['AM243x', 'AM261x', 'AM263x'] }) });
  d.select.value = 'AM263x';
  await d.select.listeners.change({ target: d.select });
  assert.equal(d.select.value, 'AM263x', 'not reverted');
  assert.equal(d.select.dataset.current, 'AM263x');
  assert.match(d.errors.join('\n'), /cores unavailable/);
});
