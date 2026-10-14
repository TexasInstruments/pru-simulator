"use strict";

// Pure helpers for the Motor control view. No DOM access, so the contract is
// exercised by tests/ui_motor_helpers.test.js without a browser.
(root => {
  const MAX_SPEED_RPM = 15000;
  const SAMPLE_CAPACITY = 2048;
  const STATUS_RUNNING = 1;
  const STATUS_INVALID_CONFIG = 2;
  const STATUS_SATURATED = 4;

  // Mirrors the foc_motor profile defaults in pru_io/foc_motor_model.py.
  const PHYSICS_DEFAULTS = {
    resistance_ohm: 0.5, inductance_h: 0.001, flux_linkage_vs: 0.05,
    pole_pairs: 4, inertia_kg_m2: 0.0005, damping_nm_s: 0.003,
    load_torque_nm: 0, dc_bus_v: 48,
  };
  const PHYSICS_RULES = {
    resistance_ohm: { label: 'Rs', min: 0, strict: true },
    inductance_h: { label: 'Ls', min: 0, strict: true },
    flux_linkage_vs: { label: 'Flux linkage', min: 0 },
    pole_pairs: { label: 'Pole pairs', min: 1, max: 64, integer: true },
    inertia_kg_m2: { label: 'Inertia', min: 0, strict: true },
    damping_nm_s: { label: 'Damping', min: 0 },
    load_torque_nm: { label: 'Load torque' },
    dc_bus_v: { label: 'Vdc', min: 0, strict: true },
  };

  function toNumber(value) {
    if (typeof value === 'number') return value;
    if (typeof value === 'string' && value.trim() !== '') return Number(value);
    return NaN;
  }

  function checkNumber(label, value, { min, max, strict = false, integer = false } = {}) {
    const number = toNumber(value);
    if (!Number.isFinite(number)) return { error: `${label} must be a number` };
    if (integer && !Number.isInteger(number)) return { error: `${label} must be a whole number` };
    if (min !== undefined && (strict ? number <= min : number < min))
      return { error: `${label} must be ${strict ? 'greater than' : 'at least'} ${min}` };
    if (max !== undefined && number > max) return { error: `${label} must be at most ${max}` };
    return { value: number };
  }

  function firstError(checks) {
    for (const check of Object.values(checks)) if (check.error) return check.error;
    return null;
  }

  // Reference form -> foc_set_reference message fields, or an error string.
  function readReferenceForm(form) {
    const checks = {
      speed: checkNumber('Speed', form.speedRpm, { min: -MAX_SPEED_RPM, max: MAX_SPEED_RPM }),
      vd: checkNumber('Vd', form.vdPu, { min: -1, max: 0.99997 }),
      vq: checkNumber('Vq', form.vqPu, { min: -1, max: 0.99997 }),
      accel: checkNumber('Acceleration', form.accelRpmS, { min: 0 }),
    };
    const error = firstError(checks);
    if (error) return { error };
    return { message: { speed_rpm: checks.speed.value, vd_pu: checks.vd.value,
      vq_pu: checks.vq.value, accel_rpm_s: checks.accel.value } };
  }

  // Physics form (keyed by parameter name) -> foc_set_motor parameters.
  function readPhysicsForm(form) {
    const parameters = {};
    for (const [name, rule] of Object.entries(PHYSICS_RULES)) {
      const { label, ...limits } = rule;
      const check = checkNumber(label, form[name], limits);
      if (check.error) return { error: check.error };
      parameters[name] = check.value;
    }
    return { parameters };
  }

  // Bounded sample history fed by foc_samples messages ("samples since index").
  function createHistory(capacity = SAMPLE_CAPACITY) {
    return { capacity, fields: [], rows: [], nextIndex: 0 };
  }

  function appendSamples(history, payload) {
    if (!payload || !Array.isArray(payload.samples) || !Number.isInteger(payload.next_index))
      return history;
    // The index only goes backwards after a step-back, reset or re-attach.
    if (payload.next_index < history.nextIndex) history.rows = [];
    history.fields = payload.fields || history.fields;
    const last = history.rows.length ? history.rows[history.rows.length - 1][0] : -1;
    for (const row of payload.samples) if (row[0] > last) history.rows.push(row);
    if (history.rows.length > history.capacity)
      history.rows.splice(0, history.rows.length - history.capacity);
    history.nextIndex = payload.next_index;
    return history;
  }

  function column(history, name, rows = history.rows) {
    const index = history.fields.indexOf(name);
    return index < 0 ? [] : rows.map(row => row[index]);
  }

  function lastRows(history, count) {
    return history.rows.slice(Math.max(0, history.rows.length - count));
  }

  // Axis limits: always include the data, never collapse to a zero span.
  function chartRange(values, { floor = null, ceil = null, minSpan = 1 } = {}) {
    let low = Infinity;
    let high = -Infinity;
    for (const value of values) {
      if (value < low) low = value;
      if (value > high) high = value;
    }
    if (!Number.isFinite(low)) return { min: floor ?? -minSpan, max: ceil ?? minSpan };
    const margin = (high - low) * 0.1;
    low -= margin;
    high += margin;
    if (high - low < minSpan) {
      const middle = (high + low) / 2;
      low = middle - minSpan / 2;
      high = middle + minSpan / 2;
    }
    return { min: floor !== null ? Math.min(low, floor) : low,
      max: ceil !== null ? Math.max(high, ceil) : high };
  }

  const TWO_PI = Math.PI * 2;
  const normalizeAngle = radians => ((radians % TWO_PI) + TWO_PI) % TWO_PI;
  const toDegrees = radians => radians * 180 / Math.PI;
  function signedDegrees(radians) {
    const wrapped = normalizeAngle(radians + Math.PI) - Math.PI;
    return toDegrees(wrapped);
  }

  function describeStatus(config) {
    if (!config) return { text: 'no control block', fault: false };
    const parts = [];
    if (config.status & STATUS_RUNNING) parts.push('running');
    else parts.push(config.enable ? 'waiting for firmware' : 'disabled');
    if (config.status & STATUS_SATURATED) parts.push('saturated');
    const invalid = !!(config.status & STATUS_INVALID_CONFIG);
    if (invalid) parts.push('invalid config');
    if (config.ack_generation !== config.requested_generation) parts.push('update pending');
    return { text: parts.join(' · '), fault: invalid };
  }

  const fixed = (value, digits, unit = '') =>
    Number.isFinite(value) ? `${value.toFixed(digits)}${unit}` : '--';

  function referenceValues(config, poles, clocks) {
    const updateHz = clocks ? clocks.iep_hz / 12500 : 16000;
    return {
      speedRpm: config.speed_ref_q28 / 2 ** 32 * updateHz * 60 / poles,
      vdPu: config.vd_ref_q15 / 32768,
      vqPu: config.vq_ref_q15 / 32768,
      accelRpmS: config.ramp_rate_q28 >= 268435456 ? null
        : config.ramp_rate_q28 / 2 ** 32 * updateHz ** 2 * 60 / poles,
    };
  }

  // Everything the readout list shows, as display strings.
  function formatReadouts(motor, config, clocks) {
    if (!motor) return null;
    const poles = motor.parameters ? motor.parameters.pole_pairs : 4;
    const requestedRpm = config
      ? referenceValues(config, poles, clocks).speedRpm : NaN;
    const duties = motor.duty_cycles || [];
    const phase = motor.phase_currents_a || [];
    const alphaBeta = motor.alpha_beta_v || [];
    const status = describeStatus(config);
    return {
      requestedSpeed: fixed(requestedRpm, 1, ' rpm'),
      commandedSpeed: fixed(motor.commanded_speed_rpm, 1, ' rpm'),
      rotorSpeed: fixed(motor.rotor_speed_rpm, 1, ' rpm'),
      commandedAngle: fixed(toDegrees(normalizeAngle(motor.commanded_angle_rad)), 1, '°'),
      rotorAngle: fixed(toDegrees(normalizeAngle(motor.rotor_angle_rad)), 1, '°'),
      angleError: fixed(signedDegrees(motor.angle_error_rad), 1, '°'),
      duties: duties.length ? duties.map(duty => duty.toFixed(3)).join(' / ') : '--',
      alphaBeta: alphaBeta.length ? alphaBeta.map(v => v.toFixed(2)).join(' / ') + ' V' : '--',
      phaseCurrents: phase.length ? phase.map(i => i.toFixed(2)).join(' / ') + ' A' : '--',
      dqCurrents: `${fixed(motor.id_a, 2)} / ${fixed(motor.iq_a, 2)} A`,
      pwmFrequency: fixed(motor.pwm_frequency_hz / 1000, 3, ' kHz'),
      pruClock: clocks ? fixed(clocks.pru_hz / 1e6, 1, ' MHz') : '--',
      iepClock: clocks ? fixed(clocks.iep_hz / 1e6, 1, ' MHz') : '--',
      status: status.text,
      fault: status.fault,
      plantTime: fixed(motor.time_s * 1000, 3, ' ms'),
      pwmPeriods: String(motor.pwm_periods ?? 0),
    };
  }

  // Run/Stop button state for the current attach and run situation.
  function controlState({ attached, loaded, running }) {
    return {
      load: true,
      apply: attached,
      start: attached && loaded && !running,
      stop: attached && running,
    };
  }

  root.MotorHelpers = {
    MAX_SPEED_RPM, SAMPLE_CAPACITY, PHYSICS_DEFAULTS, PHYSICS_RULES,
    readReferenceForm, readPhysicsForm, createHistory, appendSamples, column,
    lastRows, chartRange, normalizeAngle, toDegrees, signedDegrees,
    describeStatus, referenceValues, formatReadouts, controlState,
  };
})(globalThis);
