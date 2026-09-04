# Plan B — FOC Simulator Features + UI Tab (Python side)

**Date:** 2026-09-04
**Design spec:** `docs/superpowers/specs/2026-09-04-foc-open-loop-design.md`
**Deliverable:** the shared-memory ABI, the Python PMSM motor model, the co-sim runtime + MCP tool,
and a new "Motor Control" UI tab (reference inputs, live plots, animated rotary rotor).

Covers everything on the **simulator/Python side**. Plan A (PRU firmware) depends on task **B1**
(generated `foc_abi.inc`) and is validated against the motor model from **B2**.

## Tasks

### B1 — Shared-memory ABI (schema-generated)
- [ ] Write `schema/foc_abi.json` with the four sections from the spec (`control`, `pwm_out`,
      `motor_fb`, `sine_lut`) + a `constants` block (`abi_version`, `q_fraction_bits=24`,
      `iep_tick_hz`, `sine_lut_entries=2048`, `speed_scale`).
- [ ] Write `tools/gen_foc_abi.py` by cloning `tools/gen_ssi_abi.py`: same tiling/overlap/window
      validation, emits `source/foc_abi.inc`, `pru_io/foc_abi.py` (offset dicts +
      `pack_control`/`unpack_pwm_out`/`unpack_motor_fb`/seqlock helpers), `include/foc_abi.h`.
- [ ] Run `python tools/gen_foc_abi.py`; commit the generated files.
- [ ] **Verify:** `tests/test_foc_abi_generated.py` (clone of `test_ssi_config_abi_generated.py`) —
      regenerates in memory, diffs the checked-in copies.

### B2 — Python PMSM motor model — `pru_io/foc_motor_model.py`
- [ ] `FocMotorModel(sim)`: store `sim`, `sim.add_hard_reset_hook(self.reset)`; reuse
      `references/gan_shunt__current.py` constants + `inverse_park`/`inverse_clarke`; add `B`, `J`,
      `Tload`.
- [ ] `_on_tick()`: read `pwm_out` duties (seqlock) → `Vx=(Tx−0.5)·V_DC` → inverse-Clarke → αβ
      electrical integrator + mechanical integrator (see spec 3.4) → write `motor_fb`
      (ia/ib/ic, id/iq_meas, rotor_theta, speed_rpm) under seqlock. `dt` from `iep_tick_hz`.
- [ ] `start()`/`stop()` register/remove `sim.iep.add_counter_observer(self._on_iep_advanced)`;
      provide manual `advance_to(ticks)`/`step()` for tests; `state()` returns a JSON snapshot.
- [ ] **Verify:** `tests/test_foc_open_loop.py::test_motor_model_spins` — drive `pwm_out` with a
      rotating duty set, `advance_to` over several revolutions, assert rotor_theta rotates, speed
      converges toward the electrical frequency, currents bounded/sinusoidal.

### B3 — Runtime wrapper + Simulator hook + MCP tool
- [ ] `pru_io/foc_runtime.py` `FocRuntime(sim)` (model on `ssi_runtime.py`): owns the model, seeds
      the sine LUT into shared mem, does staged→commit control writes with the generation bump;
      exposes `start/stop/step/set_reference(speed,id,iq,ramp)/state`.
- [ ] `Simulator.motor_attach(...)` in `simulator.py`, mirroring `uart_inject`/`ssi_inject`
      (build model, attach, store handle, register reset hook).
- [ ] `pru_foc_inject` MCP tool in `mcp_server/server.py`, mirroring `pru_uart_inject`: load
      firmware, attach model, seed LUT, set references, run, read back `pwm_out`/`motor_fb`.
- [ ] **Verify:** `test_foc_runtime_end_to_end` — via `FocRuntime`: load firmware + attach + set a
      speed + run → duties rotate and model feedback is consistent. MCP smoke through
      `pru_foc_inject`.

### B4 — Backend WS handlers — `ui/server.py`
- [ ] Module-global `foc_runtime = None`; helper `_foc_state()` (model on `_ssi_runtime_state`)
      returning `{control, pwm, fb}` via `foc_abi` unpack helpers.
- [ ] Add WS `elif action == ...` branches (in the chain ending ~line 1423), mirroring
      `ssi_runtime_*`: `foc_load`, `foc_set_reference`, `foc_start`, `foc_stop`, `foc_state`.
- [ ] Emit `{"type":"foc_state", ...}` after steps and inside the run loop so plots track live.
- [ ] **Verify:** targeted server test (or manual) that each action mutates the control block /
      returns a well-formed `foc_state`.

### B5 — Frontend tab, form, dial, plots — `ui/static/index.html` + `app.js`
- [ ] `index.html`: add `<button id="tab-foc" class="view-tab" role="tab" aria-selected="false"
      aria-controls="foc-view" tabindex="-1">Motor Control</button>` in `#view-tabs` (~line 3929),
      and a `<section id="foc-view" class="app-view" role="tabpanel" aria-labelledby="tab-foc"
      aria-hidden="true" tabindex="-1" hidden>…</section>` in `#app-views`. Add inline CSS for the
      panel/canvas. Bump the `?v=` cache-busting query on the script tags.
- [ ] Reference form: SpeedRef (RPM→pu), IdRef (pu), IqRef (pu), ramp rate; Enable/Start/Stop
      buttons → `sendAction({action:"foc_set_reference"/"foc_start"/"foc_stop"})`.
- [ ] Rotary dial: dedicated `<canvas>` + `requestAnimationFrame` draw (model on `requestGraphDraw`,
      app.js ~1903) using `ctx.arc`/`ctx.rotate`; rotor needle at `motor_fb.rotor_theta`, commanded
      `pwm_out.theta_cmd` overlaid; pole-pair backdrop.
- [ ] Live plots (reuse `drawMemGraph` rolling-buffer style, app.js ~2201): Ta/Tb/Tc duties,
      Ia/Ib/Ic currents, Vα/Vβ; numeric readout (speed RPM, θ, duties).
- [ ] Extend `syncWorkspaceSubnav` (app.js ~419) to hide the simulator "Panels" strip on the FOC
      tab; add `msg.type === "foc_state"` handling in `onmessage` (app.js ~670) to push samples
      into plot buffers and update the dial.
- [ ] **Verify (manual):** `python ui/server.py` → `http://localhost:8080` → Motor Control tab →
      set a speed → rotor spins, duties form saddle waveforms, currents go sinusoidal.

### B6 — Session report + docs housekeeping
- [ ] `docs/reports/2026-09-04-foc-open-loop-session-report.html` — **copy the `<style>` block
      verbatim** from `docs/reports/2026-08-12-i2c-tca9538-session-report.html` (CSS variables,
      both dark-mode blocks, class names); numbered sections per the `project-documentation` skill.

## Definition of done (Plan B)
- [ ] `python -m pytest tests/test_foc_abi_generated.py tests/test_foc_open_loop.py -q` green;
      full suite `python -m pytest -q` still green.
- [ ] Regenerating the ABI is idempotent (drift test passes).
- [ ] Manual UI: Motor Control tab drives the firmware open-loop; the rotor animates from the PMSM
      model and current/duty plots update live over the WebSocket.
