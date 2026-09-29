# tests/test_sd_sweep.py
"""Sweep signal of the SD pattern generator (audio sweep, log or linear)."""
import math

import pytest
from pru_io.sd_modulator import SDModulator

CLK_MHZ = 3.072
RATE = CLK_MHZ * 1e6


def sweep(**kw):
    params = dict(signal="sweep", sd_clock_mhz=CLK_MHZ, amplitude=0.5,
                  f_start=20.0, f_stop=20000.0, duration_s=0.5, sweep_type="log")
    params.update(kw)
    return SDModulator(**params)


def test_defaults_are_a_log_sweep_20hz_to_20khz_in_half_a_second():
    mod = SDModulator()
    assert (mod.f_start, mod.f_stop, mod.duration_s, mod.sweep_type) == (20.0, 20000.0, 0.5, "log")


@pytest.mark.parametrize("kind, f1, f2", [("log", 20.0, 20000.0), ("linear", 0.0, 20000.0)])
def test_instantaneous_frequency_follows_the_formula(kind, f1, f2):
    mod = sweep(sweep_type=kind, f_start=f1, f_stop=f2)
    T = mod.duration_s
    assert mod.sweep_frequency(0.0) == pytest.approx(f1, abs=1e-9)
    assert mod.sweep_frequency(T) == pytest.approx(f2)
    mid = math.sqrt(f1 * f2) if kind == "log" else (f1 + f2) / 2
    assert mod.sweep_frequency(T / 2) == pytest.approx(mid)
    # The phase derivative is the instantaneous frequency (central difference).
    for t in (0.01, 0.2, 0.45):
        h = 1e-7
        dphi = (mod.sweep_phase(t + h) - mod.sweep_phase(t - h)) / (2 * h)
        assert dphi / (2 * math.pi) == pytest.approx(mod.sweep_frequency(t), rel=1e-5)


def test_input_is_the_sine_of_the_phase_then_silence():
    mod = sweep(duration_s=0.001)
    for idx in (0, 1000, 3000):
        mod._sample_index = idx
        t = idx / RATE
        assert mod._get_input() == pytest.approx(0.5 * math.sin(mod.sweep_phase(t)))
    mod._sample_index = int(0.001 * RATE) + 1
    assert mod._get_input() == 0.0
    assert mod.time_s() == pytest.approx((int(0.001 * RATE) + 1) / RATE)


def test_bit_density_follows_a_slow_sweep():
    # 100 -> 200 Hz linear over 10 ms at 3.072 MHz: average bits over 256-bit
    # blocks (83 us) and compare with the input at the block centre.
    mod = sweep(sweep_type="linear", f_start=100.0, f_stop=200.0, duration_s=0.01, amplitude=0.6)
    n = int(0.01 * RATE)
    bits = [mod.next_bit() for _ in range(n)]
    worst = 0.0
    for start in range(0, n - 256, 256):
        density = 2 * sum(bits[start:start + 256]) / 256 - 1
        t = (start + 128) / RATE
        worst = max(worst, abs(density - 0.6 * math.sin(mod.sweep_phase(t))))
    assert worst < 0.05


@pytest.mark.parametrize("kw", [
    dict(sweep_type="log", f_start=0.0),
    dict(f_start=500.0, f_stop=400.0),
    dict(duration_s=0.0),
    dict(sweep_type="chirp"),
])
def test_bad_sweep_parameters_raise(kw):
    mod = sweep(**kw)
    with pytest.raises(ValueError):
        mod.next_bit()


def test_sweep_fields_are_in_the_sd_state():
    from pru_io.sd_filter import SigmaDeltaFilter
    sd = SigmaDeltaFilter(pru_clock_mhz=200.0)
    m = sd.get_state()["modulators"][0]
    for key in ("f_start", "f_stop", "duration_s", "sweep_type"):
        assert key in m
