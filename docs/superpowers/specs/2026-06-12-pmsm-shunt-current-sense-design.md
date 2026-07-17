# PMSM GaN Shunt Current Sensing Simulation — Design Spec

**Date:** 2026-06-12  
**File:** `references/gan_shunt__current.py` (enhanced in-place)

---

## Goal

Extend the existing GaN shunt simulation to model a full 3-phase PMSM motor driven by a GaN inverter with SVPWM. Focus is the **current sensing signal chain**: what the low-side shunt amplifier actually sees at a GaN switching edge, for both no-load and load conditions at 1000 RPM.

---

## Hardware Parameters

### Motor (surface-mount PMSM)

| Parameter | Value |
|-----------|-------|
| Speed | 1000 RPM |
| Pole pairs | 4 |
| Electrical frequency | 66.7 Hz |
| Stator resistance Rs | 0.5 Ω |
| Stator inductance Ls | 1 mH (Ld = Lq) |
| Back-EMF constant Ke | 0.05 V·s/rad |
| No-load Iq reference | 0.5 A |
| Load Iq reference | 10 A |
| Id reference | 0 A (max torque/amp) |

### GaN Power Stage

| Parameter | Value |
|-----------|-------|
| DC bus voltage | 48 V |
| Switching frequency | 100 kHz |
| Dead time | 100 ns |
| di/dt at switching | 2 A/ns |

### Shunt Sensing (per phase, low-side)

| Parameter | Value |
|-----------|-------|
| Shunt resistance | 5 mΩ |
| Shunt ESL | 0.8 nH |
| Filter resistor | 100 Ω |
| Filter capacitor | 22 pF |
| Filter corner freq | ~72 MHz |

---

## Simulation Architecture

### Two-timescale approach

The motor electrical dynamics (66.7 Hz) and GaN switching artifacts (sub-ns edges) differ by ~1.5 billion in timescale. A single timestep cannot span both. The simulation runs two sequential passes.

#### Pass 1 — Coarse (motor dynamics)

- **Timestep:** dt = 100 ns
- **Duration:** 3 electrical cycles (~45 ms)
- **Purpose:** Generate realistic SVPWM-modulated phase currents

**SVPWM:**
- Fixed d/q references: Vd = 0, Vq = Rs·Iq_ref + Ke·ω (feed-forward)
- Inverse Park transform → Vα, Vβ
- Inverse Clarke → Va, Vb, Vc
- SVPWM sector detection + duty cycle calculation
- Per-phase switching state at each coarse timestep

**Motor electrical model (per phase, Euler forward):**
```
dI/dt = (V_phase(t) − V_bemf(t) − Rs·I(t)) / Ls
V_bemf_a = Ke·ω·sin(θ_e)
V_bemf_b = Ke·ω·sin(θ_e − 2π/3)
V_bemf_c = Ke·ω·sin(θ_e + 2π/3)
```

**Output:** Arrays `I_a`, `I_b`, `I_c` over time; `duty_a`, `duty_b`, `duty_c`

#### Event extraction

- Scan phase A for a low-side turn-on event near peak current (search around θ_e = 90°)
- Record: `I0` (current at event), `V_applied` (DC bus × duty cycle), timestep index

#### Pass 2 — Fine (shunt signal chain)

- **Timestep:** dt = 10 ps
- **Duration:** 50 ns window around extracted switching event
- **Purpose:** Show shunt voltage spike and RC filter response

**Current ramp (GaN edge):**
```
di/dt = 2 A/ns  (gate-drive limited, same as original script)
I(t) = I0 + di/dt · t  (linear ramp from initial current I0 at event)
```
The motor inductance sets the steady-state current; the GaN gate drive sets the switching edge slew rate. These are decoupled in this model.

**Shunt voltages:**
```
V_shunt_ideal = I · R_shunt
V_shunt_real  = I · R_shunt + L_shunt · dI/dt
V_filtered[i] = V_filtered[i-1] + (dt/τ) · (V_shunt_real[i-1] − V_filtered[i-1])
```

---

## Output

Two separate figures — one for no-load, one for load. Each figure has 2 subplots:

**Subplot 1 (top):** All 3 phase currents vs time over 2 electrical cycles (from coarse pass). Shows sinusoidal shape, SVPWM ripple, and amplitude difference between conditions.

**Subplot 2 (bottom):** Phase A shunt voltage over the 50 ns switching window (from fine pass):
- Green dashed: ideal V = I·R
- Red: real V = I·R + L·di/dt (shows inductive spike)
- Blue: RC-filtered output (what the ADC sees)

Figures titled "No-Load (Iq=0.5A)" and "Load (Iq=10A)" respectively.

---

## File Structure

Single file enhancement — `references/gan_shunt__current.py` rewritten in-place. No new files. Structure:

1. Parameters (constants block)
2. `run_simulation(iq_ref)` function — runs both passes, returns coarse arrays + fine arrays
3. `plot_results(results, title)` function — renders the 2-subplot figure
4. Main block: calls `run_simulation` twice (no-load, load), calls `plot_results` twice

---

## Non-Goals

- Closed-loop current control (PI controller) — fixed Vq feed-forward only
- Field weakening
- Thermal or mechanical model
- ADC sampling window / sample-and-hold timing
