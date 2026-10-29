"""IEP compare semantics measured on silicon (64-bit mode and reset values).

Register-level measurements on LP-AM243 (ICSSG0 IEP0) and LP-AM261 (ICSS-M0 IEP0)
with PRU firmware reading and writing the IEP registers; both boards agree.
The expected values below are the measured register words, written as literals.
Not measured, and therefore not asserted here: rollover, the interaction of the
>= compare with the CMP0 reset, CMP8..CMP15, DEFAULT_INC > 1, IEPCLK = 1.
"""

import configparser
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from perif.iep import IepTimer                                      # noqa: E402
from simulator import Simulator                                     # noqa: E402

GLOBAL_CFG = 0x00
COUNT0 = 0x10
COUNT1 = 0x14
CMP_CFG = 0x70
CMP_STATUS = 0x74
CMP0_REG0 = 0x78
CMP1_REG0 = 0x80
CMP1_REG1 = 0x84
CMP2_REG0 = 0x88

RST = 0x1
EN0, EN1, EN2 = 0x2, 0x4, 0x8


def w32(iep, off, val):
    iep.write(off, (val & 0xFFFFFFFF).to_bytes(4, "little"))


def r32(iep, off):
    return int.from_bytes(iep.read(off, 4), "little")


def _sim(tmp_path, target):
    """Simulator on a copy of the root config with [device] target = `target`."""
    root = os.path.join(os.path.dirname(__file__), "..")
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(root, "memory.cfg"))
    cfg["device"]["target"] = target
    path = tmp_path / "memory.cfg"
    with open(path, "w") as f:
        cfg.write(f)
    return Simulator(str(path))


def _period_samples(n, ticks):
    """COUNT_REG0 after every IEP clock, 64-bit mode, CMP0 = n with reset."""
    iep = IepTimer()
    w32(iep, CMP0_REG0, n)
    w32(iep, CMP_CFG, RST | EN0)
    w32(iep, GLOBAL_CFG, 0x11)
    seen = [r32(iep, COUNT0)]
    for _ in range(ticks):
        iep.tick()
        seen.append(r32(iep, COUNT0))
    return iep, seen


# --- 1. 64-bit mode CMP0 reset period is N + 1 ---------------------------------

@pytest.mark.parametrize("n", [13, 100])
def test_64_bit_period_is_n_plus_one(n):
    """Measured on both boards, SHADOW_EN = 0, N = 13 and 100 (and 5 on LP-AM243):
    the largest COUNT sampled is N and N + 1 distinct values (0..N) are seen."""
    iep, seen = _period_samples(n, 5 * (n + 1))
    assert max(seen) == n
    assert sorted(set(seen)) == list(range(n + 1))
    assert len(set(seen)) == n + 1
    assert r32(iep, CMP_STATUS) & 1 == 1          # CMP_STATUS bit 0 set afterwards


def test_64_bit_reset_comes_on_the_clock_after_the_hit():
    iep, seen = _period_samples(5, 13)
    assert seen == [0, 1, 2, 3, 4, 5, 0, 1, 2, 3, 4, 5, 0, 1]


@pytest.mark.parametrize("n", [13, 100])
def test_64_bit_bulk_advance_has_the_same_period(n):
    iep = IepTimer()
    w32(iep, CMP0_REG0, n)
    w32(iep, CMP_CFG, RST | EN0)
    w32(iep, GLOBAL_CFG, 0x11)
    iep.observe_core_cycles("pru0", 7 * (n + 1) + 3)
    assert r32(iep, COUNT0) == 3


# --- 2. compare is >=, not equality ---------------------------------------------

@pytest.mark.parametrize("cmp1, status", [(49, 0x2), (50, 0x2), (51, 0x0)])
def test_enabling_a_compare_at_or_below_a_stopped_count_sets_status(cmp1, status):
    """Counter stopped at COUNT = 50, CMP1 enabled one value at a time, status
    cleared (W1C 0xFFFF) first: CMP_STATUS reads 0x2 for 49 and 50, 0x0 for 51."""
    iep = IepTimer()
    w32(iep, COUNT0, 50)
    w32(iep, CMP1_REG0, cmp1)
    w32(iep, CMP_STATUS, 0xFFFF)
    assert r32(iep, CMP_STATUS) == 0x0
    w32(iep, CMP_CFG, EN1)
    assert r32(iep, CMP_STATUS) == status


def test_enable_sets_only_the_compares_at_or_below_the_count():
    """CMP0 = 10, CMP1 = 60, COUNT = 50, both enabled: CMP_STATUS = 0x1."""
    iep = IepTimer()
    w32(iep, CMP0_REG0, 10)
    w32(iep, CMP1_REG0, 60)
    w32(iep, COUNT0, 50)
    w32(iep, CMP_STATUS, 0xFFFF)
    w32(iep, CMP_CFG, EN0 | EN1)
    assert r32(iep, CMP_STATUS) == 0x1
    assert r32(iep, COUNT0) == 50


def test_write_below_a_running_count_sets_status_once():
    """Counter free running (no CMP0 reset), CMP1 enabled at 0xFFFFFF, COUNT
    0x333: status 0x0. CMP1_REG0 := 10 gives status 0x2 at once; after W1C the
    level does not fire again while COUNT keeps running past 10."""
    iep = IepTimer()
    w32(iep, CMP1_REG0, 0xFFFFFF)
    w32(iep, CMP_STATUS, 0xFFFF)
    w32(iep, CMP_CFG, EN1)
    w32(iep, GLOBAL_CFG, 0x11)
    w32(iep, COUNT0, 0x333)
    for _ in range(16):
        iep.tick()
    assert r32(iep, CMP_STATUS) == 0x0
    w32(iep, CMP1_REG0, 10)
    assert r32(iep, CMP_STATUS) == 0x2
    w32(iep, CMP_STATUS, 0xFFFF)
    assert r32(iep, CMP_STATUS) == 0x0
    for _ in range(200):
        iep.tick()
    assert r32(iep, CMP_STATUS) == 0x0


def test_corners_not_measured_keep_the_equality_hit_on_the_running_count():
    """A running count still hits a compare it reaches by counting (CMP1 = 5)."""
    iep = IepTimer()
    w32(iep, CMP1_REG0, 5)
    w32(iep, CMP_CFG, EN1)
    w32(iep, GLOBAL_CFG, 0x11)
    for _ in range(4):
        iep.tick()
    assert r32(iep, CMP_STATUS) == 0x0
    iep.tick()
    assert r32(iep, CMP_STATUS) == 0x2


# --- 3. REG0 write clears CMP_STATUS[j] in 64-bit mode ---------------------------

def test_reg0_write_clears_the_status_bit_in_64_bit_mode():
    """COUNT = 50 stopped, CMP0..2 = 10, 20, 30 enabled: CMP_STATUS = 0x7.
    CMP1_REG0 := 25 gives CMP_STATUS = 0x5; CMP1_REG0 reads 0x19, REG1 reads 0."""
    iep = IepTimer()
    for reg, v in ((CMP0_REG0, 10), (CMP1_REG0, 20), (CMP2_REG0, 30)):
        w32(iep, reg, v)
    w32(iep, COUNT0, 50)
    w32(iep, CMP_STATUS, 0xFFFF)
    w32(iep, CMP_CFG, EN0 | EN1 | EN2)
    assert r32(iep, CMP_STATUS) == 0x7
    w32(iep, CMP1_REG0, 25)
    assert r32(iep, CMP_STATUS) == 0x5
    assert r32(iep, CMP1_REG0) == 0x19
    assert r32(iep, CMP1_REG1) == 0x0


def test_reg0_write_clears_a_hit_from_a_counting_compare():
    iep = IepTimer()
    w32(iep, CMP1_REG0, 5)
    w32(iep, CMP_CFG, EN1)
    w32(iep, GLOBAL_CFG, 0x11)
    for _ in range(5):
        iep.tick()
    assert r32(iep, CMP_STATUS) == 0x2
    w32(iep, CMP1_REG0, 40)                       # above the count: nothing sets it again
    assert r32(iep, CMP_STATUS) == 0x0


# --- 4. COUNT_REG0 := 0xFFFFFFFF and GLOBAL_CFG reset value ------------------------

def test_count_reg0_all_ones_is_stored_not_a_clear():
    """LP-AM243 and LP-AM261: writing 0xFFFFFFFF to COUNT_REG0 reads back
    0xFFFFFFFF (counter disabled, GLOBAL_CFG 0x110)."""
    iep = IepTimer()
    w32(iep, GLOBAL_CFG, 0x110)
    w32(iep, COUNT0, 0xFFFFFFFF)
    assert r32(iep, COUNT0) == 0xFFFFFFFF
    assert r32(iep, COUNT1) == 0x0


def test_global_cfg_reset_value_is_0x550_on_am243x(tmp_path):
    sim = _sim(tmp_path, "AM243x")
    assert sim.iep.read32(GLOBAL_CFG) & 0xFFFF1 == 0x550


def test_global_cfg_reset_value_is_0x551_on_am261x(tmp_path):
    """LP-AM261 ICSS-M0 IEP0 GLOBAL_CFG resets to 0x551 (CNT_ENABLE set)."""
    sim = _sim(tmp_path, "AM261x")
    assert sim.iep.read32(GLOBAL_CFG) & 0xFFFF1 == 0x551
    sim.hard_reset()
    assert sim.iep.read32(GLOBAL_CFG) & 0xFFFF1 == 0x551
