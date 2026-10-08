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
  appendChild(child) { this.children.push(child); }
  contains(element) { return element === this || this.children.some(child => child.contains(element)); }
  replaceChildren(...children) { this.options = children; }
}
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
const selects = ['core-select', 'mc-partner-select', 'mc-load-core', 'device-core-select'];
for (const id of selects) get(id).options = ['pru0','rtu0','pru1','rtu1'].map(core => new Element(core));
const extras = ['rtu0','pru1','rtu1'].map(core => { const e = new Element(); e.dataset.mcExtra = core; return e; });
const applyRate = new Element();
get('iep-clock-custom-form').appendChild(get('iep-clock-custom'));
get('iep-clock-custom-form').appendChild(applyRate);
const sent = [];
const context = vm.createContext({ document: { getElementById: get, createElement: () => new Element(),
  querySelectorAll: () => extras, activeElement: null },
  sendAction: action => sent.push(action), currentCore: 'rtu1', mcPartner: 'rtu1',
  mcExtras: ['pru1', 'rtu1'], availableCores: ['pru0','rtu0','pru1','rtu1'],
  multiCoreMode: false, devicePanelCore: 'rtu1',
  coreSelect: get('core-select'), mcPartnerSelect: get('mc-partner-select'), mcLoadCore: get('mc-load-core'),
  deviceCoreSelect: get('device-core-select'), iepClockSelect: get('iep-clock-select'),
  stopRun: () => {}, stopSim: () => {}, syncDeviceCoreSelect: () => {},
  applyMCPartnerLabels: () => {}, applyMCCores: () => {},
});
const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');
function loadFunction(name) {
  const start = app.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} is implemented`);
  const open = app.indexOf('{', start); let depth = 1, end = open + 1;
  while (depth) { if (app[end] === '{') depth++; if (app[end] === '}') depth--; end++; }
  vm.runInContext(app.slice(start, end), context);
}
for (const name of ['showAvailableCores', 'showIepClock', 'buildGpioDirectionControls', 'updateGpioDirections']) loadFunction(name);
context.showAvailableCores(['pru0', 'rtu0', 'pru1']);
assert.equal(context.currentCore, 'pru0');
assert.equal(context.mcPartner, 'rtu0');
assert.deepEqual(Array.from(context.mcExtras), ['pru1']);
for (const id of selects) assert.equal(get(id).options.find(o => o.value === 'rtu1').hidden, true);
assert.equal(extras[2].hidden, true);

get('iep-clock-select').options = ['configured', '200','225','250','300','333','custom'].map(v => new Element(v));
context.showIepClock({ configured_mhz: 211.25, override_mhz: 333.333,
  external_mhz: 333.333, clock_mhz: 250, core_clock: true });
assert.equal(get('iep-clock-select').value, 'custom');
assert.match(get('iep-clock-status').textContent, /250 MHz.*core.*211.25.*333.333/);
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
assert.equal(sent.at(-1).mask, 7);
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

// In multi-core view GPIO shows PRU0 even if single-core selection was PRU1.
context.currentCore = 'pru1';
context.updateGpioDirections({ gpo_drive_mask: 1 }, 'pru0');
buttons[1].listeners.click();
assert.equal(sent.at(-1).core, 'pru0');

// A reloaded target must unhide the device partner option after replacing RTU1.
context.deviceCoreSelectWrap = get('device-core-select-wrap');
loadFunction('syncDeviceCoreSelect');
context.availableCores = ['pru0','rtu0','pru1','rtu1'];
context.mcPartner = 'rtu1';
get('device-core-select').options[1].value = 'rtu1';
context.showAvailableCores(['pru0','rtu0','pru1']);
assert.equal(get('device-core-select').options[1].value, 'rtu0');
assert.equal(get('device-core-select').options[1].hidden, false);
assert.equal(get('device-core-select').options[1].disabled, false);

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
