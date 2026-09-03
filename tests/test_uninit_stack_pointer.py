"""A memory fault through an uninitialised R2 should say what it is.

The behaviour is correct and unchanged: the access faults and the core halts.
Only the diagnosis is added. Without it the message reads as a wild pointer bug
in the firmware, when the actual cause is a missing C runtime — and the linker
pattern that causes it (`-e main`) is the one nearly every headless PRU example
uses.
"""

import logging

from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from pru_io.io_port import IOPort
from xfr.xfr_bus import XFRBus


def _run(source, caplog):
    """Assemble, run to halt, and return everything logged at ERROR."""
    mem = MemoryBus()
    mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
    core = PRUCore("PRU0", mem, XFRBus(), IOPort())
    errors = core.load_asm(source)
    assert errors == [], errors
    with caplog.at_level(logging.ERROR):
        core.run(20)
    return core, caplog.text


def test_spill_through_zero_r2_is_diagnosed(caplog):
    """The real sequence: a frame setup subtracts from R2, then spills.

    With R2 uninitialised that subtraction wraps below zero, so the store lands
    at the top of the address space - 0xFFFFFFE2 for a 30-byte frame, which is
    the exact address the original diagnosis reported.
    """
    core, text = _run("""
        LDI r2, 0
        SUB r2, r2, 30
        LDI r10, 0x1234
        SBBO &r10, r2, 0, 4
    """, caplog)

    assert "fault" in text
    assert "R2" in text and "stack pointer" in text
    assert "_c_int00" in text, "the hint must name what normally sets R2 up"
    assert core.halted


def test_no_hint_when_r2_is_initialised(caplog):
    """A store through a stack pointer that WAS set up is a different bug.

    R2 here points outside any mapped region, so the access still faults - but
    the stack was initialised, so the cause is pointer arithmetic rather than a
    missing runtime.

    Attaching the hint to every fault would make it noise, and would mislead on
    a firmware whose stack is fine but whose pointer arithmetic is not.
    """
    core, text = _run("""
        LDI r2, 0x7000
        LDI r10, 0x1234
        SBBO &r10, r2, 0, 4
    """, caplog)

    assert "fault" in text
    assert "_c_int00" not in text
    assert core.halted


def test_no_hint_when_the_base_is_not_r2(caplog):
    """R2 is the ABI stack pointer; a fault through any other base is unrelated,
    even when that register also happens to be zero."""
    core, text = _run("""
        LDI r5, 0
        SUB r5, r5, 30
        LDI r10, 0x1234
        SBBO &r10, r5, 0, 4
    """, caplog)

    assert "fault" in text
    assert "_c_int00" not in text
    assert core.halted


def test_the_load_side_is_diagnosed_too(caplog):
    """A spill is a store, but the reload faults the same way and is just as
    confusing on its own."""
    core, text = _run("""
        LDI r2, 0
        SUB r2, r2, 30
        LBBO &r10, r2, 0, 4
    """, caplog)

    assert "fault" in text
    assert "_c_int00" in text
    assert core.halted
