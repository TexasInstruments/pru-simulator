"""Python references for the PRU1 PDM generator (pdm_gen.asm).

``sigma_delta2`` is the bit-exact model of one channel's modulator;
``pdm_clock_edges`` is the clock schedule of the integer fractional adder
used by audio_pipe ``pdm_clk.asm`` (tests/asm/pdm_clk_ref.asm).
"""

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
