# foc_open_loop — Project Report

Open-loop Field-Oriented Control (Level 1) on a single PRU core (pru0), with firmware validated against pure-Python cross-checks and an IEP-clocked Python plant. Closed-loop current and speed feedback remain future work.

## 0. Validation repair baseline (2026-09-07)

The simulator now includes the open-loop co-simulation integration that the original report
listed as future work. This remains open loop: Vd/Vq are voltage references, the speed command
sets electrical frequency, and the Python plant reports measured behavior without closing a
current or speed loop.

- Firmware pacing uses absolute IEP deadlines at 100 kHz and publishes true IEP timestamps.
- The plant integrates elapsed IEP time with the previously coherent PWM command held between
  publications. The old loop-counter-to-time conversion and nominal-time fallback are removed.
- Disabled output is neutral (0.5/0.5/0.5) and holds the command angle. Voltage vectors above
  1/sqrt(3) pu are rejected at the host boundary and faulted/clamped in firmware.
- The generated ABI is version 2 and includes the configured period, timestamp, ramped command,
  and saturation/deadline status fields.
- The runtime owns reset/session state, 100 us sample history, coherent seqlock reads, stop-at-
  boundary behavior, and clock/wall-time telemetry. The FOC WebSocket Start action applies the
  visible references and advances bounded PRU0 batches cooperatively.
- `memory.cfg` is intentionally configured for the local 200 MHz PRU/IEP profile.

### Historical repair verification

The remaining lifecycle, breakpoint, dial, and telemetry repairs were verified against the real
FOC assembly on 2026-09-07. The focused command

```bash
python -m pytest tests/test_foc_validation_repair.py tests/test_foc_open_loop.py \
  tests/test_foc_ui.py tests/test_foc_firmware.py tests/test_mcp_server.py -q
```

passed **76 tests in 52.16 s**. The explicit 200/250/300 MHz clock check passed **6 tests**;
the control periods were 2000, 2500, and 3000 IEP ticks respectively, with the measured control
cadence remaining approximately 100 kHz. JavaScript syntax checking, Python compilation, and
the diff whitespace check also passed. The full simulator suite passed **1521 tests with 2
expected failures in 159.24 s**.

The FOC WebSocket now publishes generic PRU core-state packets alongside plant telemetry at the
dashboard cadence. This keeps the Source / Disassembly PC highlight, registers, and instruction
counters synchronized with the executing assembly. The regression reproduces the prior absence
of core-state packets and verifies live nonzero instruction counts and PC movement.

The physical acceptance runner uses the actual assembled firmware, runtime, IEP clock, PWM
publications, and PMSM plant:

```bash
python tools/run_foc_acceptance.py --clock 200 \
  --case nominal_400 --case reference_change --case reverse_reset \
  --case high_valid_1000 --case insufficient_voltage --case zero_voltage_rest
```

| Case | Simulated time (s) | Wall (s) | Active sim/wall | Final-200 ms mean RPM | Ripple p-p RPM | Result |
|---|---:|---:|---:|---:|---:|---|
| 400 RPM, Vq=0.25 | 1.001779 | 162.168 | 0.006177x | 399.999 | 0.069 | settled |
| 200 -> 400 RPM | 1.001779 | 156.519 | 0.006400x | 399.999 | 0.071 | settled |
| -400 RPM, Vq=-0.25 | 1.001779 | 159.176 | 0.006294x | -399.999 | 0.069 | settled |
| 1000 RPM, Vq=0.5 | 2.003351 | 318.921 | 0.006282x | 999.999 | 0.627 | settled |
| 800 RPM, Vq=0.25 | 1.001365 | 177.213 | 0.005651x | 77.875 | 813.226 | synchronism lost |
| 400 RPM, Vq=0 | 0.201846 | 31.298 | 0.006449x | 0.000 | 0.000 | rotor stationary |

The table above is retained as the pre-optimization repair baseline. Current measurements are in
section 0.1 below.

The first four cases satisfy the final-200-ms speed criterion (within 1% of reference and below
2% peak-to-peak ripple) with no electrical-angle slip. The insufficient-voltage case reaches
179.97 degrees of angle error, ends near -100 RPM, and has 90.27% speed error; this is the
expected open-loop loss of synchronism, not a hidden clamp or closed-loop correction. In the
zero-voltage case the command angle advances while measured rotor speed remains zero; its angle
error is therefore not a meaningful synchronism pass criterion.

The dashboard browser automation could not be executed because no in-app browser session was
available (`agent.browsers.list()` returned an empty list). WebSocket behavior was exercised by
the FastAPI test client, and the dial/telemetry contracts were checked with Node/static tests.

The operating procedure and current acceptance scenarios are maintained in
`docs/superpowers/plans/2026-09-07-foc-validation-repair.md` and
`source/foc_open_loop/README.md`. The sections below preserve the historical Phase 1 design
record; their Phase 2 roadmap statements describe the state before this repair.

## 0.1 Open-loop optimization verification (2026-09-09)

This update optimizes only the existing open-loop arithmetic. It does not add speed or current
regulation, change motor parameters, decimate any loop, or alter simulator timekeeping.

### Implementation and boundary definition

- `l_input_load_start`, `l_computation_start`, `l_computation_end`, `l_deadline_start`, and
  `l_publication_start` are zero-cost labels. Configuration adoption is measured separately from
  the per-update computation region.
- Voltage magnitude validation now runs when a new reference generation is adopted. Positive
  Vd/Vq magnitudes are cached in the private gap at `CONTROL_BASE + 0x28` (the external control
  block ends at `0x28` and `pwm_out` starts at `0x100`). Every new generation is still validated;
  invalid vectors retain neutral duties and the invalid/saturated status.
- Multiply-only MAC mode is configured once at startup. The Q24 hot-path macros use R28/R29
  operands and read the upper product word after prescaling one bounded unsigned magnitude by
  eight bits. The validation branch still reads the complete Q48 result. R30 is no longer used
  as an arithmetic temporary.
- The signed phase-accumulator MAC read now has an explicit settling instruction. Voltage signs
  are cached in persistent R18 byte fields at generation adoption, and inverse Park reconstructs
  its full-width masks from that cache without modifying it.
- Inverse Park reuses the fixed R28/R29 operand slots across adjacent products, and the cosine
  lookup writes directly into R29. This removes four expanded register moves from the old
  four-product sequence while preserving individual signed-product truncation. The phase scale
  is loaded directly into R29 by its constant multiply macro. The common-mode stage no longer
  converts its two biased extrema back before the wrapped sum, and valid configuration falls
  through the entry check without an extra jump.
- The generation-zero handshake remains valid: firmware writes the all-ones acknowledgement
  sentinel at startup before comparing the host generation. This was caught by the focused suite
  after the first optimization pass.

The reproducible measurement command is:

```bash
python -m tools.profile_foc --clock 200 --updates 20 \
  --case nominal --case reverse --case settled --case ramp_clamped \
  --case boundary --case invalid --case disabled
```

It loads the real assembly, seeds the real LUT, disables timer acceleration, and reports the
minimum/maximum instruction count, modeled stalls, total cycles, and time for each boundary. It
also measures the disabled path and derives a conservative valid-path computation bound by
combining the fixed stage measurements with the independently longest clamp branch for each
phase.

The final focused verification command (including the real-assembly Q24 benchmark) passed **155
tests in 117.07 s**. `node tests/js/test_signal_graph_helpers.js`, Python compilation, and the
whitespace check also passed. The complete simulator suite then passed **1562 tests with 2
expected failures in 208.78 s**.

### Before and after timing

The established pre-optimization measurement was 277 active cycles excluding the intentional
deadline wait: 46 cycles for enable/vector validation, 10 for speed ramp, 180 for angle/LUT,
inverse transforms, SVPWM and clamping, 11 for deadline bookkeeping, and 30 for publication.
The optimized valid-reference path measured as follows over 20 nominal and reverse updates:

| Region | Before (cycles) | After (cycles) | Instructions | Stalls | Time at 200 MHz |
|---|---:|---:|---:|---:|---:|
| Configuration adoption (not every update) | included in boundary work | 62 | 46 | 16 | 0.310 us |
| Enable/input boundary | included in validation | 4 | 2 | 2 | 0.020 us |
| Control computation | 190 of the old active path | 117 | 110 | 7 | 0.585 us |
| Deadline bookkeeping | 11 | 11 | 8 | 3 | 0.055 us |
| PWM publication | 30 | 30 | 17 | 13 | 0.150 us |
| Active path, excluding deadline wait | 277 | 162 | 137 | 25 | 0.810 us |

The profiler now places zero-cost boundaries around every valid-reference computation stage and
cross-checks each interval against the simulated IEP counter. At 200 MHz, one PRU cycle equals
one IEP tick and 5 ns:

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

The sine and cosine values are not calculated with runtime `sin()`/`cos()` calls. The host seeds
the 2048-entry Q24 table; the PRU calculates the index and performs the two LUT reads. The
reported 5-cycle sine path and 6-cycle cosine path therefore include index arithmetic and memory
access stalls. At 250 and 300 MHz the cycle/tick counts remain unchanged while the times scale to
0.468 us and 0.390 us for the complete computation.

The requested strict computation budget is at most 199 cycles, so the normal open-loop path has
82 cycles of measured margin (199 - 117). Independently taking the longest negative or upper
clamp branch for each phase produces a conservative 123-cycle valid bound, leaving 76 cycles of
margin. The full active path is reported separately because it includes input, deadline, and
publication work. Representative signed, ramp-clamped, ramp-unclamped, quadrant, and
near-boundary valid cases measured 117 cycles; the invalid-vector path measured 11 cycles and the
disabled path measured 8 cycles. These are real expanded-instruction simulator measurements, not
static source counts.

Clock consistency was checked with the same profiler and with the parameterized real-assembly
runtime tests:

| PRU/IEP clock | Period ticks at 100 kHz | Computation | Active path | Computation time | Active time |
|---:|---:|---:|---:|---:|---:|
| 200 MHz | 2,000 | 117 cycles | 162 cycles | 0.585 us | 0.810 us |
| 250 MHz | 2,500 | 117 cycles | 162 cycles | 0.468 us | 0.648 us |
| 300 MHz | 3,000 | 117 cycles | 162 cycles | 0.390 us | 0.540 us |

The 200 MHz nominal, 250 MHz nominal, and 300 MHz nominal plant runs retained approximately
100 kHz cadence and settled to 399.9998 RPM at equal simulated time. Their measured simulation /
wall ratios were 0.010917x, 0.010412x, and 0.010447x respectively. The 200 MHz physical acceptance
run therefore advances about 10.92 ms of simulated time per wall second; this is host execution
throughput, not a change to the 100 kHz simulated control period.

### Numerical and physical acceptance

An old-assembly versus optimized-assembly harness ran 20 updates for nominal, reverse, mixed-sign,
and quadrant references through the actual simulator. Duties, V-alpha/V-beta, command angle,
ramped speed, loop counter, status, and speed command were identical (maximum field difference
zero). Publication timestamps moved earlier because the active path is shorter; cadence and plant
time were compared separately.

The following command uses the actual assembly, IEP clock, runtime, PWM publications, and PMSM
plant:

```bash
python tools/run_foc_acceptance.py --clock 200 \
  --case nominal_400 --case reference_change --case reverse_reset \
  --case high_valid_1000 --case insufficient_voltage --case zero_voltage_rest
```

| Case | Simulated end (s) | Wall (s) | Sim/wall | Final-200 ms mean RPM | Ripple p-p RPM | Max angle error | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| 400 RPM, Vd=0, Vq=0.25, 1000 RPM/s | 1.000165 | 91.619 | 0.010917x | 399.9998 | 0.0024 | 97.53 deg | settled |
| 200 -> 400 RPM | 1.000165 | 91.099 | 0.010979x | 399.9998 | 0.0025 | 97.53 deg | settled |
| -400 RPM, Vq=-0.25 | 1.000165 | 90.672 | 0.011031x | -399.9998 | 0.0024 | 97.72 deg | settled |
| 1000 RPM, Vq=0.5, 2 s | 2.000115 | 183.492 | 0.010901x | 1000.0003 | 0.0074 | 115.55 deg | settled |
| 800 RPM, Vq=0.25 | 1.003955 | 92.271 | 0.010881x | 77.986 | 813.203 | 179.94 deg | expected sync loss |
| Zero voltage from rest | 0.202425 | 18.303 | 0.011060x | 0.000 | 0.000 | 179.99 deg | command moves; rotor stationary |

The first four valid settled cases meet the final-200-ms requirements: speed error is below 1%,
ripple is below 2% peak-to-peak, and the maximum angle error remains below the 180-degree slip
threshold. The 800 RPM / 0.25 pu case honestly loses synchronism: the available open-loop
voltage cannot maintain the commanded torque/speed trajectory, so no implementation change was
made to conceal that physical limitation. For zero voltage, angle error is not a synchronism
criterion; the command angle advances while measured speed and rotor motion remain zero.

### Future closed-loop feasibility budget

This is a schedule for a separate future controller, not implemented code and not a tuned motor
design. It assumes coherent phase currents, measured electrical angle, and measured speed are
already available at entry. It uses a conventional discrete PI with precomputed per-sample
integral gains and conditional-integration anti-windup. Every PI runs on every update.

| Future stage | Required work | Worst-case products | Budgeted instructions | Budgeted cycles at 200 MHz |
|---|---|---:|---:|---:|
| Speed error and PI | error, Kp/Ki products, integrator, q-reference limit | 2 | 37 | 43 |
| Clarke | alpha copy, beta scaling and sums | 1 | 20 | 24 |
| Forward Park | four current/angle products and sums | 4 | 65 | 72 |
| d/q current PIs | two errors, Kp/Ki products, integrators, conditional anti-windup | 4 | 85 | 96 |
| Voltage limiting | two magnitude squares, bounded reciprocal-sqrt LUT, two rescale products, branch/status | 4 | 70 | 80 |
| Inverse Park + SVPWM | four products, common-mode calculation, duty clamp/status | 5 | 160 | 167 |
| **Worst-case total** | **all stages every update** | **20** | **437** | **482** |

The 482-cycle estimate is about 2.41 us at 200 MHz, leaving a deficit of at least 282 cycles
against a 200-cycle target. It is intentionally conservative and does not claim that closed-loop
control fits. The voltage-limiter row makes the assumption explicit: an exact radial limiter must
use a bounded reciprocal-sqrt table or an equivalent fixed-cost routine. Replacing it with a
variable-latency divide/sqrt would require a new budget and hardware measurement; it is not an
uncounted placeholder.

An illustrative register plan is: R1-R5 for base/deadline state; R6-R8 for speed error and
current references; R9-R13 for phase/current Clarke values; R14-R19 for angle, Park values, and
PI errors; R20-R24 for voltage magnitudes, alpha/beta values, and duty temporaries; R25-R29 for
MAC control, operands, and result; and R30/R31 reserved for hardware I/O. The three PI integrators
and gain constants require persistent local/shared-memory slots because the register file is full.
Every product must preserve the current Q24 truncation and provide one-cycle MAC settling time,
with independent sign preparation scheduled in that slot where possible.

### Toolchain and hardware limitations

`clpru` and `clpru-as` were not installed, so no TI-generated listing was available. No physical
AM243x board was available either. The timing result is therefore simulator-verified and
hardware-unverified. The simulator MAC model calculates a multiply-only result when XIN is read;
the assembly uses the documented example's delay discipline, but exact silicon latency and the
final optimized schedule still require TI-listing inspection and a hardware cycle-counter test.

The in-app browser runtime had no available sessions (`agent.browsers.list()` returned `[]`), so
Load/Start/Stop/Reset, refresh/reconnect, dial, chart, and speed-readout clicks were not executed
in a browser during this run. The corresponding runtime, WebSocket/MCP, UI-contract, JavaScript,
and real-assembly tests passed.

## 1. Initial prompt

User request (2026-09-04):

"we are going to build a new project on the pru-simulator repo. follow all the guidelines for documentation on ...project-documentation.md i want you to check, fix and finish the implementation of a FOC on a pru on the pru simulator... pru-simulator should be able to simulate the motor features so the control can act, all must be on a single pru and the interface must be python baed, we need to add a new tab to the simulator ui... control outputs like pwm duty should go into shared memory, python motor model should read from there and give the current and position lectures... provide two plans for implementing... use superpowers and all the skills you need for this, follow agents.md as always"

Scoping refinement (same session):

"we are going to do first an easier implementation with an open loop, where we give a target speed with a ramp and just outputs the duty cycles to the python motor model."

## 2. Design choices and decisions

### 2.1 Phase 1 Scope: Open Loop Only

**Historical decision:** The first implementation intentionally delivered firmware + ABI only. The validation repair now includes the motor model, MCP tool, and UI integration while keeping control open loop.

**Rationale:** The staged approach first established firmware correctness and ABI stability; the current runtime builds the plant and visualization on that contract.

### 2.2 ISA Corrections vs. the Provided Reference

**Decision:** Rewrite the provided `foc_pru_documentation.md` firmware to use the simulator's actual ISA and correct MAC register mapping.

**Rationale:** The reference implementation used unimplemented instructions (`QGCT`, `QLT`, `QGT` are silently no-op in the sim, a correctness trap), mislabeled the broadside MAC as R25/R26 (actual: R28/R29 operands, R26/R27 result per `source/mac_example.asm`), and had register-reuse conflicts. Reimplemented all pipeline stages against the real ISA.

### 2.3 Q24 Fixed Point

**Decision:** Use signed Q24 (1.7.24; `0x01000000` = 1.0) for all per-unit values (voltages, currents, duties).

**Rationale:** Matches TI DMC macro set conventions; scales well with 32-bit MAC products (Q24 × Q24 = Q48, right-shift 24 bits to renormalize to Q24). The validated full-width path performs that conversion explicitly; bounded hot-path products prescale one magnitude by eight bits and read the equivalent upper word. Avoids floating-point and suits the simulator's integer-only registers. Provides ±8.0 pu range (sufficient for a ±48 V system normalized to ±1.0 pu).

### 2.4 Signed Multiply via Branchless MAC

**Decision:** Implement `MUL_Q24` macro using two's-complement decomposition (sign + magnitude) on the unsigned MAC hardware.

**Rationale:** The simulator's MAC (device 0) performs unsigned multiply only. Negative operands feed in as huge positive magnitudes unless rewritten. The branchless path (mask = 0 / 0xFFFFFFFF; XOR/SUB for abs/negate) avoids branching and sidesteps the assembler's lack of macro-local labels, preventing collisions across multiple call sites. **Found and corrected during implementation:** the first `.macro` attempt used labeled jumps that collided; the branchless version was required to be correct.

### 2.5 Ramp Control with Explicit Sign Handling

**Decision:** Compare `|diff|` (unsigned magnitude) against `RAMP_RATE`, then apply the original sign to the step.

**Rationale:** The simulator's `QBGT`/`QBLT` branch predicates are unsigned. To clamp `setpoint` toward `speed_ref` by ±`ramp_rate`, we extract the sign of the difference, compute the magnitude, compare against `ramp_rate`, and reapply the sign — entirely branchless.

### 2.6 LUT Indexing and Cosine Offset

**Decision:** Use a 2048-entry sine table (2^11 entries). Index = `theta_acc >> 21`; cosine = `sin((index + 512) & 0x7FF)`.

**Rationale:** A 32-bit phase accumulator with 2048 entries gives a 21-bit shift (2^32 / 2^11 = 2^21). Cosine is 90 degrees away, so a quarter-table offset (512 entries = 2048 / 4) reproduces the quarter-wave symmetry. Nearest-neighbor (≈0.088° step) is acceptable for Phase 1; interpolation is an optional enhancement.

### 2.7 Seqlock for `pwm_out`

**Decision:** Bracket all duty cycle writes with a seqlock (odd while writing, even when stable).

**Rationale:** The Python motor model and UI read `pwm_out` asynchronously. A seqlock ensures they never see a torn update. Readers check `seq` before and after each read; if both are even and match, the snapshot is consistent.

### 2.8 Absolute IEP timestamps

**Decision:** Read the 64-bit IEP counter at the publication boundary and write it to
`timestamp_cycles`; retain `loop_counter` only as a publication sequence diagnostic.

**Rationale:** The configured IEP clock is the single simulation timebase used by firmware,
plant integration, charts, and telemetry. A missed absolute deadline is reported as a fault.

## 3. Files generated

| File | Purpose | Category |
|---|---|---|
| `schema/foc_abi.json` | Shared-mem layout source of truth (schema) | ABI |
| `tools/gen_foc_abi.py` | ABI generator (clone of gen_ssi_abi.py) | ABI |
| `source/foc_abi.inc` | Generated PRU offsets and constants | ABI (generated) |
| `pru_io/foc_abi.py` | Generated Python helpers (pack/unpack) | ABI (generated) |
| `include/foc_abi.h` | Generated C snapshot | ABI (generated) |
| `tests/test_foc_abi_generated.py` | ABI validation tests (9 tests) | Testing |
| `source/foc_open_loop/foc_open_loop.asm` | PRU0 firmware (RC, RG, sin/cos LUT, inverse Park, SVGEN) | Firmware |
| `pru_io/foc_motor_model.py` | IEP-clocked PMSM plant and timestamped sample history | Runtime |
| `pru_io/foc_runtime.py` | Lifecycle, validation, clock metadata, and coherent state | Runtime |
| `mcp_server/server.py` | FOC load/run/fault tool | Integration |
| `ui/server.py`, `ui/static/index.html`, `ui/static/app.js` | FOC WebSocket tab and visualizations | UI |
| `source/foc_open_loop/README.md` | Quick reference (specs, memory map, run instructions) | Documentation |
| `source/foc_open_loop/PROJECT_REPORT.md` | This file | Documentation |
| `tests/test_foc_firmware.py` | Firmware cross-check tests (9 tests) | Testing |

## 4. Testing

### 4.1 ABI validation tests

All tests in `tests/test_foc_abi_generated.py` verify that the schema-generated ABI is self-consistent and correct:

1. **test_generated_files_match_schema** — Regenerate from schema; verify output matches committed files
2. **test_sections_are_generated_from_schema** — All shared-mem sections present and named correctly
3. **test_all_shared_memory_sections_are_non_overlapping_and_in_64kib** — Sections fit within the shared window
4. **test_generator_rejects_global_section_overlap** — Generator catches overlaps at schema time
5. **test_generator_rejects_section_past_shared_memory_end** — Generator catches out-of-bounds sections
6. **test_control_round_trip** — Pack/unpack `control` block (generation handshake fields)
7. **test_pack_control_unknown_field_raises** — Reject invalid field names
8. **test_unpack_pwm_out** — Unpack `pwm_out` block (duty cycles, angle, counter)
9. **test_motor_fb_pack_unpack_round_trip** — Pack/unpack `motor_fb` block (Phase 2 placeholder)

**Verification:** `python -m pytest tests/test_foc_abi_generated.py -q` → **9 passed**

### 4.2 Firmware cross-check tests

All tests in `tests/test_foc_firmware.py` run against the real PRU assembler and simulator at 200 MHz, validating each pipeline stage incrementally against pure-Python references:

1. **test_config_handshake** — Control block handshake (generation matching, reference latch)
2. **test_mul_q24_positive_operands** — Q24 multiply with positive operands
3. **test_mul_q24_negative_operand** — Q24 multiply with one negative operand
4. **test_mul_q24_both_negative_operands** — Q24 multiply with both negative operands
5. **test_ramp_generates_rotating_theta** — RC slew logic, RG angle generation, monotonic advance, 32-bit wrap
6. **test_sincos_lut** — Sine/cosine lookup accuracy (sin² + cos² ≈ 1, matching LUT samples)
7. **test_ipark** — Inverse Park transformation (Vα = Id·cos − Iq·sin, Vβ = Id·sin + Iq·cos)
8. **test_svgen** — SVPWM duty generation, common-mode identity, duty bounds [0, 1]
9. **test_full_loop_pwm** — Full electrical revolution, three duties phase-shifted 120°, saddle waveforms

**Verification:** `python -m pytest tests/test_foc_firmware.py -q` → **9 passed**

Each test loads the firmware on pru0, sets up the shared-memory control block, seeds the sine LUT, and steps the simulator until the output is stable. Results are compared against known-good Python references (pure math.sin/cos, manual inverse Park + SVGEN from gain_shunt reference) with Q24 tolerance (±1 LSB, ≈±6e−8).

### 4.3 Full suite

```bash
python -m pytest tests/test_foc_abi_generated.py tests/test_foc_firmware.py -q
```

**18 tests passed** (9 ABI + 9 firmware).

## 5. Notable observations during implementation

### 5.1 Broadside MAC Sign Handling

The simulator's MAC is unsigned only. Negative Q24 operands must be decomposed into (sign, |magnitude|) before feeding to the MAC, then the sign must be reapplied post-multiply. A branchless implementation using two's-complement inversion (XOR/SUB with a sign mask) avoids branching and sidesteps the assembler's lack of macro-local label scoping.

The two-product accumulation experiment is recorded by `tools/benchmark_q24.py`. At the selected
fractional test point, raw multiply-only with a Q48-to-Q24 conversion after each product took
17 simulator cycles; MAC accumulation followed by one conversion took 11. The production
prescaled form measured 11 cycles for two multiply-only products and 8 for the corresponding
positive-magnitude MAC sum. The latter result was one Q24 LSB different because discarded
fractions were carried into the final conversion. Across 1,000,000 deterministic random signed
pairs, 500,039 accumulated results differed from the current per-product result, with a maximum
error of one LSB. Since the MAC accumulator only adds unsigned magnitudes, mixed-sign inverse-Park
terms also need separate sign paths. The faster isolated MAC result therefore remains an
experiment, not a replacement for the bit-exact production sequence.

### 5.2 Unsigned Branch Predicates

`QBGT`, `QBLT`, and similar branch instructions in the simulator compare as unsigned. To implement signed ramp clamping, the firmware computes `|diff|` and compares that against a threshold, then applies the original sign to the step. This is encoded as a branchless sign-extraction-and-inversion sequence.

### 5.3 Arithmetic Right-Shift Synthesis

The simulator's `LSR` (logical shift right) is logical only, not arithmetic. For SVGEN's −0.5·Vβ term and the Vcom calculation (both requiring sign-extending right-shift by 1), the firmware synthesizes an arithmetic shift by OR'ing the LSR result with the sign bit of the operand.

### 5.4 LUT Index Masking

The firmware uses `& 0x7FF` (FOC_SINE_LUT_COUNT−1) to wrap the cosine offset `(index + 512)` back into [0, 2047]. This is safe because a 32-bit accumulator with a 21-bit shift ensures index ∈ [0, 2047] naturally; the offset wraps modulo 2048, reproducing quarter-wave symmetry correctly.

### 5.5 Constants and Scaling

- **FOC_SPEED_SCALE = 2863312**: Q24 phase increment scale. At 100 kHz, 1.0 pu is 66.67 electrical revolutions/s, which is 1000 mechanical RPM for this four-pole-pair motor.
- **CONST_ONE_HALF = 0x00800000**: Exactly 0.5 in Q24.
- **CONST_SQRT3_HALF ≈ 0x00DDB3D7**: `round(√3/2 · 2^24)` for SVPWM voltage reconstruction.

The two multiply constants are embedded in the prescaled upper-word form used by the hot path:
`FOC_SPEED_SCALE << 8 = 0x2BB0D000` and `CONST_SQRT3_HALF << 8 = 0xDDB3D700`.
This removes an explicit Q48-to-Q24 shift from each product. Replacing either multiplication
with shift/add decomposition was not adopted: the measured MAC path is a single product plus
operand setup, while a variable shift/add sequence would require more operations and would need
additional signed-rounding proof. The exact half-scale terms already use shifts/sign-bit
construction, and the Q24 bounds are loaded once where their lifetime permits.

These constants are baked into the firmware; motor-specific tuning is deferred to Phase 2.

## 6. Roadmap (closed-loop control)

The current repair completes the open-loop plant, runtime, MCP, UI, timestamped history, and
fault-reporting scope. The remaining roadmap is closed-loop control; the historical Phase 2
items below are now implemented and retained as a record of the original project plan.

### Historical Phase 2 deliverables (implemented in the validation repair)

- **Motor model** (`pru_io/foc_motor_model.py`): PMSM plant integrator (electrical + mechanical) that reads duty cycles from `pwm_out`, simulates dynamics, and writes currents + rotor angle to `motor_fb`
- **Runtime wrapper** (`pru_io/foc_runtime.py`): Lifecycle management (start/stop/step), reference staging, LUT seeding
- **MCP tool** (`pru_foc_inject`): Load firmware + model + run + return snapshots for scripting
- **UI tab** (Motor Control): Dashboard form (speed/Id/Iq refs), rotary dial, three-phase plots (duties, currents, voltages)
- **Validation:** Co-simulation tests; rotor angle and speed converge to expected values; currents are sinusoidal and bounded

### Phase 3+ (closed-loop control, not yet implemented)

- **L2+:** Forward Clarke + Park on `motor_fb` currents. Feedback path into the PRU.
- **L3+:** Dual PI current loops (d/q axis) replacing fixed IdRef/IqRef.
- **L4+:** Outer speed PI loop (SpeedRef → Iq_set via integrator), loop closed through shared memory.
