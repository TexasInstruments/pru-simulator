"""A store to ICSS-local RAM that starts misaligned and spans two or more
32-bit words costs one extra cycle: 2 + W instead of 1 + W.

Silicon evidence: one-instruction CYCLE-bracketed probes, 8 identical runs
each, on LP-AM261 (ICSSM0 PRU0, 2026-10-19) and LP-AM243 (ICSSG0 PRU0,
2026-10-21). Both boards gave the same table:

    SBCO &r4, C28, off, L  (Shared RAM)   off=0  1  2  3
        L=1                                     2  2  2  2
        L=2                                     2  2  2  4
        L=4                                     2  4  4  4
        L=8                                     3  5  5  5
    SBCO &r4, C24, 0x40+off, L (own DRAM0) matches the C28 rows for L=2/4/8.
    LBCO from Shared RAM is 2 + W at every offset (no misaligned penalty).
    MSRAM stores (LP-AM261) are 1 + W at every offset (no misaligned penalty).

W = the number of 32-bit words the access spans (2 bytes at offset 3 is W=2).
"""

import pytest

from simulator import Simulator

SHARED_ROWS = {
    1: [2, 2, 2, 2],
    2: [2, 2, 2, 4],
    4: [2, 4, 4, 4],
    8: [3, 5, 5, 5],
}


def _cost(sim_config, setup: str, instr: str) -> int:
    """Cycles charged for *instr* alone, after *setup* has run."""
    sim = Simulator(sim_config(target="AM243x"))
    core = sim.cores["pru0"]
    assert sim.load("pru0", setup + instr + "\nhalt\n") == []
    # LDI32 expands to two instructions, so step to the measured one by PC.
    target = len(core.instructions) - 2
    while core.pc != target:
        sim.step("pru0")
    before = core.counters.cycles
    sim.step("pru0")
    return core.counters.cycles - before


SETUP = "ldi32 r4, 0x44332211\nldi32 r5, 0x88776655\n"


@pytest.mark.parametrize("length", sorted(SHARED_ROWS))
@pytest.mark.parametrize("offset", range(4))
def test_shared_ram_store_matches_silicon(sim_config, offset, length):
    cost = _cost(sim_config, SETUP, f"sbco &r4, c28, {offset}, {length}")
    assert cost == SHARED_ROWS[length][offset]


@pytest.mark.parametrize("length", [2, 4, 8])
@pytest.mark.parametrize("offset", range(4))
def test_own_dram_store_matches_silicon(sim_config, offset, length):
    cost = _cost(sim_config, SETUP, f"sbco &r4, c24, {0x40 + offset}, {length}")
    assert cost == SHARED_ROWS[length][offset]


@pytest.mark.parametrize("offset", range(4))
def test_misaligned_shared_ram_load_has_no_penalty(sim_config, offset):
    words = (offset + 4 + 3) // 4
    assert _cost(sim_config, SETUP, f"lbco &r4, c28, {offset}, 4") == 2 + words


@pytest.mark.parametrize("offset", range(4))
def test_misaligned_msram_store_has_no_penalty(sim_config, offset):
    words = (offset + 4 + 3) // 4
    setup = SETUP + "ldi32 r1, 0x70000000\n"
    assert _cost(sim_config, setup, f"sbbo &r4, r1, {offset}, 4") == 1 + words


def test_penalty_defaults_to_off_for_a_region_that_does_not_set_it():
    from mem.regions import MemoryRegion

    region = MemoryRegion("X", 0x0, 0x100, read_latency=2, write_latency=1)
    assert region.calc_write_stalls(1, 4) == 2          # 1 + (W - 1), W = 2
    region.misaligned_write_penalty = 1
    assert region.calc_write_stalls(1, 4) == 3
    assert region.calc_write_stalls(0, 4) == 1          # aligned: unchanged
    assert region.calc_write_stalls(1, 2) == 1          # one word: unchanged


# --- shipped presets -------------------------------------------------------
# The cases above use the sim_config fixture.  The presets are separate files,
# and a preset that omits the penalty silently charges 1 + W (PR #78 review:
# config/memory_am261x.cfg did).  LP-AM261 is the board the table came from.

import configparser
import glob
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESETS = {
    "AM261x": os.path.join(REPO_ROOT, "config", "memory_am261x.cfg"),
    "AM243x": os.path.join(REPO_ROOT, "config", "memory_am243x.cfg"),
}
SHIPPED_CONFIGS = sorted(
    glob.glob(os.path.join(REPO_ROOT, "config", "memory*.cfg"))
    + glob.glob(os.path.join(REPO_ROOT, "memory*.cfg"))
)
LOCAL_RAM = ("DRAM0", "DRAM1", "ICSS_SHARED")


def _preset_cost(cfg_path: str, setup: str, instr: str) -> int:
    sim = Simulator(cfg_path)
    core = sim.cores["pru0"]
    assert sim.load("pru0", setup + instr + "\nhalt\n") == []
    target = len(core.instructions) - 2
    while core.pc != target:
        sim.step("pru0")
    before = core.counters.cycles
    sim.step("pru0")
    return core.counters.cycles - before


@pytest.mark.parametrize("preset", sorted(PRESETS))
@pytest.mark.parametrize("length", sorted(SHARED_ROWS))
@pytest.mark.parametrize("offset", range(4))
def test_preset_shared_ram_store_matches_silicon(preset, offset, length):
    cost = _preset_cost(PRESETS[preset], SETUP, f"sbco &r4, c28, {offset}, {length}")
    assert cost == SHARED_ROWS[length][offset]


@pytest.mark.parametrize("preset", sorted(PRESETS))
@pytest.mark.parametrize("length", [2, 4, 8])
@pytest.mark.parametrize("offset", range(4))
def test_preset_own_dram_store_matches_silicon(preset, offset, length):
    cost = _preset_cost(PRESETS[preset], SETUP, f"sbco &r4, c24, {0x40 + offset}, {length}")
    assert cost == SHARED_ROWS[length][offset]


@pytest.mark.parametrize("offset", range(4))
def test_am261x_preset_msram_store_has_no_penalty(offset):
    cfg = configparser.ConfigParser()
    cfg.read(PRESETS["AM261x"])
    base = int(cfg["MS_RAM"]["base"], 0)
    words = (offset + 4 + 3) // 4
    setup = SETUP + f"ldi32 r1, 0x{base:08X}\n"
    assert _preset_cost(PRESETS["AM261x"], setup, f"sbbo &r4, r1, {offset}, 4") == 1 + words


@pytest.mark.parametrize("path", SHIPPED_CONFIGS, ids=os.path.basename)
def test_every_shipped_config_sets_the_local_ram_penalty(path):
    cfg = configparser.ConfigParser()
    cfg.read(path)
    for section in LOCAL_RAM:
        if section in cfg:
            assert cfg[section].getint("misaligned_write_penalty", 0) == 1, (
                f"{os.path.basename(path)} [{section}] must set misaligned_write_penalty = 1")
    if "MS_RAM" in cfg:
        assert cfg["MS_RAM"].getint("misaligned_write_penalty", 0) == 0
