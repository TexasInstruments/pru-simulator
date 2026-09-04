# foc_open_loop — Open-Loop Field-Oriented Control (Level 1)

Single-core **PRU0** firmware that implements open-loop FOC: accepts target speed and
current references from a shared-memory control block, runs a pipeline of RC (ramp control),
RG (ramp generator), sin/cos LUT lookup, inverse Park, and SVPWM (SVGEN), and outputs
three-phase PWM duty cycles.

* **Clock rate:** 200 MHz (single PRU, AM243x profile)
* **Fixed point:** Q24 (signed 1.7.24; `0x01000000` = 1.0) for per-unit voltages, currents, and duties
* **Phase accumulator:** 32-bit for electrical angle (natural wrap = one electrical revolution)
* **Sine LUT:** 2048-entry Q24 table, host-seeded at load; index = `theta_acc >> 21`; cosine via quarter-table offset (+512 entries, mod 2048)
* **Pipeline:** Config handshake → RC → RG(θ) → sin/cos lookup → inverse Park (Vα, Vβ) → SVGEN (Ta, Tb, Tc), all published under seqlock
* **Broadside MAC:** Device 0, operands R28/R29, 64-bit product R26:R27; Q48→Q24 renormalization by `>>24`

## Shared-memory map (ABI)

| Base Address | Section | Written by | Contents |
|---|---|---|---|
| `0x00010000` | `control` | Host | enable, requested_generation, pru_ack_generation, speed_ref_q24, id_ref_q24, iq_ref_q24, ramp_rate_q24 |
| `0x00010100` | `pwm_out` | PRU0 | seqlock, ta_q24, tb_q24, tc_q24, valpha_q24, vbeta_q24, theta_cmd_u32, loop_counter, timestamp_cycles |
| `0x00010200` | `motor_fb` | (reserved) | Planned Phase 2: motor feedback (currents, rotor angle, speed) |
| `0x00011000` | `sine_lut` | Host (seed at load) | 2048 × u32 Q24 sine samples (8 KB) |

The ABI is schema-generated from `schema/foc_abi.json` via `tools/gen_foc_abi.py`, producing `source/foc_abi.inc`, `pru_io/foc_abi.py`, and `include/foc_abi.h`.

## Files

| File | Purpose |
|------|---------|
| `foc_open_loop.asm` | PRU0 firmware (open-loop pipeline) |
| `README.md` | this file |
| `PROJECT_REPORT.md` | detailed project documentation |

## Run it in the simulator

### Automated cross-check tests

```bash
python -m pytest tests/test_foc_firmware.py -q
```

**9 tests** verify the firmware's pipeline, one stage at a time, against pure-Python reference implementations:

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

## Design notes

This is **Phase 1 (open loop only)**. 

- **No current feedback:** IdRef and IqRef are fixed references that set the rotating voltage-vector magnitude
- **Speed control:** SpeedRef (slewed by a configurable ramp) sets the angular frequency
- **Shared-memory interface:** All references are staged through the `control` block with generation matching; results are published to `pwm_out` under a seqlock
- **Roadmap:** Phase 2 will add a Python motor model, motor-feedback loop, and UI visualization

See `docs/superpowers/specs/2026-09-04-foc-open-loop-design.md` for the full architecture and `docs/superpowers/plans/2026-09-04-foc-pru-firmware.md` for the incremental task breakdown (A0–A6).
