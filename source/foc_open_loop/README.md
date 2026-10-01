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
* **Broadside MAC:** Device 0, operands R28/R29, 64-bit product R26:R27; validation reads the full Q48 result, while bounded hot-path products use an 8-bit prescaled operand and the upper word for Q24

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
| `tools/profile_foc.py` | assembled stage and region timing profiler |
| `tools/benchmark_q24.py` | multiply-only versus MAC Q24 experiment |

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

### Open-loop cycle budget

The reproducible profiler runs the real assembled firmware with timer acceleration disabled:

```bash
python -m tools.profile_foc --clock 200 --updates 20 \
  --case nominal --case reverse --case settled --case ramp_clamped \
  --case boundary --case invalid --case disabled
```

At 200 MHz the measured valid-reference path is:

| Region | Cycles | Instructions | Stalls | Time at 200 MHz |
|---|---:|---:|---:|---:|
| Configuration adoption (not every update) | 62 | 46 | 16 | 0.310 us |
| Enable/input boundary | 4 | 2 | 2 | 0.020 us |
| Control computation | 117 | 110 | 7 | 0.585 us |
| Deadline bookkeeping | 11 | 8 | 3 | 0.055 us |
| PWM publication | 30 | 17 | 13 | 0.150 us |
| Active path, excluding deadline wait | 162 | 137 | 25 | 0.810 us |

The profiler also reports sequential stage boundaries inside the 117-cycle computation. The
200 MHz valid-reference measurement is:

| FOC stage | Cycles / IEP ticks | Instructions | Stalls | Time |
|---|---:|---:|---:|---:|
| Entry / validation | 5 / 5 | 2 | 3 | 0.025 us |
| Ramp | 10 / 10 | 10 | 0 | 0.050 us |
| Phase accumulator | 11 / 11 | 11 | 0 | 0.055 us |
| Sine lookup | 5 / 5 | 3 | 2 | 0.025 us |
| Cosine lookup | 6 / 6 | 4 | 2 | 0.030 us |
| Inverse Park | 34 / 34 | 34 | 0 | 0.170 us |
| Inverse Clarke | 17 / 17 | 17 | 0 | 0.085 us |
| SVPWM common mode | 13 / 13 | 13 | 0 | 0.065 us |
| SVPWM duty offset | 6 / 6 | 6 | 0 | 0.030 us |
| Duty clamp / status | 10 / 10 | 10 | 0 | 0.050 us |
| **Computation total** | **117 / 117** | **110** | **7** | **0.585 us** |

The sine and cosine stages are Q24 LUT index calculation plus memory reads; they are not runtime
calls to `sin()` or `cos()`. At 250 and 300 MHz, the same 117 cycles/IEP ticks take 0.468 us and
0.390 us respectively.

The strict below-200-cycle target applies to the computation region, so the optimized open-loop
kernel is 82 cycles below that target on the normal path. Independently taking the longest
negative or upper clamp branch for each phase produces a conservative 123-cycle valid bound,
still 76 cycles below the target. The full active path is deliberately reported separately;
it includes input, deadline, and publication work. At 250 and 300 MHz the same kernel remains
117 computation cycles and 162 active cycles, taking 0.468/0.390 us and 0.648/0.540 us
respectively. The 100 kHz control period is 2,000/2,500/3,000 IEP ticks at those clocks.

The multiply-only MAC mode is configured once at firmware startup. Products use the R28/R29
operand registers. The valid open-loop path prescales bounded unsigned magnitudes by eight bits
and reads the upper product word, which is equivalent to `(a*b) >> 24` without an explicit shift.
Each signed product is still truncated before the inverse-Park sums, preserving the existing Q24
contract. Adjacent inverse-Park products reuse the fixed R28/R29 operand slots; the cosine lookup
also writes directly to R29, removing the remaining initial cosine move. The phase scale and
sqrt(3)/2 constants use the same prescaled upper-word form. The phase-accumulator product now
has an explicit settling instruction before XIN. Voltage signs are cached in persistent R18
bytes during configuration adoption, and the valid path falls through the entry check. The
common-mode stage adds biased extrema directly because the two sign offsets cancel modulo 32
bits. The simulator's MAC implementation computes multiply-only results when XIN is read, so
this timing is simulator-verified and still requires TI-toolchain listing and hardware
measurement for a hardware guarantee.

The multiply-grouping experiment is reproducible with:

```bash
python -m tools.benchmark_q24 --samples 1000000
```

On the simulator, two raw Q48 products take 17 cycles with per-product conversion and 11 cycles
with one MAC accumulation and one final conversion. Using the current prescaled representation,
the corresponding measurements are 11 and 8 cycles. The accumulated result differs by one Q24
LSB in the selected fractional case, and 500,039 of 1,000,000 random signed pairs differ by up
to one LSB. The MAC accumulation is therefore faster in the isolated positive-magnitude test,
but it is not a drop-in replacement for this bit-exact signed FOC path: the hardware model
accumulates unsigned magnitudes and a mixed-sign pair needs separate sign handling.

The future closed-loop schedule, including speed PI, Clarke/Park, both current PIs, per-update
limiting, anti-windup, inverse Park, and SVPWM, is documented in `PROJECT_REPORT.md`. It is a
feasibility estimate only; no closed-loop controller is implemented in this phase.

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
