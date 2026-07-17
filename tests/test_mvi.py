"""Tests for MVIB/MVIW/MVID register-file indirect instructions.

The MVIx family uses a byte of R1 as a pointer (byte offset) into the 128-byte
register file (R0-R31, little-endian), transferring 1/2/4 bytes respectively.
"""

import pytest

from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from xfr.xfr_bus import XFRBus
from pru_io.io_port import IOPort


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_core(asm: str) -> PRUCore:
    mem = MemoryBus()
    mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
    xfr = XFRBus()
    io = IOPort()
    core = PRUCore("PRU0", mem, xfr, io)
    errors = core.load_asm(asm)
    assert errors == [], f"Assembly errors: {errors}"
    return core


def run_to_halt(core: PRUCore, max_steps: int = 1000) -> None:
    core.run(max_steps)


def reg(core: PRUCore, n: int) -> int:
    return core.registers.read_full(n)


def _mvi_word(mvi_subop, size, rd_sel, rd_num, rs1_sel, rs1_num):
    """Build a 32-bit MVI instruction word from PDSP spec fields."""
    word = (0b001 << 29)               # Format 2
    word |= (6 << 25)                  # subop 6 = MVI
    word |= (mvi_subop & 0xF) << 21   # bits 24:21
    word |= (size & 0x3) << 16        # bits 17:16
    word |= (rs1_sel & 0x7) << 13     # bits 15:13
    word |= (rs1_num & 0x1F) << 8     # bits 12:8
    word |= (rd_sel & 0x7) << 5       # bits 7:5
    word |= rd_num & 0x1F             # bits 4:0
    return word


# ---------------------------------------------------------------------------
# MVIB: 1-byte load (Rd = *Rs1, subop=1)
# ---------------------------------------------------------------------------

class TestMVIBLoad:
    def test_load_byte_from_pointer(self):
        """MVIB r2, *r1.b0 loads regfile[r1.b0] into r2."""
        # Byte offset 20 = register 5, byte 0 → r5.b0
        asm = (
            "ldi r5, 0x42\n"       # r5.b0 = 0x42
            "ldi r1, 20\n"         # r1.b0 = 20 → points to r5.b0
            "mvib r2, *r1.b0\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 2) & 0xFF) == 0x42

    def test_load_preserves_destination_upper_bytes(self):
        """MVIB only updates dest b0; upper bytes of dest register are unchanged."""
        asm = (
            "ldi r5, 0x77\n"
            "ldi r1, 20\n"         # → r5.b0 = 0x77
            "ldi r2, 0xABCD\n"     # pre-fill: r2 = 0x0000ABCD
            "mvib r2, *r1.b0\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 2) & 0xFF) == 0x77    # b0 updated
        assert (reg(core, 2) >> 8) == 0xAB      # b1 unchanged

    def test_load_using_r1_b1_as_pointer(self):
        """MVIB r3, *r1.b1 uses byte 1 of r1 as the pointer."""
        asm = (
            "ldi r0, 0x55\n"       # r0.b0 = 0x55 (byte offset 0)
            # Set r1.b1 = 0 via LDI (r1 = 0 → all bytes = 0)
            "ldi r1, 0\n"          # r1.b1 = 0 → pointer to byte offset 0
            "mvib r3, *r1.b1\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 3) & 0xFF) == 0x55

    def test_load_non_b0_byte_of_src_reg(self):
        """MVIB r2.b1, *r1.b0 loads into b1 of dest."""
        asm = (
            "ldi r5, 0x99\n"
            "ldi r1, 20\n"         # → r5.b0
            "mvib r2.b1, *r1.b0\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert ((reg(core, 2) >> 8) & 0xFF) == 0x99   # b1 = 0x99
        assert (reg(core, 2) & 0xFF) == 0              # b0 unchanged (was 0)


# ---------------------------------------------------------------------------
# MVIB: 1-byte store (*Rd = Rs1, subop=4)
# ---------------------------------------------------------------------------

class TestMVIBStore:
    def test_store_byte_to_register_file(self):
        """MVIB *r1.b0, r5 writes r5.b0 to regfile[r1.b0]."""
        asm = (
            "ldi r5, 0xAB\n"
            "ldi r1, 8\n"          # r1.b0 = 8 → points to r2.b0
            "mvib *r1.b0, r5\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 2) & 0xFF) == 0xAB

    def test_store_does_not_modify_pointer(self):
        """Store without auto-update leaves r1.b0 unchanged."""
        asm = (
            "ldi r5, 0x99\n"
            "ldi r1, 8\n"
            "mvib *r1.b0, r5\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 1) & 0xFF) == 8   # r1.b0 unchanged

    def test_store_from_non_b0_field(self):
        """MVIB *r1.b0, r5.b2 stores byte 2 of r5 to regfile[r1.b0]."""
        asm = (
            "ldi r5.w2, 0x1234\n"  # r5.b2 = 0x34
            "ldi r1, 8\n"          # → r2.b0
            "mvib *r1.b0, r5.b2\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 2) & 0xFF) == 0x34


# ---------------------------------------------------------------------------
# MVIB: post-increment (Rd = *Rs1++, subop=2; *Rd++ = Rs1, subop=8)
# ---------------------------------------------------------------------------

class TestMVIBPostInc:
    def test_load_post_inc_increments_pointer_by_1(self):
        """MVIB r2, *r1.b0++ loads byte and then increments r1.b0 by 1."""
        asm = (
            "ldi r5, 0x11\n"
            "ldi r1, 20\n"
            "mvib r2, *r1.b0++\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 2) & 0xFF) == 0x11   # loaded from offset 20 = r5.b0
        assert (reg(core, 1) & 0xFF) == 21     # r1.b0 incremented

    def test_store_post_inc_increments_pointer_by_1(self):
        """MVIB *r1.b0++, r5 stores byte and then increments r1.b0 by 1."""
        asm = (
            "ldi r5, 0x22\n"
            "ldi r1, 20\n"
            "mvib *r1.b0++, r5\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 5) & 0xFF) == 0x22   # r5.b0 (offset 20) = 0x22
        assert (reg(core, 1) & 0xFF) == 21     # r1.b0 incremented

    def test_sequential_loads_traverse_register_file(self):
        """Three MVIB loads with ++ step through consecutive bytes."""
        asm = (
            "ldi r5.w0, 0x0102\n"  # r5.b0=0x02, r5.b1=0x01
            "ldi r1, 20\n"         # points to r5.b0
            "mvib r2, *r1.b0++\n"  # r2.b0 = r5.b0 = 0x02; r1.b0 → 21
            "mvib r3, *r1.b0++\n"  # r3.b0 = r5.b1 = 0x01; r1.b0 → 22
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 2) & 0xFF) == 0x02
        assert (reg(core, 3) & 0xFF) == 0x01
        assert (reg(core, 1) & 0xFF) == 22


# ---------------------------------------------------------------------------
# MVIB: pre-decrement (Rd = *--Rs1, subop=3; *--Rd = Rs1, subop=12)
# ---------------------------------------------------------------------------

class TestMVIBPreDec:
    def test_load_predec_decrements_before_access(self):
        """MVIB r2, *--r1.b0 decrements r1.b0 by 1, then loads."""
        asm = (
            "ldi r5, 0x33\n"
            "ldi r1, 21\n"          # will decrement to 20 = r5.b0
            "mvib r2, *--r1.b0\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 2) & 0xFF) == 0x33   # loaded from r5.b0
        assert (reg(core, 1) & 0xFF) == 20     # r1.b0 decremented

    def test_store_predec_decrements_before_access(self):
        """MVIB *--r1.b0, r5 decrements r1.b0 by 1, then stores."""
        asm = (
            "ldi r5, 0x44\n"
            "ldi r1, 21\n"
            "mvib *--r1.b0, r5\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 5) & 0xFF) == 0x44   # stored at offset 20 = r5.b0
        assert (reg(core, 1) & 0xFF) == 20


# ---------------------------------------------------------------------------
# MVIW: 2-byte (word) transfer
# ---------------------------------------------------------------------------

class TestMVIW:
    def test_load_word_indirect(self):
        """MVIW r2.w0, *r1.b0 loads 2 bytes from regfile[r1.b0] into r2.w0."""
        asm = (
            "ldi r5.w0, 0x1234\n"  # r5 bytes 20-21 = 0x34, 0x12
            "ldi r1, 20\n"
            "mviw r2.w0, *r1.b0\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 2) & 0xFFFF) == 0x1234

    def test_store_word_indirect(self):
        """MVIW *r1.b0, r5.w0 stores 2 bytes to regfile[r1.b0]."""
        asm = (
            "ldi r5.w0, 0xABCD\n"
            "ldi r1, 20\n"
            "mviw *r1.b0, r5.w0\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 5) & 0xFFFF) == 0xABCD

    def test_post_inc_increments_by_2(self):
        """MVIW *r1.b0++, r5.w0 increments r1.b0 by 2."""
        asm = (
            "ldi r5.w0, 0x1111\n"
            "ldi r1, 20\n"
            "mviw *r1.b0++, r5.w0\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 1) & 0xFF) == 22     # 20 + 2 = 22

    def test_predec_decrements_by_2(self):
        """MVIW r2.w0, *--r1.b0 decrements r1.b0 by 2."""
        asm = (
            "ldi r5.w0, 0x5678\n"
            "ldi r1, 22\n"         # will decrement to 20 = r5.b0
            "mviw r2.w0, *--r1.b0\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 2) & 0xFFFF) == 0x5678
        assert (reg(core, 1) & 0xFF) == 20


# ---------------------------------------------------------------------------
# MVID: 4-byte (dword) transfer
# ---------------------------------------------------------------------------

class TestMVID:
    def test_load_dword_indirect(self):
        """MVID r2, *r1.b0 loads 4 bytes from regfile[r1.b0] into r2."""
        asm = (
            "ldi r5.w0, 0x5678\n"
            "ldi r5.w2, 0x1234\n"  # r5 = 0x12345678
            "ldi r1, 20\n"         # → r5.b0 (byte offset 20)
            "mvid r2, *r1.b0\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 2) == 0x12345678

    def test_store_dword_indirect(self):
        """MVID *r1.b0, r5 stores 4 bytes to regfile[r1.b0]."""
        asm = (
            "ldi r5.w0, 0xDEAD\n"
            "ldi r5.w2, 0xBEEF\n"  # r5 = 0xBEEFDEAD
            "ldi r1, 8\n"          # → r2.b0 (byte offset 8)
            "mvid *r1.b0, r5\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 2) == 0xBEEFDEAD

    def test_post_inc_increments_by_4(self):
        """MVID *r1.b0++, r5 increments r1.b0 by 4."""
        asm = (
            "ldi r5.w0, 0x1111\n"
            "ldi r5.w2, 0x2222\n"
            "ldi r1, 8\n"
            "mvid *r1.b0++, r5\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 1) & 0xFF) == 12     # 8 + 4 = 12

    def test_predec_decrements_by_4(self):
        """MVID r2, *--r1.b0 decrements r1.b0 by 4."""
        asm = (
            "ldi r5.w0, 0xCAFE\n"
            "ldi r5.w2, 0xBABE\n"  # r5 = 0xBABECAFE
            "ldi r1, 24\n"         # will decrement to 20 = r5.b0
            "mvid r2, *--r1.b0\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert reg(core, 2) == 0xBABECAFE
        assert (reg(core, 1) & 0xFF) == 20


# ---------------------------------------------------------------------------
# Pointer byte wraps at 128 (mod 128 on access; byte field wraps at 256)
# ---------------------------------------------------------------------------

class TestMVIPointerWrap:
    def test_pointer_access_wraps_at_128(self):
        """Pointer value 128 wraps to 0 (byte offset mod 128)."""
        asm = (
            "ldi r0, 0xCC\n"       # r0.b0 = 0xCC (byte offset 0)
            "ldi r1, 128\n"        # r1.b0 = 128 → wraps to 0
            "mvib r2, *r1.b0\n"
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 2) & 0xFF) == 0xCC   # read r0.b0 via wrapped offset

    def test_post_inc_byte_wraps_at_256(self):
        """Auto-increment at 255 wraps the byte field to 0."""
        asm = (
            "ldi r0, 0xDD\n"
            "ldi r1, 255\n"        # r1.b0 = 255
            "mvib r2, *r1.b0++\n"  # load from offset 255%128=127, then r1.b0 → 0
            "halt\n"
        )
        core = make_core(asm)
        run_to_halt(core)
        assert (reg(core, 1) & 0xFF) == 0      # 255+1 = 256 → 0 (byte wrap)


# ---------------------------------------------------------------------------
# Disassembler: decode binary MVI words
# ---------------------------------------------------------------------------

class TestMVIDisassembler:
    def test_decode_mvib_load_indirect(self):
        """MVIB r2, *r1.b0 — subop=1, size=0."""
        from core.disassembler import disassemble_word
        word = _mvi_word(mvi_subop=1, size=0, rd_sel=7, rd_num=2,
                         rs1_sel=0, rs1_num=1)
        instr = disassemble_word(word)
        assert instr.opcode == 'MVIB'
        assert 'r2' in instr.source_text
        assert '*r1.b0' in instr.source_text

    def test_decode_mviw_load_post_inc(self):
        """MVIW r2.w0, *r1.b0++ — subop=2, size=1."""
        from core.disassembler import disassemble_word
        word = _mvi_word(mvi_subop=2, size=1, rd_sel=4, rd_num=2,
                         rs1_sel=0, rs1_num=1)
        instr = disassemble_word(word)
        assert instr.opcode == 'MVIW'
        assert '++' in instr.source_text
        assert '*r1.b0' in instr.source_text

    def test_decode_mvid_store_indirect(self):
        """MVID *r1.b0, r14 — subop=4, size=2."""
        from core.disassembler import disassemble_word
        word = _mvi_word(mvi_subop=4, size=2, rd_sel=0, rd_num=1,
                         rs1_sel=7, rs1_num=14)
        instr = disassemble_word(word)
        assert instr.opcode == 'MVID'
        assert '*r1.b0' in instr.source_text
        assert 'r14' in instr.source_text

    def test_decode_mvid_store_predec(self):
        """MVID *--r1.b0, r14 — subop=12, size=2."""
        from core.disassembler import disassemble_word
        word = _mvi_word(mvi_subop=12, size=2, rd_sel=0, rd_num=1,
                         rs1_sel=7, rs1_num=14)
        instr = disassemble_word(word)
        assert instr.opcode == 'MVID'
        assert '--' in instr.source_text
        assert 'r14' in instr.source_text

    def test_decode_mviw_load_predec(self):
        """MVIW r2.w0, *--r1.b0 — subop=3, size=1."""
        from core.disassembler import disassemble_word
        word = _mvi_word(mvi_subop=3, size=1, rd_sel=4, rd_num=2,
                         rs1_sel=0, rs1_num=1)
        instr = disassemble_word(word)
        assert instr.opcode == 'MVIW'
        assert '--' in instr.source_text

    def test_decode_mvid_store_post_inc(self):
        """MVID *r1.b0++, r5 — subop=8, size=2."""
        from core.disassembler import disassemble_word
        word = _mvi_word(mvi_subop=8, size=2, rd_sel=0, rd_num=1,
                         rs1_sel=7, rs1_num=5)
        instr = disassemble_word(word)
        assert instr.opcode == 'MVID'
        assert '++' in instr.source_text

    def test_decode_operands_are_mvi_operand_type(self):
        """Decoded MVI instruction has MVIOperand operands."""
        from core.disassembler import disassemble_word
        from core.operands import MVIOperand
        word = _mvi_word(mvi_subop=1, size=0, rd_sel=7, rd_num=2,
                         rs1_sel=0, rs1_num=1)
        instr = disassemble_word(word)
        assert all(isinstance(op, MVIOperand) for op in instr.operands)

    def test_decode_mvi_b1_pointer(self):
        """Decode uses r1.b1 (sel=1) as source pointer."""
        from core.disassembler import disassemble_word
        word = _mvi_word(mvi_subop=1, size=0, rd_sel=7, rd_num=3,
                         rs1_sel=1, rs1_num=1)
        instr = disassemble_word(word)
        assert '*r1.b1' in instr.source_text


# ---------------------------------------------------------------------------
# R31 fix: _mvi_read_direct must route R31 through io_port.read_r31()
# ---------------------------------------------------------------------------

class TestMVIR31IOPort:
    def test_mvib_direct_r31_reads_gpi_not_raw_regfile(self):
        """MVIB *r1.b0++, r31.b0 must return io_port.gpi, not raw register file."""
        # r1.b0=8 → stores into R2.b0; io_port.gpi=0x42 → expect R2.b0 = 0x42
        core = make_core(
            "ldi r1.b0, 8\n"          # TX ptr → R2.b0 (byte addr 8)
            "mvib *r1.b0++, r31.b0\n" # regfile[8] = R31.b0; r1.b0++
            "halt\n"
        )
        core.io_port.gpi = 0x42       # set GPI value (NOT raw register file)
        core.run(1000)
        assert reg(core, 2) & 0xFF == 0x42

    def test_mvib_direct_r31_uses_updated_gpi(self):
        """Second consecutive MVIB reflects a GPI change between steps."""
        core = make_core(
            "ldi r1.b0, 8\n"
            "mvib *r1.b0++, r31.b0\n"
            "mvib *r1.b0++, r31.b0\n"
            "halt\n"
        )
        # Start with gpi=0x11; after first mvib set gpi=0x22
        core.io_port.gpi = 0x11
        core.step()   # ldi
        core.step()   # first mvib → R2.b0 should get 0x11
        core.io_port.gpi = 0x22
        core.step()   # second mvib → R2.b1 should get 0x22
        core.step()   # halt
        assert reg(core, 2) & 0x00FF == 0x11
        assert (reg(core, 2) >> 8) & 0xFF == 0x22
