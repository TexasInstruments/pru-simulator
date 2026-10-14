import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// A browserless harness for the DOM wiring of ui/static/motor.js: forms,
// buttons and the messages they send. Canvas drawing is not exercised here.
class Element {
  constructor(id = '') {
    this.id = id; this.dataset = {}; this.value = ''; this.textContent = ''; this.hidden = false;
    this.disabled = false; this.children = []; this.attributes = {}; this.listeners = {};
    this.className = '';
  }
  get classList() { return { toggle: () => {}, add: () => {}, remove: () => {} }; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getBoundingClientRect() { return { width: 0, height: 0 }; }
  querySelectorAll(selector) {
    if (selector === '[data-physics]') return physicsInputs;
    if (selector === '[data-readout]') return readoutElements;
    return [];
  }
}
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
const physicsNames = ['resistance_ohm', 'inductance_h', 'flux_linkage_vs', 'pole_pairs',
  'inertia_kg_m2', 'damping_nm_s', 'load_torque_nm', 'dc_bus_v'];
const physicsInputs = physicsNames.map(name => { const e = new Element(`p-${name}`); e.dataset.physics = name; return e; });
const readoutNames = ['requestedSpeed', 'commandedSpeed', 'rotorSpeed', 'commandedAngle', 'rotorAngle',
  'angleError', 'duties', 'alphaBeta', 'phaseCurrents', 'dqCurrents', 'pwmFrequency', 'pruClock',
  'iepClock', 'status', 'plantTime', 'pwmPeriods'];
const readoutElements = readoutNames.map(name => { const e = new Element(`r-${name}`); e.dataset.readout = name; return e; });
const view = get('motor-view');
view.hidden = true;
view.querySelectorAll = Element.prototype.querySelectorAll;
get('motor-error').hidden = true;
get('motor-speed-rpm').value = '400'; get('motor-vd-pu').value = '0';
get('motor-vq-pu').value = '0.25'; get('motor-accel').value = '1000';

const sent = [];
const calls = [];
const document = {
  getElementById: get, activeElement: null,
  createElement: () => new Element(),
  addEventListener: () => {},
};
const context = vm.createContext({
  document, window: { addEventListener: () => {}, requestAnimationFrame: fn => fn() },
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  sendAction: message => sent.push(message),
  startRun: () => { context.running = true; calls.push('startRun'); },
  stopRun: () => { context.running = false; calls.push('stopRun'); },
  openFileAsTab: (name) => calls.push(`open:${name}`),
  fetch: async url => ({ ok: true, status: 200, text: async () => `; ${url}` }),
  currentCore: 'pru0', multiCoreMode: false, Date,
});
context.running = false;
context.globalThis = context;
const load = file => vm.runInContext(readFileSync(new URL(`../ui/static/${file}`, import.meta.url), 'utf8'), context);
load('motor_helpers.js');
let currentHistory;
const createHistory = context.MotorHelpers.createHistory;
context.MotorHelpers.createHistory = (...args) => (currentHistory = createHistory(...args));
load('motor.js');
const M = context.window.MotorControl;
assert.ok(M, 'motor.js registers window.MotorControl');
const click = id => get(id).listeners.click();
const submit = id => get(id).listeners.submit({ preventDefault() {} });
const drain = () => sent.splice(0).map(message => JSON.parse(JSON.stringify(message)));
const idle = () => new Promise(resolve => setTimeout(resolve, 0));

const stateWith = (extra = {}) => ({
  type: 'state', core: 'pru0', labels: { control_update: 3 }, instructions: [{ addr: 0 }],
  io: {
    device_bus: { devices: [{ model: 'pmsm', name: 'foc_motor', core: 'pru0', sample_index: 3,
      cycles: 10, rotor_angle_rad: 0.2, commanded_angle_rad: 0.5, angle_error_rad: 0.3,
      parameters: { pole_pairs: 4, dc_bus_v: 48, resistance_ohm: 0.5, inductance_h: 0.001,
        flux_linkage_vs: 0.05, inertia_kg_m2: 0.0005, damping_nm_s: 0.003, load_torque_nm: 0 },
      duty_cycles: [0.5, 0.6, 0.4], alpha_beta_v: [0, 1], phase_currents_a: [1, -1, 0],
      commanded_speed_rpm: 10, rotor_speed_rpm: 9, id_a: 0, iq_a: 1, pwm_frequency_hz: 16000,
      time_s: 0.001, pwm_periods: 5 }] },
    foc_config: { speed_ref_q28: 7158279, status: 1, enable: 1, requested_generation: 1, ack_generation: 1,
      vd_ref_q15: 0, vq_ref_q15: 8192, ramp_rate_q28: 1118 },
    foc_clocks: { pru_hz: 250e6, iep_hz: 200e6 }, sd: { input_routes: [3, 4, null] },
    ...extra },
});

// Nothing attached: only Load is possible; Apply/Start stay disabled.
assert.equal(get('motor-start').disabled, true);
assert.equal(get('motor-apply').disabled, true);
assert.match(get('motor-status').textContent, /not loaded/i);

// Load firmware attaches, loads the source and stages the references (disabled).
click('motor-load');
await idle();
const loadMessages = drain();
assert.deepEqual(loadMessages.map(message => message.action), ['device_attach', 'load', 'foc_set_reference']);
assert.ok(calls.includes('open:foc_open_loop.asm'));
assert.equal(loadMessages[0].profile, 'foc_motor');
assert.equal(loadMessages[1].filename, 'foc_open_loop.asm');
assert.deepEqual(loadMessages[2], { core: 'pru0', action: 'foc_set_reference', enable: false,
  speed_rpm: 400, vd_pu: 0, vq_pu: 0.25, accel_rpm_s: 1000 });
M.onLoadError(['line 3: bad']);
assert.match(get('motor-error').textContent, /line 3/);
assert.equal(get('motor-error').hidden, false);

// A state with the motor and firmware enables the controls and fills readouts.
M.onState(stateWith());
assert.equal(get('motor-start').disabled, false);
assert.equal(get('motor-apply').disabled, false);
assert.equal(get('motor-stop').disabled, true);
assert.equal(readoutElements.find(e => e.dataset.readout === 'rotorSpeed').textContent, '9.0 rpm');
assert.equal(readoutElements.find(e => e.dataset.readout === 'pruClock').textContent, '250.0 MHz');
assert.equal(get('motor-route-0').value, '3');
const requested = drain();
assert.deepEqual(requested, [{ core: 'pru0', action: 'foc_state', since: 0 }], 'new samples are requested by index');
M.onState(stateWith());
assert.deepEqual(drain(), [], 'only one sample request is outstanding');
M.onSamples({ type: 'foc_samples', fields: ['index', 'time_s', 'ia'], next_index: 2, dropped: 0,
  samples: [[0, 0, 1], [1, 0.0001, 2]] });

// Invalid reference input is rejected locally; valid input is sent as-is.
get('motor-vq-pu').value = '1.5';
submit('motor-reference-form');
assert.deepEqual(drain(), []);
assert.match(get('motor-error').textContent, /Vq must be at most/);
get('motor-vq-pu').value = '0.3';
submit('motor-reference-form');
assert.deepEqual(drain(), [{ core: 'pru0', action: 'foc_set_reference',
  speed_rpm: 400, vd_pu: 0, vq_pu: 0.3, accel_rpm_s: 1000 }]);
assert.equal(get('motor-error').hidden, true, 'a new action clears the previous error');

// Start enables and uses the normal run loop; Stop stops it and disables.
click('motor-start');
assert.deepEqual(drain().map(message => [message.action, message.enable]), [['foc_set_reference', true]]);
assert.deepEqual(calls.slice(-1), ['startRun']);
M.onState(stateWith());
assert.equal(get('motor-stop').disabled, false);
assert.equal(get('motor-start').disabled, true);
click('motor-stop');
assert.deepEqual(calls.slice(-1), ['stopRun']);
assert.deepEqual(drain(), [{ core: 'pru0', action: 'foc_enable', enable: false }]);

// Starting on another core is refused without sending anything.
context.currentCore = 'rtu0';
click('motor-start');
assert.deepEqual(drain(), []);
assert.match(get('motor-error').textContent, /PRU0/);
context.currentCore = 'pru0';

// Physics: bad values never leave the page; defaults restore the form.
physicsInputs.find(e => e.dataset.physics === 'resistance_ohm').value = '0';
submit('motor-physics-form');
assert.deepEqual(drain(), []);
assert.match(get('motor-error').textContent, /Rs must be greater than 0/);
click('motor-physics-defaults');
const defaults = drain()[0];
assert.equal(defaults.action, 'foc_set_motor');
assert.deepEqual(Object.keys(defaults.parameters).sort(), physicsNames.slice().sort());
assert.equal(physicsInputs.find(e => e.dataset.physics === 'dc_bus_v').value, '48');
physicsInputs.find(e => e.dataset.physics === 'load_torque_nm').value = '0.2';
submit('motor-physics-form');
assert.equal(drain()[0].parameters.load_torque_nm, 0.2);

// A backend rejection is shown and the form returns to the plant's real values.
M.onError(['load_torque_nm must be a finite number']);
assert.match(get('motor-error').textContent, /finite number/);
M.onState(stateWith());
assert.equal(physicsInputs.find(e => e.dataset.physics === 'load_torque_nm').value, '0');

// Routes and detach.
get('motor-route-1').value = '9';
click('motor-routes-apply');
assert.deepEqual(drain().at(-1), { core: 'pru0', action: 'foc_apply', config: {}, routes: [3, 9] });
click('motor-detach');
assert.deepEqual(drain().at(-1), { core: 'pru0', action: 'device_detach', name: 'foc_motor' });
M.onState({ type: 'state', core: 'pru0', labels: {}, instructions: [], io: {} });
assert.equal(get('motor-start').disabled, true, 'controls disable once the motor is gone');

console.log('Motor control view sends validated actions and follows backend state');

// A clock change resynchronizes clean staged fields using actual fixed-tick cadence.
for (const id of ['motor-speed-rpm', 'motor-accel', 'motor-vd-pu', 'motor-vq-pu']) delete get(id).dataset.dirty;
M.onState(stateWith({ foc_clocks: { pru_hz: 250e6, iep_hz: 300e6 } }));
assert.equal(get('motor-speed-rpm').value, '600');
assert.equal(get('motor-accel').value, '2249');
assert.equal(readoutElements.find(e => e.dataset.readout === 'requestedSpeed').textContent, '600.0 rpm');
get('motor-speed-rpm').value = '77';
get('motor-speed-rpm').listeners.input();
M.onState(stateWith({ foc_clocks: { pru_hz: 250e6, iep_hz: 333.333e6 } }));
assert.equal(get('motor-speed-rpm').value, '77', 'clock updates preserve user edits');

// Reset state clears plot rows immediately, even while sample requests are throttled.
M.onConnect();
M.onState(stateWith());
drain();
M.onSamples({ type: 'foc_samples', fields: ['index', 'time_s', 'ia'], next_index: 3,
  dropped: 0, samples: [[0, 0.0001, 1], [1, 0.0002, 2], [2, 0.0003, 3]] });
assert.equal(currentHistory.rows.length, 3);
const resetState = stateWith();
resetState.io.device_bus.devices[0].sample_index = 0;
resetState.io.device_bus.devices[0].cycles = 0;
M.onState(resetState);
assert.equal(currentHistory.rows.length, 0, 'reset state must clear plots before a sample reply');
assert.equal(currentHistory.nextIndex, 0);
M.onSamples({ type: 'foc_samples', fields: ['index', 'time_s', 'ia'], next_index: 2,
  dropped: 0, samples: [[0, 0.0001, 4], [1, 0.0002, 5]] });
assert.deepEqual(Array.from(currentHistory.rows, row => Array.from(row)), [[0, 0.0001, 4], [1, 0.0002, 5]]);
