import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// The toolbar SKIP toggle (idle skip on/off): markup, click and state sync.
const html = readFileSync(new URL('../ui/static/index.html', import.meta.url), 'utf8');
const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');

function loadFunction(context, name) {
  const start = app.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} is implemented`);
  const open = app.indexOf('{', start); let depth = 1, end = open + 1;
  while (depth) { if (app[end] === '{') depth++; if (app[end] === '}') depth--; end++; }
  vm.runInContext(app.slice(start, end), context);
}

function harness() {
  const button = { attributes: {}, title: '',
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name]; } };
  const sent = [];
  const context = vm.createContext({ btnIdleSkip: button, currentCore: 'pru1',
    sendAction: action => { sent.push(JSON.parse(JSON.stringify(action))); return true; } });
  for (const name of ['showIdleSkip', 'toggleIdleSkip']) loadFunction(context, name);
  return { context, button, sent };
}

test('the toolbar has a SKIP toggle in the execution group', () => {
  const start = html.indexOf('aria-label="Execution controls"');
  const group = html.slice(start, html.indexOf('aria-label="Session actions"', start));
  assert.match(group, /id="btn-idle-skip"[^>]*aria-pressed="true"/);
  assert.match(group, />Skip</);
});

test('state messages set the toggle and its tooltip', () => {
  const { context, button } = harness();
  context.showIdleSkip(false);
  assert.equal(button.getAttribute('aria-pressed'), 'false');
  assert.match(button.title, /off/i);
  context.showIdleSkip(true);
  assert.equal(button.getAttribute('aria-pressed'), 'true');
  assert.match(button.title, /identical/);
});

test('a click asks the server for the opposite state', () => {
  const { context, sent } = harness();
  context.showIdleSkip(true);
  context.toggleIdleSkip();
  assert.deepEqual(sent, [{ action: 'set_idle_skip', core: 'pru1', enabled: false }]);
  context.showIdleSkip(false);
  context.toggleIdleSkip();
  assert.equal(sent[1].enabled, true);
});

test('state handling and the click listener are wired', () => {
  assert.match(app, /showIdleSkip\(state\.idle_skip\)/);
  assert.match(app, /btnIdleSkip\.addEventListener\("click", toggleIdleSkip\)/);
});
