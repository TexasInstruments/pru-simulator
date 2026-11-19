"""Issue #59: per-device MSRAM LBBO cost, measured on silicon.

* LP-AM261 (ICSSM) at the SDK's 225 MHz ICSSM clock: 12 + 9 cycles per 32-bit word
  (21 / 48 / 84 / 156 cycles for 4 / 16 / 32 / 64 B).
* LP-AM261 (ICSSM) with ICSSM clock and SYS_CLK at 200 MHz: 12 + 10 per word
  (22 / 52 / 92 / 172 cycles for 4 / 16 / 32 / 64 B).
* LP-AM243 (ICSSG): 21 cycles for the first word, +1 per extra word (21 / 24 / 28 / 36
  cycles for 4 / 16 / 32 / 64 B).

Both deterministic when the other PRU is idle.
"""

import os

import pytest

from simulator import Simulator

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AM261X_CFG = os.path.join(REPO_ROOT, "config", "memory_am261x.cfg")            # 225 MHz
AM261X_200_CFG = os.path.join(REPO_ROOT, "config", "memory_am261x_200mhz.cfg")  # 200 MHz
AM243X_CFG = os.path.join(REPO_ROOT, "config", "memory_am243x.cfg")

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


@pytest.mark.parametrize("nbytes,cycles", [(4, 21), (16, 48), (32, 84), (64, 156)])
def test_msram_lbbo_matches_silicon(nbytes, cycles):
    assert _lbbo_cycles(AM261X_CFG, MSRAM_BASE, nbytes) == cycles


@pytest.mark.parametrize("nbytes,cycles", [(4, 22), (16, 52), (32, 92), (64, 172)])
def test_msram_lbbo_matches_silicon_200mhz(nbytes, cycles):
    assert _lbbo_cycles(AM261X_200_CFG, MSRAM_BASE, nbytes) == cycles


@pytest.mark.parametrize("cfg,cycles", [(AM261X_CFG, 48), (AM261X_200_CFG, 52)])
def test_msram_lbbo_is_deterministic(cfg, cycles):
    runs = {_lbbo_cycles(cfg, MSRAM_BASE, 16) for _ in range(20)}
    assert runs == {cycles}


@pytest.mark.parametrize("nbytes,cycles", [(4, 3), (16, 6)])
@pytest.mark.parametrize("cfg", [AM261X_CFG, AM261X_200_CFG])
def test_am261x_local_ram_unchanged(cfg, nbytes, cycles):
    # DRAM0 / Shared RAM measured equal on silicon (3 / 6 cycles) at both clocks.
    assert _lbbo_cycles(cfg, 0x0000, nbytes) == cycles
    assert _lbbo_cycles(cfg, 0x10000, nbytes) == cycles


@pytest.mark.parametrize("nbytes,cycles", [(4, 21), (16, 24), (32, 28), (64, 36)])
def test_am243x_msram_lbbo_matches_silicon(nbytes, cycles):
    assert _lbbo_cycles(AM243X_CFG, 0x70000000, nbytes) == cycles


def test_am243x_msram_lbbo_is_deterministic():
    runs = {_lbbo_cycles(AM243X_CFG, 0x70000000, 16) for _ in range(20)}
    assert runs == {24}


@pytest.mark.parametrize("nbytes,cycles", [(4, 3), (16, 6), (64, 18)])
def test_am243x_local_ram_unchanged(nbytes, cycles):
    # DRAM0 / Shared RAM measured equal to the simulator on silicon (3 / 6 / 18 cycles).
    assert _lbbo_cycles(AM243X_CFG, 0x0000, nbytes) == cycles
    assert _lbbo_cycles(AM243X_CFG, 0x10000, nbytes) == cycles
