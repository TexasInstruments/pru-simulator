# foc_open_loop — Open-Loop Field-Oriented Control (Level 1)

Single-core **PRU0** firmware plus an IEP-clocked Python PMSM plant. The firmware accepts a
speed command and voltage references from shared memory, runs RC (ramp control), RG (ramp
generator), sin/cos LUT lookup, inverse Park, conventional inverse Clarke/SVPWM, and outputs
three-phase PWM duty cycles. The host model applies each coherent PWM publication to the plant
using elapsed IEP time; it does not convert the firmware loop counter into time.

* **Clock rate:** 200 MHz (single PRU, AM243x profile)
* **Fixed point:** Q24 (signed 1.7.24; `0x01000000` = 1.0) for per-unit voltages, currents, and duties
* **Phase accumulator:** 32-bit for electrical angle (natural wrap = one electrical revolution)
* **Sine LUT:** 2048-entry Q24 table, host-seeded at load; index = `theta_acc >> 21`; cosine via quarter-table offset (+512 entries, mod 2048)
* **Timing:** Absolute IEP deadlines at 100 kHz, using the configured IEP clock and an explicit control period
* **Safety:** Disabled output is neutral `0.5/0.5/0.5`; voltage magnitude is limited to `1/sqrt(3)` pu; saturation and deadline faults are reported
* **Pipeline:** Config handshake → RC → RG(θ) → sin/cos lookup → inverse Park (Vα, Vβ) → SVGEN (Ta, Tb, Tc), all published under seqlock
* **Broadside MAC:** Device 0, operands R28/R29, 64-bit product R26:R27; Q48→Q24 renormalization by `>>24`

## Shared-memory map (ABI)

| Base Address | Section | Written by | Contents |
|---|---|---|---|
| `0x00010000` | `control` | Host | enable, generation handshake, speed reference, Vd/Vq references (legacy `id`/`iq` aliases), acceleration slew, control period |
| `0x00010100` | `pwm_out` | PRU0 | seqlock, Ta/Tb/Tc, Valpha/Vbeta, command angle, loop counter, true IEP timestamp, status, ramped speed command |
| `0x00010200` | `motor_fb` | Python plant | phase currents, d/q currents, rotor electrical angle, measured speed, true IEP timestamp |
| `0x00011000` | `sine_lut` | Host (seed at load) | 2048 × u32 Q24 sine samples (8 KB) |

The ABI is schema-generated from `schema/foc_abi.json` via `tools/gen_foc_abi.py`, producing `source/foc_abi.inc`, `pru_io/foc_abi.py`, and `include/foc_abi.h`.

## Files

| File | Purpose |
|------|---------|
| `foc_open_loop.asm` | PRU0 firmware (open-loop pipeline) |
| `pru_io/foc_motor_model.py` | IEP-clocked PMSM plant and timestamped 100 us samples |
| `pru_io/foc_runtime.py` | Reference validation, lifecycle, coherent telemetry, and clock metadata |
| `README.md` | this file |
| `PROJECT_REPORT.md` | detailed project documentation |

## Run it in the simulator

### Automated cross-check and integration tests

```bash
python -m pytest tests/test_foc_validation_repair.py tests/test_foc_firmware.py tests/test_foc_open_loop.py tests/test_foc_ui.py tests/test_mcp_server.py -q
```

The focused suite covers the firmware pipeline, ABI generation, IEP timing, model lifecycle,
WebSocket controls, UI contracts, and MCP load/fault reporting. The complete project suite is:

```bash
python -m pytest -q
```

The firmware cross-checks verify the pipeline, one stage at a time, against pure-Python reference implementations:

1. **test_config_handshake** — Control block handshake (generation matching, reference latch)
2. **test_mul_q24_positive_operands** — Q24 multiply with positive operands
3. **test_mul_q24_negative_operand** — Q24 multiply with one negative operand
4. **test_mul_q24_both_negative_operands** — Q24 multiply with both negative operands
5. **test_ramp_generates_rotating_theta** — Ramp control slew logic, angle generation, 32-bit wrap
6. **test_sincos_lut** — Sine/cosine lookup accuracy (sin² + cos² ≈ 1, matching LUT samples)
7. **test_ipark** — Inverse Park transformation (Vα = Id·cos − Iq·sin, Vβ = Id·sin + Iq·cos)
8. **test_svgen** — SVPWM duty generation, common-mode identity, duty bounds [0, 1]
9. **test_full_loop_pwm** — Full electrical revolution, three duties phase-shifted 120°, saddle waveforms

Each test loads the firmware on pru0 at 200 MHz, sets up the shared-memory control block, seeds the sine LUT, and steps the simulator until the output is stable. Results are compared against known-good Python references with Q24 tolerance (±1 LSB).

## Operating procedure

1. Load `foc_open_loop/foc_open_loop.asm` from the FOC tab. Load creates a paused runtime with
   neutral duties, zero measured speed, a fresh session id, and a reset chart history.
2. Set **Speed reference**, **Vd reference**, **Vq reference**, and **Acceleration (RPM/s)**.
   The legacy `IdRef`/`IqRef` protocol names remain accepted but represent voltage references,
   not regulated currents. The default point is 400 RPM, Vd=0, Vq=0.25 pu, and 1000 RPM/s.
3. Press Start. Start applies the visible references, enables the control block, starts the
   plant observer, and runs bounded PRU0 batches that yield back to WebSocket controls.
4. Press Stop to disable at a published control boundary. The plant then pauses with the last
   measured speed intact; it is not replaced with an artificial zero.
5. Reset restores firmware state, FOC-owned shared memory, the IEP counter, plant state, and
   chart/session history together. The hardware-style reset leaves the IEP disabled; the next
   Start restores its clock configuration before execution. FOC step-back is intentionally
   disabled until a complete plant/timebase snapshot exists.

### Reset, reconnect, and breakpoints

The hardware-style reset disables the IEP as part of the reset sequence. The next **Start**
restores the FOC IEP clock configuration, derives the control period from the active IEP
frequency, and then starts the firmware and plant. If the runtime is disconnected while active,
the shared FOC task is cancelled and the plant is paused coherently; reconnecting shows **Paused**
and re-enables **Start**. Only one WebSocket may own FOC execution at a time.

Adding a breakpoint no longer disables execution. It only disables the timer-wait fast path, so
ordinary instructions run until the actual breakpoint PC is reached. At that point the runtime
publishes a paused state with the breakpoint reason. Clear the breakpoint or use **Step** to
continue; generic instruction stepping remains independent of the FOC task.

The FOC task publishes both plant telemetry and the generic PRU core state. Consequently the
Source / Disassembly panel, PC highlight, registers, and instruction counters follow the same
executing firmware while Motor Control is running; the source panel is not a separate execution
engine.

The runtime publishes approximately 30 Hz dashboard updates. Plant history is sampled every
100 us of simulated time and the browser retains a 100 ms simulation-time window, including
stationary and reverse motion. All PWM and feedback reads use seqlocks; an incomplete read
retains the last coherent sample.

The firmware publishes a true absolute IEP timestamp. If a control pass misses its next
deadline it publishes neutral output and sets the deadline-miss status instead of silently
changing the loop frequency.

The FOC telemetry separates the configured PRU clock, active IEP clock, measured control-loop
cadence, and execution throughput. The simulation/wall readout uses only active execution time,
shows adaptive precision for sub-0.01x rates, and also reports simulated milliseconds per wall
second. It does not alter simulated timestamps or rotor motion. The rotor dial uses the same
coordinate convention for both arrows: 0° right, 90° up, 180° left, and 270° down; while paused,
the arrows remain at their last simulation-derived values.

The long physical acceptance cases use the real assembly and may take several minutes on the
host:

```bash
python tools/run_foc_acceptance.py --clock 200 \
  --case nominal_400 --case reference_change --case reverse_reset \
  --case high_valid_1000 --case insufficient_voltage --case zero_voltage_rest
```

Repeat with `--clock 250` or `--clock 300` for clock-consistency checks. The insufficient-voltage
case is expected to lose synchronism in this open-loop model; the zero-voltage case is expected
to move the command angle while the rotor remains stationary.

### UI smoke-test matrix

Use the **Motor Control** tab with target **PRU0**. After **Load firmware**, the tab should be
paused with neutral duties and zero simulation time. Apply references before each **Start**.

| Scenario | UI inputs | Expected observation |
|---|---|---|
| Nominal | 400 RPM, Vd 0, Vq 0.25, 1000 RPM/s | Simulated time, IEP time, loop counter, charts, and both dial arrows advance; speed ramps and settles near 400 RPM. |
| Stop | Press **Stop** during nominal run | Status becomes paused, duties go neutral, measured speed is retained, and the dial/charts stop changing. |
| Reset -> Start | Press **Reset**, then **Start** without reloading | IEP time and loop counter restart from reset; they advance again after Start. |
| Refresh/reconnect | Refresh or close/reopen the dashboard while running | The shared simulation pauses; reconnect shows paused with Start enabled. Starting again produces one progressing stream, not duplicate speed/chart updates. |
| Reference change | Start at 200 RPM, then apply 400 RPM | The new command ramps to 400 RPM without resetting the session or fabricating timestamps. |
| Reverse | -400 RPM, Vd 0, Vq -0.25 | Speed becomes negative, command/rotor rotation reverses, and the dial follows the same 0° right / 90° up convention. |
| High valid point | 1000 RPM, Vd 0, Vq 0.5, 1000 RPM/s | Speed settles near 1000 RPM with bounded angle error and low ripple. |
| Insufficient voltage | 800 RPM, Vd 0, Vq 0.25 | The open-loop plant eventually loses synchronism; large angle error and speed error are shown honestly. |
| Zero voltage from rest | 400 RPM, Vd 0, Vq 0, 1000 RPM/s | Command angle moves, but rotor speed and phase currents remain at rest. |
| Clock check | Repeat nominal at 200, 250, and 300 MHz | PRU/IEP readouts change, control cadence remains about 100 kHz, and simulated-time behavior remains physically consistent. |

For a dial-only check, pause at known cardinal angles: 0° points right, 90° up, 180° left,
and 270° down. Both arrows must freeze when the simulation is paused; neither should continue
animating independently.

## Design notes

This is **Phase 1 (open loop only)**. 

- **Open loop:** Vd/Vq are fixed voltage references that set the rotating voltage-vector magnitude; there is no current or speed regulator
- **Speed control:** SpeedRef (slewed by a configurable ramp) sets the angular frequency
- **Shared-memory interface:** All references are staged through the `control` block with generation matching; results are published to `pwm_out` under a seqlock
- **Model/UI:** The Python motor model and UI are part of the current simulator integration; closed-loop current and speed control remain future phases

See `docs/superpowers/specs/2026-09-04-foc-open-loop-design.md` for the full architecture and `docs/superpowers/plans/2026-09-04-foc-pru-firmware.md` for the incremental task breakdown (A0–A6).
