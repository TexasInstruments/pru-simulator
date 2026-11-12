"""AM261x / AM263x (ICSS-M): C10 and C12-C14 show a pointer, loads read zero.

Silicon (LP-AM261, ICTE direct read and CCS register view, both PRUs): the
constant table holds C10 0x2A000 (PRU0) / 0x2A200 (PRU1), C12 0x27000, C13
0x2C000, C14 0x24800, and an LBCO through each returns zeros without a fault.
The reserved ranges are sized from the AM261x TRM SPRUJB6 local memory map:
C14 up to PROTECT at 0x24C00, C12 up to UART0 at 0x28000, C10/C13 up to IEP0 at
0x2E000. The ranges are modelled by mem/icssm_absent.py, together with the
ICSSG-only blocks that share them. A 32-bit store was dropped on silicon at the
start of PA_STATS_QRAM, TM_CFG_PRU0 and PA_STATS_CRAM; writes elsewhere in the
ranges were not measured. They are ignored and logged here.
"""
import logging

import pytest

from mem.icssm_absent import IcssgOnlyBlockRegion
from simulator import Simulator

TARGETS = ["AM261x", "AM263x"]
CORES = ["pru0", "pru1"]
POINTERS = {"pru0": {10: 0x2A000, 12: 0x27000, 13: 0x2C000, 14: 0x24800},
            "pru1": {10: 0x2A200, 12: 0x27000, 13: 0x2C000, 14: 0x24800}}
# (first byte, last byte + 1) of each reserved window.
WINDOWS = {"C14": (0x24800, 0x24C00),
           "C12": (0x27000, 0x28000),
           "C10/C13": (0x2A000, 0x2E000)}


def _run(sim, core, source):
    assert sim.load(core, source) == []
    for _ in range(100):
        if sim.cores[core].halted:
            break
        sim.step(core)
    return sim.cores[core]


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("core", CORES)
@pytest.mark.parametrize("c", [10, 12, 13, 14])
def test_lbco_through_a_reserved_pointer_returns_zeros(sim_config, target, core, c):
    sim = Simulator(sim_config(target=target))
    # Non-zero own DRAM at local 0 and in the target: a load that fell through
    # to own DRAM, or read stale data, would not give zeros.
    sim.memory.write(0x0000, b"\xFF" * 64)
    sim.memory.write(0x2000, b"\xFF" * 64)
    src = f"""
        ldi32 r2, 0xFFFFFFFF
        ldi32 r3, 0xFFFFFFFF
        ldi32 r4, 0xFFFFFFFF
        ldi32 r5, 0xFFFFFFFF
        lbco &r2, c{c}, 0, 16
        halt
    """
    cpu = _run(sim, core, src)
    assert cpu.fault is None and cpu.halted
    assert [cpu.registers.read_full(i) for i in range(2, 6)] == [0, 0, 0, 0]
    assert sim.cores[core].constant_table.resolve(c) == POINTERS[core][c]


@pytest.mark.parametrize("target", TARGETS)
def test_whole_reserved_windows_read_zero_to_the_last_word(sim_config, target):
    sim = Simulator(sim_config(target=target))
    for name, (lo, hi) in WINDOWS.items():
        assert sim.memory_read(lo, hi - lo) == b"\0" * (hi - lo), name
        # Every word is in a read-zero window, and the windows stop at hi.
        for addr in range(lo, hi, 4):
            assert isinstance(sim.memory._find_region(addr), IcssgOnlyBlockRegion), hex(addr)
        try:
            after = sim.memory._find_region(hi)
        except ValueError:
            after = None
        assert not isinstance(after, IcssgOnlyBlockRegion), name


@pytest.mark.parametrize("target", TARGETS)
def test_neighbouring_blocks_are_not_swallowed(sim_config, target):
    sim = Simulator(sim_config(target=target))
    # IEP0 starts right after the C10/C13 window, CFG registers sit below C12.
    assert sim.memory.regions and sim.memory._find_region(0x2E000).name == "ICSS_IEP"
    assert sim.memory._find_region(0x26030).name == "ICSS_IEPCLK"
    with pytest.raises(ValueError):
        sim.memory.read(0x24C00, 4)          # PROTECT: not modelled, not reserved-zero
    with pytest.raises(ValueError):
        sim.memory.read(0x28000, 4)          # UART0: not modelled


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("core", CORES)
@pytest.mark.parametrize("c", [10, 12, 13, 14])
def test_sbco_through_a_reserved_pointer_is_ignored_and_logged(
        sim_config, target, core, c, caplog):
    sim = Simulator(sim_config(target=target))
    src = f"""
        ldi32 r2, 0xDEADBEEF
        sbco &r2, c{c}, 0x10, 4
        lbco &r3, c{c}, 0x10, 4
        halt
    """
    with caplog.at_level(logging.WARNING, logger="simulator"):
        cpu = _run(sim, core, src)
    assert cpu.fault is None and cpu.halted        # no raise
    assert cpu.registers.read_full(3) == 0         # write had no effect
    assert any("ignored" in r.getMessage() and "absent on ICSS-M" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("core", CORES)
def test_constant_table_view_lists_the_pointers(sim_config, target, core):
    sim = Simulator(sim_config(target=target))
    view = {row["c"]: row["addr"] for row in sim.constant_table_view(core)}
    assert len(view) == 32
    for c, addr in POINTERS[core].items():
        assert view[c] == addr, f"c{c}"


def test_am243x_is_unchanged(sim_config):
    sim = Simulator(sim_config(target="AM243x"))
    assert not any(r.name.startswith("RSVD_") for r in sim.memory.regions)
    assert sim.cores["pru0"].constant_table.resolve(10) == 0x2A000
    with pytest.raises(ValueError):
        sim.memory.read(0x2A000, 4)
