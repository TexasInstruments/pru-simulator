# Open-Loop FOC (Level 1) on a Single PRU — Design Spec

**Date:** 2026-09-04
**Status:** Draft for review
**Scope:** Phase 1 (open loop) only. Closed-loop levels are roadmap, not built.

## 1. Problem statement

Add a `pru-simulator` project that demonstrates **open-loop Field-Oriented Control** — the TI
"Level 1 Incremental System Build" — running on a **single PRU core (pru0)**, driven by a
**Python motor model**, and observable through a **new simulator UI tab** (reference inputs,
real-time plots, and an animated rotary rotor).

The attached `foc_pru_documentation.md` describes a full closed-loop torque controller, but its
assembly will not run on the simulator and is not the requested starting point:

- Uses instructions the simulator does not implement — `QGCT`, `QLT`, `QGT` are unrecognized and
  **silently no-op** (PC still advances), a correctness trap, not a load error.
- Mislabels the broadside MAC registers: it uses R25/R26 as operands, but the simulator's MAC
  (device 0) reads operands from **R28/R29** and returns the 64-bit product in **R26/R27**
  (confirmed in `source/mac_example.asm`).
- Reuses registers across pipeline steps in conflicting ways (e.g. R6 is both `I_alpha` and an
  integrator pointer).

We therefore implement the simpler open-loop path the user provided (RC → RG → inverse Park →
SVGEN → duty cycles) correctly against the simulator's real ISA.

## 2. Scope decisions

- **Open loop only.** No current feedback into the PRU. `IdRef`/`IqRef` are fixed references that
  set the rotating voltage-vector magnitude; `SpeedRef` (through a ramp) sets its angular
  frequency. The loop is **not** closed back to the PRU in this phase.
- **Sensored-from-model visualization.** The Python motor model integrates a PMSM plant from the
  applied duty cycles and produces rotor angle + phase currents. This drives the UI dial/plots
  only; it is not read by the firmware yet (but the `motor_fb` block is laid out so Phase 2 can).
- **Single PRU:** firmware runs on `pru0` (`dram_swap=False`, so PRU-local addresses equal global
  addresses — shared window at `0x00010000`).
- **Fixed point:** signed **Q24** (1.7.24; `0x0100_0000` = 1.0) for per-unit voltages, currents,
  duties. Angle uses a **32-bit phase accumulator** (natural wrap = one electrical revolution);
  top 11 bits index a 2048-entry sine LUT.
- **Reuse** `references/gan_shunt__current.py` constants (V_DC=48, POLE_PAIRS=4, RS, LS, KE) and its
  `inverse_park`/`inverse_clarke` helpers. Note it is a *fixed-speed feed-forward* reference, not a
  drop-in plant — the motor model adds a proper αβ electrical + mechanical integrator.

## 3. Architecture & data flow

```
UI tab ──foc_set_reference──▶ control block (shared) ──▶ PRU reads SpeedRef,IdRef,IqRef
                                                                     │
  PRU firmware (pru0): RC → RG(θ) → sin/cos LUT → IPARK(Vα,Vβ) → SVGEN(Ta,Tb,Tc)
                                                                     │
                                      writes Ta,Tb,Tc,θ ──▶ pwm_out block (shared)
                                                                     │
  Python motor model (IEP-clocked): reads duties ──▶ PMSM αβ + mechanical integrator
                                      writes Ia/Ib/Ic, rotor θ, speed ──▶ motor_fb block
                                                                     │
  UI tab reads motor_fb + pwm_out over WebSocket ──▶ rotary dial + live current/duty plots
```

### 3.1 Shared-memory ABI (schema-generated)

`schema/foc_abi.json` is the single source of truth; `tools/gen_foc_abi.py` (clone of
`tools/gen_ssi_abi.py`) emits `source/foc_abi.inc`, `pru_io/foc_abi.py`, `include/foc_abi.h`.
Never hand-edit the generated files. All sections live in the 64 KiB shared window
(`ICSS_SHARED_BASE = 0x00010000`).

| Section | Base | Written by | Read by | Fields |
|---|---|---|---|---|
| `control` | 0x0000 | UI/host | PRU | `abi_version`, `struct_size`, `enable` u32, `requested_generation`, `pru_ack_generation`, `speed_ref_q24`, `id_ref_q24`, `iq_ref_q24`, `ramp_rate_q24` |
| `pwm_out` | 0x0100 | PRU | model, UI | `seq` (seqlock), `ta_q24`, `tb_q24`, `tc_q24`, `valpha_q24`, `vbeta_q24`, `theta_cmd_u32`, `loop_counter`, `timestamp_cycles` u64 |
| `motor_fb` | 0x0200 | model | UI (PRU in Phase 2) | `seq`, `ia_q24`, `ib_q24`, `ic_q24`, `id_meas_q24`, `iq_meas_q24`, `rotor_theta_u32`, `speed_rpm_q24`, `timestamp` |
| `sine_lut` | 0x1000 | host (seed at load) | PRU | 2048 × u32 Q24 sine samples (8 KB) |

- **Config handshake** (mirrors SSI config): UI stages references, bumps `requested_generation`;
  the PRU copies references into registers and writes `pru_ack_generation` when applied.
- **Seqlock** on `pwm_out`/`motor_fb`: `seq` odd while writing, even when stable; readers retry.

### 3.2 Sine LUT

2048-entry Q24 sine table seeded into shared mem by the host at load (PRU cannot cheaply generate
it). Index = `theta_acc >> 21`; cosine = `sin(index + 512)` (quarter-table offset, masked to 2048).
Phase 1 uses nearest-neighbor (≈0.088° step); MAC-based linear interpolation is an optional
enhancement.

### 3.3 PRU pipeline (firmware)

TI DMC macro set, re-implemented against the real ISA. Q24 multiplies use the broadside MAC:
`mov r28,A` / `mov r29,B` / `xout 0,&r25,1` / `nop` / `xin 0,&r26,4`, then renormalize Q48→Q24 by
`>>24` across R26:R27.

1. **Init** — load shared base; wait on `requested_generation`; latch references; ack.
2. **RC (ramp control)** — slew `setpoint` toward `speed_ref` by ±`ramp_rate` (clamp). Add/sub +
   **explicit sign handling** (the sim's `QB*LT/GT` are unsigned).
3. **RG (ramp generator)** — `theta_acc += freq`, `freq = setpoint · SPEED_SCALE` (one MAC); 32-bit
   wrap = one revolution. Store `theta_cmd`.
4. **sin/cos LUT** — index/lookup as in 3.2.
5. **IPARK** — `Vα = Vd·cos − Vq·sin`, `Vβ = Vd·sin + Vq·cos` (Vd=IdRef, Vq=IqRef); 4 MACs.
6. **SVGEN (SVPWM)** — `Va=Vβ; Vb=−0.5·Vβ+(√3/2)·Vα; Vc=−0.5·Vβ−(√3/2)·Vα`; `Vcom=0.5·(max+min)`;
   `Tx = Vx − Vcom + 0.5`. MAC for √3/2; `MIN`/`MAX` for extremes; arithmetic-right-shift
   synthesized (`LSR` is logical only). Store Ta,Tb,Tc under seqlock; bump `loop_counter`; loop.

### 3.4 Python motor model — `pru_io/foc_motor_model.py`

`FocMotorModel(sim)`, modeled on `pru_io/ssi_position_producer.py`:
- Registers `sim.add_hard_reset_hook(self.reset)`; `start()`/`stop()` add/remove
  `sim.iep.add_counter_observer(self._on_iep_advanced)`; manual `advance_to`/`step` for tests.
- Per cadence tick: read `pwm_out` (seqlock) → `Vx = (Tx − 0.5)·V_DC` → inverse-Clarke → integrate:
  - Electrical (αβ): `di/dt = (V − Rs·i − e)/Ls`, `eα=−Ke·ωe·sinθe`, `eβ=Ke·ωe·cosθe`.
  - Mechanical: `Te = 1.5·P·Ke·iq`; `ω += (Te − B·ω − Tload)/J·dt`; `θmech += ω·dt`; `θe = P·θmech`.
  - `dt` from `iep_tick_hz`; constants reused from `gan_shunt`, plus `B`, `J`, `Tload`.
- Write `ia/ib/ic`, `id_meas/iq_meas`, `rotor_theta`, `speed_rpm` into `motor_fb` (seqlock).
- `state()` → JSON snapshot for UI/runtime.

### 3.5 Runtime + MCP

`pru_io/foc_runtime.py` `FocRuntime(sim)` (modeled on `ssi_runtime.py`) owns the model + LUT
seeding + staged/commit control writes; exposes `start/stop/step/set_reference/state`.
`Simulator.motor_attach(...)` mirrors `uart_inject`/`ssi_inject`. MCP `pru_foc_inject`
(mirror `pru_uart_inject`) loads firmware + attaches model + runs + reads back `pwm_out`/`motor_fb`.

### 3.6 UI (FastAPI + WebSocket + vanilla JS + Canvas 2D)

- **Tab** auto-discovered: `<button id="tab-foc" role="tab" aria-controls="foc-view">` +
  `<section id="foc-view" class="app-view" role="tabpanel" hidden>`; extend `syncWorkspaceSubnav`.
- **Reference form** → `foc_set_reference`/`foc_start`/`foc_stop` WS actions.
- **Rotary dial**: `<canvas>` + `requestAnimationFrame` (`ctx.arc`/`ctx.rotate`), rotor needle at
  `motor_fb.rotor_theta`, commanded `pwm_out.theta_cmd` overlaid.
- **Plots** (reuse `drawMemGraph` rolling buffer): Ta/Tb/Tc duties, Ia/Ib/Ic currents, Vα/Vβ.
- Server emits `{"type":"foc_state", control, pwm, fb}`; `onmessage` pushes to buffers + updates dial.

## 4. Testing strategy

1. **ABI drift** — `python tools/gen_foc_abi.py` + `tests/test_foc_abi_generated.py`.
2. **Firmware cross-check** (`tests/test_foc_open_loop.py`, 200 MHz via `conftest.py`): known
   (IdRef, IqRef, θ) → assert `pwm_out` Vα/Vβ and Ta/Tb/Tc match a NumPy reference within Q24
   tolerance; duties in [0,1]; common-mode identity; θ advances/wraps with SpeedRef.
3. **Co-sim loop**: attach model, set SpeedRef, run N steps → `rotor_theta` rotates, `speed_rpm`
   converges, currents bounded/sinusoidal.
4. **MCP smoke**: `pru_foc_inject` returns rotating duties + feedback.
5. **Manual UI**: `python ui/server.py` → Motor Control tab → set speed → rotor spins, saddle
   waveforms, sinusoidal currents.
6. Full suite: `python -m pytest -q`.

## 5. Files

| File | Purpose | New/Edit |
|---|---|---|
| `schema/foc_abi.json` | Shared-mem layout source of truth | New |
| `tools/gen_foc_abi.py` | ABI generator | New |
| `source/foc_abi.inc` | Generated PRU offsets | New (generated) |
| `pru_io/foc_abi.py` | Generated Python helpers | New (generated) |
| `include/foc_abi.h` | Generated C snapshot | New (generated) |
| `source/foc_open_loop/foc_open_loop.asm` | PRU firmware | New |
| `source/foc_open_loop/README.md`, `PROJECT_REPORT.md` | Project docs | New |
| `pru_io/foc_motor_model.py` | PMSM plant model | New |
| `pru_io/foc_runtime.py` | Runtime wrapper | New |
| `simulator.py` | `motor_attach(...)` | Edit |
| `mcp_server/server.py` | `pru_foc_inject` | Edit |
| `ui/server.py` | `foc_*` WS handlers + `_foc_state` | Edit |
| `ui/static/index.html` | FOC tab markup + CSS | Edit |
| `ui/static/app.js` | FOC actions, `foc_state`, dial/plots | Edit |
| `tests/test_foc_open_loop.py`, `tests/test_foc_abi_generated.py` | Tests | New |

## 6. Roadmap (future phases, not built here)

- **L2+**: forward Clarke + Park reading `motor_fb` currents.
- **L3+**: dual PI current loops (torque control) replacing fixed IdRef/IqRef.
- **L4+**: outer speed PI loop (SpeedRef → Iq_set), loop closed through shared memory.
