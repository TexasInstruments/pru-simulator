"""Tests for core/alu.py"""

import pytest
from core.alu import ALU


# ---------------------------------------------------------------------------
# ADD
# ---------------------------------------------------------------------------

class TestAdd:
    def test_add_basic(self):
        result, carry = ALU.add(3, 4, 32)
        assert result == 7
        assert carry is False

    def test_add_no_overflow_8bit(self):
        result, carry = ALU.add(100, 27, 8)
        assert result == 127
        assert carry is False

    def test_add_overflow_8bit(self):
        result, carry = ALU.add(200, 100, 8)
        assert result == 44   # (300 & 0xFF)
        assert carry is True

    def test_add_overflow_16bit(self):
        result, carry = ALU.add(0xFFFF, 1, 16)
        assert result == 0
        assert carry is True

    def test_add_overflow_32bit(self):
        result, carry = ALU.add(0xFFFF_FFFF, 1, 32)
        assert result == 0
        assert carry is True

    def test_add_exact_max_no_carry(self):
        result, carry = ALU.add(0xFF, 0, 8)
        assert result == 0xFF
        assert carry is False

    def test_add_zero(self):
        result, carry = ALU.add(0, 0, 32)
        assert result == 0
        assert carry is False


# ---------------------------------------------------------------------------
# ADC
# ---------------------------------------------------------------------------

class TestAdc:
    def test_adc_without_carry(self):
        result, carry = ALU.adc(10, 5, False, 32)
        assert result == 15
        assert carry is False

    def test_adc_with_carry(self):
        result, carry = ALU.adc(10, 5, True, 32)
        assert result == 16
        assert carry is False

    def test_adc_carry_causes_overflow(self):
        result, carry = ALU.adc(0xFF, 0, True, 8)
        assert result == 0
        assert carry is True

    def test_adc_all_contribute_to_overflow(self):
        result, carry = ALU.adc(0xFE, 1, True, 8)
        assert result == 0
        assert carry is True


# ---------------------------------------------------------------------------
# SUB
# ---------------------------------------------------------------------------

class TestSub:
    def test_sub_basic(self):
        result, carry = ALU.sub(10, 3, 32)
        assert result == 7
        assert carry is True   # no borrow → carry set

    def test_sub_underflow(self):
        result, carry = ALU.sub(3, 10, 32)
        # 3 - 10 = -7 → wraps to 0xFFFF_FFF9
        assert result == (3 - 10) & 0xFFFF_FFFF
        assert carry is False  # borrow → carry clear

    def test_sub_underflow_8bit(self):
        result, carry = ALU.sub(0, 1, 8)
        assert result == 0xFF
        assert carry is False  # borrow → carry clear

    def test_sub_equal(self):
        result, carry = ALU.sub(5, 5, 8)
        assert result == 0
        assert carry is True   # no borrow (a >= b) → carry set

    def test_sub_zero(self):
        result, carry = ALU.sub(42, 0, 32)
        assert result == 42
        assert carry is True   # no borrow → carry set


# ---------------------------------------------------------------------------
# SUC
# ---------------------------------------------------------------------------

class TestSuc:
    def test_suc_no_carry(self):
        # carry_in=False → borrow=~0=1 → 10-3-1=6
        result, carry = ALU.suc(10, 3, False, 32)
        assert result == 6
        assert carry is True   # no underflow

    def test_suc_with_carry(self):
        # carry_in=True → borrow=~1=0 → 10-3-0=7
        result, carry = ALU.suc(10, 3, True, 32)
        assert result == 7
        assert carry is True   # no underflow

    def test_suc_underflow(self):
        # carry_in=True → borrow=0 → 0-0-0=0
        result, carry = ALU.suc(0, 0, True, 8)
        assert result == 0
        assert carry is True   # 0 >= 0, no underflow


# ---------------------------------------------------------------------------
# RSB
# ---------------------------------------------------------------------------

class TestRsb:
    def test_rsb_basic(self):
        # b - a  →  10 - 3 = 7, no borrow → carry set
        result, carry = ALU.rsb(3, 10, 32)
        assert result == 7
        assert carry is True

    def test_rsb_underflow(self):
        # b - a → 3 - 10, borrow → carry clear
        result, carry = ALU.rsb(10, 3, 32)
        assert carry is False


# ---------------------------------------------------------------------------
# RSC
# ---------------------------------------------------------------------------

class TestRsc:
    def test_rsc_basic(self):
        # RSC(a=3, b=10, carry=False) → SUC(10, 3, False) → 10-3-1=6, carry=True
        result, carry = ALU.rsc(3, 10, False, 32)
        assert result == 6
        assert carry is True

    def test_rsc_with_carry(self):
        # RSC(a=3, b=10, carry=True) → SUC(10, 3, True) → 10-3-0=7, carry=True
        result, carry = ALU.rsc(3, 10, True, 32)
        assert result == 7
        assert carry is True


# ---------------------------------------------------------------------------
# Logic ops
# ---------------------------------------------------------------------------

class TestLogic:
    def test_and(self):
        assert ALU.and_(0b1100, 0b1010) == 0b1000

    def test_or(self):
        assert ALU.or_(0b1100, 0b1010) == 0b1110

    def test_xor(self):
        assert ALU.xor_(0b1100, 0b1010) == 0b0110

    def test_not_zero(self):
        assert ALU.not_(0) == 0xFFFF_FFFF

    def test_not_all_ones(self):
        assert ALU.not_(0xFFFF_FFFF) == 0

    def test_not_masks_to_32_bits(self):
        result = ALU.not_(0x0000_FFFF)
        assert result == 0xFFFF_0000

    def test_not_pattern(self):
        assert ALU.not_(0xAAAA_AAAA) == 0x5555_5555


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------

class TestShifts:
    def test_lsl_basic(self):
        assert ALU.lsl(1, 4) == 16

    def test_lsl_overflow_masked(self):
        assert ALU.lsl(0x8000_0000, 1) == 0   # bit 31 shifted out

    def test_lsl_by_zero(self):
        assert ALU.lsl(0xABCD, 0) == 0xABCD

    def test_lsr_basic(self):
        assert ALU.lsr(16, 4) == 1

    def test_lsr_by_zero(self):
        assert ALU.lsr(0xABCD, 0) == 0xABCD

    def test_lsr_high_bit(self):
        # LSR is unsigned – high bit does not propagate
        assert ALU.lsr(0x8000_0000, 1) == 0x4000_0000


# ---------------------------------------------------------------------------
# Bit manipulation
# ---------------------------------------------------------------------------

class TestBitManip:
    def test_set_bit(self):
        assert ALU.set_bit(0, 3) == 0b1000

    def test_set_bit_already_set(self):
        assert ALU.set_bit(0b1000, 3) == 0b1000

    def test_clr_bit(self):
        assert ALU.clr_bit(0b1111, 2) == 0b1011

    def test_clr_bit_already_clear(self):
        assert ALU.clr_bit(0b0101, 1) == 0b0101


# ---------------------------------------------------------------------------
# LMBD
# ---------------------------------------------------------------------------

class TestLmbd:
    def test_find_highest_one(self):
        # 0b1000 – highest '1' is at bit 3
        assert ALU.lmbd(0b1000, 1) == 3

    def test_find_highest_one_bit31(self):
        assert ALU.lmbd(0x8000_0000, 1) == 31

    def test_find_highest_zero(self):
        # 0b1111_1110 – bits 31..8 are all 0, so the highest '0' is at bit 31
        assert ALU.lmbd(0b1111_1110, 0) == 31

    def test_find_highest_zero_low_bit(self):
        # 0xFFFF_FFFE – all bits set except bit 0; highest '0' is bit 0
        assert ALU.lmbd(0xFFFF_FFFE, 0) == 0

    def test_find_highest_zero_in_msb(self):
        # All ones except bit 31
        val = 0x7FFF_FFFF
        assert ALU.lmbd(val, 1) == 30

    def test_not_found_returns_32_for_one(self):
        assert ALU.lmbd(0, 1) == 32

    def test_not_found_returns_32_for_zero(self):
        assert ALU.lmbd(0xFFFF_FFFF, 0) == 32

    def test_target_masked_to_bit0(self):
        # target=2 → treated as 0
        assert ALU.lmbd(0, 2) == ALU.lmbd(0, 0)


# ---------------------------------------------------------------------------
# Min / Max
# ---------------------------------------------------------------------------

class TestMinMax:
    def test_min_basic(self):
        assert ALU.min_(3, 7) == 3

    def test_min_equal(self):
        assert ALU.min_(5, 5) == 5

    def test_min_reversed(self):
        assert ALU.min_(10, 1) == 1

    def test_max_basic(self):
        assert ALU.max_(3, 7) == 7

    def test_max_equal(self):
        assert ALU.max_(5, 5) == 5

    def test_max_reversed(self):
        assert ALU.max_(10, 1) == 10
