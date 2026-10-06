import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const context = vm.createContext({});
vm.runInContext(readFileSync(new URL('../ui/static/motor_helpers.js', import.meta.url), 'utf8'), context);
const H = context.MotorHelpers;
const plain = value => JSON.parse(JSON.stringify(value));
const ids = h => Array.from(h.rows, row => row[0]);

// Reference form: valid, then every invalid class is reported and sends nothing.
assert.deepEqual(plain(H.readReferenceForm({ speedRpm: '400', vdPu: '0', vqPu: '0.25', accelRpmS: '1000' })),
  { message: { speed_rpm: 400, vd_pu: 0, vq_pu: 0.25, accel_rpm_s: 1000 } });
assert.deepEqual(plain(H.readReferenceForm({ speedRpm: -250.5, vdPu: -0.1, vqPu: 0.3, accelRpmS: 0 })).message,
  { speed_rpm: -250.5, vd_pu: -0.1, vq_pu: 0.3, accel_rpm_s: 0 });
for (const [form, pattern] of [
  [{ speedRpm: '', vdPu: 0, vqPu: 0, accelRpmS: 0 }, /Speed must be a number/],
  [{ speedRpm: 'abc', vdPu: 0, vqPu: 0, accelRpmS: 0 }, /Speed must be a number/],
  [{ speedRpm: 20000, vdPu: 0, vqPu: 0, accelRpmS: 0 }, /Speed must be at most 15000/],
  [{ speedRpm: 0, vdPu: 1.5, vqPu: 0, accelRpmS: 0 }, /Vd must be at most/],
  [{ speedRpm: 0, vdPu: 0, vqPu: -1.2, accelRpmS: 0 }, /Vq must be at least -1/],
  [{ speedRpm: 0, vdPu: 0, vqPu: 0, accelRpmS: -1 }, /Acceleration must be at least 0/],
  [{ speedRpm: Infinity, vdPu: 0, vqPu: 0, accelRpmS: 0 }, /Speed must be a number/],
]) {
  const result = H.readReferenceForm(form);
  assert.match(result.error, pattern);
  assert.equal(result.message, undefined);
}

// Physics form: defaults round-trip, each rule rejects its own bad value.
const formOf = overrides => ({ ...H.PHYSICS_DEFAULTS, ...overrides });
assert.deepEqual(plain(H.readPhysicsForm(formOf({})).parameters), plain(H.PHYSICS_DEFAULTS));
assert.equal(H.readPhysicsForm(formOf({ flux_linkage_vs: '0' })).parameters.flux_linkage_vs, 0, 'zero flux is a valid RL load');
assert.equal(H.readPhysicsForm(formOf({ load_torque_nm: '-0.2' })).parameters.load_torque_nm, -0.2);
for (const [name, value, pattern] of [
  ['resistance_ohm', '0', /Rs must be greater than 0/],
  ['inductance_h', '-1', /Ls must be greater than 0/],
  ['flux_linkage_vs', '-0.1', /Flux linkage must be at least 0/],
  ['pole_pairs', '2.5', /Pole pairs must be a whole number/],
  ['pole_pairs', '0', /Pole pairs must be at least 1/],
  ['pole_pairs', '65', /Pole pairs must be at most 64/],
  ['inertia_kg_m2', '', /Inertia must be a number/],
  ['dc_bus_v', 'x', /Vdc must be a number/],
]) {
  const result = H.readPhysicsForm(formOf({ [name]: value }));
  assert.match(result.error, pattern);
  assert.equal(result.parameters, undefined);
}

// Samples since index: incremental, bounded, tolerant of resets and replays.
const fields = ['index', 'time_s', 'ia'];
const batch = (from, to, next = to) => ({
  fields, next_index: next, dropped: 0,
  samples: Array.from({ length: to - from }, (_, i) => [from + i, (from + i) / 16000, from + i]),
});
let history = H.createHistory(5);
H.appendSamples(history, batch(0, 3));
assert.equal(history.nextIndex, 3);
H.appendSamples(history, batch(3, 6));
assert.deepEqual(ids(history), [1, 2, 3, 4, 5], 'history stays bounded to its capacity');
H.appendSamples(history, batch(4, 8));
assert.deepEqual(ids(history), [3, 4, 5, 6, 7], 'overlapping samples are not duplicated');
assert.deepEqual(plain(H.column(history, 'ia')), [3, 4, 5, 6, 7]);
assert.deepEqual(plain(H.lastRows(history, 2)).map(row => row[0]), [6, 7]);
H.appendSamples(history, batch(0, 2));
assert.deepEqual(ids(history), [0, 1], 'an index that moved backwards (step-back) restarts the history');
assert.equal(history.nextIndex, 2);
assert.equal(H.appendSamples(history, { error: true }), history, 'malformed payloads are ignored');
assert.equal(history.rows.length, 2);

// Chart ranges never collapse and always include the data.
assert.deepEqual(plain(H.chartRange([])), { min: -1, max: 1 });
const flat = H.chartRange([5, 5, 5], { minSpan: 2 });
assert.ok(flat.min <= 4.01 && flat.max >= 5.99 && flat.max - flat.min >= 2);
const spread = H.chartRange([-3, 8], { floor: 0 });
assert.ok(spread.min <= -3 && spread.max >= 8);
assert.deepEqual(plain(H.chartRange([], { floor: 0, ceil: 100, minSpan: 100 })), { min: 0, max: 100 });

// Angles.
assert.equal(H.normalizeAngle(-Math.PI / 2).toFixed(6), (Math.PI * 1.5).toFixed(6));
assert.equal(Math.round(H.signedDegrees(3 * Math.PI / 2)), -90);
assert.equal(Math.round(H.signedDegrees(0.1)), 6);

// Status decoding includes the handshake.
assert.deepEqual(plain(H.describeStatus({ status: 1, enable: 1, ack_generation: 3, requested_generation: 3 })),
  { text: 'running', fault: false });
assert.deepEqual(plain(H.describeStatus({ status: 5, enable: 1, ack_generation: 2, requested_generation: 3 })),
  { text: 'running · saturated · update pending', fault: false });
assert.deepEqual(plain(H.describeStatus({ status: 2, enable: 1, ack_generation: 1, requested_generation: 1 })),
  { text: 'waiting for firmware · invalid config', fault: true });
assert.equal(H.describeStatus({ status: 0, enable: 0, ack_generation: 1, requested_generation: 1 }).text, 'disabled');
assert.equal(H.describeStatus(null).text, 'no control block');

// Readouts come from the device state and control block; nothing is hard-coded.
const motor = {
  parameters: { pole_pairs: 2 }, commanded_speed_rpm: 399.96, rotor_speed_rpm: 380.04,
  commanded_angle_rad: 1.0, rotor_angle_rad: 0.5, angle_error_rad: 0.5,
  duty_cycles: [0.5, 0.6666, 0.3333], alpha_beta_v: [1.5, -2.25], phase_currents_a: [1, -0.5, -0.5],
  id_a: 0.1234, iq_a: 4.5, pwm_frequency_hz: 16000, time_s: 0.0123, pwm_periods: 197,
};
const config = { speed_ref_q28: 2 ** 28 / 1000 * (400 * 2 / 60), status: 1, enable: 1,
  ack_generation: 2, requested_generation: 2 };
const text = H.formatReadouts(motor, config, { pru_hz: 250e6, iep_hz: 200e6 });
assert.equal(text.requestedSpeed, '400.0 rpm');
assert.equal(text.commandedSpeed, '400.0 rpm');
assert.equal(text.rotorSpeed, '380.0 rpm');
assert.equal(text.angleError, '28.6°');
assert.equal(text.duties, '0.500 / 0.667 / 0.333');
assert.equal(text.pwmFrequency, '16.000 kHz');
assert.equal(text.pruClock, '250.0 MHz');
assert.equal(text.iepClock, '200.0 MHz');
assert.equal(text.plantTime, '12.300 ms');
assert.equal(text.status, 'running');
assert.equal(text.fault, false);
assert.equal(H.formatReadouts(null, config, null), null);
assert.equal(H.formatReadouts(motor, null, null).requestedSpeed, '--');

// Button state follows attach/load/run.
assert.deepEqual(plain(H.controlState({ attached: false, loaded: false, running: false })),
  { load: true, apply: false, start: false, stop: false });
assert.deepEqual(plain(H.controlState({ attached: true, loaded: true, running: false })),
  { load: true, apply: true, start: true, stop: false });
assert.deepEqual(plain(H.controlState({ attached: true, loaded: true, running: true })),
  { load: true, apply: true, start: false, stop: true });

console.log('Motor control helpers validate forms, bound history and format readouts');
