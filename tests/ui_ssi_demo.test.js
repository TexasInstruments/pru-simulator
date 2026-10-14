import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor() { this.listeners = {}; this.dataset = {}; this.value = ''; this.hidden = false; this.options = []; }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  click() { this.listeners.click?.({ currentTarget: this, target: this }); }
  dispatchEvent(event) { this.listeners[event.type]?.({ ...event, currentTarget: this, target: this }); }
  setAttribute(name, value) { this[name] = value; }
  reportValidity() { return !this.validityMessage; }
  setCustomValidity(value) { this.validityMessage = value; }
  get valueAsNumber() { return this.value === '' ? NaN : Number(this.value); }
}
const nodes = new Map();
const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
const single = new Element(), multi = new Element();
single.dataset.scenario = 'SSI encoder (single core)';
multi.dataset.scenario = 'SSI multi-core (emulator + reader)';
const buttons = [single, multi];
const sent = [], files = [];
let view = '';
const document = { getElementById: get, activeElement: null,
  querySelectorAll: () => buttons,
  querySelector: selector => ({ click() { view = selector; } }) };
const context = vm.createContext({ document, window: {}, Event: class { constructor(type) { this.type = type; } },
  sendAction: action => sent.push(action), stopRun() {}, stopSim() {}, clearDeviceError() {},
  currentCore: 'pru1', devicePanelCore: 'pru1', multiCoreMode: true, mcPartner: 'rtu0', mcExtras: ['rtu0'],
  coreSelect: get('core-select'), mcPartnerSelect: get('mc-partner-select'),
  memAddrInput: get('mem-addr'), memLenInput: get('mem-len'), memAutoRefreshBox: get('mem-auto'),
  graphResizeWindow() {}, graphSetRecording() {}, drawGraph() {}, refreshMemory() {},
  toggleMultiCore() { context.multiCoreMode = !context.multiCoreMode; },
  applyMCCores() {}, syncDeviceCoreSelect() {},
  openFileAsTab: (path, source) => files.push([path, source]),
  fetch: async path => ({ ok: true, text: async () => path }),
});
get('mc-partner-select').addEventListener('change', () => { context.mcPartner = get('mc-partner-select').value; });
let coreChanges = 0;
get('core-select').addEventListener('change', () => {
  coreChanges++;
  context.currentCore = get('core-select').value;
});
const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');
const setterStart = app.indexOf('function syncSsiSetter(');
const setterEnd = app.indexOf('\n}\n', setterStart) + 3;
vm.runInContext(app.slice(setterStart, setterEnd), context);
const start = app.indexOf('// ---- Examples (one-click scenarios)');
const end = app.indexOf('function buildMCRegTable', start);
const html = readFileSync(new URL('../ui/static/index.html', import.meta.url), 'utf8');
const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map(match => match[1]));
for (const match of app.slice(start, end).matchAll(/getElementById\('([^']+)'\)/g)) {
  assert.ok(ids.has(match[1]), `the dashboard includes ${match[1]}`);
}
vm.runInContext(app.slice(start, end), context);

assert.equal(typeof context.updateSsiDemo, 'function', 'SSI demo readouts must be rendered');
const sample = { reader_core: 'pru0', encoder_core: 'pru1', frame_bits: 12, error_bits: 0,
  requested_position: 2748, position_max: 4095, position: null, error: null, raw_frame: null,
  frame_count: 0, coherent: true, reader_status: 0, emulator_status: 0, timer_enabled: true };
context.updateSsiDemo(sample);
assert.match(get('ssi-demo-wiring').textContent, /PRU0.*PRU1.*16/);
assert.match(get('ssi-demo-status').textContent, /Run/);
assert.equal(get('ssi-demo-position').value, '2748');
context.updateSsiDemo({ ...sample, position: 2748, error: 0, raw_frame: '0xABC', frame_count: 2 });
assert.equal(get('ssi-received-position').textContent, '2748 (0xABC)');
assert.equal(get('ssi-received-error').textContent, 'Not present');
assert.equal(get('ssi-received-count').textContent, '2');
assert.equal(get('ssi-firmware-status').textContent, 'Reader 0 / encoder 0');
get('ssi-demo-position').value = '1234';
get('ssi-demo-position').dispatchEvent({ type: 'input' });
context.updateSsiDemo({ ...sample, position: 2748, frame_count: 3 });
assert.equal(get('ssi-demo-position').value, '1234', 'telemetry preserves the draft');
get('ssi-demo-set-position').click();
assert.equal(sent.at(-1).action, 'ssi_demo_position');
assert.equal(sent.at(-1).position, 1234);
context.updateSsiDemo({ ...sample, requested_position: 1234, position: 1234, raw_frame: '0x4D2', frame_count: 4 });
assert.equal(get('ssi-demo-position').dataset.dirty, undefined);
const beforeInvalid = sent.length;
get('ssi-demo-position').value = '4096';
get('ssi-demo-set-position').click();
assert.equal(sent.length, beforeInvalid, 'an out-of-range position is not sent');
assert.match(get('ssi-demo-position').validityMessage, /4095/);
context.updateSsiDemo({ ...sample, coherent: false, frame_count: null });
assert.match(get('ssi-demo-status').textContent, /Publishing/);
assert.equal(get('ssi-received-position').textContent, '—');
context.updateSsiDemo(null);
assert.equal(get('ssi-demo-result').hidden, true);
assert.equal(get('ssi-demo-set-position').disabled, true);

const scenario = { name: multi.dataset.scenario, lead: 'pru0', cores: ['pru0', 'pru1'], multicore: true,
  firmware: { pru0: 'ssi_generic_reader/ssi_generic_reader.asm', pru1: 'ssi_generic_emulator.asm' },
  ui: { view: 'io', capture_stride: 2, graph_window: 4096 } };
context.populateScenarios([scenario]);
assert.equal(single.disabled, true);
assert.equal(multi.disabled, false);
await context.applyScenario(scenario);
assert.deepEqual(Array.from(context.mcExtras), [], 'old extra cores must not run in the two-core demo');
assert.equal(context.mcPartner, 'pru1');
assert.equal(context.currentCore, 'pru0');
assert.equal(context.devicePanelCore, 'pru0');
assert.match(view, /ioTab|io-tab/);
assert.equal(files.at(-1)[0], scenario.firmware.pru0);
context.currentCore = 'pru1';
context.multiCoreMode = false;
const changesBefore = coreChanges;
await context.applyScenario({ ...scenario, name: single.dataset.scenario,
  cores: ['pru0'], multicore: false, firmware: { pru0: scenario.firmware.pru0 } });
assert.equal(coreChanges, changesBefore + 1, 'single-core demo runs normal core-switch housekeeping');
console.log('SSI demos render received frames, preserve drafts and select exactly the required cores');
