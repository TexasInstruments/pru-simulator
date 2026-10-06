import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const focused = [];
class Element {
  constructor() {
    this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {};
    this.props = {};
    this.style = { setProperty: (name, value) => { this.props[name] = value; } };
  }
  get classList() {
    return { contains: name => (this.className || '').split(' ').includes(name),
      add: name => { if (!(this.className || '').split(' ').includes(name)) this.className = `${this.className || ''} ${name}`.trim(); },
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
  getAttribute(key) { return this.attributes[key]; }
  focus() { focused.push(this); }
  remove() {}
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  replaceChildren() { this.children = []; }
  addEventListener(event, fn) { this.listeners[event] = fn; }
  get offsetLeft() { return this.box?.left ?? 0; }
  get offsetWidth() { return this.box?.width ?? 0; }
  querySelector() { return null; }
}
const ids = ['main', 'io-view', 'devices-view', 'peripherals-view', 'events-view', 'motor-view',
  'panel-visibility', 'panel-visibility-buttons', 'device-runtime-panel', 'workspace-theme',
  'device-event-log', 'io-subtabs', 'core-fault-banner', 'io-fault-badge'];
const elements = new Map(ids.map(id => [id, new Element()]));
elements.get('io-fault-badge').children = [new Element(), new Element()];
const buttons = ['simulator', 'io', 'motor'].map((name, index) => {
  const button = new Element(); button.dataset.workspaceView = name;
  button.box = { left: index * 100, width: 100 }; return button;
});
const subtabs = ['devices', 'peripherals', 'events'].map((name, index) => {
  const tab = new Element(); tab.dataset.ioTab = name;
  tab.box = { left: index * 80, width: 80 }; return tab;
});
const viewSwitch = new Element();
const listeners = {};
const rafQueue = [];
const storage = new Map();
const document = {
  documentElement: new Element(),
  getElementById: id => elements.get(id), querySelector: () => viewSwitch,
  querySelectorAll: selector => (selector.includes('data-io-tab') ? subtabs : buttons),
  createElement: () => new Element(),
  addEventListener: (name, fn) => { listeners[name] = fn; },
  dispatchEvent: event => { listeners[event.type]?.(event); },
};
const resizeListeners = [];
const context = vm.createContext({ document,
  window: { addEventListener: (name, fn) => { if (name === 'resize') resizeListeners.push(fn); } },
  requestAnimationFrame: fn => { rafQueue.push(fn); },
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
run(readFileSync(new URL('../ui/static/chrome.js', import.meta.url), 'utf8'));
run(readFileSync(new URL('../ui/static/layout.js', import.meta.url), 'utf8'));
run(`PANEL_REGISTRY.source = {querySelector: () => null};
  PANEL_REGISTRY.editor = {querySelector: () => null};
  modePanelIds.sc = ['source', 'editor']; currentTree = SC_DEFAULT_TREE;
  render = () => {};`);
run(readFileSync(new URL('../ui/static/workspace.js', import.meta.url), 'utf8'));

const panes = ['devices-view', 'peripherals-view', 'events-view'];
const visiblePanes = () => panes.filter(id => !elements.get(id).hidden);
const viewEvents = [];
listeners['pru-workspace-view-changed'] = event => viewEvents.push([event.detail.view, event.detail.tab]);

// First paint: Simulator view, no glider transition armed yet.
assert.equal(elements.get('main').hidden, false);
assert.equal(elements.get('io-view').hidden, true);
assert.equal(elements.get('motor-view').hidden, true);
assert.equal(buttons[0].attributes['aria-pressed'], 'true');
assert.equal(viewSwitch.dataset.ready, undefined, 'no glider animation on first paint');
assert.equal(viewSwitch.props['--glider-x'], '0px');
assert.equal(viewSwitch.props['--glider-w'], '100px');
assert.ok(viewSwitch.classList.contains('has-glider'));
rafQueue.splice(0).forEach(fn => fn());
rafQueue.splice(0).forEach(fn => fn());
assert.equal(viewSwitch.dataset.ready, '1', 'transitions are enabled after the first paint');

// I/O & Devices opens on Devices; it has no content of its own.
buttons[1].listeners.click();
assert.equal(elements.get('io-view').hidden, false);
assert.equal(elements.get('main').hidden, true);
assert.equal(elements.get('panel-visibility').hidden, true, 'panel toggles are Simulator-only');
assert.deepEqual(visiblePanes(), ['devices-view']);
assert.equal(storage.get('pru-workspace-view'), 'io');
assert.equal(storage.get('pru-io-tab'), 'devices');
assert.equal(buttons[1].attributes['aria-pressed'], 'true');
assert.deepEqual(viewEvents.at(-1), ['io', 'devices']);
assert.equal(viewSwitch.props['--glider-x'], '100px', 'glider follows the active top-level tab');

// Sub-tabs: click, ARIA state, roving tabindex, persistence, glider.
subtabs[1].listeners.click();
assert.deepEqual(visiblePanes(), ['peripherals-view']);
assert.deepEqual(subtabs.map(tab => tab.attributes['aria-selected']), ['false', 'true', 'false']);
assert.deepEqual(subtabs.map(tab => tab.attributes.tabindex), ['-1', '0', '-1']);
assert.equal(storage.get('pru-io-tab'), 'peripherals');
assert.equal(elements.get('io-subtabs').props['--glider-x'], '80px');
assert.equal(elements.get('io-subtabs').props['--glider-w'], '80px');

// Keyboard: arrows wrap, Home/End jump, focus moves with selection, other keys are ignored.
const key = (tab, name) => { let prevented = false;
  tab.listeners.keydown({ key: name, preventDefault: () => { prevented = true; } }); return prevented; };
assert.equal(key(subtabs[1], 'ArrowRight'), true);
assert.deepEqual(visiblePanes(), ['events-view']);
assert.equal(focused.at(-1), subtabs[2]);
key(subtabs[2], 'ArrowRight');
assert.deepEqual(visiblePanes(), ['devices-view'], 'ArrowRight wraps to the first tab');
key(subtabs[0], 'ArrowLeft');
assert.deepEqual(visiblePanes(), ['events-view'], 'ArrowLeft wraps to the last tab');
key(subtabs[2], 'Home');
assert.deepEqual(visiblePanes(), ['devices-view']);
key(subtabs[0], 'End');
assert.deepEqual(visiblePanes(), ['events-view']);
assert.equal(key(subtabs[2], 'a'), false, 'unrelated keys keep their default action');
assert.deepEqual(visiblePanes(), ['events-view']);

// Motor control hides the I/O view; coming back keeps the sub-tab.
buttons[2].listeners.click();
assert.equal(elements.get('motor-view').hidden, false, 'Motor control view is reachable');
assert.equal(elements.get('io-view').hidden, true);
assert.equal(elements.get('main').hidden, true);
assert.equal(storage.get('pru-workspace-view'), 'motor');
assert.deepEqual(viewEvents.at(-1), ['motor', 'events'], 'view changes are announced so canvases can redraw');
buttons[1].listeners.click();
assert.equal(elements.get('motor-view').hidden, true);
assert.deepEqual(visiblePanes(), ['events-view'], 'the sub-tab is remembered');
buttons[0].listeners.click();
assert.equal(elements.get('main').hidden, false);
assert.equal(elements.get('panel-visibility').hidden, false);

// Fault badge: counts only faults not yet seen, clears when Events is opened.
const badge = elements.get('io-fault-badge');
const update = state => context.window.updateWorkspaceEvents({ core: 'pru0', ...state });
update({ io: { device_bus: { faults: ['a: bad', 'bus contention'] } }, core_faults: {} });
assert.equal(badge.hidden, false);
assert.equal(badge.children[0].textContent, '2');
assert.equal(badge.children[1].textContent, ' 2 new faults', 'text alternative, not colour only');
update({ io: { device_bus: { faults: ['a: bad', 'bus contention', 'c: bad'] } }, core_faults: {} });
assert.equal(badge.children[0].textContent, '3');
buttons[1].listeners.click();
subtabs[2].listeners.click();
assert.equal(badge.hidden, true, 'opening Events acknowledges the faults');
update({ io: { device_bus: { faults: ['a: bad', 'bus contention', 'c: bad', 'd: bad'] } }, core_faults: {} });
assert.equal(badge.hidden, true, 'faults arriving while Events is open are seen');
subtabs[0].listeners.click();
update({ io: { device_bus: { faults: ['a: bad', 'bus contention', 'c: bad', 'd: bad'] } },
  core_faults: { pru0: { opcode: 'LBBO', address: 12, error: 'Unmapped memory' } } });
assert.equal(badge.children[0].textContent, '1', 'a core memory fault is a new fault');
assert.equal(badge.children[1].textContent, ' 1 new fault');
update({ io: {}, core_faults: {} });
assert.equal(badge.hidden, true, 'a reset that clears the faults clears the badge');
update({ io: { device_bus: { faults: ['e: again'] } }, core_faults: {} });
assert.equal(badge.children[0].textContent, '1', 'new faults after a reset re-arm it');
update({ io: { device_bus: { events: [{ cycle: 1, device: 'x', kind: 'frame' }] } }, core_faults: {} });
assert.equal(badge.hidden, true, 'ordinary events never count');

// The gliders are measured when the badge changes, not on every state message.
const activeButton = buttons.find(button => button.getAttribute('aria-pressed') === 'true');
const measuredWidth = activeButton.box.width;
activeButton.box = { left: activeButton.box.left, width: measuredWidth + 40 };
update({ io: { device_bus: { events: [{ cycle: 2, device: 'x', kind: 'frame' }] } }, core_faults: {} });
assert.equal(viewSwitch.props['--glider-w'], measuredWidth + 'px', 'unchanged badge: no re-measure');
update({ io: { device_bus: { faults: ['f: new'] } }, core_faults: {} });
assert.equal(viewSwitch.props['--glider-w'], (measuredWidth + 40) + 'px', 'changed badge: re-measure');
activeButton.box = { left: activeButton.box.left, width: measuredWidth };
update({ io: {}, core_faults: {} });

// Simulator view shows the core memory fault; other views do not.
buttons[0].listeners.click();
const banner = elements.get('core-fault-banner');
assert.equal(banner.hidden, true);
update({ io: {}, core_faults: { pru0: { opcode: 'LBBO', address: 12, error: 'Unmapped memory' } } });
assert.equal(banner.hidden, false);
assert.match(banner.textContent, /Core fault.*pru0.*LBBO at 12: Unmapped memory/);
{
  // role="alert": an unchanged fault must not be rewritten (re-announced) on every state.
  let text = banner.textContent; let writes = 0;
  Object.defineProperty(banner, 'textContent', { configurable: true,
    get: () => text, set: value => { writes += 1; text = value; } });
  update({ io: {}, core_faults: { pru0: { opcode: 'LBBO', address: 12, error: 'Unmapped memory' } } });
  assert.equal(writes, 0, 'the same fault leaves the alert untouched');
  delete banner.textContent; banner.textContent = text;
}
buttons[1].listeners.click();
assert.equal(banner.hidden, true, 'the banner belongs to the Simulator view');
buttons[0].listeners.click();
assert.equal(banner.hidden, false);
update({ io: {}, core_faults: {} });
assert.equal(banner.hidden, true);

// Saved views from before the I/O & Devices tab map onto it.
for (const [saved, tab] of [['devices', 'devices'], ['events', 'events']]) {
  storage.set('pru-workspace-view', saved);
  storage.delete('pru-io-tab');
  run(readFileSync(new URL('../ui/static/workspace.js', import.meta.url), 'utf8'));
  assert.equal(elements.get('io-view').hidden, false, `saved "${saved}" opens I/O & Devices`);
  assert.deepEqual(visiblePanes(), [`${tab}-view`]);
  assert.equal(storage.get('pru-workspace-view'), 'io');
}
storage.set('pru-workspace-view', 'io');
storage.set('pru-io-tab', 'peripherals');
run(readFileSync(new URL('../ui/static/workspace.js', import.meta.url), 'utf8'));
assert.deepEqual(visiblePanes(), ['peripherals-view'], 'the saved sub-tab is restored');
buttons[0].listeners.click();
const themeEvents = [];
listeners['pru-theme-changed'] = event => themeEvents.push(event.detail.theme);
elements.get('workspace-theme').value = 'contrast';
elements.get('workspace-theme').listeners.change();
assert.equal(document.documentElement.dataset.theme, 'contrast');
assert.equal(storage.get('pru-workspace-theme'), 'contrast');
assert.deepEqual(themeEvents, ['contrast'], 'theme changes are announced so canvases can redraw');

const firstToggle = elements.get('panel-visibility-buttons').children[0];
assert.equal(firstToggle.children[0].className, 'panel-toggle-icon', 'panel toggles carry an icon');
assert.equal(firstToggle.children[1].textContent, 'Source / Disassembly', 'and a readable label');
firstToggle.listeners.click();
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

// Default Simulator layout: the I/O Pins tile is tall enough for R30 and R31.
assert.equal(run("SC_DEFAULT_TREE.children[1].sizes.join(',')"), '30,45,25');

// Three and four core layouts show every core's source and registers once.
for (const [mode, cores] of [['mc', ['pru0', 'rtu0']], ['mc3', ['pru0', 'rtu0', 'x2']], ['mc4', ['pru0', 'rtu0', 'x2', 'x3']]]) {
  const leaves = run(`getLeafIds(defaultTree('${mode}'))`);
  assert.equal(new Set(leaves).size, leaves.length, `${mode}: no panel twice`);
  for (const core of cores) for (const kind of ['source', 'registers'])
    assert.ok(leaves.includes(`mc-${core}-${kind}`), `${mode} shows mc-${core}-${kind}`);
  assert.equal(leaves.filter(id => id.startsWith('mc-')).length, cores.length * 2, `${mode}: only its own cores`);
  for (const shared of ['editor', 'io', 'signal-graph', 'mem-graph', 'memory1', 'memory2'])
    assert.ok(leaves.includes(shared), `${mode} keeps ${shared}`);
}

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
