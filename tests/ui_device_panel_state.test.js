import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const appPath = fileURLToPath(new URL('../ui/static/app.js', import.meta.url));
const app = readFileSync(appPath, 'utf8');
const start = app.indexOf('// ---- TCA9538 I2C IO expander');
const end = app.indexOf('// ---- Signal graph', start);
assert.ok(start >= 0 && end > start, 'the device panel code should be present in app.js');

// Just enough DOM for the device list: elements with children, dataset and
// the few selectors the row builders use.
class Node {
  constructor() { this.children = []; this.dataset = {}; this.attributes = {}; this.className = ''; this.options = []; this.textContent = ''; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  replaceChildren() { this.children = []; }
  insertBefore(child, before) {
    this.children = this.children.filter(c => c !== child);
    const at = before ? this.children.indexOf(before) : -1;
    this.children.splice(at < 0 ? this.children.length : at, 0, child);
    child.parent = this;
  }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener() {}
  querySelector(selector) {
    const role = /\[data-role="([\w-]+)"\]/.exec(selector);
    const cls = /^\.([\w-]+)$/.exec(selector);
    const walk = node => node.children.flatMap(child => [child, ...walk(child)]);
    return walk(this).find(n => (role && n.dataset.role === role[1]) ||
      (cls && n.className.split(' ').includes(cls[1]))) || null;
  }
}
const nodes = new Map();
for (const id of ['device-runtime-panel', 'ssi-device-list', 'tca-empty-state', 'tca-state', 'tca-leds',
  'tca-registers', 'tca-log', 'tca-detach-button']) nodes.set(id, new Node());

const document = {
  activeElement: null,
  getElementById: id => nodes.get(id) || null,
  createElement: () => new Node(),
};
const harness = new Function('document', `
  let currentCore = 'pru0';
  let devicePanelCore = 'pru0';
  let multiCoreMode = false;
  ${app.slice(start, end)}
  return { updateDevicePanel, ssiFieldMax, syncSsiSetter, parseI2CAddress };
`)(document);

// The FOC motor is controlled from the Motor control view; the device panel
// must tolerate a motor in the bus state without touching any FOC element.
harness.updateDevicePanel({ device_bus: { devices: [
  { model: 'pmsm', name: 'foc_motor', core: 'pru0', phase_currents_a: [] }] } }, 'pru0');

const list = nodes.get('ssi-device-list');
assert.deepEqual(list.children.map(row => [row.dataset.deviceName, row.dataset.kind]), [['foc_motor', 'generic']],
  'the list shows every device on the core, not only SSI encoders');
assert.deepEqual(list.children[0].querySelector('.runtime-device-actions').children.map(b => b.dataset.deviceAction),
  ['open-motor', 'detach']);

// A TCA9538 gets a row and fills its card (levels, registers, detach target).
const tca = { model: 'tca9538', name: 'io', core: 'pru0', address: 0x23, scl_pin: 0, sda_pin: 1,
  output_reg: 0x05, polarity_reg: 0, config_reg: 0xF0, driven_mask: 0x0F, levels: [1, 0, 1, 0, 0, 0, 0, 0],
  protocol_state: 'IDLE', events: 2, faults: 1, last_transaction: { address: 0x23, reg: 1, data: 5, ack: true } };
harness.updateDevicePanel({ device_bus: { devices: [tca], faults: ['io: SDA changed while SCL high'] } }, 'pru0');
assert.deepEqual(list.children.map(row => row.dataset.deviceName), ['io'], 'the motor row left, the TCA row arrived');
assert.match(list.children[0].querySelector('[data-role="detail"]').textContent, /address 0x23.*SCL pin 0.*SDA pin 1/);
assert.match(list.children[0].querySelector('[data-role="fault"]').textContent, /SDA changed while SCL high/);
assert.match(nodes.get('tca-empty-state').textContent, /io attached to pru0 at 0x23/);
assert.equal(nodes.get('tca-state').hidden, false);
assert.deepEqual(nodes.get('tca-leds').children.map(led => led.textContent), ['1', '0', '1', '0', '0', '0', '0', '0'],
  'levels are written as digits, not colour only');
assert.match(nodes.get('tca-registers').textContent, /OUTPUT=0x05.*CONFIG=0xF0.*1 fault/s);
assert.equal(nodes.get('tca-detach-button').dataset.name, 'io');
harness.updateDevicePanel({ device_bus: { devices: [] } }, 'pru0');
assert.equal(list.children.length, 0);
assert.equal(nodes.get('tca-state').hidden, true);
assert.match(nodes.get('tca-empty-state').textContent, /No TCA9538 attached to pru0/);

assert.equal(harness.parseI2CAddress('0x23'), 0x23);
assert.equal(harness.parseI2CAddress(' 35 '), 35);
assert.equal(harness.parseI2CAddress('0x7F'), 0x7F);
for (const bad of ['', '0x80', '128', 'abc', '0x', '-1', '0x123']) assert.equal(harness.parseI2CAddress(bad), null, bad);

assert.equal(harness.ssiFieldMax(3), 7, 'SSI field limit follows the field width');
assert.equal(harness.ssiFieldMax(64), Number.MAX_SAFE_INTEGER, 'wide SSI fields stay exact in JSON');

const setter = { dataset: {} };
harness.syncSsiSetter(setter, 5, 7);
assert.deepEqual([setter.value, setter.max], ['5', '7'], 'SSI setter follows backend value and limit');
setter.dataset.dirty = 'true';
harness.syncSsiSetter(setter, 6, 7);
assert.equal(setter.value, '5', 'a dirty SSI setter keeps the draft');
setter.dataset.pending = '6';
harness.syncSsiSetter(setter, 6, 7);
assert.equal(setter.value, '6', 'an acknowledged SSI setter follows the backend again');
assert.equal(setter.dataset.dirty, undefined);

console.log('Device panel ignores the FOC motor and keeps SSI setters in sync with the backend');
