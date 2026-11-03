"""Shared helpers of the PDM demo tests (test_pdm_rx.py, test_pdm_e2e.py).

Stdlib only (no numpy): reinit recording and the link-based PCM reference,
plus the signal measurements of the end-to-end checks (zero-crossing
frequency, Hann-windowed Goertzel level, spectrogram peak bins, pipeline
delay by cross-correlation).
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "source"))

from pdm import reference as R           # noqa: E402


# ---------------------------------------------------------------------------
# Link-based reference
# ---------------------------------------------------------------------------

def record_reinits(sim, link, channels) -> dict[int, list[int]]:
    """Record, per PRU0 SD channel, ``len(link.sampled_bits[ch])`` at each reinit.

    The returned lists fill while the cores run. *link* must keep history.
    """
    reinits = {ch: [] for ch in channels}
    for ch in channels:
        chan = sim.cores["pru0"].io_port.sd_filter.channels[ch]
        reinit = chan.reinit

        def recording_reinit(_ch=ch, _reinit=reinit):
            reinits[_ch].append(len(link.sampled_bits[_ch]))
            _reinit()

        chan.reinit = recording_reinit
    return reinits


def link_reference(link, reinits, ch: int) -> list[int]:
    """``pcm_chain_for_channel`` of the bits channel *ch* sampled after its reinit."""
    assert len(reinits[ch]) == 1, reinits
    return R.pcm_chain_for_channel(link.sampled_bits[ch][reinits[ch][0]:], ch)


# ---------------------------------------------------------------------------
# Signal measurements
# ---------------------------------------------------------------------------

def zero_crossing_hz(x: list[int], fs: float) -> float:
    """Frequency from the rising zero crossings (linearly interpolated)."""
    crossings = [i + (-a) / (b - a) for i, (a, b) in enumerate(zip(x, x[1:]))
                 if a < 0 <= b]
    assert len(crossings) >= 2, "fewer than two rising zero crossings"
    return (len(crossings) - 1) * fs / (crossings[-1] - crossings[0])


def _hann(n: int) -> list[float]:
    return [0.5 - 0.5 * math.cos(2 * math.pi * i / n) for i in range(n)]


def goertzel_db(x: list[int], freq: float, fs: float) -> float:
    """Level of *freq* in the Hann-windowed *x*, dB (arbitrary reference)."""
    w = _hann(len(x))
    coeff = 2 * math.cos(2 * math.pi * freq / fs)
    s1 = s2 = 0.0
    for v, h in zip(x, w):
        s1, s2 = v * h + coeff * s1 - s2, s1
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return 10 * math.log10(max(power, 1e-300))


def peak_bins(x: list[int], n: int = 256, hop: int = 128) -> list[tuple[int, int]]:
    """[(frame centre sample, peak bin 1..n/2)] of Hann frames of *x*.

    Bin 0 (DC) is excluded.
    """
    w = _hann(n)
    cos_t = [math.cos(2 * math.pi * i / n) for i in range(n)]
    sin_t = [math.sin(2 * math.pi * i / n) for i in range(n)]
    out = []
    for start in range(0, len(x) - n + 1, hop):
        frame = [x[start + i] * w[i] for i in range(n)]
        best, best_k = -1.0, 0
        for k in range(1, n // 2 + 1):
            re = im = 0.0
            j = 0
            for v in frame:
                re += v * cos_t[j]
                im += v * sin_t[j]
                j = (j + k) % n
            mag = re * re + im * im
            if mag > best:
                best, best_k = mag, k
        out.append((start + n // 2, best_k))
    return out


def pipeline_delay(out: list[int], clip: list[int], max_lag: int = 64) -> int:
    """Lag (samples) of *out* behind *clip* with the largest cross-correlation."""
    n = len(clip) - max_lag
    best, best_lag = -math.inf, 0
    for lag in range(max_lag + 1):
        c = sum(clip[i] * out[i + lag] for i in range(n))
        if c > best:
            best, best_lag = c, lag
    return best_lag
