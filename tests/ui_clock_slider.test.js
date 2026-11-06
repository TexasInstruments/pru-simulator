import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Loads the real cores-deck.js against a stub DOM whose querySelector hands back one
// stable stub per selector, so the clock slider and its label can be driven directly.
const read = name => readFileSync(new URL(`../ui/static/${name}`, import.meta.url), 'utf8');

class Stub {
  constructor(selector = '', parent = null) {
    this.selector = selector; this.parent = parent; this.kids = new Map(); this.listeners = {};
    this.dataset = {}; this.attrs = {}; this.style = { setProperty() {} }; this.textContent = '';
    this.value = '0'; this.min = 0; this.max = 0; this.disabled = false; this.hidden = false;
    this.children = []; this.firstChild = selector === 'el' ? { classList: { toggle() {} } } : undefined;
    const cls = new Set();
    this.classList = { add: n => cls.add(n), remove: n => cls.delete(n), toggle: (n, v) => { v ? cls.add(n) : cls.delete(n); } };
  }
  querySelector(selector) {
    if (!this.kids.has(selector)) this.kids.set(selector, new Stub(selector, this));
    return this.kids.get(selector);
  }
  querySelectorAll() { return []; }
  closest(selector) {
    for (let n = this; n; n = n.parent) if (n.selector === selector) return n;
    return null;
  }
  contains() { return false; }
  matches() { return false; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  setAttribute(k, v) { this.attrs[k] = v; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren() { this.children = []; }
  getBoundingClientRect() { return { bottom: 0, width: 0 }; }
  emit(type, event) { (this.listeners[type] || []).forEach(fn => fn(event)); }
}

function deck() {
  const root = new Stub('root');
  const closest = root.closest.bind(root);
  root.closest = selector => selector === '.toolbar-context' ? root : closest(selector);
  const calls = [];
  const context = vm.createContext({
    PruCores: { ORDER: ['pru0', 'pru1', 'rtu0', 'rtu1'] },
    document: { createElement: () => new Stub('el'), addEventListener() {} },
    window: { addEventListener() {} },
    matchMedia: () => ({ matches: true }), setTimeout,
  });
  vm.runInContext(`${read('cores-deck.js')}; globalThis.createCoresDeck = createCoresDeck;`, context);
  const api = context.createCoresDeck(root, { action() {}, setClock: mhz => calls.push(mhz) });
  const row = root.querySelector('.cores-clock-single');
  const slider = row.querySelector('.cores-clock-slider');
  const rate = row.querySelector('[data-core-rate]');
  const state = (clocks, extra = {}) => api.update({ selection: { used: ['pru0'], shown: ['pru0'] },
    available: ['pru0'], states: {}, clocks, running: false, busy: false, ...extra });
  return { root, calls, slider, rate, state };
}
const CLOCKS = { mhz: 200, choices_mhz: [100, 150, 200, 250] };

test('dragging the clock slider only previews; the clock is set once on release', () => {
  const d = deck();
  d.state(CLOCKS);
  assert.equal(d.slider.value, 2);
  for (const value of [3, 1, 0]) {                       // drag through three steps
    d.slider.value = String(value);
    d.root.emit('input', { target: d.slider });
  }
  assert.deepEqual(d.calls, [], 'input events never reload the simulator');
  assert.equal(d.rate.textContent, '100 MHz', 'label previews the thumb position');
  d.root.emit('change', { target: d.slider });
  assert.deepEqual(d.calls, [100], 'release applies the final step only, not the first');
});

test('a state update during a drag does not move the thumb or the label', () => {
  const d = deck();
  d.state(CLOCKS);
  d.slider.value = '0';
  d.root.emit('input', { target: d.slider });
  d.state(CLOCKS);                                       // e.g. a core-state refresh mid-drag
  assert.equal(d.slider.value, '0');
  assert.equal(d.rate.textContent, '100 MHz');
  d.root.emit('change', { target: d.slider });
  d.state({ ...CLOCKS, mhz: 100 });                      // after release the model wins again
  assert.equal(d.slider.value, 0);
  assert.equal(d.rate.textContent, '100 MHz');
});

test('a drag cancelled back to the current value restores the model position', () => {
  const d = deck();
  d.state(CLOCKS);
  d.slider.value = '3';
  d.root.emit('input', { target: d.slider });
  d.root.emit('pointerup', { target: d.slider });
  return new Promise(resolve => setTimeout(resolve, 5)).then(() => {
    d.state(CLOCKS);
    assert.equal(d.slider.value, 2);
    assert.equal(d.rate.textContent, '200 MHz');
  });
});

test('app.js no longer carries the unused changeClock handler', () => {
  const app = read('app.js');
  assert.doesNotMatch(app, /changeClock/);
  assert.doesNotMatch(read('cores-deck.js'), /handlers\.clock\b/);
});
