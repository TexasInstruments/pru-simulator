import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor() { this.listeners = {}; this.dataset = {}; this.value = ''; this.hidden = false; this.options = []; }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  click() { this.listeners.click?.({ currentTarget: this, target: this }); }
  dispatchEvent(event) { this.listeners[event.type]?.({ ...event, currentTarget: this, target: this }); }
  setAttribute(name, value) { this[name] = value; }
  checkValidity() { return !this.validityMessage && this.value !== '' && (this.min === undefined || this.valueAsNumber >= Number(this.min)) && (this.max === undefined || this.valueAsNumber <= Number(this.max)); }
  reportValidity() { return this.checkValidity(); }
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
  availableCores:['pru0','pru1','rtu0','rtu1'],coreSelection:{used:['pru1','rtu0'],shown:['pru1'],focus:'pru1',pinned:false},
  applyCoreSelection(next) {context.coreSelection=next;context.currentCore=next.focus;context.multiCoreMode=next.used.length>1;},
  applyMCCores() {}, syncDeviceCoreSelect() {}, selectEditorCore(core) { context.editorCore = core; },
  openFileAsTab: (path, source, core) => files.push([path, source, core]),
  fetch: async path => ({ ok: true, text: async () => path }),
});
get('mc-partner-select').addEventListener('change', () => { context.mcPartner = get('mc-partner-select').value; });
let coreChanges = 0;
get('core-select').addEventListener('change', () => {
  coreChanges++;
  context.currentCore = get('core-select').value;
});
vm.runInContext(readFileSync(new URL('../ui/static/cores.js', import.meta.url), 'utf8'), context);
const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
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
for (const name of ['deviceNumberValue', 'ssiFieldMax', 'readSsiFrameConfig']) {
  const offset = app.indexOf(`function ${name}(`);
  assert.notEqual(offset, -1, `${name} is available to SSI setup`);
  const finish = app.indexOf('\n}\n', offset) + 3;
  vm.runInContext(app.slice(offset, finish), context);
}
const defaults = {'ssi-position':'703710', 'ssi-resolution':'20', 'ssi-error-bits':'0',
  'ssi-error-value':'0', 'ssi-encoding':'binary', 'ssi-preset':''};
for (const [id, value] of Object.entries(defaults)) get(id).value = value;
for (const id of ['ssi-position', 'ssi-error-bits', 'ssi-error-value']) get(id).min = '0';
get('ssi-resolution').min = '1'; get('ssi-resolution').max = '64';
vm.runInContext(app.slice(start, end), context);
for (const button of buttons) {
  button.click();
  assert.deepEqual(JSON.parse(JSON.stringify(sent.at(-1))), {
    action: 'scenario_load', name: button.dataset.scenario,
    config: {preset:null, position:703710, resolution:20, position_bits:20,
      error_bits:0, error_value:0, encoding:'binary'},
  }, 'both setup buttons use the chosen 20-bit frame');
}
const beforeBadSetup = sent.length;
get('ssi-resolution').value = '12';
single.click();
assert.equal(sent.length, beforeBadSetup, 'invalid position must not replace the running setup');
get('ssi-position').value = '2748';
single.click();
assert.equal(sent.length, beforeBadSetup + 1, 'corrected position clears stale custom validation');
get('ssi-error-bits').value = '12';
multi.click();
assert.equal(sent.length, beforeBadSetup + 1, 'frame must leave a position bit');
get('ssi-error-bits').value = '0';
get('ssi-preset').value = 'RM08_12BIT_4MHZ';
multi.click();
assert.equal(sent.at(-1).config.preset, 'RM08_12BIT_4MHZ', 'chosen preset is included');
const attachStart = app.indexOf("document.getElementById('ssi-attach-button')?.addEventListener");
const attachEnd = app.indexOf("document.getElementById('tca-attach-button')", attachStart);
context.sendDeviceAction = action => sent.push(action);
vm.runInContext(app.slice(attachStart, attachEnd), context);
get('ssi-device-name').value = 'my_encoder';
get('ssi-clock-pin').value = '3'; get('ssi-data-pin').value = '8';
get('ssi-attach-button').click();
assert.equal(sent.at(-1).action, 'device_attach', 'advanced attach never replaces reader code');
assert.equal(sent.at(-1).core, 'pru1');
assert.equal(sent.at(-1).config.name, 'my_encoder');
assert.equal(sent.at(-1).config.clock_pin, 3);
assert.equal(sent.at(-1).config.resolution, 12);



assert.equal(typeof context.updateSsiDemo, 'function', 'SSI demo readouts must be rendered');
const sample = { reader_core: 'pru0', encoder_core: 'pru1', frame_bits: 12, error_bits: 0,
  requested_position: 2748, position_max: 4095, position: null, error: null, raw_frame: null,
  frame_count: 0, coherent: true, reader_status: 0, emulator_status: 0, timer_enabled: true };
context.updateSsiDemo(sample);
assert.match(get('ssi-demo-wiring').textContent, /PRU0.*PRU1.*12-bit.*16/);
context.updateSsiDemo({...sample, frame_bits:20, position_max:1048575});
assert.match(get('ssi-demo-wiring').textContent, /20-bit/);
assert.equal(get('ssi-demo-position').max, '1048575');
context.updateSsiDemo(sample);
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
context.updateSsiDemo({...sample, frame_bits:64, position_max:2**64-1});
assert.equal(get('ssi-demo-position').max, String(Number.MAX_SAFE_INTEGER), 'wide frames keep an exact position input limit');
get('ssi-demo-position').value = '9007199254740993';
const beforeUnsafe = sent.length;
get('ssi-demo-set-position').click();
assert.equal(sent.length, beforeUnsafe, 'rounded unsafe positions are never sent');


const scenario = { name: multi.dataset.scenario, lead: 'pru0', cores: ['pru0', 'pru1'], multicore: true,
  firmware: { pru0: 'ssi_generic_reader/ssi_generic_reader.asm', pru1: 'ssi_generic_emulator.asm' },
  ui: { view: 'io', capture_stride: 2, graph_window: 4096 } };
context.populateScenarios([scenario]);
assert.equal(single.disabled, true);
assert.equal(multi.disabled, false);
await context.applyScenario(scenario);
assert.deepEqual(Array.from(context.coreSelection.used), ['pru0','pru1'], 'only the example cores run');
assert.deepEqual(Array.from(context.coreSelection.shown), ['pru0','pru1']);
assert.equal(context.currentCore, 'pru0');
assert.equal(context.devicePanelCore, 'pru0');
assert.match(view, /ioTab|io-tab/);
assert.equal(context.editorCore, 'pru0');
assert.deepEqual(files.toSorted((a, b) => a[2].localeCompare(b[2])), [
  [scenario.firmware.pru0, `/source/${scenario.firmware.pru0}`, 'pru0'],
  [scenario.firmware.pru1, `/source/${scenario.firmware.pru1}`, 'pru1'],
], 'each core receives its own SSI firmware');
context.currentCore = 'pru1';
context.multiCoreMode = false;
const changesBefore = coreChanges;
await context.applyScenario({ ...scenario, name: single.dataset.scenario,
  cores: ['pru0'], multicore: false, firmware: { pru0: scenario.firmware.pru0 } });
assert.deepEqual(Array.from(context.coreSelection.used), ['pru0']);
assert.equal(context.coreSelection.focus, 'pru0');
console.log('SSI demos render received frames, preserve drafts and select exactly the required cores');
