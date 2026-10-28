"""Reference values from clpru/dispru 2.3.3; see fixtures/clpru_syntax/README.md."""

from pathlib import Path

import pytest

from core.disassembler import disassemble_word
from core.operands import Immediate, Label, Register
from core.parser import Parser
from simulator import Simulator


FIXTURES = Path(__file__).parent / "fixtures" / "clpru_syntax"


def source(name):
    return (FIXTURES / f"{name}.asm").read_text(encoding="utf-8")


def execute(text):
    sim = Simulator()
    assert sim.load("pru0", text) == []
    core = sim.cores["pru0"]
    core.run(max_steps=100)
    assert core.halted
    return core


def test_call_ret_matches_explicit_clpru_sequence():
    parser = Parser()
    instructions = parser.parse_text(source("call"))
    assert [i.opcode for i in instructions] == ["LDI", "JAL", "HALT", "LDI", "JMP"]
    assert instructions[1].operands == [Register(3, 16, 16), Label("fn", 3)]
    assert instructions[4].operands == [Register(3, 16, 16)]
    assert parser.labels["fn"] == 3
    core = execute(source("call"))
    assert core.registers.read_full(14) == 99
    assert core.registers.read_full(3) == 2 << 16
    assert core.counters.cycles == 5


def test_symbolic_set_matches_clpru_constants():
    instructions = Parser().parse_text(source("set"))
    assert [i.operands[1] for i in instructions[:3]] == [Immediate(32), Immediate(32), Immediate(8)]
    core = execute(source("set"))
    assert [core.registers.read_full(i) for i in (1, 2, 3)] == [32, 32, 8]
    assert core.counters.cycles == 4


def test_sassign_matches_clpru_register_fields():
    instructions = Parser().parse_text(source("struct_reference"))
    assert [i.operands[0] for i in instructions[3:7]] == [
        Register(6, 0, 16), Register(6, 16, 8), Register(6, 24, 8), Register(7, 0, 32)]
    assert instructions[2].operands[2] == Register(5, 0, 32)
    assert instructions[6].operands[1] == Immediate(12)
    core = execute(source("struct_reference"))
    assert core.registers.read_full(1) == 7
    assert core.registers.read_full(6) == 0x0A090008
    assert core.registers.read_full(7) == 12
    assert core.counters.cycles == 8


def test_metadata_matches_clpru_addresses_and_emits_no_code():
    parser = Parser()
    instructions = parser.parse_text(source("directives"))
    assert parser.labels == {"start": 0}
    assert [(i.address, i.opcode) for i in instructions] == [(0, "LDI"), (1, "HALT")]
    assert execute(source("directives")).counters.cycles == 2


def test_unknown_mnemonic_is_load_error_like_clpru():
    sim = Simulator()
    errors = sim.load("pru0", source("unknown"))
    assert errors and "line 1" in errors[0] and "bogus" in errors[0]
    assert sim.cores["pru0"].instructions == []


def test_ti_function_label_and_symbol_metadata_emit_no_code():
    parser = Parser()
    instructions = parser.parse_text(source("metadata_symbols"))
    assert [(i.address, i.opcode) for i in instructions] == [(0, "LDI"), (1, "HALT")]
    assert parser.labels == {"start": 0, "exported": 1}


@pytest.mark.parametrize("text, message", [
    ("call", "CALL takes exactly one label operand"),
    ("ret r1", "RET takes no operands"),
    ("call r1, r2", "CALL takes exactly one label operand"),
    ("CALL .macro t\njal r28.w0, t\n.endm\nstart: call f\nf: ret", "defined as a macro"),
    ("ldi32 r1, -1", "ldi32 needs a register and a decimal or 0x literal"),
    ("ldi32 r1, MISSING", "ldi32 needs a register and a decimal or 0x literal"),
    ("ldi r1, 5 junk", "line 1: Invalid numeric literal"),
    ("nop\nldi r1, 0x", "line 2: Invalid numeric literal"),
    ("mvib r1.b0, *r2.b0++ junk", "line 1"),
    ("N .set " + "(" * 5000 + "1" + ")" * 5000 + "\nhalt", "line 1: unsupported .set/.equ expression"),
    ("N .set " + "~" * 5000 + "1\nhalt", "line 1: unsupported .set/.equ expression"),
    ("S .struct\nx .uint\nx .ushort\n.endstruct", "duplicate structure member"),
    ("lbco &r1, c24, c5, 4\nhalt", "undefined operand symbol"),
    ("lbbo &r1, r2, 0, B0\nhalt", "undefined operand symbol 'B0'"),
    ("ldi r1, b0\nhalt", "undefined operand symbol 'b0'"),
    ("xin b0, &r1, 4\nhalt", "undefined operand symbol 'b0'"),
    ("xin 10, &r1, b4\nhalt", "undefined operand symbol 'b4'"),
    (".retain garbage\nhalt", "unsupported directive syntax"),
    ("N .set 0 << 32\nhalt", "unsupported integer expression"),
    ("N .set 4294967295 + 1\nhalt", "outside the supported range"),
    ("N .set -2147483649\nhalt", "outside the supported range"),
    ("call missing", "undefined operand symbol"),
    ("ldi r1, missing", "undefined operand symbol"),
    ("add r1, r1, Ctx.count", "undefined operand symbol"),
    ("N .set (1 << MISSING)\nhalt", "unsupported .set/.equ expression"),
    ("N .set 1 / 0\nhalt", "unsupported .set/.equ expression"),
    ("N .set 2 ** 3\nhalt", "unsupported .set/.equ expression"),
    ("N .set float(3)\nhalt", "unsupported .set/.equ expression"),
    ("N .set (1 << 32)\nhalt", "unsupported .set/.equ expression"),
    ("N .set -1 >> 1\nhalt", "unsupported integer expression"),
    ("N .set 65536\nldi r1, N", "out of range"),
    ("N .set 7\nN .set 9\nhalt", "redefinition"),
    ("S .struct\nT .struct\n.endstruct", "nested .struct"),
    ("S .struct\nx .tag T\n.endstruct", "unsupported structure member"),
    ("S .struct\nx .u32\n.endstruct", "unsupported structure member"),
    ("S .struct\nx .uint", "line 1: unterminated .struct"),
    ("S .struct\nx .uint\n.endstruct\nS .struct\ny .uint\n.endstruct", "redefinition of structure"),
    (".sassign r5, S", "unsupported structure syntax"),
    ("A .sassign r5, Missing", "defined nonempty structure"),
    ("S .struct\nx .uint\ny .uint\n.endstruct\nA .sassign r31, S", "register layout"),
    ("S .struct\nx .ubyte\ny .uint\n.endstruct\nA .sassign r5, S", "register layout"),
    (".sect \".data\"\nhalt", "unsupported code section"),
    ("nop\n.sect \".text:other\"\nhalt", "multiple code sections"),
    (".asmfunc stack_usage(4)\nhalt", "unsupported directive syntax"),
    (".mystery\nhalt", "unsupported directive"),
    ("M .macro\nnop", "unterminated .macro"),
    ("S .struct\nx .uint\n.endstruct\nA .sassign r5, S\nldi r1, A.x.w0", "undefined operand symbol"),
    (source("struct"), "unsupported structure member"),
])
def test_unsupported_syntax_is_load_error(text, message):
    errors = Simulator().load("pru0", text)
    assert errors and message in errors[0]


def test_set_uses_integer_division():
    text = "A .set 7\nB .set A / 2\nldi r1, B\nhalt"
    assert execute(text).registers.read_full(1) == 3


def test_integer_expression_subset_matches_clpru():
    core = execute(source("expressions"))
    assert [core.registers.read_full(i) for i in range(1, 6)] == [3, 7, 9, 15, 28]


def test_expanded_operands_keep_source_line_in_load_error():
    errors = Simulator().load("pru0", "; comment\nN .set 65536\nldi r1, N")
    assert errors and "line 3" in errors[0] and "out of range" in errors[0]


def test_parser_reuse_does_not_keep_previous_symbols():
    parser = Parser()
    parser.parse_text("A .set 7\nldi r1, A")
    parser.parse_text("A .set 9\nldi r1, A")
    with pytest.raises(SyntaxError, match="undefined operand symbol"):
        parser.parse_text("ldi r1, A")


def test_call_and_ret_inline_labels():
    instructions = Parser().parse_text("entry: call fn\nhalt\nfn: ret")
    assert instructions[0].operands[1] == Label("fn", 2)
    assert instructions[2].opcode == "JMP"


def test_inactive_structure_directives_do_not_define_symbols():
    instructions = Parser().parse_text(".if 0\nS .struct\nx .uint\n.endstruct\n.endif\nhalt")
    assert len(instructions) == 1 and instructions[0].opcode == "HALT"


def test_constant_table_names_remain_supported_in_their_operand_slot():
    instructions = Parser().parse_text("lbco &r1, C31, 0, 4\nhalt")
    assert instructions[0].operands[1].name == "C31"
    with pytest.raises(SyntaxError, match="undefined operand symbol"):
        Parser().parse_text("ldi r1, C31")


def test_ti_macro_in_existing_reference_firmware():
    instructions = Parser().parse_text(source("macro"))
    assert [i.opcode for i in instructions] == ["LDI", "SBCO", "ADD", "HALT"]
    assert instructions[1].operands[0] == Register(4, 0, 32)
    assert execute(source("macro")).registers.read_full(0) == 4


def test_register_burst_counts_in_existing_encoding_reference():
    instructions = Parser().parse_text(source("burst"))
    assert [i.operands[3] for i in instructions] == [Register(0, n * 8, 8) for n in range(4)]
    core = execute("ldi r0.b0, 2\nldi r2, 0\nldi r1, 0x1234\nsbbo &r1, r2, 0, b0\n"
                   "ldi r1, 0\nlbbo &r1, r2, 0, b0\nhalt")
    assert core.registers.read_full(1) == 0x1234


@pytest.mark.parametrize("name, count", [("isa_execution_test", 574), ("pru_encoding_test", 144)])
def test_existing_assembly_references_match_clpru_code_size(name, count):
    parser = Parser()
    instructions = parser.parse_text((FIXTURES.parents[2] / "references" / f"{name}.asm").read_text())
    assert len(instructions) == count
    assert parser.labels["main"] == 0


def test_unsigned_32_bit_constants_match_clpru():
    instructions = Parser().parse_text(source("unsigned_set"))
    # ldi32 halves: clpru emits .w2 first, the simulator .w0 first; same pairs.
    assert {(i.operands[0], i.operands[1].value) for i in instructions[:7]} == {
        (Register(1, 16, 16), 0xFFFF), (Register(1, 0, 16), 0xFFFF),
        (Register(2, 16, 16), 0x8000), (Register(2, 0, 16), 0),
        (Register(3, 0, 32), 0xFFFF), (Register(4, 16, 16), 0x8000),
        (Register(4, 0, 16), 0)}
    assert instructions[7].operands == [Register(5, 0, 32), Immediate(0xFF)]
    core = execute(source("unsigned_set"))
    assert [core.registers.read_full(i) for i in range(1, 6)] == [
        0xFFFFFFFF, 0x80000000, 0xFFFF, 0x80000000, 0xFF]


def test_whole_register_alias_through_set_and_equ_matches_clpru():
    instructions = Parser().parse_text(source("reg_alias"))
    assert [i.opcode for i in instructions] == ["LDI", "ADD", "LDI", "HALT"]
    assert instructions[0].operands == [Register(5, 0, 32), Immediate(1)]
    assert instructions[1].operands == [Register(5, 0, 32), Register(5, 0, 32), Immediate(3)]
    assert instructions[2].operands[0] == Register(6, 0, 32)
    core = execute(source("reg_alias"))
    assert core.registers.read_full(5) == 4 and core.registers.read_full(6) == 2


@pytest.mark.parametrize("invocation", ["call f", "CALL f", "Call f"])
def test_user_call_macro_wins_over_shorthand_in_any_case(invocation):
    text = source("call_macro").replace("call f", invocation)
    instructions = Parser().parse_text(text)
    assert instructions[1].operands == [Register(28, 0, 16), Label("f", 3)]
    assert instructions[4].operands == [Register(28, 0, 16)]
    core = execute(text)
    assert core.registers.read_full(14) == 99
    assert core.registers.read_full(3) == 0


def test_user_ret_macro_wins_over_shorthand():
    text = "RET .macro\njmp r28.w0\n.endm\njal r28.w0, f\nhalt\nf:\nret"
    assert Parser().parse_text(text)[-1].operands == [Register(28, 0, 16)]


def test_register_counts_for_xfr_match_clpru():
    instructions = Parser().parse_text(source("xfr_count"))
    assert [i.opcode for i in instructions[:5]] == ["XIN", "XOUT", "ZERO", "FILL", "XCHG"]
    assert instructions[0].operands[2] == Register(0, 0, 8)
    assert instructions[1].operands[2] == Register(0, 8, 8)
    assert instructions[2].operands[1] == Register(0, 0, 8)
    assert instructions[3].operands[1] == Register(0, 16, 8)
    assert instructions[4].operands[2] == Register(0, 24, 8)


def test_zero_and_fill_use_register_count_at_run_time():
    core = execute("ldi r0.b0, 2\nldi r0.b2, 3\nldi32 r1, 0x11223344\nldi32 r2, 0x55667788\n"
                   "zero &r1, b0\nfill &r2, b2\nhalt")
    assert core.registers.read_full(1) == 0x11220000
    assert core.registers.read_full(2) == 0x55FFFFFF


@pytest.mark.parametrize("word, expected", [
    (0xFF00C201, ("LBBO", 3, 0)), (0xEF00C281, ("SBBO", 3, 1)),
    (0x9F00F801, ("LBCO", 3, 2)), (0x8F00F881, ("SBCO", 3, 3)),
    (0x2E853E01, ("XIN", 2, 0)), (0x2F053E81, ("XOUT", 2, 1)),
    (0x2EFFBE01, ("ZERO", 1, 0)), (0x2EFF3F01, ("FILL", 1, 2)),
    (0x2F853F81, ("XCHG", 2, 3)),
])
def test_disassembler_decodes_clpru_register_counts_like_the_assembler(word, expected):
    opcode, index, byte = expected
    decoded = disassemble_word(word)
    assert decoded.opcode == opcode
    assert decoded.operands[index] == Register(0, byte * 8, 8)


def test_disassembler_keeps_literal_counts():
    # Count code 123 is a literal 124 bytes; only 124-127 name R0 bytes.
    word = 0x2E853E01 & ~(0x7F << 7) | (123 << 7)
    assert disassemble_word(word).operands[2] == Immediate(124)


def test_ldi32_with_label_matches_clpru():
    parser = Parser()
    instructions = parser.parse_text("start: ldi32 r1, 0x12345678\nldi r2, 1\nqba start\nhalt")
    assert parser.labels["start"] == 0
    assert [i.opcode for i in instructions] == ["LDI", "LDI", "LDI", "QBA", "HALT"]
    assert {i.operands[1].value for i in instructions[:2]} == {0x1234, 0x5678}
    assert instructions[3].operands[0] == Label("start", 0)


def test_source_line_survives_macro_expansion():
    text = "M .macro\nnop\nnop\n.endm\nldi r1, 1\nM\nldi r2, MISSING"
    instructions = Parser().parse_text(text.replace("ldi r2, MISSING", "halt"))
    assert [i.source_line for i in instructions] == [5, 6, 6, 7]
    errors = Simulator().load("pru0", text)
    assert errors and "line 7" in errors[0]


@pytest.mark.parametrize("text", [
    "N .set 5\nS .struct\nx .uint\nN .endstruct\nhalt",
    "S .struct\nx .uint\nS .endstruct\nS .set 4\nhalt",
    "S .struct\nx .uint\nN .endstruct\nN .set 4\nhalt",
    "S .struct\nx .uint\n.endstruct\nA .struct\nx .uint\n.endstruct\nA .sassign r5, S\nhalt",
])
def test_structure_symbols_do_not_overwrite_existing_symbols(text):
    errors = Simulator().load("pru0", text)
    assert errors and "redefinition" in errors[0]


def test_named_endstruct_defines_size_when_name_is_free():
    text = "S .struct\nx .uint\ny .ushort\nSize .endstruct\nldi r1, Size\nhalt"
    assert execute(text).registers.read_full(1) == 6


@pytest.mark.parametrize("text", [
    ".text\nold:\n.sect \".text:other\"\nnop\nhalt",
    ".sect \".text:a\"\nold:\n.sect \".text:b\"\nhalt",
])
def test_label_only_line_counts_as_code_for_section_switch(text):
    errors = Simulator().load("pru0", text)
    assert errors and "multiple code sections" in errors[0]


def test_label_then_same_section_is_accepted():
    execute(".text\nold:\n.text\nldi r1, 3\nhalt")
    execute(".sect \".text:a\"\nold:\n.sect \".text:a\"\nhalt")


def test_defined_symbol_does_not_rewrite_dotted_member():
    text = "count .set 3\nCtx .struct\ncount .uint\n.endstruct\nldi r1, Ctx.count\nhalt"
    assert execute(text).registers.read_full(1) == 0
    text = ("count .set 3\nS .struct\nx .uint\ncount .uint\n.endstruct\n"
            "A .sassign r5, S\nldi A.count, 9\nhalt")
    assert execute(text).registers.read_full(6) == 9


def test_whole_register_alias_still_resolves_field():
    core = execute("Alias .set r5\nldi Alias.w0, 8\nhalt")
    assert core.registers.read_full(5) == 8


@pytest.mark.parametrize("text", [
    "jmp ret\nret:\nhalt",
    "jmp ret\nret: ldi r1, 1\nhalt",
    "jmp call\ncall:\nhalt",
    "jmp call\ncall: ldi r1, 1\nhalt",
])
def test_call_and_ret_usable_as_labels(text):
    assert Simulator().load("pru0", text) == []
