# pru_io/sweep_analysis.py
"""Frequency response from a recorded sweep: level vs instantaneous input frequency.

Each window of output samples is fitted to a*sin(phi) + b*cos(phi) of the known
input phase at the samples' (delay-corrected) input times. Sampling cannot
change the value of sin(phi) at the sample instants, so output that aliased
below the output Nyquist frequency is still measured against the input
frequency that caused it.
"""

import math

FLOOR_DB = -140.0
MAX_SPREAD = 0.05       # warn when one window covers more than +-5 % (log) of its frequency


def resolution_note(modulator, window, fs_hz):
    """Empty string, or a warning when one analysis window spans too much of the sweep.

    A window of `window` output samples covers window / fs_hz seconds of the sweep;
    a fast sweep then averages the response over a wide band and smears steep slopes.
    """
    if fs_hz <= 0:
        return ""
    span_s = window / fs_hz
    f1, f2, T = modulator.f_start, modulator.f_stop, modulator.duration_s
    if modulator.sweep_type == "log":
        rel = math.expm1(math.log(f2 / f1) * span_s / T)
        if rel > 2 * MAX_SPREAD:
            return (f"each point averages +-{rel * 50:.0f} % of its frequency ({window}-sample window): "
                    f"steep slopes are smeared - use a longer sweep or a smaller window")
        return ""
    df = (f2 - f1) * span_s / T
    if df > 2 * MAX_SPREAD * (f1 + f2) / 2:
        return (f"each point averages +-{df / 2:.0f} Hz ({window}-sample window): "
                f"steep slopes are smeared - use a longer sweep or a smaller window")
    return ""


def sweep_response(samples, times, modulator, full_scale, delay_s, window=64, hop=None):
    """Return [(f_hz, level_db), ...] for a recorded sweep.

    samples     recorded output values
    times       time of each sample in seconds, on the modulator's time base
    modulator   the SDModulator that produced the sweep (sweep_phase/frequency)
    full_scale  output value of a full-scale input (level 0 dB = amplitude * full_scale)
    delay_s     filter delay: sample k responds to the input at times[k] - delay_s
    """
    if len(samples) != len(times):
        raise ValueError(f"{len(samples)} samples but {len(times)} times")
    if window < 4:
        raise ValueError(f"window must be >= 4, got {window}")
    hop = hop or max(1, window // 2)
    ref = modulator.amplitude * full_scale
    if ref <= 0:
        raise ValueError("amplitude * full_scale must be > 0")
    points = []
    for s in range(0, len(samples) - window + 1, hop):
        ts = [times[k] - delay_s for k in range(s, s + window)]
        if ts[0] < 0 or ts[-1] >= modulator.duration_s:
            continue
        ph = [modulator.sweep_phase(t) for t in ts]
        if ph[-1] - ph[0] < math.pi / 2:          # less than a quarter period
            continue
        sn = [math.sin(p) for p in ph]
        cs = [math.cos(p) for p in ph]
        ss = sum(v * v for v in sn)
        cc = sum(v * v for v in cs)
        sc = sum(a * b for a, b in zip(sn, cs))
        det = ss * cc - sc * sc
        if det < 1e-3 * ss * cc:                  # sampled sin/cos nearly collinear (f near k*fs/2)
            continue
        ys = samples[s:s + window]
        ys_ = sum(a * b for a, b in zip(ys, sn))
        yc = sum(a * b for a, b in zip(ys, cs))
        a = (ys_ * cc - yc * sc) / det
        b = (yc * ss - ys_ * sc) / det
        amp = math.hypot(a, b)
        db = 20.0 * math.log10(amp / ref) if amp > 0 else FLOOR_DB
        f = modulator.sweep_frequency((ts[0] + ts[-1]) / 2)
        points.append((f, max(db, FLOOR_DB)))
    return points
