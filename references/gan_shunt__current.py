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
    valpha = vd * np.cos(theta) - vq * np.sin(theta)
    vbeta  = vd * np.sin(theta) + vq * np.cos(theta)
    return valpha, vbeta


def inverse_clarke(valpha, vbeta):
    """αβ → abc: amplitude-invariant inverse Clarke transform."""
    va =  valpha
    vb = -0.5 * valpha + (np.sqrt(3) / 2.0) * vbeta
    vc = -0.5 * valpha - (np.sqrt(3) / 2.0) * vbeta
    return va, vb, vc


def run_simulation(iq_ref):
    """Run coarse motor simulation + fine shunt signal-chain pass.

    Returns dict with keys:
        time_c, i_a, i_b, i_c  — coarse arrays
        time_f, v_ideal, v_real, v_filtered, i0  — fine arrays
    """
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


def plot_results(results, title):
    """Render 2-subplot figure: phase currents (top) + shunt voltage (bottom)."""
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


if __name__ == '__main__':
    no_load_results = run_simulation(IQ_NO_LOAD)
    plot_results(no_load_results, f'No-Load (Iq = {IQ_NO_LOAD} A)')

    load_results = run_simulation(IQ_LOAD)
    plot_results(load_results, f'Load (Iq = {IQ_LOAD} A)')

    plt.show()
