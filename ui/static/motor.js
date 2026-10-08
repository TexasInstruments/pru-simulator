"use strict";

// Motor control view: DOM wiring and canvas drawing around motor_helpers.js.
// It only talks to the simulator through the existing WebSocket actions and
// the existing Run/Stop loop in app.js; it has no execution loop of its own.
(() => {
  const H = globalThis.MotorHelpers;
  const $ = id => document.getElementById(id);
  const view = $('motor-view');
  if (!H || !view) return;

  const SAMPLE_WINDOW = 1024;
  const SAMPLE_REQUEST_GAP_MS = 80;
  const FIRMWARE_PATH = 'foc_open_loop.asm';
  const COLORS = { a: '#70d6b4', b: '#7eb8e8', c: '#e5a66d', alpha: '#da8ee6', beta: '#e4d27d' };

  let motor = null;
  let config = null;
  let clocks = null;
  let firmwareLoaded = false;
  let awaitingLoad = false;
  let history = H.createHistory();
  let samplePending = false;
  let sampleRequestedAt = 0;
  let drawQueued = false;
  let lastStateKey = '';
  // The reference form follows the backend only until the user has staged values.
  let referenceLocked = false;

  const send = message => (typeof sendAction === 'function' ? sendAction({ core: 'pru0', ...message }) : undefined);
  const isRunning = () => typeof running !== 'undefined' && running;

  function setStatus(text) { $('motor-status').textContent = text; }

  function showError(errors) {
    const el = $('motor-error');
    el.textContent = Array.isArray(errors) ? errors.join('\n') : String(errors || 'Motor action failed');
    el.hidden = false;
  }

  function clearError() {
    const el = $('motor-error');
    el.textContent = '';
    el.hidden = true;
  }

  // ---- Forms -------------------------------------------------------------

  const referenceInputs = {
    speedRpm: $('motor-speed-rpm'), vdPu: $('motor-vd-pu'),
    vqPu: $('motor-vq-pu'), accelRpmS: $('motor-accel'),
  };
  const physicsInputs = Array.from(view.querySelectorAll('[data-physics]'));

  const referenceForm = () => Object.fromEntries(
    Object.entries(referenceInputs).map(([key, input]) => [key, input.value]));
  const physicsForm = () => Object.fromEntries(
    physicsInputs.map(input => [input.dataset.physics, input.value]));

  function fillPhysics(values) {
    for (const input of physicsInputs) {
      input.value = String(values[input.dataset.physics]);
      delete input.dataset.dirty;
    }
  }

  fillPhysics(H.PHYSICS_DEFAULTS);
  for (const input of [...Object.values(referenceInputs), ...physicsInputs]) {
    input.addEventListener('input', () => { input.dataset.dirty = 'true'; });
  }

  // Adopt backend values only into fields the user has not edited.
  function syncFields() {
    if (motor && motor.parameters) {
      for (const input of physicsInputs) {
        if (input.dataset.dirty || document.activeElement === input) continue;
        input.value = String(motor.parameters[input.dataset.physics]);
      }
    }
    if (!referenceLocked && config && config.requested_generation > 0 && motor) {
      referenceLocked = true;
      const poles = motor.parameters.pole_pairs;
      const values = H.referenceValues(config, poles, clocks);
      for (const [key, input] of Object.entries(referenceInputs)) {
        if (input.dataset.dirty || document.activeElement === input || values[key] === null) continue;
        input.value = String(Number(values[key].toFixed(key === 'vdPu' || key === 'vqPu' ? 4 : 1)));
      }
    }
  }

  function fillRoutes(select) {
    const internal = document.createElement('option');
    internal.value = '-1';
    internal.textContent = 'Internal modulator';
    select.appendChild(internal);
    for (let pin = 0; pin < 20; pin++) {
      const option = document.createElement('option');
      option.value = String(pin);
      option.textContent = `GPI ${pin}`;
      select.appendChild(option);
    }
  }
  const routeSelects = [$('motor-route-0'), $('motor-route-1')];
  routeSelects.forEach(fillRoutes);
  routeSelects.forEach(select => select.addEventListener('change', () => { select.dataset.dirty = 'true'; }));

  function syncRoutes(io) {
    const routes = io && io.sd && Array.isArray(io.sd.input_routes) ? io.sd.input_routes : null;
    if (!routes) return;
    routeSelects.forEach((select, channel) => {
      if (select.dataset.dirty || document.activeElement === select) return;
      select.value = String(routes[channel] === null || routes[channel] === undefined ? -1 : routes[channel]);
    });
  }

  // ---- Buttons -----------------------------------------------------------

  function updateButtons() {
    const state = H.controlState({ attached: !!motor, loaded: firmwareLoaded, running: isRunning() });
    $('motor-apply').disabled = !state.apply;
    $('motor-start').disabled = !state.start;
    $('motor-stop').disabled = !state.stop;
    $('motor-physics-apply').disabled = !state.apply;
    $('motor-physics-defaults').disabled = !state.apply;
    $('motor-detach').disabled = !state.apply;
    $('motor-routes-apply').disabled = !state.apply;
    $('motor-start').setAttribute('aria-pressed', String(isRunning()));
    $('motor-nav-button').classList.toggle('has-motor', !!motor);
  }

  function describe() {
    if (!motor) return awaitingLoad ? 'Loading firmware…' : 'Firmware not loaded. No FOC motor attached.';
    const parts = [`Motor ${motor.name} attached`, firmwareLoaded ? 'firmware loaded' : 'firmware not loaded'];
    if (firmwareLoaded) parts.push(isRunning() ? 'running' : 'stopped');
    return parts.join(' · ') + '.';
  }

  $('motor-load').addEventListener('click', async () => {
    clearError();
    if (typeof stopRun === 'function') stopRun();
    const reference = H.readReferenceForm(referenceForm());
    if (reference.error) { showError(reference.error); return; }
    awaitingLoad = true;
    referenceLocked = true;
    setStatus('Loading firmware…');
    try {
      const response = await fetch(`/source/${FIRMWARE_PATH}`);
      if (!response.ok) throw new Error(`Could not read source/${FIRMWARE_PATH} (${response.status})`);
      const source = await response.text();
      if (typeof openFileAsTab === 'function') openFileAsTab(FIRMWARE_PATH, source);
      history = H.createHistory();
      samplePending = false;
      lastStateKey = '';
      if (!motor) send({ action: 'device_attach', profile: 'foc_motor' });
      send({ action: 'load', source, filename: FIRMWARE_PATH });
      send({ action: 'foc_set_reference', enable: false, ...reference.message });
      for (const input of Object.values(referenceInputs)) delete input.dataset.dirty;
    } catch (error) {
      awaitingLoad = false;
      showError(String(error.message || error));
      setStatus(describe());
    }
  });

  $('motor-detach').addEventListener('click', () => {
    clearError();
    if (typeof stopRun === 'function') stopRun();
    if (motor) send({ action: 'device_detach', name: motor.name });
  });

  $('motor-reference-form').addEventListener('submit', event => {
    event.preventDefault();
    clearError();
    const reference = H.readReferenceForm(referenceForm());
    if (reference.error) { showError(reference.error); return; }
    send({ action: 'foc_set_reference', ...reference.message });
    referenceLocked = true;
    for (const input of Object.values(referenceInputs)) delete input.dataset.dirty;
  });

  // Start = enable the controller and use the normal Run loop, so breakpoints,
  // capture and step-back behave exactly as for any other firmware.
  $('motor-start').addEventListener('click', () => {
    clearError();
    if (typeof currentCore !== 'undefined' && currentCore !== 'pru0' && !multiCoreMode) {
      showError('Select PRU0 in the core selector: the FOC firmware runs on PRU0.');
      return;
    }
    const reference = H.readReferenceForm(referenceForm());
    if (reference.error) { showError(reference.error); return; }
    send({ action: 'foc_set_reference', enable: true, ...reference.message });
    referenceLocked = true;
    for (const input of Object.values(referenceInputs)) delete input.dataset.dirty;
    if (typeof startRun === 'function') startRun();
    updateButtons();
    setStatus(describe());
  });

  $('motor-stop').addEventListener('click', () => {
    if (typeof stopRun === 'function') stopRun();
    send({ action: 'foc_enable', enable: false });
    updateButtons();
    setStatus(describe());
  });

  $('motor-physics-form').addEventListener('submit', event => {
    event.preventDefault();
    clearError();
    const physics = H.readPhysicsForm(physicsForm());
    if (physics.error) { showError(physics.error); return; }
    send({ action: 'foc_set_motor', parameters: physics.parameters });
    for (const input of physicsInputs) delete input.dataset.dirty;
  });

  $('motor-physics-defaults').addEventListener('click', () => {
    clearError();
    fillPhysics(H.PHYSICS_DEFAULTS);
    send({ action: 'foc_set_motor', parameters: { ...H.PHYSICS_DEFAULTS } });
  });

  $('motor-routes-apply').addEventListener('click', () => {
    clearError();
    const routes = routeSelects.map(select => Number.parseInt(select.value, 10));
    send({ action: 'foc_apply', config: {}, routes });
    routeSelects.forEach(select => { delete select.dataset.dirty; });
  });

  // ---- Backend messages ----------------------------------------------------

  function renderReadouts() {
    const text = H.formatReadouts(motor, config, clocks);
    for (const element of view.querySelectorAll('[data-readout]')) {
      const key = element.dataset.readout;
      element.textContent = text ? text[key] : '--';
      element.classList.toggle('fault', !!text && key === 'status' && text.fault);
    }
  }

  function requestSamples() {
    const now = Date.now();
    if (samplePending && now - sampleRequestedAt > 2000) samplePending = false;
    if (!motor || samplePending || motor.sample_index === history.nextIndex) return;
    if (now - sampleRequestedAt < SAMPLE_REQUEST_GAP_MS) return;
    samplePending = true;
    sampleRequestedAt = now;
    send({ action: 'foc_state', since: history.nextIndex });
  }

  function onState(msg) {
    if (msg.core && msg.core !== 'pru0') return;
    const io = msg.io || {};
    const bus = io.device_bus || {};
    const found = (bus.devices || []).find(device => device.model === 'pmsm' && device.core === 'pru0');
    if (!found && motor) {
      history = H.createHistory();
      lastStateKey = '';
    }
    motor = found || null;
    config = motor ? io.foc_config || null : null;
    const previousIepHz = clocks?.iep_hz;
    clocks = motor ? io.foc_clocks || null : null;
    if (previousIepHz && clocks?.iep_hz !== previousIepHz) referenceLocked = false;
    firmwareLoaded = !!(msg.labels && msg.labels.control_update !== undefined) &&
      Array.isArray(msg.instructions) && msg.instructions.length > 0;
    if (awaitingLoad && motor && firmwareLoaded) awaitingLoad = false;
    syncFields();
    syncRoutes(io);
    renderReadouts();
    updateButtons();
    setStatus(describe());
    requestSamples();
    // Redraw only when something visible changed: a paused firmware is quiet.
    const key = motor ? `${motor.sample_index}|${motor.cycles}|${motor.rotor_angle_rad}` : 'none';
    if (key !== lastStateKey) { lastStateKey = key; queueDraw(); }
  }

  function onSamples(msg) {
    samplePending = false;
    H.appendSamples(history, msg);
    queueDraw();
    if (motor && motor.sample_index !== history.nextIndex) requestSamples();
  }

  function onError(errors) {
    awaitingLoad = false;
    showError(errors);
    // Rejected parameters leave the plant untouched: show what is in effect.
    for (const input of physicsInputs) delete input.dataset.dirty;
    setStatus(describe());
  }

  function onLoadError(errors) {
    if (!awaitingLoad) return;
    awaitingLoad = false;
    showError(errors);
    setStatus(describe());
  }

  function onConnect() {
    samplePending = false;
    history = H.createHistory();
    lastStateKey = '';
  }

  // ---- Drawing -----------------------------------------------------------

  function themeColor(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function surface(canvas) {
    const rect = canvas.getBoundingClientRect();
    const width = Math.floor(rect.width);
    const height = Math.floor(rect.height);
    if (width <= 0 || height <= 0) return null;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== Math.floor(width * dpr) || canvas.height !== Math.floor(height * dpr)) {
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, width, height };
  }

  function drawLines(canvas, rows, series, range, emptyText) {
    const target = surface(canvas);
    if (!target) return;
    const { ctx, width, height } = target;
    const bg = themeColor('--graph-bg', '#1b2127');
    const grid = themeColor('--graph-grid', '#35414b');
    const dim = themeColor('--graph-label', '#c0c7cd');
    const placeholder = themeColor('--graph-placeholder', '#9da9b3');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);
    const left = 42, right = 8, top = 16, bottom = 8;
    const plotW = Math.max(1, width - left - right);
    const plotH = Math.max(1, height - top - bottom);
    ctx.strokeStyle = grid;
    ctx.lineWidth = 1;
    ctx.fillStyle = dim;
    ctx.font = '10px monospace';
    for (let i = 0; i <= 4; i++) {
      const y = top + (i / 4) * plotH + 0.5;
      ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.stroke();
      const value = range.max - (i / 4) * (range.max - range.min);
      ctx.fillText(Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(2), 2, y + 3);
    }
    let labelX = left;
    for (const lane of series) {
      ctx.fillStyle = lane.color;
      ctx.fillRect(labelX, 4, 8, 8);
      ctx.fillStyle = dim;
      ctx.fillText(lane.label, labelX + 11, 12);
      labelX += lane.label.length * 7 + 22;
    }
    if (rows.length < 2) {
      ctx.fillStyle = placeholder;
      ctx.fillText(emptyText, left + 6, top + plotH / 2);
      return;
    }
    const times = H.column(history, 'time_s', rows);
    const span = Math.max(1e-9, times[times.length - 1] - times[0]);
    const spanY = Math.max(1e-12, range.max - range.min);
    for (const lane of series) {
      const values = H.column(history, lane.field, rows);
      ctx.strokeStyle = lane.color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      values.forEach((value, index) => {
        const x = left + (times[index] - times[0]) / span * plotW;
        const y = top + (range.max - value) / spanY * plotH;
        if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
  }

  function drawDial() {
    const target = surface($('motor-dial'));
    if (!target) return;
    const { ctx, width, height } = target;
    const bg = themeColor('--graph-bg', '#1b2127');
    const grid = themeColor('--graph-grid-strong', '#4b5863');
    const dim = themeColor('--graph-label', '#c0c7cd');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);
    const cx = width / 2;
    const cy = height / 2;
    const radius = Math.max(30, Math.min(width, height) * 0.4);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.strokeStyle = grid;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(0, 0, radius, 0, Math.PI * 2); ctx.stroke();
    for (let tick = 0; tick < 12; tick++) {
      const angle = tick * Math.PI / 6;
      ctx.beginPath();
      ctx.moveTo(Math.cos(angle) * radius * 0.9, -Math.sin(angle) * radius * 0.9);
      ctx.lineTo(Math.cos(angle) * radius, -Math.sin(angle) * radius);
      ctx.stroke();
    }
    const needle = (angle, length, color, lineWidth, dashed) => {
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = lineWidth;
      if (dashed) ctx.setLineDash([6, 4]);
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(Math.cos(angle) * length, -Math.sin(angle) * length);
      ctx.stroke();
      ctx.restore();
    };
    if (motor) {
      const rotor = motor.rotor_angle_rad;
      const command = motor.commanded_angle_rad;
      // Wedge between the two needles shows the angle error.
      ctx.fillStyle = COLORS.alpha;
      ctx.globalAlpha = 0.18;
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.arc(0, 0, radius * 0.5, -command, -rotor, motor.angle_error_rad < 0);
      ctx.closePath();
      ctx.fill();
      ctx.globalAlpha = 1;
      needle(command, radius * 0.95, COLORS.alpha, 2, true);
      needle(rotor, radius * 0.8, COLORS.a, 3, false);
    }
    ctx.restore();
    ctx.fillStyle = dim;
    ctx.font = '10px monospace';
    ctx.fillText('0°', cx + radius + 3, cy + 3);
    ctx.fillText('90°', cx - 9, cy - radius - 4);
    ctx.fillText('180°', cx - radius - 28, cy + 3);
    ctx.fillText('270°', cx - 11, cy + radius + 12);
    ctx.fillText(motor ? `error ${H.signedDegrees(motor.angle_error_rad).toFixed(1)}°` : 'no motor', 6, height - 6);
  }

  function draw() {
    drawQueued = false;
    if (view.hidden) return;
    drawDial();
    const rows = H.lastRows(history, SAMPLE_WINDOW);
    const waiting = motor ? 'waiting for PWM periods…' : 'attach the motor and run the firmware';
    const col = field => H.column(history, field, rows);
    const currents = [...col('ia'), ...col('ib'), ...col('ic')];
    const peak = H.chartRange(currents.map(Math.abs), { floor: 0, minSpan: 2 }).max;
    drawLines($('motor-duty-plot'), rows, [
      { label: 'Da', color: COLORS.a, field: 'duty_a' },
      { label: 'Db', color: COLORS.b, field: 'duty_b' },
      { label: 'Dc', color: COLORS.c, field: 'duty_c' },
    ], { min: 0, max: 1 }, waiting);
    drawLines($('motor-current-plot'), rows, [
      { label: 'Ia', color: COLORS.a, field: 'ia' },
      { label: 'Ib', color: COLORS.b, field: 'ib' },
      { label: 'Ic', color: COLORS.c, field: 'ic' },
    ], { min: -peak * 1.1, max: peak * 1.1 }, waiting);
    const vdc = motor && motor.parameters ? motor.parameters.dc_bus_v : 48;
    drawLines($('motor-voltage-plot'), rows, [
      { label: 'Vα', color: COLORS.alpha, field: 'valpha_v' },
      { label: 'Vβ', color: COLORS.beta, field: 'vbeta_v' },
    ], { min: -vdc * 0.7, max: vdc * 0.7 }, waiting);
    drawLines($('motor-speed-plot'), rows, [
      { label: 'commanded', color: COLORS.alpha, field: 'commanded_speed_rpm' },
      { label: 'rotor', color: COLORS.a, field: 'rotor_speed_rpm' },
    ], H.chartRange([...col('commanded_speed_rpm'), ...col('rotor_speed_rpm')], { floor: 0, ceil: 100, minSpan: 100 }), waiting);
    drawLines($('motor-dq-plot'), rows, [
      { label: 'Id', color: COLORS.b, field: 'id' },
      { label: 'Iq', color: COLORS.c, field: 'iq' },
    ], H.chartRange([...col('id'), ...col('iq')], { minSpan: 2 }), waiting);
  }

  function queueDraw() {
    if (drawQueued || view.hidden) return;
    drawQueued = true;
    (window.requestAnimationFrame || (fn => setTimeout(fn, 16)))(draw);
  }

  document.addEventListener('pru-workspace-view-changed', event => {
    if (event.detail && event.detail.view === 'motor') queueDraw();
  });
  document.addEventListener('pru-theme-changed', queueDraw);
  window.addEventListener('resize', queueDraw);

  window.MotorControl = { onState, onSamples, onError, onLoadError, onConnect };
  updateButtons();
  setStatus(describe());
})();
