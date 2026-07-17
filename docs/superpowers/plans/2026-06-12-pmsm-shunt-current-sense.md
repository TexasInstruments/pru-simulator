# PMSM GaN Shunt Current Sensing Simulation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `references/gan_shunt__current.py` to simulate a full 3-phase PMSM driven by a GaN inverter with SVPWM, showing the low-side shunt current sensing signal chain for no-load and load conditions at 1000 RPM.

**Architecture:** Two-timescale simulation — a coarse pass (dt=100 ns, 3 electrical cycles) models the PMSM in the dq frame and converts to 3-phase currents; a fine pass (dt=10 ps, 50 ns window) zooms into a single GaN turn-on edge to show the inductive spike and RC filter response on the shunt. Two separate figures (no-load / load), each with phase currents on top and shunt voltage signal chain on bottom.

**Tech Stack:** Python 3, NumPy, Matplotlib

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `references/gan_shunt__current.py` | Rewrite | All simulation logic + plotting |
| `references/test_gan_shunt_current.py` | Create | Unit tests for helper functions + integration checks |

---

### Task 1: File skeleton — parameters and empty stubs

**Files:**
- Modify: `references/gan_shunt__current.py`

- [ ] **Step 1.1: Replace entire file with skeleton**

```python
import numpy as np
import matplotlib.pyplot as plt

# ── Hardware parameters ──────────────────────────────────────────────────────
V_DC       = 48.0        # DC bus voltage (V)
DT_COARSE  = 100e-9      # coarse timestep: 100 ns
DT_FINE    = 1e-11       # fine timestep: 10 ps
T_FINE     = 50e-9       # fine window: 50 ns

# Motor — surface-mount PMSM
POLE_PAIRS = 4
RPM        = 1000.0
F_ELEC     = RPM / 60.0 * POLE_PAIRS   # 66.67 Hz
OMEGA      = 2 * np.pi * F_ELEC         # electrical angular velocity (rad/s)
RS         = 0.5                         # stator resistance (Ohm)
LS         = 1e-3                        # stator inductance (H), Ld = Lq
KE         = 0.05                        # back-EMF constant (V·s/rad)
DIDT_GAN   = 2e9                         # GaN slew rate: 2 A/ns in SI

# Shunt sensing
R_SHUNT  = 0.005    # 5 mOhm
L_SHUNT  = 0.8e-9   # 0.8 nH ESL
R_FILTER = 100.0    # Ohm
C_FILTER = 22e-12   # 22 pF  (corner ~72 MHz)
TAU      = R_FILTER * C_FILTER

# Simulation conditions
IQ_NO_LOAD = 0.5   # A  — mostly reactive/magnetising
IQ_LOAD    = 10.0  # A  — torque-producing


def inverse_park(vd, vq, theta):
    """dq → αβ: standard inverse Park transform."""
    pass


def inverse_clarke(valpha, vbeta):
    """αβ → abc: amplitude-invariant inverse Clarke transform."""
    pass


def run_simulation(iq_ref):
    """Run coarse motor simulation + fine shunt signal-chain pass.

    Returns dict with keys:
        time_c, i_a, i_b, i_c  — coarse arrays
        time_f, v_ideal, v_real, v_filtered, i0  — fine arrays
    """
    pass


def plot_results(results, title):
    """Render 2-subplot figure: phase currents (top) + shunt voltage (bottom)."""
    pass


if __name__ == '__main__':
    no_load_results = run_simulation(IQ_NO_LOAD)
    plot_results(no_load_results, f'No-Load (Iq = {IQ_NO_LOAD} A)')

    load_results = run_simulation(IQ_LOAD)
    plot_results(load_results, f'Load (Iq = {IQ_LOAD} A)')

    plt.show()
```

- [ ] **Step 1.2: Verify file runs without error (stubs return None)**

```bash
cd c:\ti\industrial-automation-lab\Projects\pru_simulator
python references/gan_shunt__current.py
```
Expected: exits immediately with no output, no traceback.

- [ ] **Step 1.3: Commit**

```bash
git add references/gan_shunt__current.py
git commit -m "feat: add PMSM shunt simulation skeleton with parameters"
```

---

### Task 2: Implement `inverse_park` and `inverse_clarke`

**Files:**
- Modify: `references/gan_shunt__current.py`
- Create: `references/test_gan_shunt_current.py`

- [ ] **Step 2.1: Create test file**

```python
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pytest
from gan_shunt__current import inverse_park, inverse_clarke


def test_inverse_park_at_zero_angle():
    # At θ=0: α = Vd, β = Vq
    valpha, vbeta = inverse_park(10.0, 0.0, 0.0)
    assert abs(valpha - 10.0) < 1e-10
    assert abs(vbeta  -  0.0) < 1e-10


def test_inverse_park_at_90_degrees():
    # At θ=π/2: α = -Vq, β = Vd
    valpha, vbeta = inverse_park(0.0, 10.0, np.pi / 2)
    assert abs(valpha - (-10.0)) < 1e-10
    assert abs(vbeta  -   0.0 ) < 1e-10


def test_inverse_park_roundtrip():
    # Park followed by inverse Park should return the same vector
    from gan_shunt__current import inverse_park
    theta = 1.23
    vd, vq = 5.0, 8.0
    # Forward Park: Vd = Vα·cos + Vβ·sin, Vq = -Vα·sin + Vβ·cos
    valpha, vbeta = inverse_park(vd, vq, theta)
    vd_back = valpha * np.cos(theta) + vbeta * np.sin(theta)
    vq_back = -valpha * np.sin(theta) + vbeta * np.cos(theta)
    assert abs(vd_back - vd) < 1e-10
    assert abs(vq_back - vq) < 1e-10


def test_inverse_clarke_balanced():
    # Va + Vb + Vc = 0 for any (valpha, vbeta)
    va, vb, vc = inverse_clarke(10.0, 5.0)
    assert abs(va + vb + vc) < 1e-10


def test_inverse_clarke_alpha_passthrough():
    # Va = Vα (amplitude-invariant convention)
    va, vb, vc = inverse_clarke(7.0, 0.0)
    assert abs(va - 7.0) < 1e-10
```

- [ ] **Step 2.2: Run tests — expect FAIL**

```bash
cd c:\ti\industrial-automation-lab\Projects\pru_simulator
python -m pytest references/test_gan_shunt_current.py -v
```
Expected: 5 FAILED (inverse_park returns None)

- [ ] **Step 2.3: Implement transforms in `gan_shunt__current.py`**

Replace `inverse_park` and `inverse_clarke` stubs:

```python
def inverse_park(vd, vq, theta):
    """dq → αβ: standard inverse Park transform."""
    valpha = vd * np.cos(theta) - vq * np.sin(theta)
    vbeta  = vd * np.sin(theta) + vq * np.cos(theta)
    return valpha, vbeta


def inverse_clarke(valpha, vbeta):
    """αβ → abc: amplitude-invariant inverse Clarke transform."""
    va =  valpha
    vb = -0.5 * valpha + (np.sqrt(3) / 2.0) * vbeta
    vc = -0.5 * valpha - (np.sqrt(3) / 2.0) * vbeta
    return va, vb, vc
```

- [ ] **Step 2.4: Run tests — expect PASS**

```bash
python -m pytest references/test_gan_shunt_current.py -v
```
Expected: 5 PASSED

- [ ] **Step 2.5: Commit**

```bash
git add references/gan_shunt__current.py references/test_gan_shunt_current.py
git commit -m "feat: implement inverse Park and Clarke transforms with tests"
```

---

### Task 3: Implement coarse motor simulation (dq-frame Euler pass)

**Files:**
- Modify: `references/gan_shunt__current.py`
- Modify: `references/test_gan_shunt_current.py`

The PMSM is modelled in the rotating dq frame where Id and Iq are DC quantities in steady state. This is mathematically equivalent to applying average SVPWM voltages — SVPWM ensures the average phase-to-neutral voltage equals the commanded reference voltage each PWM period, which is exactly what the dq model computes. Modelling in dq eliminates the need to track per-timestep PWM switching states for the motor integration while still capturing the correct fundamental current. Feed-forward with cross-coupling decoupling ensures Id → 0 and Iq → iq_ref with time constant LS/RS = 2 ms.

dq equations:
```
LS·dId/dt = Vd_cmd - RS·Id + ω·LS·Iq
LS·dIq/dt = Vq_cmd - RS·Iq - ω·LS·Id - Ke·ω
```

Feed-forward commands (exact decoupling):
```
Vd_cmd = -ω·LS·iq_ref   (cancels cross-coupling)
Vq_cmd =  RS·iq_ref + Ke·ω  (cancels back-EMF)
```

After decoupling the equations reduce to dId/dt = -RS·Id/LS and dIq/dt = RS·(iq_ref-Iq)/LS, driving Id→0 and Iq→iq_ref.

- [ ] **Step 3.1: Add tests for coarse pass**

Append to `references/test_gan_shunt_current.py`:

```python
from gan_shunt__current import run_simulation, IQ_NO_LOAD, IQ_LOAD


def test_coarse_no_load_peak_current():
    results = run_simulation(IQ_NO_LOAD)
    i_a = results['i_a']
    ss = i_a[2 * len(i_a) // 3:]          # last third = steady state
    peak = float(np.max(np.abs(ss)))
    assert 0.3 < peak < 0.8, f"Expected ~{IQ_NO_LOAD} A peak, got {peak:.3f} A"


def test_coarse_load_peak_current():
    results = run_simulation(IQ_LOAD)
    i_a = results['i_a']
    ss = i_a[2 * len(i_a) // 3:]
    peak = float(np.max(np.abs(ss)))
    assert 8.0 < peak < 12.0, f"Expected ~{IQ_LOAD} A peak, got {peak:.3f} A"


def test_coarse_three_phases_balanced():
    results = run_simulation(IQ_NO_LOAD)
    ss = slice(2 * len(results['i_a']) // 3, None)
    # Sum of balanced 3-phase currents is zero at every point
    total = results['i_a'][ss] + results['i_b'][ss] + results['i_c'][ss]
    assert np.max(np.abs(total)) < 0.05, "Phase currents not balanced"
```

- [ ] **Step 3.2: Run new tests — expect FAIL**

```bash
python -m pytest references/test_gan_shunt_current.py::test_coarse_no_load_peak_current -v
```
Expected: FAILED (run_simulation returns None)

- [ ] **Step 3.3: Implement coarse pass in `run_simulation`**

Replace `run_simulation` stub with (fine pass will be added in Task 4):

```python
def run_simulation(iq_ref):
    # Feed-forward with cross-coupling decoupling
    vd_cmd = -OMEGA * LS * iq_ref
    vq_cmd =  RS * iq_ref + KE * OMEGA

    # Coarse time array: 3 electrical cycles
    n_coarse = int(3.0 / F_ELEC / DT_COARSE)   # ~450 000 steps
    time_c   = np.arange(n_coarse) * DT_COARSE

    id_arr = np.zeros(n_coarse)
    iq_arr = np.zeros(n_coarse)

    for k in range(1, n_coarse):
        id_k = id_arr[k - 1]
        iq_k = iq_arr[k - 1]
        did_dt = (vd_cmd - RS * id_k + OMEGA * LS * iq_k) / LS
        diq_dt = (vq_cmd - RS * iq_k - OMEGA * LS * id_k - KE * OMEGA) / LS
        id_arr[k] = id_k + DT_COARSE * did_dt
        iq_arr[k] = iq_k + DT_COARSE * diq_dt

    # dq → αβ → abc
    theta_e = OMEGA * time_c
    ialpha =  id_arr * np.cos(theta_e) - iq_arr * np.sin(theta_e)
    ibeta  =  id_arr * np.sin(theta_e) + iq_arr * np.cos(theta_e)
    i_a =  ialpha
    i_b = -0.5 * ialpha + (np.sqrt(3) / 2.0) * ibeta
    i_c = -0.5 * ialpha - (np.sqrt(3) / 2.0) * ibeta

    # Fine pass placeholder — completed in Task 4
    n_fine    = int(T_FINE / DT_FINE)
    time_f    = np.arange(n_fine) * DT_FINE
    v_ideal   = np.zeros(n_fine)
    v_real    = np.zeros(n_fine)
    v_filtered = np.zeros(n_fine)
    i0        = 0.0

    return {
        'time_c':    time_c,
        'i_a':       i_a,
        'i_b':       i_b,
        'i_c':       i_c,
        'time_f':    time_f,
        'v_ideal':   v_ideal,
        'v_real':    v_real,
        'v_filtered': v_filtered,
        'i0':        i0,
    }
```

- [ ] **Step 3.4: Run coarse tests — expect PASS**

```bash
python -m pytest references/test_gan_shunt_current.py::test_coarse_no_load_peak_current references/test_gan_shunt_current.py::test_coarse_load_peak_current references/test_gan_shunt_current.py::test_coarse_three_phases_balanced -v
```
Expected: 3 PASSED (note: each call takes ~3–5 s due to the Python loop)

- [ ] **Step 3.5: Commit**

```bash
git add references/gan_shunt__current.py references/test_gan_shunt_current.py
git commit -m "feat: implement coarse dq-frame PMSM motor simulation with tests"
```

---

### Task 4: Implement fine pass (event extraction + shunt signal chain)

**Files:**
- Modify: `references/gan_shunt__current.py`
- Modify: `references/test_gan_shunt_current.py`

The fine pass takes the peak steady-state phase-A current (I0) as the turn-on target. It models the low-side FET conducting: current ramps from 0 → I0 at the GaN-limited slew rate (2 A/ns), then holds. The shunt and its ESL produce the characteristic inductive spike that the RC filter must attenuate.

- [ ] **Step 4.1: Add fine-pass tests**

Append to `references/test_gan_shunt_current.py`:

```python
def test_fine_pass_spike_exceeds_ideal():
    results = run_simulation(IQ_NO_LOAD)
    assert np.max(results['v_real']) > np.max(results['v_ideal']), \
        "L·di/dt spike should exceed ideal resistive voltage"


def test_fine_pass_filter_attenuates_spike():
    results = run_simulation(IQ_NO_LOAD)
    assert np.max(results['v_filtered']) < np.max(results['v_real']), \
        "RC filter should reduce peak voltage"


def test_fine_pass_i0_matches_iq_ref():
    results = run_simulation(IQ_LOAD)
    assert 8.0 < results['i0'] < 12.0, \
        f"i0 should be near IQ_LOAD, got {results['i0']:.2f}"
```

- [ ] **Step 4.2: Run new tests — expect FAIL**

```bash
python -m pytest references/test_gan_shunt_current.py::test_fine_pass_spike_exceeds_ideal -v
```
Expected: FAILED (v_real is zeros)

- [ ] **Step 4.3: Replace fine-pass placeholder inside `run_simulation`**

Replace the block from `# Fine pass placeholder` to the end of `run_simulation`:

```python
    # Event extraction: peak phase-A current in steady state
    ss_start = 2 * n_coarse // 3
    i0 = float(np.max(np.abs(i_a[ss_start:])))

    # Fine pass: GaN low-side turn-on — current ramps 0 → i0 at DIDT_GAN
    n_fine  = int(T_FINE / DT_FINE)
    time_f  = np.arange(n_fine) * DT_FINE
    t_rise  = i0 / DIDT_GAN                        # time for 0 → i0 ramp

    i_fine  = np.where(time_f < t_rise, DIDT_GAN * time_f, i0)
    di_dt_f = np.gradient(i_fine, DT_FINE)

    v_ideal    = i_fine * R_SHUNT
    v_real     = i_fine * R_SHUNT + L_SHUNT * di_dt_f

    v_filtered = np.zeros(n_fine)
    for k in range(1, n_fine):
        v_filtered[k] = (v_filtered[k - 1]
                         + (DT_FINE / TAU) * (v_real[k - 1] - v_filtered[k - 1]))

    return {
        'time_c':    time_c,
        'i_a':       i_a,
        'i_b':       i_b,
        'i_c':       i_c,
        'time_f':    time_f,
        'v_ideal':   v_ideal,
        'v_real':    v_real,
        'v_filtered': v_filtered,
        'i0':        i0,
    }
```

- [ ] **Step 4.4: Run all tests — expect all PASS**

```bash
python -m pytest references/test_gan_shunt_current.py -v
```
Expected: all PASSED

- [ ] **Step 4.5: Commit**

```bash
git add references/gan_shunt__current.py references/test_gan_shunt_current.py
git commit -m "feat: implement fine-pass GaN shunt signal chain with tests"
```

---

### Task 5: Implement `plot_results`

**Files:**
- Modify: `references/gan_shunt__current.py`

- [ ] **Step 5.1: Replace `plot_results` stub**

```python
def plot_results(results, title):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))
    fig.suptitle(title, fontsize=13, fontweight='bold')

    # ── Subplot 1: phase currents (last 2 electrical cycles) ─────────────────
    time_c = results['time_c']
    t_show = 2.0 / F_ELEC
    mask   = time_c >= (time_c[-1] - t_show)
    t_ms   = time_c[mask] * 1e3

    ax1.plot(t_ms, results['i_a'][mask], label='Phase A')
    ax1.plot(t_ms, results['i_b'][mask], label='Phase B')
    ax1.plot(t_ms, results['i_c'][mask], label='Phase C')
    ax1.set_xlabel('Time (ms)')
    ax1.set_ylabel('Current (A)')
    ax1.set_title(
        f'3-Phase Currents — {RPM:.0f} RPM, {POLE_PAIRS} pole-pairs, '
        f'I₀ = {results["i0"]:.2f} A'
    )
    ax1.legend()
    ax1.grid(True)

    # ── Subplot 2: shunt voltage at GaN turn-on edge ─────────────────────────
    t_ns = results['time_f'] * 1e9

    ax2.plot(t_ns, results['v_ideal']    * 1e3, 'g--',
             label=r'Ideal ($I \cdot R$)')
    ax2.plot(t_ns, results['v_real']     * 1e3, 'r',
             label=r'Real ($I \cdot R + L \cdot di/dt$)')
    ax2.plot(t_ns, results['v_filtered'] * 1e3, 'b',
             label='RC-Filtered (ADC input)')
    ax2.set_xlabel('Time (ns)')
    ax2.set_ylabel('Voltage (mV)')
    ax2.set_title(
        f'Phase A Low-Side Shunt — GaN Turn-On Edge '
        f'(I₀ = {results["i0"]:.2f} A, di/dt = {DIDT_GAN/1e9:.0f} A/ns)'
    )
    ax2.legend()
    ax2.grid(True)

    plt.tight_layout()
```

- [ ] **Step 5.2: Smoke-test plot generation (non-interactive)**

```bash
cd c:\ti\industrial-automation-lab\Projects\pru_simulator\references
python -c "
import matplotlib; matplotlib.use('Agg')
from gan_shunt__current import run_simulation, plot_results, IQ_NO_LOAD
import matplotlib.pyplot as plt
r = run_simulation(IQ_NO_LOAD)
plot_results(r, 'Test')
plt.savefig('test_plot.png')
print('Plot OK — peak v_real:', round(max(r['v_real'])*1e3, 2), 'mV')
"
```
Expected output: `Plot OK — peak v_real:` followed by a non-zero mV value (the L·di/dt spike).

- [ ] **Step 5.3: Commit**

```bash
git add references/gan_shunt__current.py
git commit -m "feat: implement plot_results with phase currents and shunt voltage subplots"
```

---

### Task 6: End-to-end run and final verification

**Files:**
- No changes (wiring already in `__main__` block from Task 1)

- [ ] **Step 6.1: Run full test suite**

```bash
cd c:\ti\industrial-automation-lab\Projects\pru_simulator
python -m pytest references/test_gan_shunt_current.py -v
```
Expected: all 11 tests PASSED

- [ ] **Step 6.2: Run the simulation end-to-end with display**

```bash
python references/gan_shunt__current.py
```
Expected: two matplotlib windows appear in sequence.

Verify visually:
- **No-load figure (top subplot):** sinusoidal 3-phase currents at ~0.5 A peak, ~66.7 Hz
- **No-load figure (bottom subplot):** small spike on `v_real` (proportional to 0.5 A × 2 A/ns); RC filter attenuates it
- **Load figure (top subplot):** same shape, ~10 A peak
- **Load figure (bottom subplot):** larger spike (10× vs no-load); filter more visibly lags

- [ ] **Step 6.3: Final commit**

```bash
git add references/gan_shunt__current.py references/test_gan_shunt_current.py
git commit -m "feat: complete PMSM GaN shunt current sensing simulation

3-phase PMSM at 1000 RPM driven by GaN inverter with SVPWM.
Two-timescale model: dq-frame Euler (coarse) + GaN edge zoom (fine).
Separate figures for no-load (0.5 A) and load (10 A) conditions.
Shows ideal vs real shunt voltage and RC-filter response."
```
