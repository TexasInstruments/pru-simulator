"""Tests for operand range validation in the PRU parser."""
import pytest

from core.parser import Parser


@pytest.fixture
def parser():
    return Parser()


def _parse(parser, source):
    """Helper: parse source, return instructions or raise."""
    return parser.parse_text(source + "\nhalt\n")


# ---------------------------------------------------------------------------
# LDI — 16-bit immediate (0-65535)
# ---------------------------------------------------------------------------

class TestLDI:
    def test_max_valid(self, parser):
        instrs = _parse(parser, "ldi r0, 65535")
        assert instrs[0].opcode == "LDI"

    def test_hex_max_valid(self, parser):
        instrs = _parse(parser, "ldi r0, 0xFFFF")
        assert instrs[0].opcode == "LDI"

    def test_zero_valid(self, parser):
        instrs = _parse(parser, "ldi r0, 0")
        assert instrs[0].opcode == "LDI"

    def test_exceeds_16bit(self, parser):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, "ldi r0, 65536")

    def test_exceeds_16bit_hex(self, parser):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, "ldi r0, 0x1FFFF")

    def test_large_value(self, parser):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, "ldi r0, 0xDEADBEEF")


# ---------------------------------------------------------------------------
# ALU 3-op — OP(255) on 3rd operand
# ---------------------------------------------------------------------------

class TestALU8Bit:
    @pytest.mark.parametrize("op", ["add", "sub", "and", "or", "xor", "min", "max"])
    def test_max_valid(self, parser, op):
        instrs = _parse(parser, f"{op} r0, r1, 255")
        assert instrs[0].opcode == op.upper()

    @pytest.mark.parametrize("op", ["add", "sub", "and", "or", "xor", "min", "max"])
    def test_exceeds_8bit(self, parser, op):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, f"{op} r0, r1, 256")

    @pytest.mark.parametrize("op", ["add", "sub", "and", "or", "xor"])
    def test_register_operand_no_check(self, parser, op):
        """Register in 3rd position should not trigger range check."""
        instrs = _parse(parser, f"{op} r0, r1, r2")
        assert instrs[0].opcode == op.upper()


# ---------------------------------------------------------------------------
# Shifts — OP(31) on 3rd operand
# ---------------------------------------------------------------------------

class TestShifts:
    @pytest.mark.parametrize("op", ["lsl", "lsr"])
    def test_max_valid(self, parser, op):
        instrs = _parse(parser, f"{op} r0, r1, 31")
        assert instrs[0].opcode == op.upper()

    @pytest.mark.parametrize("op", ["lsl", "lsr"])
    def test_exceeds_5bit(self, parser, op):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, f"{op} r0, r1, 32")

    @pytest.mark.parametrize("op", ["lsl", "lsr"])
    def test_register_operand_ok(self, parser, op):
        instrs = _parse(parser, f"{op} r0, r1, r2")
        assert instrs[0].opcode == op.upper()


# ---------------------------------------------------------------------------
# SET/CLR — OP(31) on 3rd operand (3-operand form)
# ---------------------------------------------------------------------------

class TestSetClr:
    @pytest.mark.parametrize("op", ["set", "clr"])
    def test_max_valid(self, parser, op):
        instrs = _parse(parser, f"{op} r0, r1, 31")
        assert instrs[0].opcode == op.upper()

    @pytest.mark.parametrize("op", ["set", "clr"])
    def test_exceeds_bit_position(self, parser, op):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, f"{op} r0, r1, 32")


# ---------------------------------------------------------------------------
# Conditional branches — OP(255) / OP(31) on 3rd operand
# ---------------------------------------------------------------------------

class TestBranches:
    @pytest.mark.parametrize("op", ["qbeq", "qbne", "qbgt", "qbge", "qblt", "qble"])
    def test_max_valid(self, parser, op):
        instrs = _parse(parser, f"target: halt\n{op} target, r1, 255")
        assert any(i.opcode == op.upper() for i in instrs)

    @pytest.mark.parametrize("op", ["qbeq", "qbne", "qbgt", "qbge", "qblt", "qble"])
    def test_exceeds_8bit(self, parser, op):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, f"target: halt\n{op} target, r1, 256")

    @pytest.mark.parametrize("op", ["qbbs", "qbbc"])
    def test_bit_max_valid(self, parser, op):
        instrs = _parse(parser, f"target: halt\n{op} target, r1, 31")
        assert any(i.opcode == op.upper() for i in instrs)

    @pytest.mark.parametrize("op", ["qbbs", "qbbc"])
    def test_bit_exceeds(self, parser, op):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, f"target: halt\n{op} target, r1, 32")


# ---------------------------------------------------------------------------
# LOOP — OP(256) on 2nd operand
# ---------------------------------------------------------------------------

class TestLoop:
    def test_max_valid(self, parser):
        instrs = _parse(parser, "loop end, 256\nend: halt")
        assert instrs[0].opcode == "LOOP"

    def test_exceeds(self, parser):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, "loop end, 257\nend: halt")


# ---------------------------------------------------------------------------
# Memory ops — IM(124) on 4th operand, OP(255) on 3rd
# ---------------------------------------------------------------------------

class TestMemoryOps:
    @pytest.mark.parametrize("op", ["lbbo", "sbbo"])
    def test_length_max_valid(self, parser, op):
        instrs = _parse(parser, f"{op} &r2, r1, 0, 124")
        assert instrs[0].opcode == op.upper()

    @pytest.mark.parametrize("op", ["lbbo", "sbbo"])
    def test_length_exceeds(self, parser, op):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, f"{op} &r2, r1, 0, 125")

    @pytest.mark.parametrize("op", ["lbbo", "sbbo"])
    def test_offset_exceeds(self, parser, op):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, f"{op} &r2, r1, 256, 4")


# ---------------------------------------------------------------------------
# XFR — IM(253) on 1st, IM(124) on 3rd
# ---------------------------------------------------------------------------

class TestXFR:
    @pytest.mark.parametrize("op", ["xin", "xout", "xchg"])
    def test_device_id_max_valid(self, parser, op):
        instrs = _parse(parser, f"{op} 253, &r2, 8")
        assert instrs[0].opcode == op.upper()

    @pytest.mark.parametrize("op", ["xin", "xout", "xchg"])
    def test_device_id_exceeds(self, parser, op):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, f"{op} 254, &r2, 8")

    @pytest.mark.parametrize("op", ["xin", "xout", "xchg"])
    def test_length_exceeds(self, parser, op):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, f"{op} 10, &r2, 125")


# ---------------------------------------------------------------------------
# ZERO/FILL — IM(124)
# ---------------------------------------------------------------------------

class TestZeroFill:
    @pytest.mark.parametrize("op", ["zero", "fill"])
    def test_max_valid(self, parser, op):
        instrs = _parse(parser, f"{op} &r0, 124")
        assert instrs[0].opcode == op.upper()

    @pytest.mark.parametrize("op", ["zero", "fill"])
    def test_exceeds(self, parser, op):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, f"{op} &r0, 125")


# ---------------------------------------------------------------------------
# SLP — 0 or 1 only
# ---------------------------------------------------------------------------

class TestSLP:
    def test_valid_0(self, parser):
        instrs = _parse(parser, "slp 0")
        assert instrs[0].opcode == "SLP"

    def test_valid_1(self, parser):
        instrs = _parse(parser, "slp 1")
        assert instrs[0].opcode == "SLP"

    def test_exceeds(self, parser):
        with pytest.raises(SyntaxError, match="out of range"):
            _parse(parser, "slp 2")
