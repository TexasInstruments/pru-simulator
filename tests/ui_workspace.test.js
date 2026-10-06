import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor() { this.children = []; this.dataset = {}; this.style = {}; this.attributes = {}; this.listeners = {}; }
  get classList() {
    return { contains: name => (this.className || '').split(' ').includes(name),
      toggle: name => {
        const names = new Set((this.className || '').split(' ').filter(Boolean));
        const added = !names.delete(name);
        if (added) names.add(name);
        this.className = Array.from(names).join(' ');
        return added;
      } };
  }
  closest(selector) {
    for (let element = this; element; element = element.parentNode)
      if (element.classList.contains(selector.slice(1))) return element;
    return null;
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  replaceChildren() { this.children = []; }
  addEventListener(event, fn) { this.listeners[event] = fn; }
  querySelector() { return null; }
}
const ids = ['main', 'devices-view', 'motor-view', 'events-view', 'panel-visibility',
  'panel-visibility-buttons', 'device-runtime-panel', 'workspace-theme', 'device-event-log'];
const elements = new Map(ids.map(id => [id, new Element()]));
const buttons = ['simulator', 'devices', 'motor', 'events'].map(name => {
  const button = new Element(); button.dataset.workspaceView = name; return button;
});
const listeners = {};
const storage = new Map();
const document = {
  documentElement: new Element(),
  getElementById: id => elements.get(id), querySelectorAll: () => buttons,
  createElement: () => new Element(),
  addEventListener: (name, fn) => { listeners[name] = fn; },
  dispatchEvent: event => { listeners[event.type]?.(event); },
};
const context = vm.createContext({ document, window: {},
  CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init?.detail; } },
  localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
});
const run = source => vm.runInContext(source, context);
const html = readFileSync(new URL('../ui/static/index.html', import.meta.url), 'utf8');
for (const selector of ['core-select', 'mc-partner-select', 'mc-load-core']) {
  const select = html.match(new RegExp(`<select[^>]*id="${selector}"[^>]*>([\\s\\S]*?)</select>`));
  assert.ok(select, `${selector} exists`);
  assert.match(select[1], /value="rtu1"/, `${selector} supports RTU1`);
}
run(readFileSync(new URL('../ui/static/layout.js', import.meta.url), 'utf8'));
run(`PANEL_REGISTRY.source = {querySelector: () => null};
  PANEL_REGISTRY.editor = {querySelector: () => null};
  modePanelIds.sc = ['source', 'editor']; currentTree = SC_DEFAULT_TREE;
  render = () => {};`);
run(readFileSync(new URL('../ui/static/workspace.js', import.meta.url), 'utf8'));

assert.equal(elements.get('main').hidden, false);
buttons[1].listeners.click();
assert.equal(elements.get('devices-view').hidden, false);
assert.equal(elements.get('main').hidden, true);
assert.equal(elements.get('panel-visibility').hidden, true);
assert.equal(storage.get('pru-workspace-view'), 'devices');
assert.equal(buttons[1].attributes['aria-pressed'], 'true');
assert.ok(elements.get('devices-view').children.includes(elements.get('device-runtime-panel')));
const viewEvents = [];
listeners['pru-workspace-view-changed'] = event => viewEvents.push(event.detail.view);
buttons[2].listeners.click();
assert.equal(elements.get('motor-view').hidden, false, 'Motor control view is reachable');
assert.equal(elements.get('devices-view').hidden, true);
assert.equal(elements.get('main').hidden, true);
assert.equal(storage.get('pru-workspace-view'), 'motor');
assert.deepEqual(viewEvents, ['motor'], 'view changes are announced so canvases can redraw');
buttons[1].listeners.click();
assert.equal(elements.get('motor-view').hidden, true);
elements.get('workspace-theme').value = 'contrast';
elements.get('workspace-theme').listeners.change();
assert.equal(document.documentElement.dataset.theme, 'contrast');
assert.equal(storage.get('pru-workspace-theme'), 'contrast');

elements.get('panel-visibility-buttons').children[0].listeners.click();
assert.equal(run("getPanelVisibility('source')"), false);
assert.equal(run("setPanelsVisibility(['editor'], false)"), false, 'cannot hide every panel');
run('resetLayout()');
assert.equal(run("getPanelVisibility('source')"), true);
run("setPanelsVisibility(['source'], false); switchLayoutMode('mc'); switchLayoutMode('sc');");
assert.equal(run("getPanelVisibility('source')"), false, 'visibility survives mode changes');

context.window.updateWorkspaceEvents({ core: 'pru0', io: { device_bus: {
  devices: [{ name: '<encoder>', core: 'pru1' }],
  events: [{ cycle: 42, device: '<encoder>', kind: 'frame', position: 123 }],
  faults: ['conflict'],
} } });
assert.match(elements.get('device-event-log').textContent, /pru1.*42.*<encoder>.*frame/);
assert.match(elements.get('device-event-log').textContent, /FAULT.*conflict/);
context.window.updateWorkspaceEvents({ io: {} });
assert.match(elements.get('device-event-log').textContent, /No device events/);
const fault = {opcode: 'LBBO', address: 12, error: 'Unmapped memory'};
context.window.updateWorkspaceEvents({core: 'pru0', core_faults: {pru0: fault}}, ['pru0', 'rtu1']);
context.window.updateWorkspaceEvents({core: 'rtu1', core_faults: {pru0: fault}}, ['pru0', 'rtu1']);
assert.match(elements.get('device-event-log').textContent, /pru0.*LBBO.*Unmapped memory/);
context.window.updateWorkspaceEvents({core: 'rtu1', core_faults: {pru0: fault}}, ['rtu1']);
assert.match(elements.get('device-event-log').textContent, /No device events/);
context.window.updateWorkspaceEvents({core: 'pru0', core_faults: {}}, ['pru0', 'rtu1']);
assert.match(elements.get('device-event-log').textContent, /No device events/);

// Resize correctly persists the pair when a hidden sibling sits between them.
run(`hiddenPanelIds = new Set(['hidden']);
  const tree = {type: 'split', children: [
    {type:'leaf', panelId:'source'}, {type:'leaf', panelId:'hidden'}, {type:'leaf', panelId:'editor'}
  ], sizes: [30, 20, 50]};
  const leaf = id => ({querySelectorAll: () => [{dataset:{panelId:id}}]});
  updateSizesInTree(tree, leaf('source'), leaf('editor'), 50, 50);
  if (tree.sizes.join(',') !== '40,20,40') throw new Error('hidden sibling resize');`);

// Keeping a vertical wrapper for a lone visible child prevents collapsing
// its horizontal column, and rebuilding visibility must preserve collapse.
context.testPanel = new Element();
run(`PANEL_REGISTRY.registers = testPanel;
  hiddenPanelIds = new Set(['hidden']);
  const loneTree = {type:'split', dir:'h', sizes:[100], children:[
    {type:'split', dir:'v', sizes:[40,60], children:[
      {type:'leaf', panelId:'registers'}, {type:'leaf', panelId:'hidden'}
    ]}
  ]};
  renderNode(loneTree);
  const child = testPanel.closest('.tile-child');
  if (child.parentNode.className !== 'tile-split tile-split-v') throw new Error('lost vertical wrapper');
  togglePanelCollapse(testPanel);
  if (child.style.flex !== '0 0 26px') throw new Error('collapse height');
  renderNode(loneTree);
  if (testPanel.closest('.tile-child').style.flex !== '0 0 26px') throw new Error('collapse lost during render');
  togglePanelCollapse(testPanel);
  if (testPanel.closest('.tile-child').style.flex !== '40') throw new Error('expand size');`);
console.log('Workspace navigation, theme, events, panel persistence and hidden-sibling resizing pass');
