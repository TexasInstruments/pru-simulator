"""LP-AM243 PRU0 measurements from issue #69; extras are silicon data."""

import pytest

from mem.regions import MemoryRegion
from simulator import Simulator
from tests.test_msram_lbbo_cost import AM243X_CFG, AM261X_CFG, REPO_ROOT, _lbbo_cycles

import os
ROOT_CFG = os.path.join(REPO_ROOT, "memory.cfg")

BASE = 0x70000000
# (byte offset, byte length, measured extra over read_latency + words - 1).
SILICON_CROSSINGS = [
    (63, 2, 13),
    (61, 4, 13), (62, 4, 13), (63, 4, 13),
    (57, 8, 12), (60, 8, 13), (63, 8, 13),
    (49, 16, 11), (56, 16, 12), (63, 16, 13),
    (33, 32, 9), (48, 32, 11), (63, 32, 13),
    (1, 64, 5), (4, 64, 6), (32, 64, 9), (63, 64, 13),
    (127, 4, 13), (255, 4, 13), (511, 4, 13), (1023, 4, 13),
]


@pytest.mark.parametrize("offset,nbytes,extra", SILICON_CROSSINGS)
def test_am243x_msram_line_crossing_silicon(offset, nbytes, extra):
    # Count touched words independently of the crossing fit. The table supplies extra.
    words = (offset % 4 + nbytes + 3) // 4
    assert _lbbo_cycles(AM243X_CFG, BASE + offset, nbytes) - 1 == 20 + words - 1 + extra


@pytest.mark.parametrize("offset,nbytes", [(0, 64), (1, 1), (1, 4), (13, 4),
                                          (29, 4), (49, 8), (61, 3), (63, 1)])
def test_am243x_reads_inside_line_unchanged(offset, nbytes):
    words = (offset % 4 + nbytes + 3) // 4
    assert _lbbo_cycles(AM243X_CFG, BASE + offset, nbytes) == 20 + words


@pytest.mark.parametrize("offset,nbytes,_extra", SILICON_CROSSINGS)
def test_am261x_line_crossing_unchanged(offset, nbytes, _extra):
    words = (offset % 4 + nbytes + 3) // 4
    assert _lbbo_cycles(AM261X_CFG, BASE + offset, nbytes) == 22 + (words - 1) * 10


@pytest.mark.parametrize("base", [0, 0x2000, 0x10000])
def test_am243x_other_regions_line_crossing_unchanged(base):
    assert _lbbo_cycles(AM243X_CFG, base + 63, 4) == 4


def test_line_crossing_defaults_off():
    region = MemoryRegion("plain", 0, 128, 20, 1)
    assert region.calc_read_stalls(63, 4) == 21


@pytest.mark.parametrize("options", [{"line_bytes": 64}, {"line_cross_fit": 13}])
def test_both_line_keys_required(options):
    region = MemoryRegion("plain", 0, 128, 20, 1, **options)
    assert region.calc_read_stalls(63, 4) == 21


@pytest.mark.parametrize("options", [{"line_bytes": -4}, {"line_bytes": 63},
                                    {"line_cross_fit": -1}])
def test_invalid_line_options(options):
    with pytest.raises(ValueError):
        MemoryRegion("plain", 0, 128, 20, 1, **options)


def test_am243x_larger_bursts_unmodelled():
    assert _lbbo_cycles(AM243X_CFG, BASE + 63, 124) == 52


def test_am243x_lbco_line_crossing():
    sim = Simulator(AM243X_CFG)
    sim.cores["pru0"].constant_table.set(28, BASE)
    assert sim.load("pru0", "lbco &r2, c28, 63, 4\nhalt") == []
    sim.step("pru0")
    assert sim.cores["pru0"].fault is None
    assert sim.cores["pru0"].counters.cycles == 35


def test_am243x_straddling_stores_unchanged():
    sim = Simulator(AM243X_CFG)
    assert sim.memory.write(BASE + 63, bytes(4)) == 2


def test_am243x_pru1_line_crossing_unmodelled():
    sim = Simulator(AM243X_CFG)
    assert sim.load("pru1", "ldi32 r1, 0x7000003f\nlbbo &r2, r1, 0, 4\nhalt") == []
    sim.step("pru1", 2)
    before = sim.cores["pru1"].counters.cycles
    sim.step("pru1")
    assert sim.cores["pru1"].fault is None
    assert sim.cores["pru1"].counters.cycles - before == 22


def test_root_am243x_template_has_the_same_msram_model():
    # The default template is AM243x-targeted and carries the same measurement.
    assert _lbbo_cycles(ROOT_CFG, BASE + 63, 4) == 35
    assert _lbbo_cycles(ROOT_CFG, BASE + 0, 4) == 21


def _write_cfg(tmp_path, readers):
    text = open(AM243X_CFG).read().replace("line_readers = PRU0", f"line_readers = {readers}")
    path = tmp_path / "memory.cfg"
    path.write_text(text)
    return str(path)


@pytest.mark.parametrize("readers", ["PRU0", "pru0", "Pru0"])
def test_line_readers_are_case_insensitive(tmp_path, readers):
    assert _lbbo_cycles(_write_cfg(tmp_path, readers), BASE + 63, 4) == 35


def test_line_readers_unknown_name_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="line_readers.*pru2"):
        Simulator(_write_cfg(tmp_path, "pru0 pru2"))


def test_line_crossing_term_never_goes_negative():
    # A wide line makes W1//2 exceed the fit constant for a long first segment.
    region = MemoryRegion("plain", 0, 1024, 20, 1, line_bytes=256, line_cross_fit=13,
                          line_readers=("PRU0",))
    # 65 words touched, 64 in the first line: 13 - 32 would be negative.
    assert region.calc_read_stalls(2, 255, requester="PRU0") == 20 + 64
    assert region.calc_read_stalls(255, 4, requester="pru0") == 20 + 1 + 13 - 0
