import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Panels move between their hidden pool and real tiles, as in the browser DOM.
class Element {
  constructor(id = '') { this.id = id; this.children = []; this.style = {}; this.dataset = {}; this.className = ''; }
  get firstChild() { return this.children[0] || null; }
  get classList() { return {
    contains: name => this.className.split(' ').includes(name),
    remove: name => { this.className = this.className.split(' ').filter(value => value !== name).join(' '); },
    toggle: name => {
      const names = new Set(this.className.split(' ').filter(Boolean));
      const added = !names.delete(name);
      if (added) names.add(name);
      this.className = [...names].join(' ');
      return added;
    },
  }; }
  appendChild(child) {
    child.parentNode?.removeChild(child);
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parentNode = null; }
  closest(selector) {
    for (let node = this; node; node = node.parentNode)
      if (node.classList.contains(selector.slice(1))) return node;
    return null;
  }
  querySelector(selector) {
    return this.children.find(node => selector === '.panel-collapse-btn' &&
      node.classList.contains('panel-collapse-btn')) || null;
  }
  addEventListener() {}
  contains(child) { return this === child || this.children.some(node => node.contains(child)); }
}
const source = readFileSync(new URL('../ui/static/layout.js', import.meta.url), 'utf8');
function dashboard(saved, preferences = {}) {
  const storage = new Map([['pru-layout-ver', '4'], ['pru-layout-sc', JSON.stringify(saved)],
    ...Object.entries(preferences)]);
  const events = [];
  const nodes = new Map();
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, new Element(id));
    return nodes.get(id);
  };
  const context = vm.createContext({
    document: { getElementById: get, createElement: () => new Element(), dispatchEvent: event => events.push(event.type) },
    localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value),
      removeItem: key => storage.delete(key) },
    CustomEvent: class { constructor(type) { this.type = type; } },
  });
  const run = code => vm.runInContext(code, context);
  run(source);
  run("initLayout('sc')");
  return { run, get, storage, events };
}
const split = (children, sizes, dir = 'h') => ({ type: 'split', dir, children, sizes });
const leaf = panelId => ({ type: 'leaf', panelId });
const invalidFullTree = change => {
  const tree = dashboard(null).run("defaultTree('sc')");
  change(tree);
  return tree;
};

for (const [name, saved] of [
  ['empty tree', split([], [])],
  ['unknown panels', leaf('retired-panel')],
  ['missing panel', leaf('memory1')],
  ['duplicate panels', split([leaf('memory1'), leaf('memory1')], [50, 50])],
  ['zero widths', invalidFullTree(tree => { tree.sizes = [0, 0, 0]; })],
  ['invalid child', invalidFullTree(tree => { tree.children[0] = null; })],
  ['invalid direction', invalidFullTree(tree => { tree.dir = 'diagonal'; })],
  ['invalid size', invalidFullTree(tree => { tree.sizes[0] = null; })],
]) {
  test(`shared layout recovers from saved ${name}`, () => {
    const d = dashboard(saved);
    assert.equal(d.get('tile-root').contains(d.get('source-panel')), true,
      'the shared source panel must be attached on first paint');
    assert.equal(d.get('tile-root').contains(d.get('signal-graph-panel')), true);
    assert.equal(d.run('JSON.stringify(currentTree) === JSON.stringify(SC_DEFAULT_TREE)'), true);
    assert.equal(JSON.parse(d.storage.get('pru-layout-sc')).children.length, 3,
      'replace the invalid saved layout so it cannot return on reload');
  });
}

test('valid reordered panels and hidden preferences survive reload', () => {
  const saved = dashboard(null).run("defaultTree('sc')");
  saved.children.reverse();
  saved.sizes.reverse();
  const d = dashboard(saved, { 'pru-panel-visibility-sc': JSON.stringify(['memory2']) });
  assert.equal(d.run('JSON.stringify(currentTree)'), JSON.stringify(saved));
  assert.equal(d.get('tile-root').contains(d.get('memory-panel-2')), false);
  assert.equal(d.get('tile-root').contains(d.get('source-panel')), true);
  d.run("initLayout('sc')");
  assert.equal(d.run('JSON.stringify(currentTree)'), JSON.stringify(saved));
  assert.equal(d.get('tile-root').contains(d.get('memory-panel-2')), false);
});

test('abandoned multicore layouts do not replace the shared layout', () => {
  const d = dashboard(null, { 'pru-layout-mc': JSON.stringify(leaf('retired-panel')),
    'pru-panel-visibility-mc': JSON.stringify(['source']) });
  assert.equal(d.run('JSON.stringify(currentTree) === JSON.stringify(SC_DEFAULT_TREE)'), true);
  assert.equal(d.get('tile-root').contains(d.get('source-panel')), true);
  assert.equal(d.run('getLeafIds(currentTree).length'), 8);
});

// #61: the panel itself survives rebuilding its tile, including its collapsed class.
test('Reset Layout expands minimized and hidden panels and resets core selection', () => {
    const d = dashboard(null);
    const panel = d.get('memory-panel');
    const button = new Element();
    button.className = 'panel-collapse-btn';
    button.textContent = '−';
    panel.appendChild(button);
    d.run("togglePanelCollapse(PANEL_REGISTRY.memory1)");
    assert.equal(panel.classList.contains('panel-collapsed'), true);
    assert.equal(panel.closest('.tile-child').style.flex, '0 0 26px');
    d.run("setPanelsVisibility(['memory1'], false)");
    d.run('resetLayout()');
    assert.equal(d.events.filter(type => type === 'pru-cores-reset').length, 1);
    assert.equal(d.get('tile-root').contains(panel), true);
    assert.equal(panel.classList.contains('panel-collapsed'), false);
    assert.equal(button.textContent, '−');
    assert.notEqual(panel.closest('.tile-child').style.flex, '0 0 26px');
    assert.equal(d.run('hiddenPanelIds.size'), 0);
    // The first click after Reset must minimize, rather than expand a stale state.
    d.run("togglePanelCollapse(PANEL_REGISTRY.memory1)");
    assert.equal(panel.classList.contains('panel-collapsed'), true);
});
