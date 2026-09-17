"""Tests for core/pru_core.py — PRU Core execution engine."""

import logging

import pytest

from core.pru_core import PRUCore, UnsupportedXFRError
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from xfr.xfr_bus import XFRBus
from pru_io.io_port import IOPort


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_core(asm: str) -> PRUCore:
    """Create a PRUCore loaded with *asm* and a small DRAM region."""
    mem = MemoryBus()
    mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
    xfr = XFRBus()
    io = IOPort()
    core = PRUCore("PRU0", mem, xfr, io)
    errors = core.load_asm(asm)
    assert errors == [], f"Assembly errors: {errors}"
    return core


def run_to_halt(core: PRUCore, max_steps: int = 1000) -> None:
    """Run the core until halted or max_steps exceeded."""
    core.run(max_steps)


def reg(core: PRUCore, n: int) -> int:
    """Return the full 32-bit value of register Rn."""
    return core.registers.read_full(n)


def core_warnings(caplog) -> list[str]:
    """Warnings emitted by the core itself, excluding lower-level bus logging."""
    return [r.getMessage() for r in caplog.records if r.name == "core.pru_core"]


def test_unmodelled_xfr_keeps_hardware_zero_semantics_and_is_recorded(caplog):
    """An unconnected broadside ID reads zeros on hardware; it must here too.

    The simulator's job is to make the event visible, not to invent a trap the
    silicon does not have -- so the run continues, and the record is what tells
    a caller the result came from an unmodelled widget rather than a real one.
    """
    core = make_core("ldi r2, 0x1234\nxin 99, &r2, 4\nxin 99, &r2, 4\nhalt\n")
    with caplog.at_level(logging.WARNING, logger="core.pru_core"):
        run_to_halt(core)
    assert reg(core, 2) == 0
    record = core.unsupported_xfr[99]
    assert record["core"] == "PRU0"
    assert record["opcodes"] == ["XIN"]
    assert record["count"] == 2
    assert record["first_pc"] == 1
    # Warned once per device ID, not once per transfer.
    assert len(core_warnings(caplog)) == 1


def test_unmodelled_xout_is_a_no_op_and_does_not_halt_the_run():
    """XOUT to an unconnected ID is ignored on hardware; the run must survive it."""
    core = make_core("ldi r2, 0x1234\nxout 99, &r2, 4\nhalt\n")
    run_to_halt(core)
    assert core.halted
    assert reg(core, 2) == 0x1234
    assert core.unsupported_xfr[99]["opcodes"] == ["XOUT"]


def test_registered_scratchpad_is_not_reported_as_unmodelled():
    """Guard against the diagnostic misclassifying a device the bus does model."""
    core = make_core("ldi r2, 0x1234\nxout 10, &r2, 4\n"
                     "ldi r2, 0\nxin 10, &r2, 4\nhalt\n")
    core.strict_unsupported_xfr = True   # a false positive would raise here
    run_to_halt(core)
    assert reg(core, 2) == 0x1234
    assert core.unsupported_xfr == {}


def test_reset_clears_the_unsupported_xfr_record():
    core = make_core("xin 99, &r2, 4\nhalt\n")
    run_to_halt(core)
    assert 99 in core.unsupported_xfr
    core.reset()
    assert core.unsupported_xfr == {}


@pytest.mark.parametrize("opcode", ["xin", "xout", "xchg"])
def test_strict_mode_fails_the_run_on_an_unmodelled_device(opcode: str):
    """Opt-in oracle check: a caller can demand a failure instead of zero data."""
    core = make_core(f"{opcode} 99, &r2, 4\nhalt\n")
    core.strict_unsupported_xfr = True
    with pytest.raises(UnsupportedXFRError,
                       match=rf"{opcode.upper()} XFR device ID 99 \(0x63\)"
                             rf".*4 byte\(s\).*R2\.b0"):
        core.run(4)
    assert core.unsupported_xfr[99]["opcodes"] == [opcode.upper()]


# ---------------------------------------------------------------------------
# LDI — load immediate
# ---------------------------------------------------------------------------

class TestLDI:
    def test_ldi_basic(self):
        core = make_core("ldi r0, 42\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 42

    def test_ldi_hex(self):
        core = make_core("ldi r3, 0xFF\nhalt\n")
        run_to_halt(core)
        assert reg(core, 3) == 255

    def test_ldi_zero(self):
        core = make_core("ldi r5, 0\nhalt\n")
        run_to_halt(core)
        assert reg(core, 5) == 0

    def test_ldi_large(self):
        core = make_core("ldi r1, 65535\nhalt\n")
        run_to_halt(core)
        assert reg(core, 1) == 65535


# ---------------------------------------------------------------------------
# MOV
# ---------------------------------------------------------------------------

class TestMOV:
    def test_mov_register_to_register(self):
        core = make_core("ldi r1, 77\nmov r0, r1\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 77

    def test_mov_immediate(self):
        core = make_core("mov r2, 99\nhalt\n")
        run_to_halt(core)
        assert reg(core, 2) == 99


# ---------------------------------------------------------------------------
# ADD
# ---------------------------------------------------------------------------

class TestADD:
    def test_add_immediate(self):
        core = make_core("ldi r1, 10\nadd r0, r1, 5\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 15

    def test_add_register(self):
        core = make_core("ldi r1, 10\nldi r2, 20\nadd r0, r1, r2\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 30

    def test_add_carry_set(self):
        core = make_core("ldi32 r1, 0xFFFFFFFF\nadd r0, r1, 1\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 0
        assert core.registers.carry is True

    def test_ldi32_malformed_hex_rejected(self):
        mem = MemoryBus()
        mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
        core = PRUCore("PRU0", mem, XFRBus(), IOPort())
        errors = core.load_asm("ldi32 r2, 0xabcv1234\nhalt\n")
        assert errors != []

    def test_add_no_carry(self):
        core = make_core("ldi r1, 10\nadd r0, r1, 5\nhalt\n")
        run_to_halt(core)
        assert core.registers.carry is False


# ---------------------------------------------------------------------------
# SUB
# ---------------------------------------------------------------------------

class TestSUB:
    def test_sub_basic(self):
        core = make_core("ldi r1, 50\nsub r0, r1, 20\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 30

    def test_sub_underflow(self):
        core = make_core("ldi r1, 5\nsub r0, r1, 10\nhalt\n")
        run_to_halt(core)
        # 5 - 10 wraps around to 0xFFFFFFFB
        assert reg(core, 0) == (5 - 10) & 0xFFFFFFFF
        assert core.registers.carry is False  # borrow → carry clear


# ---------------------------------------------------------------------------
# ADC / SUC / RSB / RSC
# ---------------------------------------------------------------------------

class TestArithWithCarry:
    def test_adc(self):
        # Set carry by overflow, then use ADC
        core = make_core(
            "ldi32 r1, 0xFFFFFFFF\n"
            "add r0, r1, 1\n"   # sets carry
            "ldi r2, 5\n"
            "adc r3, r2, 0\n"   # r3 = 5 + 0 + carry(1) = 6
            "halt\n"
        )
        run_to_halt(core)
        assert reg(core, 3) == 6

    def test_rsb(self):
        core = make_core("ldi r1, 10\nldi r2, 30\nrsb r0, r1, r2\nhalt\n")
        run_to_halt(core)
        # rsb: src2 - src1 = 30 - 10 = 20
        assert reg(core, 0) == 20


# ---------------------------------------------------------------------------
# AND / OR / XOR / NOT
# ---------------------------------------------------------------------------

class TestLogic:
    def test_and(self):
        core = make_core("ldi r1, 0xFF\nldi r2, 0x0F\nand r0, r1, r2\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 0x0F

    def test_or(self):
        core = make_core("ldi r1, 0xF0\nldi r2, 0x0F\nor r0, r1, r2\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 0xFF

    def test_xor(self):
        core = make_core("ldi r1, 0xFF\nldi r2, 0x0F\nxor r0, r1, r2\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 0xF0

    def test_not(self):
        core = make_core("ldi r1, 0\nnot r0, r1\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 0xFFFFFFFF


# ---------------------------------------------------------------------------
# LSL / LSR
# ---------------------------------------------------------------------------

class TestShift:
    def test_lsl(self):
        core = make_core("ldi r1, 1\nlsl r0, r1, 4\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 16

    def test_lsr(self):
        core = make_core("ldi r1, 256\nlsr r0, r1, 4\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 16

    def test_lsl_overflow_masked(self):
        core = make_core("ldi32 r1, 0x80000000\nlsl r0, r1, 1\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 0


# ---------------------------------------------------------------------------
# SET / CLR bit operations
# ---------------------------------------------------------------------------

class TestBitOps:
    def test_set_bit(self):
        core = make_core("ldi r1, 0\nset r0, r1, 3\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 8

    def test_clr_bit(self):
        core = make_core("ldi r1, 0xFF\nclr r0, r1, 3\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 0xFF & ~8

    def test_lmbd_finds_bit(self):
        core = make_core("ldi r1, 0x10\nlmbd r0, r1, 1\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 4  # bit 4 is the leftmost 1

    def test_min(self):
        core = make_core("ldi r1, 10\nldi r2, 20\nmin r0, r1, r2\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 10

    def test_max(self):
        core = make_core("ldi r1, 10\nldi r2, 20\nmax r0, r1, r2\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 20


# ---------------------------------------------------------------------------
# QBA — unconditional branch
# ---------------------------------------------------------------------------

class TestQBA:
    def test_qba_jumps_over_instruction(self):
        asm = (
            "qba skip\n"
            "ldi r0, 99\n"  # should be skipped
            "skip:\n"
            "ldi r1, 42\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 0) == 0    # skipped
        assert reg(core, 1) == 42

    def test_qba_loops(self):
        asm = (
            "ldi r0, 0\n"
            "ldi r1, 0\n"
            "top:\n"
            "add r0, r0, 1\n"
            "qbeq done, r0, 3\n"
            "qba top\n"
            "done:\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 0) == 3


# ---------------------------------------------------------------------------
# QBEQ — conditional branch equal
# ---------------------------------------------------------------------------

class TestQBEQ:
    def test_qbeq_taken(self):
        asm = (
            "ldi r1, 5\n"
            "qbeq done, r1, 5\n"
            "ldi r0, 99\n"   # should be skipped
            "done:\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 0) == 0

    def test_qbeq_not_taken(self):
        asm = (
            "ldi r1, 5\n"
            "qbeq done, r1, 6\n"
            "ldi r0, 99\n"   # should execute
            "done:\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 0) == 99

    def test_qbne_taken(self):
        asm = (
            "ldi r1, 5\n"
            "qbne done, r1, 6\n"   # 6 != 5, taken
            "ldi r0, 99\n"
            "done:\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 0) == 0

    def test_qbgt_taken(self):
        asm = (
            "ldi r1, 3\n"
            "qbgt done, r1, 1\n"   # op_val=1 > reg_val=3? No; but qbgt: op_val > reg_val
            "ldi r0, 77\n"
            "done:\n"
            "halt\n"
        )
        # qbgt: op_val(1) > reg_val(3)? No → not taken → r0=77
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 0) == 77

    def test_qbgt_taken_correct(self):
        asm = (
            "ldi r1, 3\n"
            "qbgt done, r1, 5\n"   # op_val=5 > reg_val=3? Yes → taken
            "ldi r0, 77\n"
            "done:\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 0) == 0

    def test_qbbs_bit_set(self):
        asm = (
            "ldi r1, 0x08\n"   # bit 3 set
            "qbbs done, r1, 3\n"
            "ldi r0, 55\n"
            "done:\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 0) == 0  # branch taken

    def test_qbbc_bit_clear(self):
        asm = (
            "ldi r1, 0\n"   # bit 3 clear
            "qbbc done, r1, 3\n"
            "ldi r0, 55\n"
            "done:\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 0) == 0  # branch taken


# ---------------------------------------------------------------------------
# HALT
# ---------------------------------------------------------------------------

class TestHALT:
    def test_halt_stops_execution(self):
        asm = "ldi r0, 10\nhalt\nldi r0, 99\n"
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 0) == 10
        assert core.halted is True

    def test_step_after_halt_does_nothing(self):
        core = make_core("halt\n")
        core.step()
        assert core.halted is True
        cycles_after_halt = core.counters.cycles
        core.step()
        assert core.counters.cycles == cycles_after_halt


# ---------------------------------------------------------------------------
# Cycle counter
# ---------------------------------------------------------------------------

class TestCycleCounter:
    def test_counter_increments_per_step(self):
        core = make_core("ldi r0, 1\nldi r1, 2\nhalt\n")
        run_to_halt(core)
        # 3 instructions: ldi r0, ldi r1, halt
        assert core.counters.instruction_count == 3

    def test_counter_starts_at_zero(self):
        core = make_core("halt\n")
        assert core.counters.cycles == 0

    def test_counter_after_one_step(self):
        core = make_core("ldi r0, 5\nhalt\n")
        core.step()
        assert core.counters.cycles == 1


# ---------------------------------------------------------------------------
# Memory stall cycles
# ---------------------------------------------------------------------------

class TestMemoryStalls:
    def test_memory_stalls_added(self):
        asm = (
            "ldi r1, 0x100\n"     # base address
            "sbbo &r2, r1, 0, 4\n"  # write 4 bytes
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        # stall_cycles should be > 0 because region has write_latency=1
        assert core.counters.stall_cycles > 0

    def test_sbbo_lbbo_roundtrip(self):
        asm = (
            "ldi32 r2, 0xABCD1234\n"  # data to store
            "ldi r1, 0x100\n"          # address
            "sbbo &r2, r1, 0, 4\n"     # store r2 to mem[0x100]
            "ldi r2, 0\n"              # clear r2
            "lbbo &r3, r1, 0, 4\n"     # load from mem[0x100] into r3
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 3) == 0xABCD1234

    def test_sbbo_lbbo_multiple_regs(self):
        asm = (
            "ldi32 r2, 0x11223344\n"
            "ldi32 r3, 0x55667788\n"
            "ldi r1, 0x200\n"
            "sbbo &r2, r1, 0, 8\n"    # store 8 bytes: r2 and r3
            "ldi r2, 0\n"
            "ldi r3, 0\n"
            "lbbo &r4, r1, 0, 8\n"    # load 8 bytes into r4, r5
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 4) == 0x11223344
        assert reg(core, 5) == 0x55667788


# ---------------------------------------------------------------------------
# XFR operations
# ---------------------------------------------------------------------------

class TestXFR:
    def test_xout_xin_roundtrip(self):
        asm = (
            "ldi32 r2, 0xDEADBEEF\n"
            "xout 10, &r2, 4\n"        # write r2 to scratchpad bank0
            "ldi r2, 0\n"
            "xin 10, &r2, 4\n"         # read back into r2
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 2) == 0xDEADBEEF

    def test_xchg(self):
        asm = (
            "ldi32 r2, 0xAAAAAAAA\n"
            "xout 11, &r2, 4\n"         # write to bank1
            "ldi32 r2, 0xBBBBBBBB\n"
            "xchg 11, &r2, 4\n"         # exchange: old(0xAAAA) → r2, new(0xBBBB) → pad
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 2) == 0xAAAAAAAA


# ---------------------------------------------------------------------------
# R30 / R31 I/O port
# ---------------------------------------------------------------------------

class TestIOPort:
    def test_write_r30_updates_gpo(self):
        core = make_core("ldi r30, 0xAB\nhalt\n")
        run_to_halt(core)
        assert core.io_port.gpo == 0xAB

    def test_ldi_r30_all_pins(self):
        core = make_core("ldi32 r30, 0xFFFFF\nhalt\n")
        run_to_halt(core)
        assert core.io_port.gpo == 0xFFFFF

    def test_read_r31_gets_gpi(self):
        asm = "mov r0, r31\nhalt\n"
        core = make_core(asm)
        core.io_port.set_gpi_pin(5, True)   # set GPI pin 5
        run_to_halt(core)
        assert reg(core, 0) == (1 << 5)

    def test_r31_reads_current_gpi(self):
        asm = "mov r0, r31\nhalt\n"
        core = make_core(asm)
        core.io_port.set_gpi_word(0x3FF)
        run_to_halt(core)
        assert reg(core, 0) == 0x3FF


# ---------------------------------------------------------------------------
# ZERO / FILL
# ---------------------------------------------------------------------------

class TestZeroFill:
    def test_zero_register(self):
        asm = (
            "ldi32 r2, 0xFFFFFFFF\n"
            "zero &r2, 4\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 2) == 0

    def test_fill_register(self):
        asm = (
            "ldi r2, 0\n"
            "fill &r2, 4\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 2) == 0xFFFFFFFF

    def test_zero_multiple_regs(self):
        asm = (
            "ldi32 r2, 0x12345678\n"
            "ldi32 r3, 0xABCDEF01\n"
            "zero &r2, 8\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 2) == 0
        assert reg(core, 3) == 0


# ---------------------------------------------------------------------------
# LOOP instruction
# ---------------------------------------------------------------------------

class TestLOOP:
    def test_loop_executes_count_times(self):
        asm = (
            "ldi r0, 0\n"
            "ldi r1, 3\n"
            "loop end, r1\n"
            "add r0, r0, 1\n"
            "end:\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 0) == 3


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_clears_registers(self):
        core = make_core("ldi r0, 42\nhalt\n")
        run_to_halt(core)
        assert reg(core, 0) == 42
        core.reset()
        assert reg(core, 0) == 0

    def test_reset_clears_pc(self):
        core = make_core("ldi r0, 42\nhalt\n")
        run_to_halt(core)
        assert core.pc > 0
        core.reset()
        assert core.pc == 0

    def test_reset_clears_halted(self):
        core = make_core("halt\n")
        run_to_halt(core)
        assert core.halted is True
        core.reset()
        assert core.halted is False

    def test_reset_clears_counters(self):
        core = make_core("ldi r0, 1\nhalt\n")
        run_to_halt(core)
        assert core.counters.cycles > 0
        core.reset()
        assert core.counters.cycles == 0


# ---------------------------------------------------------------------------
# load_asm error handling
# ---------------------------------------------------------------------------

class TestLoadAsm:
    def test_load_asm_returns_empty_errors_on_success(self):
        mem = MemoryBus()
        mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
        core = PRUCore("PRU0", mem, XFRBus(), IOPort())
        errors = core.load_asm("ldi r0, 1\nhalt\n")
        assert errors == []

    def test_load_asm_populates_instructions(self):
        core = make_core("ldi r0, 1\nldi r1, 2\nhalt\n")
        assert len(core.instructions) == 3


# ---------------------------------------------------------------------------
# Sub-register operations
# ---------------------------------------------------------------------------

class TestSubRegisters:
    def test_ldi_byte_field(self):
        asm = "ldi r0.b1, 0xAB\nhalt\n"
        core = make_core(asm)
        run_to_halt(core)
        # b1 is bits 8-15
        assert (reg(core, 0) >> 8) & 0xFF == 0xAB

    def test_add_word_field(self):
        asm = "ldi r1.w0, 100\nadd r0.w0, r1.w0, 50\nhalt\n"
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 0) & 0xFFFF == 150


# ---------------------------------------------------------------------------
# R31 byte / half-word selection
# ---------------------------------------------------------------------------

class TestR31SubRegisterSelection:
    """R31 is read live and written to hardware, but the operand's byte or
    half-word selection still applies.

    R31.b3 is the standard idiom for the Peripheral Interface valid/overflow
    flags (bits [31:24]); R30 in the same function already derives its write
    strobe from offset/width, so the two paths disagreed.
    """

    @staticmethod
    def _reg(offset, width):
        from core.operands import Register
        return Register(index=31, offset=offset, width=width)

    def _core(self):
        mem = MemoryBus()
        mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
        return PRUCore("PRU0", mem, XFRBus(), IOPort())

    def test_read_honours_byte_selection(self):
        core = self._core()
        core.io_port.read_r31 = lambda: 0xAABBCCDD
        b3, b0, full = self._reg(24, 8), self._reg(0, 8), self._reg(0, 32)
        assert core._read_operand(b3) == 0xAA
        assert core._read_operand(b0) == 0xDD
        assert core._read_operand(full) == 0xAABBCCDD

    def test_write_honours_byte_selection(self):
        core = self._core()
        seen = []
        core.io_port.write_r31 = seen.append
        core._write_operand(self._reg(24, 8), 0x01)
        core._write_operand(self._reg(0, 8), 0x01)
        core._write_operand(self._reg(0, 32), 0x12345678)
        assert seen == [0x01000000, 0x00000001, 0x12345678]
# WBS / WBC — wait until bit set / clear
# ---------------------------------------------------------------------------

class TestWaitBitInstructions:
    """WBS/WBC must STALL the core, not fall through.

    In the ISA they are QBBS/QBBC with a zero branch offset - branch-to-self -
    so holding the PC is the encoding's own semantics. A no-op implementation
    lets peripheral-driven firmware race past its own status polls: it never
    yields the simulated time the peripheral needs to produce data, so the bit
    it is waiting for never gets a chance to change.
    """

    @staticmethod
    def _wait_word(rs1_num: int, bit: int, wbc: bool) -> int:
        """Encode format-5 with a zero branch offset, which decodes to WBS/WBC."""
        word = 0b110 << 29                 # format 5
        word |= (1 if wbc else 0) << 28    # bs: set -> WBC, clear -> WBS
        word |= 1 << 24                    # io = 1: immediate bit number
        word |= (bit & 0x1F) << 16         # bit number
        word |= (0 & 7) << 13              # rs1_sel: byte 0
        word |= (rs1_num & 0x1F) << 8
        return word                        # br_hi/br_lo left 0 -> offset 0

    def _core_with(self, words):
        mem = MemoryBus()
        mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
        core = PRUCore("PRU0", mem, XFRBus(), IOPort())
        core.load_binary(words, b"", 0)
        return core

    def test_wbs_decodes_from_a_zero_offset_branch(self):
        core = self._core_with([self._wait_word(2, 4, wbc=False)])
        assert core.instructions[0].opcode == "WBS"

    def test_wbs_holds_pc_until_bit_is_set(self):
        core = self._core_with([self._wait_word(2, 4, wbc=False)])
        core.registers.write_full(2, 0)
        for _ in range(20):
            core.step()
        assert core.pc == 0, "WBS fell through with the bit clear"

        core.registers.write_full(2, 1 << 4)
        core.step()
        assert core.pc == 1, "WBS did not release once the bit was set"

    def test_wbc_holds_pc_until_bit_is_clear(self):
        core = self._core_with([self._wait_word(2, 4, wbc=True)])
        core.registers.write_full(2, 1 << 4)
        for _ in range(20):
            core.step()
        assert core.pc == 0, "WBC fell through with the bit set"

        core.registers.write_full(2, 0)
        core.step()
        assert core.pc == 1

    def test_waiting_accumulates_stall_cycles(self):
        core = self._core_with([self._wait_word(2, 4, wbc=False)])
        core.registers.write_full(2, 0)
        for _ in range(10):
            core.step()
        assert core.counters.stall_cycles > 0
