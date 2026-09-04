# foc_open_loop — Project Report

Open-loop Field-Oriented Control (Level 1) on a single PRU core (pru0), with firmware validated against pure-Python cross-checks. This Phase 1 build establishes the shared-memory ABI and pipeline correctness; Phase 2 (motor model, UI, closed-loop feedback) is roadmap.

## 1. Initial prompt

User request (2026-09-04):

"we are going to build a new project on the pru-simulator repo. follow all the guidelines for documentation on ...project-documentation.md i want you to check, fix and finish the implementation of a FOC on a pru on the pru simulator... pru-simulator should be able to simulate the motor features so the control can act, all must be on a single pru and the interface must be python baed, we need to add a new tab to the simulator ui... control outputs like pwm duty should go into shared memory, python motor model should read from there and give the current and position lectures... provide two plans for implementing... use superpowers and all the skills you need for this, follow agents.md as always"

Scoping refinement (same session):

"we are going to do first an easier implementation with an open loop, where we give a target speed with a ramp and just outputs the duty cycles to the python motor model."

## 2. Design choices and decisions

### 2.1 Phase 1 Scope: Open Loop Only

**Decision:** Implement firmware + ABI only. Motor model, MCP tools, and UI are deferred to Phase 2.

**Rationale:** The initial request was broad (firmware + motor model + UI in one go). Breaking it into smaller phases lets Phase 1 focus on firmware correctness, shared-memory ABI stability, and validation strategy before adding simulation and visualization layers. Phase 2 can then build on proven firmware behavior.

### 2.2 ISA Corrections vs. the Provided Reference

**Decision:** Rewrite the provided `foc_pru_documentation.md` firmware to use the simulator's actual ISA and correct MAC register mapping.

**Rationale:** The reference implementation used unimplemented instructions (`QGCT`, `QLT`, `QGT` are silently no-op in the sim, a correctness trap), mislabeled the broadside MAC as R25/R26 (actual: R28/R29 operands, R26/R27 result per `source/mac_example.asm`), and had register-reuse conflicts. Reimplemented all pipeline stages against the real ISA.

### 2.3 Q24 Fixed Point

**Decision:** Use signed Q24 (1.7.24; `0x01000000` = 1.0) for all per-unit values (voltages, currents, duties).

**Rationale:** Matches TI DMC macro set conventions; scales well with 32-bit MAC products (Q24 × Q24 = Q48, right-shift 24 bits to renormalize to Q24). Avoids floating-point and suits the simulator's integer-only registers. Provides ±8.0 pu range (sufficient for a ±48 V system normalized to ±1.0 pu).

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

**Rationale:** Future motor model and UI readers will access `pwm_out` asynchronously. A seqlock ensures they never see a torn update. Readers check `seq` before and after each read; if both are even and match, the snapshot is consistent.

### 2.8 Timestamp as Loop Counter Placeholder

**Decision:** Write `loop_counter` to both `timestamp_cycles` fields (low and high words).

**Rationale:** This single-PRU build has no hardware IEP cycle counter. The loop counter is monotonic and sufficient for motor model cadence tracking in Phase 2. (A true multi-core / multi-PRU setup would pull real cycle counts from the IEP.)

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

### 5.2 Unsigned Branch Predicates

`QBGT`, `QBLT`, and similar branch instructions in the simulator compare as unsigned. To implement signed ramp clamping, the firmware computes `|diff|` and compares that against a threshold, then applies the original sign to the step. This is encoded as a branchless sign-extraction-and-inversion sequence.

### 5.3 Arithmetic Right-Shift Synthesis

The simulator's `LSR` (logical shift right) is logical only, not arithmetic. For SVGEN's −0.5·Vβ term and the Vcom calculation (both requiring sign-extending right-shift by 1), the firmware synthesizes an arithmetic shift by OR'ing the LSR result with the sign bit of the operand.

### 5.4 LUT Index Masking

The firmware uses `& 0x7FF` (FOC_SINE_LUT_COUNT−1) to wrap the cosine offset `(index + 512)` back into [0, 2047]. This is safe because a 32-bit accumulator with a 21-bit shift ensures index ∈ [0, 2047] naturally; the offset wraps modulo 2048, reproducing quarter-wave symmetry correctly.

### 5.5 Constants and Scaling

- **FOC_SPEED_SCALE = 954**: An empirical scale factor. Phase 2 motor model will use this to convert normalized speed ref (±1.0 pu) to motor RPM. Documented as revisitable based on motor parameters.
- **CONST_ONE_HALF = 0x00800000**: Exactly 0.5 in Q24.
- **CONST_SQRT3_HALF ≈ 0x00DDB3D7**: `round(√3/2 · 2^24)` for SVPWM voltage reconstruction.

These constants are baked into the firmware; motor-specific tuning is deferred to Phase 2.

## 6. Roadmap (Phase 2 and beyond, not built here)

### Phase 2 deliverables (planned, not yet implemented)

- **Motor model** (`pru_io/foc_motor_model.py`): PMSM plant integrator (electrical + mechanical) that reads duty cycles from `pwm_out`, simulates dynamics, and writes currents + rotor angle to `motor_fb`
- **Runtime wrapper** (`pru_io/foc_runtime.py`): Lifecycle management (start/stop/step), reference staging, LUT seeding
- **MCP tool** (`pru_foc_inject`): Load firmware + model + run + return snapshots for scripting
- **UI tab** (Motor Control): Dashboard form (speed/Id/Iq refs), rotary dial, three-phase plots (duties, currents, voltages)
- **Validation:** Co-simulation tests; rotor angle and speed converge to expected values; currents are sinusoidal and bounded

### Phase 3+ (closed-loop control, not yet implemented)

- **L2+:** Forward Clarke + Park on `motor_fb` currents. Feedback path into the PRU.
- **L3+:** Dual PI current loops (d/q axis) replacing fixed IdRef/IqRef.
- **L4+:** Outer speed PI loop (SpeedRef → Iq_set via integrator), loop closed through shared memory.
