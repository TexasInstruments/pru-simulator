"""Tests for core/parser.py – Two-pass assembly parser."""

import textwrap

import pytest

from core.parser import Parser, Instruction
from core.operands import Register, Immediate, BitField, Label


def make_parser() -> Parser:
    return Parser()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def parse(src: str) -> list[Instruction]:
    return make_parser().parse_text(textwrap.dedent(src))


# ---------------------------------------------------------------------------
# Basic instruction parsing
# ---------------------------------------------------------------------------

class TestBasicInstructions:
    def test_add_opcode_uppercase(self):
        instrs = parse("add r0, r1, r2")
        assert len(instrs) == 1
        assert instrs[0].opcode == "ADD"

    def test_add_operands(self):
        instrs = parse("add r0, r1, r2")
        ops = instrs[0].operands
        assert ops[0] == Register(0, 0, 32)
        assert ops[1] == Register(1, 0, 32)
        assert ops[2] == Register(2, 0, 32)

    def test_ldi_with_immediate(self):
        instrs = parse("ldi r3, 42")
        assert instrs[0].opcode == "LDI"
        assert instrs[0].operands[0] == Register(3, 0, 32)
        assert instrs[0].operands[1] == Immediate(42)

    def test_ldi_hex_immediate(self):
        instrs = parse("ldi r5, 0xFF")
        assert instrs[0].operands[1] == Immediate(255)

    def test_no_operands(self):
        instrs = parse("halt")
        assert instrs[0].opcode == "HALT"
        assert instrs[0].operands == []


# ---------------------------------------------------------------------------
# Sub-register operands
# ---------------------------------------------------------------------------

class TestSubRegisterOperands:
    def test_byte_field_b0(self):
        instrs = parse("mov r0.b0, r1.b0")
        assert instrs[0].operands[0] == Register(0, 0, 8)
        assert instrs[0].operands[1] == Register(1, 0, 8)

    def test_byte_field_b1(self):
        instrs = parse("mov r0.b1, 5")
        assert instrs[0].operands[0] == Register(0, 8, 8)

    def test_byte_field_b3(self):
        instrs = parse("ldi r2.b3, 0")
        assert instrs[0].operands[0] == Register(2, 24, 8)

    def test_word_field_w0(self):
        instrs = parse("mov r4.w0, r5.w0")
        assert instrs[0].operands[0] == Register(4, 0, 16)

    def test_word_field_w2(self):
        instrs = parse("ldi r1.w2, 100")
        assert instrs[0].operands[0] == Register(1, 16, 16)

    def test_bit_field(self):
        instrs = parse("clr r5.t7")
        assert instrs[0].operands[0] == BitField(5, 7)

    def test_ampersand_prefix(self):
        instrs = parse("lbbo &r2, r1, 0, 4")
        assert instrs[0].operands[0] == Register(2, 0, 32)


# ---------------------------------------------------------------------------
# Sequential addresses
# ---------------------------------------------------------------------------

class TestAddresses:
    def test_first_instruction_address_zero(self):
        instrs = parse("nop")
        assert instrs[0].address == 0

    def test_sequential_addresses(self):
        src = """\
            add r0, r1, r2
            ldi r3, 5
            sub r4, r0, r3
        """
        instrs = parse(src)
        assert [i.address for i in instrs] == [0, 1, 2]

    def test_many_instructions(self):
        lines = "\n".join(f"ldi r0, {n}" for n in range(10))
        instrs = parse(lines)
        assert [i.address for i in instrs] == list(range(10))


# ---------------------------------------------------------------------------
# Source line tracking
# ---------------------------------------------------------------------------

class TestSourceLineTracking:
    def test_source_line_single(self):
        instrs = parse("add r0, r1, r2")
        assert instrs[0].source_line == 1

    def test_source_text_preserved(self):
        instrs = parse("add r0, r1, r2")
        assert "add" in instrs[0].source_text.lower()
        assert "r0" in instrs[0].source_text

    def test_source_line_multiline(self):
        src = "ldi r0, 1\nldi r1, 2\nldi r2, 3"
        instrs = make_parser().parse_text(src)
        # Each instruction should be on a different line
        lines = [i.source_line for i in instrs]
        assert lines[0] < lines[1] < lines[2]


# ---------------------------------------------------------------------------
# Label resolution
# ---------------------------------------------------------------------------

class TestLabelResolution:
    def test_label_only_line_no_instruction(self):
        src = """\
            loop:
            add r0, r1, r2
        """
        instrs = parse(src)
        # label-only line produces no instruction
        assert len(instrs) == 1
        assert instrs[0].opcode == "ADD"

    def test_label_maps_to_correct_address(self):
        src = """\
            add r0, r1, r2
            loop:
            sub r4, r0, r1
        """
        p = make_parser()
        instrs = p.parse_text(textwrap.dedent(src))
        assert p.labels["loop"] == 1   # second instruction position

    def test_label_at_address_zero(self):
        p = make_parser()
        p.parse_text("start:\nadd r0, r1, r2\n")
        assert p.labels["start"] == 0

    def test_label_inline_with_instruction(self):
        """Label on same line as instruction – label maps to that address."""
        src = "start: ldi r0, 1\nnop\n"
        p = make_parser()
        instrs = p.parse_text(src)
        # start label should be at address 0
        assert p.labels["start"] == 0
        # Both lines are instructions
        assert len(instrs) == 2
        assert instrs[0].opcode == "LDI"
        assert instrs[0].address == 0
        assert instrs[1].opcode == "NOP"
        assert instrs[1].address == 1

    def test_label_resolved_in_operand(self):
        src = """\
            ldi r0, 0
            target:
            add r1, r1, r0
            qba target
        """
        instrs = parse(src)
        qba = instrs[-1]
        assert qba.opcode == "QBA"
        label_op = qba.operands[0]
        assert isinstance(label_op, Label)
        assert label_op.name == "target"
        assert label_op.resolved_addr == 1  # second instruction (address 1)

    def test_label_colon_syntax(self):
        src = "my_label:\nldi r0, 5\n"
        p = make_parser()
        p.parse_text(src)
        assert "my_label" in p.labels
        assert p.labels["my_label"] == 0

    def test_multiple_labels(self):
        src = """\
            nop
            label_a:
            nop
            label_b:
            nop
        """
        p = make_parser()
        p.parse_text(textwrap.dedent(src))
        assert p.labels["label_a"] == 1
        assert p.labels["label_b"] == 2


# ---------------------------------------------------------------------------
# Forward references
# ---------------------------------------------------------------------------

class TestForwardReferences:
    def test_qba_forward_reference(self):
        src = """\
            qba end
            ldi r0, 0
            end:
            nop
        """
        instrs = parse(src)
        qba = instrs[0]
        assert qba.opcode == "QBA"
        label_op = qba.operands[0]
        assert isinstance(label_op, Label)
        assert label_op.name == "end"
        # "end" label is at address 2 (two instructions before it)
        assert label_op.resolved_addr == 2

    def test_qbgt_forward_reference(self):
        src = """\
            qbgt done, r0, r1
            add r2, r0, r1
            done:
            nop
        """
        instrs = parse(src)
        qbgt = instrs[0]
        # The label "done" should be resolved to address 2
        label_operands = [op for op in qbgt.operands if isinstance(op, Label)]
        assert len(label_operands) == 1
        assert label_operands[0].resolved_addr == 2

    def test_unresolved_label_gets_minus_one(self):
        src = "qba unknown_label\n"
        instrs = parse(src)
        label_op = instrs[0].operands[0]
        assert isinstance(label_op, Label)
        assert label_op.resolved_addr == -1


# ---------------------------------------------------------------------------
# Integration with preprocessor
# ---------------------------------------------------------------------------

class TestPreprocessorIntegration:
    def test_set_substitution(self):
        src = """\
            COUNT .set 10
            ldi r0, COUNT
        """
        instrs = parse(src)
        assert len(instrs) == 1
        assert instrs[0].operands[1] == Immediate(10)

    def test_macro_expansion(self):
        src = """\
            .macro ZERO_REG reg
            ldi reg, 0
            .endm
            ZERO_REG r5
        """
        instrs = parse(src)
        assert len(instrs) == 1
        assert instrs[0].opcode == "LDI"
        assert instrs[0].operands[0] == Register(5, 0, 32)
        assert instrs[0].operands[1] == Immediate(0)

    def test_comment_stripped_before_parsing(self):
        src = "add r0, r1, r2  ; this is a comment\n"
        instrs = parse(src)
        assert len(instrs) == 1
        assert instrs[0].opcode == "ADD"
        assert len(instrs[0].operands) == 3

    def test_empty_lines_ignored(self):
        src = "\n\nadd r0, r1, r2\n\nldi r1, 5\n\n"
        instrs = parse(src)
        assert len(instrs) == 2
        assert instrs[0].address == 0
        assert instrs[1].address == 1

    def test_set_with_label_and_instruction(self):
        src = """\
            BASE .set 4
            ldi r1, BASE
            loop:
            sub r1, r1, 1
            qbne loop, r1, 0
        """
        instrs = parse(src)
        assert instrs[0].operands[1] == Immediate(4)
        # "loop" forward ref to address 1
        p = make_parser()
        p.parse_text(textwrap.dedent(src))
        assert p.labels["loop"] == 1

    def test_lbbo_operand_splitting(self):
        """Ensure LBBO with 4 operands splits correctly."""
        src = "lbbo &r2, r1, 0, 4\n"
        instrs = parse(src)
        assert instrs[0].opcode == "LBBO"
        ops = instrs[0].operands
        assert len(ops) == 4
        assert ops[0] == Register(2, 0, 32)   # &r2
        assert ops[1] == Register(1, 0, 32)   # r1
        assert ops[2] == Immediate(0)          # 0
        assert ops[3] == Immediate(4)          # 4
