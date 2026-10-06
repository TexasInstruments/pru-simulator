import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const appPath = fileURLToPath(new URL('../ui/static/app.js', import.meta.url));
const app = readFileSync(appPath, 'utf8');
const start = app.indexOf('function updateDevicePanel(');
const end = app.indexOf('// ---- Signal graph', start);
assert.ok(start >= 0 && end > start, 'updateDevicePanel should be present in app.js');

const nodes = new Map();
for (const id of ['device-runtime-panel']) nodes.set(id, {});

const document = {
  activeElement: null,
  getElementById: id => nodes.get(id) || null,
  createElement: () => ({ dataset: {}, options: [], appendChild() {}, addEventListener() {} }),
};
const harness = new Function('document', `
  let currentCore = 'pru0';
  let devicePanelCore = 'pru0';
  let multiCoreMode = false;
  ${app.slice(start, end)}
  return { updateDevicePanel, ssiFieldMax, syncSsiSetter };
`)(document);

// The FOC motor is controlled from the Motor control view; the device panel
// must tolerate a motor in the bus state without touching any FOC element.
harness.updateDevicePanel({ device_bus: { devices: [
  { model: 'pmsm', name: 'foc_motor', core: 'pru0', phase_currents_a: [] }] } }, 'pru0');

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
