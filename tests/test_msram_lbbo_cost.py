"""Issue #59: LBBO from MSRAM costs 12 + 10 cycles per 32-bit word
on LP-AM261 silicon (22 / 52 / 92 cycles for 4 / 16 / 32 B, deterministic).
"""

import os

import pytest

from simulator import Simulator

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AM261X_CFG = os.path.join(REPO_ROOT, "config", "memory_am261x.cfg")

MSRAM_BASE = 0x70170000


def _lbbo_cycles(cfg: str, base: int, nbytes: int) -> int:
    sim = Simulator(cfg)
    src = f"ldi32 r1, 0x{base:08X}\nlbbo &r2, r1, 0, {nbytes}\nhalt\n"
    assert sim.load("pru0", src) == []
    core = sim.cores["pru0"]
    while core.pc < 2:                      # run the ldi32 pair, stop at LBBO
        sim.step("pru0")
    before = core.counters.cycles
    sim.step("pru0")
    return core.counters.cycles - before


@pytest.mark.parametrize("nbytes,cycles", [(4, 22), (16, 52), (32, 92)])
def test_msram_lbbo_matches_silicon(nbytes, cycles):
    assert _lbbo_cycles(AM261X_CFG, MSRAM_BASE, nbytes) == cycles


def test_msram_lbbo_is_deterministic():
    runs = {_lbbo_cycles(AM261X_CFG, MSRAM_BASE, 16) for _ in range(20)}
    assert runs == {52}


@pytest.mark.parametrize("nbytes,cycles", [(4, 3), (16, 6)])
def test_am261x_local_ram_unchanged(nbytes, cycles):
    # DRAM0 / Shared RAM measured equal on silicon (3 / 6 cycles).
    assert _lbbo_cycles(AM261X_CFG, 0x0000, nbytes) == cycles
    assert _lbbo_cycles(AM261X_CFG, 0x10000, nbytes) == cycles
