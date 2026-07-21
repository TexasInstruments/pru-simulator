"""Per-core DRAM mapping: each PRU sees its own DRAM at core-local 0x0000."""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from simulator import Simulator

_PROBE = """
start:
        ldi  r10, 0
        lbbo r1, r10, 0, 4
        ldi  r10, 0x2000
        lbbo r2, r10, 0, 4
        ldi  r10, 0
        lbco r3, c24, r10, 4
        lbco r4, c25, r10, 4
        halt
"""


def _run(core):
    sim = Simulator()
    sim.memory.write(0x0000, bytes([0xA0, 0xA1, 0xA2, 0xA3]))   # DRAM0
    sim.memory.write(0x2000, bytes([0xB0, 0xB1, 0xB2, 0xB3]))   # DRAM1
    assert sim.load(core, _PROBE) == []
    sim.step(core, 20)
    r = sim.registers(core)
    return r[1], r[2], r[3], r[4]


DRAM0 = 0xA3A2A1A0
DRAM1 = 0xB3B2B1B0


def test_pru0_sees_dram0_at_local_zero():
    at0, at2000, c24, c25 = _run("pru0")
    assert at0 == DRAM0 and at2000 == DRAM1
    assert c24 == DRAM0 and c25 == DRAM1


def test_pru1_sees_its_own_dram1_at_local_zero():
    """PRU1's local 0x0000 is DRAM1, and 0x2000 is DRAM0 -- the swap."""
    at0, at2000, c24, c25 = _run("pru1")
    assert at0 == DRAM1, "PRU1 local 0x0000 must reach DRAM1 (own DRAM)"
    assert at2000 == DRAM0, "PRU1 local 0x2000 must reach DRAM0"
    assert c24 == DRAM1, "PRU1 c24 must reach its own DRAM (DRAM1)"
    assert c25 == DRAM0, "PRU1 c25 must reach the other core's DRAM (DRAM0)"


def test_rtu0_is_not_swapped():
    at0, at2000, _, _ = _run("rtu0")
    assert at0 == DRAM0 and at2000 == DRAM1


def test_pru1_store_lands_in_dram1():
    sim = Simulator()
    src = """
start:
        ldi  r5, 0x1234
        ldi  r10, 0x0100
        sbbo r5, r10, 0, 4
        halt
"""
    assert sim.load("pru1", src) == []
    sim.step("pru1", 20)
    # Written at PRU1-local 0x0100 -> global 0x2100 (DRAM1), not 0x0100.
    assert int.from_bytes(sim.memory_read(0x2100, 4), "little") == 0x1234
    assert int.from_bytes(sim.memory_read(0x0100, 4), "little") == 0


def test_pru1_sbco_store_is_translated():
    """SBCO via c24 (core-local 0x0000) must translate to DRAM1 (global 0x2000)."""
    sim = Simulator()
    src = """
start:
        ldi  r5, 0x4567
        sbco r5, c24, 0x80, 4
        halt
"""
    assert sim.load("pru1", src) == []
    sim.step("pru1", 20)
    # Written at PRU1-local 0x80 (via c24) -> global 0x2080 (DRAM1), not 0x80.
    assert int.from_bytes(sim.memory_read(0x2080, 4), "little") == 0x4567
    assert int.from_bytes(sim.memory_read(0x0080, 4), "little") == 0


def test_shared_ram_is_not_swapped():
    sim = Simulator()
    src = """
start:
        ldi  r5, 0x55
        ldi  r10, 0
        ldi  r10.w2, 0x0001
        sbbo r5, r10, 0, 4
        halt
"""
    assert sim.load("pru1", src) == []
    sim.step("pru1", 20)
    assert int.from_bytes(sim.memory_read(0x00010000, 4), "little") == 0x55
