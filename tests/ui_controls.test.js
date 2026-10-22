import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Exercise the dashboard's real control rendering/listeners without a browser.
class Element {
  constructor(value = '') { this.value = value; this.options = []; this.children = [];
    this.hidden = false; this.disabled = false; this.dataset = {}; this.listeners = {};
    this.style = {}; this.textContent = ''; this.attributes = {}; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = value; }
  appendChild(child) { this.children.push(child); if (child.tagName === 'option') this.options.push(child); }
  contains(element) { return element === this || this.children.some(child => child.contains(element)); }
  replaceChildren(...children) { this.options = [...children]; this.children = [...children]; }
}
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
const applyRate = new Element();
get('iep-clock-custom-form').appendChild(get('iep-clock-custom'));
get('iep-clock-custom-form').appendChild(applyRate);
const sent = [];
const storage = new Map();
const context = vm.createContext({ document: { getElementById: get,
  createElement: tag => { const element = new Element(); element.tagName = tag; return element; },
  activeElement: null, querySelectorAll: () => [] },
  localStorage: { setItem: (key, value) => storage.set(key, value) },
  sendAction: action => sent.push(action), currentCore: 'rtu1',
  availableCores: ['pru0','pru1','rtu0','rtu1'],
  coreSelection: { used: ['pru0', 'pru1', 'rtu1'], shown: ['rtu1'], focus: 'rtu1', pinned: true },
  multiCoreMode: true, devicePanelCore: 'rtu1', coreStates: {},
  deviceCoreSelect: get('device-core-select'), iepClockSelect: get('iep-clock-select'),
  deviceCoreSelectWrap: get('device-core-select-wrap'), sourceList: new Element(),
  stopRun: () => {}, stopSim: () => {}, mcFreshRegs: () => new Array(32).fill('0x00000000'),
  initSpadState: () => {}, updateCtableForCore: () => {}, renderCorePanes: () => {},
  paintCoreState: () => {}, updateCoresControl: () => {},
});
vm.runInContext(readFileSync(new URL('../ui/static/cores.js', import.meta.url), 'utf8'), context);
const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');
function loadFunction(name) {
  const start = app.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} is implemented`);
  const open = app.indexOf('{', start); let depth = 1, end = open + 1;
  while (depth) { if (app[end] === '{') depth++; if (app[end] === '}') depth--; end++; }
  vm.runInContext(app.slice(start, end), context);
}
for (const name of ['persistCoreSelection', 'applyCoreSelection', 'syncDeviceCoreSelect',
  'syncGpioLoopback', 'showAvailableCores', 'showIepClock', 'buildGpioDirectionControls', 'updateGpioDirections']) loadFunction(name);
context.showAvailableCores(['pru0', 'rtu0', 'pru1']);
assert.equal(context.currentCore, 'pru0');
assert.deepEqual(JSON.parse(JSON.stringify(context.coreSelection)), {
  used: ['pru0', 'pru1'], shown: ['pru0'], focus: 'pru0', pinned: true,
});
assert.deepEqual(get('device-core-select').options.map(option => option.value), ['pru0', 'pru1', 'rtu0']);
assert.equal(context.devicePanelCore, 'pru0');
assert.deepEqual(JSON.parse(storage.get('pru-cores-v1')).used, ['pru0', 'pru1']);
sent.length = 0;

get('iep-clock-select').options = ['configured', '200','225','250','300','333','custom'].map(v => new Element(v));
context.showIepClock({ configured_mhz: 211.25, override_mhz: 333.333,
  external_mhz: 333.333, clock_mhz: 250, core_clock: true });
assert.equal(get('iep-clock-select').value, 'custom');
assert.match(get('iep-clock-select').title, /250 MHz.*core.*211.25.*333.333/);
assert.equal(get('iep-clock-custom').value, '333.333');
context.showIepClock({ configured_mhz: 211.25, override_mhz: null,
  external_mhz: 211.25, clock_mhz: 211.25, core_clock: false });
assert.equal(get('iep-clock-select').value, 'configured');

context.buildGpioDirectionControls();
assert.equal(get('gpio-drive-controls').children.length, 20);
context.updateGpioDirections({ gpo_drive_mask: 3 });
const buttons = get('gpio-drive-controls').children;
assert.equal(buttons[0].textContent, '0: Drive');
assert.equal(buttons[2].textContent, '2: Release');
buttons[2].listeners.click();
assert.equal(sent.at(-1).action, 'set_gpio_drive_mask');
assert.equal(sent.at(-1).pin, 2);
assert.equal(sent.at(-1).mask, undefined);
assert.equal(buttons[0].attributes['aria-pressed'], 'true');

const clockSection = app.slice(app.indexOf('// ---- IEP counter clock selector'), app.indexOf('// ---- end IEP counter clock selector'));
vm.runInContext(clockSection, context);
get('iep-clock-select').value = '250';
get('iep-clock-select').listeners.change();
assert.equal(sent.at(-1).mhz, 250);
get('iep-clock-select').value = 'configured';
get('iep-clock-select').listeners.change();
assert.equal(sent.at(-1).mhz, null);
for (const value of ['', '0', '-2', 'Infinity', 'bad']) {
  get('iep-clock-custom').value = value;
  const count = sent.length;
  get('iep-clock-custom-form').listeners.submit({ preventDefault() {} });
  assert.equal(sent.length, count);
  assert.equal(get('iep-clock-error').hidden, false);
}
get('iep-clock-custom').value = '333.333';
get('iep-clock-custom-form').listeners.submit({ preventDefault() {} });
assert.equal(sent.at(-1).mhz, 333.333);

// GPIO clicks address the core whose state was rendered, even after focus moves.
context.currentCore = 'pru1';
context.updateGpioDirections({ gpo_drive_mask: 1 }, 'pru0');
buttons[1].listeners.click();
assert.equal(sent.at(-1).core, 'pru0');

// Withhold state feedback: send each pin intent without rebuilding a stale mask.
context.updateGpioDirections({ gpo_drive_mask: 0 }, 'pru0');
sent.length = 0;
buttons[0].listeners.click();
buttons[1].listeners.click();
assert.deepEqual(sent.map(message => message.pin), [0, 1]);
assert.ok(sent.every(message => message.mask === undefined));
sent.length = 0;
buttons[0].listeners.click();
buttons[0].listeners.click();
assert.deepEqual(sent.map(message => message.pin), [0, 0]);

// Target reloads rebuild available device choices without stale hidden options.
context.showAvailableCores(['pru0','rtu0','pru1','rtu1']);
assert.deepEqual(get('device-core-select').options.map(option => option.value), ['pru0', 'pru1', 'rtu0', 'rtu1']);
assert.equal(get('device-core-select').options[3].hidden, false);
assert.equal(get('device-core-select').options[3].disabled, false);
context.showAvailableCores(['pru0']);
assert.equal(get('device-core-select-wrap').hidden, true);
assert.deepEqual(get('device-core-select').options.map(option => option.value), ['pru0']);

// Live state refreshes preserve a custom-rate draft while the number field has focus.
get('iep-clock-select').value = 'custom';
get('iep-clock-custom').value = '275.125';
context.document.activeElement = get('iep-clock-custom');
context.showIepClock({ configured_mhz: 200, override_mhz: null,
  external_mhz: 200, clock_mhz: 200, core_clock: false });
assert.equal(get('iep-clock-select').value, 'custom');
assert.equal(get('iep-clock-custom').value, '275.125');
assert.equal(get('iep-clock-custom-form').hidden, false);

// Tab from the custom input to Apply, then refresh: the draft remains submittable.
context.document.activeElement = applyRate;
context.showIepClock({ configured_mhz: 200, override_mhz: null,
  external_mhz: 200, clock_mhz: 200, core_clock: false });
assert.equal(get('iep-clock-select').value, 'custom');
assert.equal(get('iep-clock-custom').value, '275.125');
assert.equal(get('iep-clock-custom-form').hidden, false);
get('iep-clock-custom-form').listeners.submit({ preventDefault() {} });
assert.equal(sent.at(-1).mhz, 275.125);

// Reading the next focused core must replace the strip's previous active mask.
const loopbackButtons = Array.from({length:5},(_,group)=>({dataset:{group:String(group)},
  active:false,classList:{toggle(name,value){loopbackButtons[group].active=value;}}}));
context.document.querySelectorAll = () => loopbackButtons;
loadFunction('syncGpioLoopback');
context.syncGpioLoopback({gpio_loopback_mask:0xF});
assert.deepEqual(loopbackButtons.map(b=>b.active),[true,false,false,false,false]);
context.syncGpioLoopback({gpio_loopback_mask:0xF0});
assert.deepEqual(loopbackButtons.map(b=>b.active),[false,true,false,false,false]);
