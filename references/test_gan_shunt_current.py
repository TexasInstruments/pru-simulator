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
