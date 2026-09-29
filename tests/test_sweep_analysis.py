# tests/test_sweep_analysis.py
"""sweep_response: level vs input frequency from a recorded sweep output."""
import cmath
import math

import pytest
from pru_io.sd_modulator import SDModulator
from pru_io.sweep_analysis import sweep_response

FS = 16000.0
FULL = 2 ** 23


def sweep(**kw):
    params = dict(signal="sweep", sd_clock_mhz=3.072, amplitude=0.5,
                  f_start=20.0, f_stop=20000.0, duration_s=0.5, sweep_type="log")
    params.update(kw)
    return SDModulator(**params)


def sampled(mod, gain=lambda f: 1.0, delay=0.0, t0=0.0):
    """Output samples at FS: gain(f_inst) * amplitude * FULL * sin(phase), and their times."""
    n = int((mod.duration_s + delay) * FS)
    times = [t0 + k / FS for k in range(n)]
    ys = []
    for t in times:
        u = t - delay
        ys.append(0 if u < 0 or u >= mod.duration_s else
                  round(gain(mod.sweep_frequency(u)) * mod.amplitude * FULL * math.sin(mod.sweep_phase(u))))
    return ys, times


def test_identity_is_0_db_across_the_sweep():
    mod = sweep()
    ys, ts = sampled(mod)
    pts = sweep_response(ys, ts, mod, full_scale=FULL, delay_s=0.0)
    assert len(pts) > 100
    measured = [(f, db) for f, db in pts if not 7800 < f < 8200]   # f_s/2: fit is ill-conditioned
    assert all(abs(db) < 0.05 for _, db in measured), max(measured, key=lambda p: abs(p[1]))
    assert min(f for f, _ in pts) < 200 and max(f for f, _ in pts) > 15000


def test_one_pole_lowpass_matches_its_magnitude():
    mod = sweep(f_start=50.0, f_stop=7500.0)
    x, ts = sampled(mod)
    a = 1 - math.exp(-2 * math.pi * 1000 / FS)            # fc about 1 kHz
    y, prev = [], 0.0
    for v in x:
        prev = a * v + (1 - a) * prev
        y.append(round(prev))
    def h_db(f):
        z = cmath.exp(-2j * math.pi * f / FS)
        return 20 * math.log10(abs(a / (1 - (1 - a) * z)))
    pts = sweep_response(y, ts, mod, full_scale=FULL, delay_s=0.0)
    checked = [(f, db) for f, db in pts if f > 100]
    assert len(checked) > 50
    assert all(abs(db - h_db(f)) < 0.3 for f, db in checked), \
        max(checked, key=lambda p: abs(p[1] - h_db(p[0])))


def test_a_pure_delay_is_compensated():
    mod = sweep()
    ys, ts = sampled(mod, delay=0.001)
    pts = sweep_response(ys, ts, mod, full_scale=FULL, delay_s=0.001)
    measured = [db for f, db in pts if not 7800 < f < 8200]
    assert all(abs(db) < 0.05 for db in measured)


def test_aliased_output_is_measured_against_the_input_frequency():
    # Input above f_s/2 = 8 kHz folds when sampled at 16 kHz; a 0.1 gain (-20 dB)
    # applied there must be reported at the input frequency.
    mod = sweep(f_start=1000.0, f_stop=20000.0, duration_s=0.25)
    ys, ts = sampled(mod, gain=lambda f: 0.1 if f > 8000 else 1.0)
    pts = sweep_response(ys, ts, mod, full_scale=FULL, delay_s=0.0)
    low = [db for f, db in pts if 1500 < f < 7500]
    high = [db for f, db in pts if 8500 < f < 15500 or 16500 < f < 19500]
    assert low and high
    assert all(abs(db) < 0.1 for db in low)
    assert all(abs(db + 20) < 0.5 for db in high)


def test_silence_is_clamped_at_minus_140_db():
    mod = sweep(f_start=1000.0, f_stop=2000.0, duration_s=0.02)
    _, ts = sampled(mod)
    pts = sweep_response([0] * len(ts), ts, mod, full_scale=FULL, delay_s=0.0)
    assert pts and all(db == -140.0 for _, db in pts)


def test_windows_outside_the_sweep_are_skipped():
    mod = sweep(f_start=1000.0, f_stop=2000.0, duration_s=0.01)
    ys, ts = sampled(mod, t0=-0.01)                        # first 10 ms before the sweep
    pts = sweep_response(ys, ts, mod, full_scale=FULL, delay_s=0.0)
    assert all(1000 <= f <= 2000 for f, _ in pts)


def test_mismatched_lengths_raise():
    mod = sweep()
    with pytest.raises(ValueError):
        sweep_response([1, 2, 3], [0.0, 1.0], mod, full_scale=FULL, delay_s=0.0)


def test_resolution_note_warns_when_a_window_spans_much_of_an_octave():
    from pru_io.sweep_analysis import resolution_note
    fast = sweep(f_start=100.0, f_stop=16000.0, duration_s=0.05)      # 64 samples at 16 kHz: +-25 %
    slow = sweep(f_start=100.0, f_stop=16000.0, duration_s=0.5)       # +-2 %
    assert "smeared" in resolution_note(fast, window=64, fs_hz=16000.0)
    assert resolution_note(slow, window=64, fs_hz=16000.0) == ""
    lin = sweep(sweep_type="linear", f_start=0.0, f_stop=20000.0, duration_s=0.05)
    assert "smeared" in resolution_note(lin, window=64, fs_hz=16000.0)
