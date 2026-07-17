"""Tests for core/registers.py — RegisterFile."""

import pytest
from core.registers import RegisterFile


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_initial_regs_are_zero():
    rf = RegisterFile()
    assert len(rf.regs) == 32
    assert all(v == 0 for v in rf.regs)


def test_initial_carry_is_false():
    rf = RegisterFile()
    assert rf.carry is False


# ---------------------------------------------------------------------------
# Full 32-bit read / write
# ---------------------------------------------------------------------------

def test_write_full_and_read_full():
    rf = RegisterFile()
    rf.write_full(0, 0xDEADBEEF)
    assert rf.read_full(0) == 0xDEADBEEF


def test_write_full_masks_to_32_bits():
    rf = RegisterFile()
    rf.write_full(5, 0x1_FFFFFFFF)   # 33-bit value
    assert rf.read_full(5) == 0xFFFFFFFF


def test_write_full_all_registers():
    rf = RegisterFile()
    for i in range(32):
        rf.write_full(i, i * 0x01010101)
    for i in range(32):
        assert rf.read_full(i) == (i * 0x01010101) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Byte sub-registers (offset, width=8)
# ---------------------------------------------------------------------------

def test_byte_fields_b0_b1_b2_b3():
    rf = RegisterFile()
    rf.write_full(1, 0xAABBCCDD)
    # b0 → bits [7:0]
    assert rf.read(1, offset=0,  width=8) == 0xDD
    # b1 → bits [15:8]
    assert rf.read(1, offset=8,  width=8) == 0xCC
    # b2 → bits [23:16]
    assert rf.read(1, offset=16, width=8) == 0xBB
    # b3 → bits [31:24]
    assert rf.read(1, offset=24, width=8) == 0xAA


def test_write_byte_b0_preserves_other_bytes():
    rf = RegisterFile()
    rf.write_full(2, 0x11223344)
    rf.write(2, offset=0, width=8, value=0xFF)
    assert rf.read_full(2) == 0x112233FF


def test_write_byte_b1_preserves_other_bytes():
    rf = RegisterFile()
    rf.write_full(2, 0x11223344)
    rf.write(2, offset=8, width=8, value=0xFF)
    assert rf.read_full(2) == 0x1122FF44


def test_write_byte_b2_preserves_other_bytes():
    rf = RegisterFile()
    rf.write_full(2, 0x11223344)
    rf.write(2, offset=16, width=8, value=0xFF)
    assert rf.read_full(2) == 0x11FF3344


def test_write_byte_b3_preserves_other_bytes():
    rf = RegisterFile()
    rf.write_full(2, 0x11223344)
    rf.write(2, offset=24, width=8, value=0xFF)
    assert rf.read_full(2) == 0xFF223344


# ---------------------------------------------------------------------------
# Word sub-registers (width=16)
# ---------------------------------------------------------------------------

def test_word_fields_w0_w1_w2():
    rf = RegisterFile()
    rf.write_full(3, 0xAABBCCDD)
    # w0 → bits [15:0]
    assert rf.read(3, offset=0,  width=16) == 0xCCDD
    # w1 → bits [23:8]
    assert rf.read(3, offset=8,  width=16) == 0xBBCC
    # w2 → bits [31:16]
    assert rf.read(3, offset=16, width=16) == 0xAABB


def test_write_word_w0_preserves_upper_bits():
    rf = RegisterFile()
    rf.write_full(4, 0xABCD1234)
    rf.write(4, offset=0, width=16, value=0xFFFF)
    assert rf.read_full(4) == 0xABCDFFFF


def test_write_word_w2_preserves_lower_bits():
    rf = RegisterFile()
    rf.write_full(4, 0xABCD1234)
    rf.write(4, offset=16, width=16, value=0xFFFF)
    assert rf.read_full(4) == 0xFFFF1234


# ---------------------------------------------------------------------------
# write masks value to 'width' bits
# ---------------------------------------------------------------------------

def test_write_masks_value_to_width():
    rf = RegisterFile()
    rf.write_full(6, 0x00000000)
    rf.write(6, offset=0, width=8, value=0x1FF)   # 9-bit value into 8-bit field
    assert rf.read(6, offset=0, width=8) == 0xFF
    assert rf.read(6, offset=8, width=8) == 0x00   # bit 8 must NOT bleed into b1


# ---------------------------------------------------------------------------
# Carry flag
# ---------------------------------------------------------------------------

def test_carry_flag_set_and_clear():
    rf = RegisterFile()
    rf.carry = True
    assert rf.carry is True
    rf.carry = False
    assert rf.carry is False
