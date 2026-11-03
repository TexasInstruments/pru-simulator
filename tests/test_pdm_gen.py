"""pdm: PRU1 generator pdm_gen.asm against its Python references.

PRU1 runs source/pdm/pdm_gen.asm alone (producer side only): PRU0 stays
idle and unstepped with every SD channel "off". The pins are observed
through ``PdmLink(keep_history=True)``; an edge from a PRU1 instruction
starting at cycle c is stamped ``time_of(pru1, c + 1)``, converted back to
c here. Edge times must equal ``reference.pdm_clock_edges`` exactly and the
data line must carry ``reference.sigma_delta2`` (left at rising edges,
right at falling edges).
"""
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "source"))

from pdm import pdm_layout as L          # noqa: E402
from pdm import reference as R           # noqa: E402
from simulator import Simulator          # noqa: E402

PDM_DIR = REPO / "source" / "pdm"
GEN_ASM = PDM_DIR / "pdm_gen.asm"
CLK_REF_ASM = Path(__file__).parent / "asm" / "pdm_clk_ref.asm"
CLIPS = PDM_DIR / "clips"
CLK_PIN, DAT_PIN = 0, 1
PERIODS = 192            # PDM periods per PCM frame


def _build_option(name: str) -> int:
    m = re.search(rf"^{name}\s+\.set\s+(\d+)\s*(;.*)?$", GEN_ASM.read_text(), re.M)
    assert m, f"{name} is not a plain-literal .set in pdm_gen.asm"
    return int(m.group(1))


def _tones():
    return L.load_clip(CLIPS / "tone_1000.wav"), L.load_clip(CLIPS / "tone_3000.wav")


def _make_sim(config, left, right, source=None, include_paths=None):
    sim = Simulator(config_path=config)
    for ch in range(len(sim.cores["pru0"].io_port.sd_filter.channels)):
        sim.set_sd_source("pru0", ch, "off")
    if source is None:
        source = GEN_ASM.read_text()
        include_paths = [str(PDM_DIR)]
        L.write_clips(sim.memory, left, right)
    assert sim.load("pru1", source, include_paths) == []
    assert sim.load("pru0", "idle:\n    jmp idle\n") == []
    link = sim.attach_pdm_link("pru1", "pru0", clock_pin=CLK_PIN,
                               data_pins={0: DAT_PIN, 1: DAT_PIN}, keep_history=True)
    return sim, link


def _run(sim, cycles, skip=False):
    core = sim.cores["pru1"]
    while core.counters.cycles < cycles and not core.halted:
        if skip:
            core.step_run(cycles - core.counters.cycles)
        else:
            core.step()
    assert not core.halted


def _cycles_for(n_periods: int) -> int:
    """PRU1 cycles that surely cover init plus *n_periods* + 1 rising edges."""
    return 300 + (n_periods + 2) * 66


def _events(sim, link):
    """Link history as (start cycle of the writing instruction, GPO word)."""
    t0 = sim.iep.core_time_units("pru1", 0)
    unit = sim.iep.core_time_units("pru1", 1) - t0
    out = []
    for stamp, word in link.edge_history:
        assert (stamp - t0) % unit == 0
        out.append(((stamp - t0) // unit - 1, word))
    return out


def _decode(events):
    """-> (clock edges [(cycle, rising, data level)], data change cycles)."""
    clk, data = [], []
    prev = 0
    for cycle, word in events:
        if (word ^ prev) >> CLK_PIN & 1:
            clk.append((cycle, bool(word >> CLK_PIN & 1), word >> DAT_PIN & 1))
        if (word ^ prev) >> DAT_PIN & 1:
            data.append(cycle)
        prev = word
    return clk, data


def _run_decode(config, left, right, n_periods, **kw):
    sim, link = _make_sim(config, left, right, **kw)
    _run(sim, _cycles_for(n_periods))
    clk, data = _decode(_events(sim, link))
    assert len(clk) >= 2 * n_periods
    assert clk[0][1], "first clock edge must be rising"
    return sim, clk[:2 * n_periods], data


def _assert_schedule(clk, n_periods):
    got = [(c, rising) for c, rising, _ in clk]
    assert got == R.pdm_clock_edges(n_periods, clk[0][0])


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

def test_reference_clock_matches_pdm_clk_ref(nominal_config):
    """pdm_clock_edges is exactly the audio_pipe pdm_clk.asm pattern."""
    sim, link = _make_sim(nominal_config, [], [], source=CLK_REF_ASM.read_text())
    _run(sim, _cycles_for(480))
    clk, _ = _decode(_events(sim, link))
    _assert_schedule(clk[:962], 481)
    rises = [c for c, rising, _ in clk[:962] if rising]
    periods = [b - a for a, b in zip(rises, rises[1:])]
    assert sorted(set(periods)) == [65, 66]
    # 480 periods = 10 x 48 at 65 + 5/48 cycles: 31250 cycles, 1-cycle jitter
    assert abs(rises[480] - rises[0] - 31250) <= 1


def test_reference_sigma_delta2_density():
    """Bit density follows the input; output length and silence padding."""
    bits = R.sigma_delta2([8192] * 10, 192)
    assert len(bits) == 1920
    mean = sum(2 * b - 1 for b in bits) * R.FS / len(bits)
    assert abs(mean - 8192) < 0.01 * R.FS
    padded = R.sigma_delta2([8192] * 2, 192, n_periods=1000)
    assert padded[:384] == bits[:384]
    tail = padded[384 + 200:]
    assert abs(sum(2 * b - 1 for b in tail)) <= 4


def test_build_options_are_plain_literals():
    for name, value in (("PDM_CLK_PIN", 0), ("PDM_DAT_PIN", 1), ("CFG_T_SH_CYC", 65),
                        ("CFG_FRAC_ACC", 5), ("CFG_FRAC_MAX", 48),
                        ("PCM_PERIODS", 192)):
        assert _build_option(name) == value, name
    assert _build_option("SETUP_MIN_CYC") >= 8
    assert re.search(r"^PDM_GEN:", GEN_ASM.read_text(), re.M)
    assert '.include "pdm_shared.inc"' in GEN_ASM.read_text()


# ---------------------------------------------------------------------------
# Firmware
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("frames", [None, 2, 0], ids=["tone_clip", "len2", "len0"])
def test_clock_edges_follow_fractional_schedule(nominal_config, frames):
    """First 480 periods: every edge on the fractional-adder schedule, across
    PCM fetches (periods 190, 382), clip end and both bit values."""
    left, right = _tones()
    if frames is not None:
        left, right = left[:frames], right[:frames]
    _, clk, _ = _run_decode(nominal_config, left, right, 480)
    _assert_schedule(clk, 480)


def test_data_bits_match_reference(nominal_config):
    left, right = _tones()
    n = 2 * PERIODS + 10
    _, clk, _ = _run_decode(nominal_config, left, right, n)
    rising = [d for _, r, d in clk if r]
    falling = [d for _, r, d in clk if not r]
    assert rising == R.sigma_delta2(left, PERIODS, n_periods=n)
    assert falling == R.sigma_delta2(right, PERIODS, n_periods=n)
    assert 0 < sum(rising) < n and 0 < sum(falling) < n


@pytest.mark.parametrize("frames", [None, 3], ids=["tone_clip", "len3"])
def test_setup_margin(nominal_config, frames):
    """Every sampling edge: >= SETUP_MIN_CYC cycles since the last data change."""
    setup = _build_option("SETUP_MIN_CYC")
    left, right = _tones()
    if frames is not None:
        left, right = left[:frames], right[:frames]
    n = 3 * PERIODS + 10
    _, clk, data = _run_decode(nominal_config, left, right, n)
    assert len(data) > n // 2
    margins = []
    for cycle, _, _ in clk:
        before = [d for d in data if d < cycle]
        assert cycle not in data
        if before:
            margins.append(cycle - before[-1])
    assert min(margins) >= setup, min(margins)


def test_clip_end_sets_done_and_silence(nominal_config):
    left, right = _tones()
    left, right = left[:3], right[:3]
    sim, link = _make_sim(nominal_config, left, right)
    assert L.read_header(sim.memory)["clip_len"] == 3

    # Just after rising edge 568: the last frame (2) is still being held.
    core = sim.cores["pru1"]
    while sum(r for _, r, _ in _decode(_events(sim, link))[0]) < 3 * PERIODS - 8:
        for _ in range(64):
            core.step()
    hdr = L.read_header(sim.memory)
    assert (hdr["gen_pos"], hdr["gen_done"]) == (3, 0)

    n = 3 * PERIODS + 40
    _run(sim, _cycles_for(n))
    hdr = L.read_header(sim.memory)
    assert (hdr["gen_pos"], hdr["gen_done"]) == (3, 1)
    clk, _ = _decode(_events(sim, link))
    clk = clk[:2 * n]
    _assert_schedule(clk, n)
    assert [d for _, r, d in clk if r] == R.sigma_delta2(left, PERIODS, n_periods=n)
    assert [d for _, r, d in clk if not r] == R.sigma_delta2(right, PERIODS, n_periods=n)


def test_idle_skip_collapses_padding(nominal_config):
    left, right = _tones()
    runs = []
    for skip in (False, True):
        sim, link = _make_sim(nominal_config, left, right)
        _run(sim, 50_000, skip=skip)
        core = sim.cores["pru1"]
        runs.append((list(link.edge_history), sim.registers("pru1"), core.pc,
                     core.counters.cycles, core.counters.instruction_count,
                     L.read_header(sim.memory), core.skipped_cycles))
    off, on = runs
    assert on[:6] == off[:6]
    assert off[6] == 0
    assert on[6] > 0
