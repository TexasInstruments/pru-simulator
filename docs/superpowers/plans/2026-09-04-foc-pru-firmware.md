# Plan A — Open-Loop FOC PRU Firmware

**Date:** 2026-09-04
**Design spec:** `docs/superpowers/specs/2026-09-04-foc-open-loop-design.md`
**Target:** single PRU core `pru0`, AM243x profile, 200 MHz. Q24 fixed point, MAC device 0.
**Deliverable:** `source/foc_open_loop/foc_open_loop.asm` that reads references from the `control`
block, runs RC → RG → sin/cos → inverse Park → SVGEN, and writes duty cycles + angle to `pwm_out`.

Firmware is built and tested **one macro at a time** — each macro gets a firmware↔Python cross-check
before the next is added. This follows TDD and avoids the known failure mode of large one-shot `.asm`
rewrites (see memory `pru-asm-rewrites-stall-sonnet`).

> Prerequisite: `source/foc_abi.inc` must be generated first (owned by Plan B, task B1). This plan
> `.include`s it for all shared-memory offsets and bases.

## Tasks

### A0 — Skeleton + shared-memory access + config handshake
- [ ] Create `source/foc_open_loop/foc_open_loop.asm` with `.include "foc_abi.inc"`, constant
      `.set`s (`MAC_DEVICE_ID=0`, `CONST_ONE_HALF=0x0800000`, `CONST_SQRT3_HALF`, `LUT_SHIFT=21`,
      `LUT_MASK=0x7FF`, `SPEED_SCALE`), and a `main`/`foc_core_loop` structure.
- [ ] Load the shared-window base (`0x00010000`) into a base register once.
- [ ] Init: poll `control.requested_generation`; when it changes, `LBBO` the references
      (`speed_ref_q24`, `id_ref_q24`, `iq_ref_q24`, `ramp_rate_q24`, `enable`) into registers and
      `SBBO` `pru_ack_generation = requested_generation`.
- [ ] **Verify:** `tests/test_foc_firmware.py::test_config_handshake` — write a control block +
      generation, run until ack, assert `pru_ack_generation` matches and references are latched
      (observe via a scratch `SBBO` of a latched value or a register snapshot).

### A1 — MAC multiply helper (Q24 × Q24 → Q24)
- [ ] Establish the multiply sequence: `mov r28,A` / `mov r29,B` / `xout MAC_DEVICE_ID,&r25,1` /
      `nop` / `xin MAC_DEVICE_ID,&r26,4`, then renormalize Q48→Q24 (`>>24` across R26:R27) into a
      result register. Encode as a `.macro MUL_Q24 out, a, b`.
- [ ] **Verify:** `test_mul_q24` — feed known Q24 operands (incl. one negative), assert product
      within ±1 LSB of the NumPy reference. (Guards the signed-product path.)

### A2 — RC (ramp control) + RG (ramp generator) → theta
- [ ] RC: slew `setpoint` toward `speed_ref` by ±`ramp_rate`, clamping at target. Use explicit
      sign handling for the compare (sim `QB*LT/GT` are **unsigned**).
- [ ] RG: `freq = MUL_Q24(setpoint, SPEED_SCALE)`; `theta_acc += freq` (32-bit natural wrap).
- [ ] `SBBO` `theta_cmd = theta_acc` into `pwm_out`.
- [ ] **Verify:** `test_ramp_generates_rotating_theta` — nonzero SpeedRef, run N loops, assert
      `theta_cmd` advances monotonically and wraps; assert step size scales with SpeedRef; assert
      RC slews (no instantaneous jump to target).

### A3 — sin/cos from 2048-entry Q24 LUT
- [ ] index = `theta_acc >> LUT_SHIFT` (`& LUT_MASK`); `LBBO sin` from `sine_lut_base + index*4`.
- [ ] cosine = `sin((index + 512) & LUT_MASK)`.
- [ ] Nearest-neighbor for Phase 1. (Optional stretch: MAC-based linear interpolation using the
      low `LUT_SHIFT` bits as the fraction.)
- [ ] **Verify:** `test_sincos_lut` — seed the LUT (host helper), sweep theta across a revolution,
      assert returned sin/cos match `np.sin/np.cos` within Q24 + quantization tolerance and satisfy
      `sin²+cos² ≈ 1`.

### A4 — IPARK (inverse Park): Vα, Vβ
- [ ] `Vα = MUL_Q24(id_ref, cos) − MUL_Q24(iq_ref, sin)`;
      `Vβ = MUL_Q24(id_ref, sin) + MUL_Q24(iq_ref, cos)`.
- [ ] `SBBO` `valpha_q24`, `vbeta_q24` into `pwm_out`.
- [ ] **Verify:** `test_ipark` — for a grid of (IdRef, IqRef, θ), assert Vα/Vβ match
      `gan_shunt.inverse_park` within Q24 tolerance.

### A5 — SVGEN (SVPWM): Ta, Tb, Tc
- [ ] `Va = Vβ`; `Vb = −0.5·Vβ + MUL_Q24(CONST_SQRT3_HALF, Vα)`;
      `Vc = −0.5·Vβ − MUL_Q24(CONST_SQRT3_HALF, Vα)`. Synthesize arithmetic `>>1` for the −0.5
      terms (sign-extend; `LSR` is logical only).
- [ ] `Vmax = MAX(Va,Vb,Vc)`, `Vmin = MIN(...)` (register-compare `MIN`/`MAX`); `Vcom = (max+min)>>1`.
- [ ] `Tx = Vx − Vcom + CONST_ONE_HALF` for x∈{a,b,c}.
- [ ] `SBBO` under seqlock: set `seq` odd → write `ta/tb/tc_q24`, `theta_cmd`, `valpha/vbeta`,
      `timestamp_cycles`, `loop_counter` → set `seq` even. Then `JMP foc_core_loop`.
- [ ] **Verify:** `test_svgen` — assert Ta/Tb/Tc match a NumPy SVGEN reference within tolerance,
      all duties in [0,1], and the common-mode identity holds; `test_full_loop_pwm` — run a full
      revolution, assert the three duties trace the classic saddle waveforms (phase-shifted 120°).

### A6 — Firmware docs
- [ ] `source/foc_open_loop/README.md` — title line, specs (200 MHz, Q24, open-loop pipeline),
      pin/shared-mem map table, files table, "run it in the simulator" (multi-core / single-core /
      MCP / pytest). Use `source/ssi_reader_4mhz_12bit/README.md` as the format reference.
- [ ] `source/foc_open_loop/PROJECT_REPORT.md` — initial prompt, design decisions (why open loop
      first, ISA fixes vs the attached doc, Q24 choice, LUT seeding), files generated, testing,
      notable observations.

## Definition of done (Plan A)
- [ ] `python -m pytest tests/test_foc_firmware.py -q` green.
- [ ] Firmware loads with no parser errors on `pru0`; a full-revolution run produces valid,
      rotating SVPWM duties in `pwm_out`.
- [ ] No use of unsupported instructions (`QGCT`/`QLT`/`QGT`); MAC uses R28/R29 operands, R26/R27
      result.
