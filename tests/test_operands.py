"""Tests for core/operands.py"""

import pytest
from core.operands import parse_operand, Register, Immediate, BitField, Label


# ---------------------------------------------------------------------------
# Plain registers
# ---------------------------------------------------------------------------

class TestPlainRegisters:
    def test_r0(self):
        assert parse_operand("r0") == Register(0, 0, 32)

    def test_r5(self):
        assert parse_operand("r5") == Register(5, 0, 32)

    def test_r31(self):
        assert parse_operand("r31") == Register(31, 0, 32)

    def test_r5_uppercase(self):
        assert parse_operand("R5") == Register(5, 0, 32)

    def test_r5_mixed_case(self):
        assert parse_operand("R05") == Register(5, 0, 32)

    def test_trailing_comma_stripped(self):
        assert parse_operand("r5,") == Register(5, 0, 32)


# ---------------------------------------------------------------------------
# Byte sub-fields
# ---------------------------------------------------------------------------

class TestByteFields:
    def test_b0(self):
        assert parse_operand("r5.b0") == Register(5, 0, 8)

    def test_b1(self):
        assert parse_operand("r5.b1") == Register(5, 8, 8)

    def test_b2(self):
        assert parse_operand("r5.b2") == Register(5, 16, 8)

    def test_b3(self):
        assert parse_operand("r5.b3") == Register(5, 24, 8)

    def test_b0_uppercase(self):
        assert parse_operand("R5.B0") == Register(5, 0, 8)

    def test_b1_trailing_comma(self):
        assert parse_operand("r5.b1,") == Register(5, 8, 8)


# ---------------------------------------------------------------------------
# Word sub-fields
# ---------------------------------------------------------------------------

class TestWordFields:
    def test_w0(self):
        assert parse_operand("r5.w0") == Register(5, 0, 16)

    def test_w1(self):
        assert parse_operand("r5.w1") == Register(5, 8, 16)

    def test_w2(self):
        assert parse_operand("r5.w2") == Register(5, 16, 16)

    def test_w0_uppercase(self):
        assert parse_operand("R5.W0") == Register(5, 0, 16)


# ---------------------------------------------------------------------------
# Bit fields
# ---------------------------------------------------------------------------

class TestBitFields:
    def test_t0(self):
        assert parse_operand("r5.t0") == BitField(5, 0)

    def test_t7(self):
        assert parse_operand("r5.t7") == BitField(5, 7)

    def test_t31(self):
        assert parse_operand("r5.t31") == BitField(5, 31)

    def test_t7_uppercase(self):
        assert parse_operand("R5.T7") == BitField(5, 7)


# ---------------------------------------------------------------------------
# Ampersand prefix (burst ops)
# ---------------------------------------------------------------------------

class TestAmpersandPrefix:
    def test_ampersand_plain(self):
        assert parse_operand("&r5") == Register(5, 0, 32)

    def test_ampersand_uppercase(self):
        assert parse_operand("&R5") == Register(5, 0, 32)

    def test_ampersand_trailing_comma(self):
        assert parse_operand("&r5,") == Register(5, 0, 32)


# ---------------------------------------------------------------------------
# Immediates
# ---------------------------------------------------------------------------

class TestImmediates:
    def test_decimal(self):
        assert parse_operand("123") == Immediate(123)

    def test_decimal_zero(self):
        assert parse_operand("0") == Immediate(0)

    def test_hex_lowercase(self):
        assert parse_operand("0xff") == Immediate(255)

    def test_hex_uppercase_prefix(self):
        assert parse_operand("0XFF") == Immediate(255)

    def test_hex_mixed(self):
        assert parse_operand("0xDEAD") == Immediate(0xDEAD)

    def test_hex_trailing_comma(self):
        assert parse_operand("0xFF,") == Immediate(255)


# ---------------------------------------------------------------------------
# Unknown tokens → string (label)
# ---------------------------------------------------------------------------

class TestLabels:
    def test_plain_label(self):
        result = parse_operand("my_label")
        assert result == "my_label"

    def test_label_with_colon(self):
        # Colons are label definitions at line level, but if passed they come back as-is
        result = parse_operand("loop_start")
        assert isinstance(result, str)

    def test_unrecognized_returns_string(self):
        result = parse_operand("SOME_DEFINE")
        assert isinstance(result, str)
        assert result == "SOME_DEFINE"


# ---------------------------------------------------------------------------
# Malformed numeric literals - must raise, not silently become a label
# ---------------------------------------------------------------------------

class TestMalformedNumericLiterals:
    def test_invalid_hex_digit(self):
        with pytest.raises(ValueError):
            parse_operand("0xabcv1234")

    def test_digit_leading_garbage(self):
        with pytest.raises(ValueError):
            parse_operand("123abc")

    def test_bare_hex_prefix(self):
        with pytest.raises(ValueError):
            parse_operand("0x")
