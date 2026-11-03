"""Python references for the PDM generator (pdm_gen.asm) and receiver (pdm_rx.asm).

``sigma_delta2`` is the bit-exact model of one channel's modulator;
``pdm_clock_edges`` is the clock schedule of the integer fractional adder
used by audio_pipe ``pdm_clk.asm`` (tests/asm/pdm_clk_ref.asm).
``pcm_chain`` is the bit-exact model of one receiver channel: CIC3,
FIR /2, DC blocker and sat24 (audio_pipe tests/test_pdm_rx.py).
"""
import re
from pathlib import Path

FS = 32768                # feedback level: bit 1 = +FS, bit 0 = -FS


def sigma_delta2(pcm: list[int], periods_per_sample: int = 192,
                 n_periods: int | None = None) -> list[int]:
    """One bit per PDM period for one channel (2nd-order error feedback).

    Each PCM sample is held for *periods_per_sample* periods (zero-order
    hold). After the clip the input is 0 (silence) forever. Returns
    ``len(pcm) * periods_per_sample`` bits, or *n_periods* bits if given
    (shorter: truncated; longer: continued with silence).
    """
    if n_periods is None:
        n_periods = len(pcm) * periods_per_sample
    out = []
    i1 = i2 = 0
    fb = -FS                  # previous bit 0
    for n in range(n_periods):
        k = n // periods_per_sample
        x = pcm[k] if k < len(pcm) else 0
        i1 += x - fb
        i2 += i1 - fb
        bit = 1 if i2 >= 0 else 0
        fb = FS if bit else -FS
        out.append(bit)
    return out


def pdm_clock_edges(n_periods: int, start_cycle: int, t_sh_cyc: int = 65,
                    frac_acc: int = 5, frac_max: int = 48) -> list[tuple[int, bool]]:
    """Clock edges ``(cycle, rising)`` of *n_periods* periods from *start_cycle*.

    Fractional adder as pdm_clk.asm: high ``t_sh_cyc // 2`` (32), low the
    rest (33). At the end of each high time the accumulator is tested: if
    ``acc >= frac_max`` it drops by ``frac_max - frac_acc`` and the *next*
    period's high time is one cycle longer (66-cycle period), otherwise it
    grows by ``frac_acc``. Defaults: 43 x 65 + 5 x 66 cycles per 48 periods.
    """
    high = t_sh_cyc // 2
    low = t_sh_cyc - high
    edges = []
    t = start_cycle
    acc = 0
    extra = 0
    for _ in range(n_periods):
        edges.append((t, True))
        t += high + extra
        edges.append((t, False))
        if acc >= frac_max:
            acc += frac_acc - frac_max
            extra = 1
        else:
            acc += frac_acc
            extra = 0
        t += low
    return edges


# ---------------------------------------------------------------------------
# PRU0 receiver (pdm_rx.asm): bit-exact decode chain, ported from audio_pipe
# tests/test_pdm_rx.py (cic3_reference, fir_reference, dcblock_reference,
# saturate).
# ---------------------------------------------------------------------------

FIR_COEFS = Path(__file__).resolve().parent / "fir_coefs.inc"
WARMUP = 3                # comb outputs dropped after the SD reinit
MASK28 = (1 << 28) - 1    # SD accumulators and the comb are modulo 2^28
PCM_MAX = (1 << 23) - 1   # sat24: +-(2^23 - 1)


def fir_table(path=FIR_COEFS) -> list[int]:
    """T[0..63] (Q27, oldest input first) as generated into fir_coefs.inc."""
    t = [int(v) for v in re.findall(r"; T\[\d+\] = (-?\d+)", Path(path).read_text())]
    if len(t) != 64:
        raise ValueError(f"{path}: expected 64 coefficients, found {len(t)}")
    return t


def cic3(bits: list[int], osr: int = 96, warmup: int = WARMUP) -> list[int]:
    """32 kHz signed SINC3 samples of *bits* (first bit = first after reinit).

    Three integrators latched every *osr* bits (the SD hardware, mod 2^28),
    the firmware comb y = x - 3x[-1] + 3x[-2] - x[-3] mod 2^28, minus
    osr^3 / 2. The first *warmup* comb outputs are dropped.
    """
    a1 = a2 = a3 = 0
    latched = []
    for i, b in enumerate(bits):
        a1 += b
        a2 += a1
        a3 += a2
        if (i + 1) % osr == 0:
            latched.append(a3 & MASK28)
    d1 = d3 = d5 = 0
    out = []
    half = osr ** 3 // 2
    for x in latched:
        c3 = x - d1
        d1 = x
        c4 = c3 - d3
        d3 = c3
        c5 = (c4 - d5) & MASK28
        d5 = c4
        out.append(c5 - half)
    return out[warmup:]


def fir_decimate(x32: list[int], table: list[int] | None = None) -> list[int]:
    """16 kHz FIR output: 64-tap integer FIR on every 2nd sample (k = 1, 3,
    ...), y = (sum T[j] * D[j] + 2^26) >> 27 (floor), not saturated."""
    if table is None:
        table = fir_table()
    window = [0] * len(table)
    out = []
    for k, x in enumerate(x32):
        window = window[1:] + [x]
        if k % 2 == 1:
            acc = sum(t * d for t, d in zip(table, window))
            out.append((acc + (1 << 26)) >> 27)
    return out


def dc_block(ys: list[int], e: int = 0) -> list[int]:
    """20 Hz DC blocker: e += (y * 2^32 - e) >> 7 (Q32, floor shift), then
    y minus e rounded to nearest (e is the int64 state, start value *e*)."""
    out = []
    for y in ys:
        e += ((y << 32) - e) >> 7
        out.append(y - ((e + (1 << 31)) >> 32))
    return out


_dc_block = dc_block      # pcm_chain's dc_block argument shadows the name


def sat24(y: int) -> int:
    """Saturate to signed 24 bit, +-(2^23 - 1)."""
    return max(-PCM_MAX, min(PCM_MAX, y))


def pcm_chain(bits: list[int], osr: int = 96, dc_block: bool = True,
              warmup: int = WARMUP) -> list[int]:
    """16 kHz PCM that pdm_rx.asm writes for one channel's sampled *bits*.

    CIC3 (comb mod 2^28, *warmup* outputs dropped) -> FIR /2 (Q27 table
    from fir_coefs.inc) -> DC blocker (if *dc_block*) -> sat24. *bits*
    start with the first bit sampled after the channel's SD reinit. The
    firmware drops WARMUP_SAMPLES + 1 outputs on odd channels (FIR stagger),
    so pass ``warmup=WARMUP + 1`` for those.
    """
    ys = fir_decimate(cic3(bits, osr, warmup))
    if dc_block:
        ys = _dc_block(ys)
    return [sat24(y) for y in ys]
