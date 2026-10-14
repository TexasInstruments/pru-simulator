import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Panels move between their hidden pool and real tiles, as in the browser DOM.
class Element {
  constructor(id = '') { this.id = id; this.children = []; this.style = {}; this.dataset = {}; this.className = ''; }
  get firstChild() { return this.children[0] || null; }
  get classList() { return { contains: name => this.className.split(' ').includes(name) }; }
  appendChild(child) {
    child.parentNode?.removeChild(child);
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parentNode = null; }
  querySelector() { return null; }
  addEventListener() {}
  contains(child) { return this === child || this.children.some(node => node.contains(child)); }
}
const source = readFileSync(new URL('../ui/static/layout.js', import.meta.url), 'utf8');
function dashboard(saved, mode = 'mc') {
  const storage = new Map([['pru-layout-ver', '4'], [`pru-layout-${mode}`, JSON.stringify(saved)]]);
  const nodes = new Map();
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, new Element(id));
    return nodes.get(id);
  };
  const context = vm.createContext({
    document: { getElementById: get, createElement: () => new Element(), dispatchEvent() {} },
    localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value),
      removeItem: key => storage.delete(key) },
    CustomEvent: class {},
  });
  const run = code => vm.runInContext(code, context);
  run(source);
  run("initLayout('sc')");
  return { run, get, storage };
}
const split = (children, sizes, dir = 'h') => ({ type: 'split', dir, children, sizes });
const leaf = panelId => ({ type: 'leaf', panelId });
const invalidFullTree = change => {
  const tree = dashboard(null).run("defaultTree('mc')");
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
  test(`multicore recovers from saved ${name}`, () => {
    const d = dashboard(saved);
    assert.doesNotThrow(() => d.run("switchLayoutMode('mc')"));
    assert.equal(d.get('tile-root').contains(d.get('mc-pru0-source-panel')), true,
      'an enabled PRU0 panel must be attached to the visible tile tree');
    assert.equal(d.get('tile-root').contains(d.get('signal-graph-panel')), true);
    assert.equal(d.run('JSON.stringify(currentTree) === JSON.stringify(MC_DEFAULT_TREE)'), true);
    assert.equal(JSON.parse(d.storage.get('pru-layout-mc')).children.length, 3,
      'replace the invalid saved layout so it cannot return on reload');
  });
}

test('valid reordered panels and hidden preferences survive switching', () => {
  const d = dashboard(null);
  const saved = d.run("defaultTree('mc')");
  saved.children.reverse();
  saved.sizes.reverse();
  d.storage.set('pru-layout-mc', JSON.stringify(saved));
  d.storage.set('pru-panel-visibility-mc', JSON.stringify(['memory2']));
  d.run("switchLayoutMode('mc')");
  assert.equal(d.run('JSON.stringify(currentTree)'), JSON.stringify(saved));
  assert.equal(d.get('tile-root').contains(d.get('memory-panel-2')), false);
  assert.equal(d.get('tile-root').contains(d.get('mc-pru0-source-panel')), true);
  d.run("switchLayoutMode('sc'); switchLayoutMode('mc')");
  assert.equal(d.run('JSON.stringify(currentTree)'), JSON.stringify(saved));
});

for (const mode of ['sc', 'mc3', 'mc4']) {
  test(`${mode} recovers its own panels on first paint or mode switch`, () => {
    const d = dashboard(split([], []), mode);
    d.run(`switchLayoutMode('${mode}')`);
    assert.equal(d.run(`JSON.stringify(currentTree) === JSON.stringify(defaultTree('${mode}'))`), true);
    assert.equal(d.get('tile-root').contains(d.get('signal-graph-panel')), true);
    if (mode === 'sc') assert.equal(d.get('tile-root').contains(d.get('source-panel')), true);
    else assert.equal(d.get('tile-root').contains(d.get('mc-x2-source-panel')), true);
  });
}
