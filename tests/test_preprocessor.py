"""Tests for core/preprocessor.py"""

import os
import tempfile
import textwrap
import pytest

from core.preprocessor import Preprocessor


def make_pp() -> Preprocessor:
    return Preprocessor()


# ---------------------------------------------------------------------------
# Comment stripping
# ---------------------------------------------------------------------------

class TestComments:
    def test_full_line_comment(self):
        pp = make_pp()
        assert pp.process_text("; this is a comment") == []

    def test_inline_comment(self):
        pp = make_pp()
        result = pp.process_text("MOV r1, r2  ; move it")
        assert result == ["MOV r1, r2"]

    def test_no_comment(self):
        pp = make_pp()
        result = pp.process_text("MOV r1, r2")
        assert result == ["MOV r1, r2"]


# ---------------------------------------------------------------------------
# Empty line stripping
# ---------------------------------------------------------------------------

class TestEmptyLines:
    def test_blank_lines_removed(self):
        pp = make_pp()
        src = "\nMOV r1, r2\n\nADD r3, r4, r5\n"
        result = pp.process_text(src)
        assert result == ["MOV r1, r2", "ADD r3, r4, r5"]

    def test_whitespace_only_lines_removed(self):
        pp = make_pp()
        src = "   \nMOV r1, r2\n   "
        result = pp.process_text(src)
        assert result == ["MOV r1, r2"]


# ---------------------------------------------------------------------------
# .set substitution
# ---------------------------------------------------------------------------

class TestSetDirective:
    def test_basic_substitution(self):
        pp = make_pp()
        src = "MYVAL .set 42\nMOV r1, MYVAL"
        result = pp.process_text(src)
        assert result == ["MOV r1, 42"]

    def test_multiple_defines(self):
        pp = make_pp()
        src = "A .set 1\nB .set 2\nADD r1, A, B"
        result = pp.process_text(src)
        assert result == ["ADD r1, 1, 2"]

    def test_set_not_emitted(self):
        pp = make_pp()
        src = "X .set 99"
        result = pp.process_text(src)
        assert result == []

    def test_set_no_preceding_use(self):
        pp = make_pp()
        src = "MOV r1, UNDEFINED"
        result = pp.process_text(src)
        assert result == ["MOV r1, UNDEFINED"]


# ---------------------------------------------------------------------------
# .asg directive (TI alias: reversed argument order vs .set)
# ---------------------------------------------------------------------------

class TestAsgDirective:
    def test_asg_substitutes_register_alias(self):
        pp = make_pp()
        src = ".asg r2, DN0\nMOV DN0, r5"
        result = pp.process_text(src)
        assert result == ["MOV r2, r5"]

    def test_asg_directive_not_emitted(self):
        pp = make_pp()
        src = ".asg r2, DN0"
        result = pp.process_text(src)
        assert result == []

    def test_asg_multiple_aliases(self):
        pp = make_pp()
        src = ".asg r2, DN0\n.asg r3, DN1\nADD DN0, DN1, DN0"
        result = pp.process_text(src)
        assert result == ["ADD r2, r3, r2"]

    def test_asg_value_can_be_numeric_string(self):
        pp = make_pp()
        src = ".asg 64, OSR_VAL\nLDI r0, OSR_VAL"
        result = pp.process_text(src)
        assert result == ["LDI r0, 64"]


# ---------------------------------------------------------------------------
# .if / .else / .endif
# ---------------------------------------------------------------------------

class TestConditionals:
    def test_if_true(self):
        pp = make_pp()
        src = "FLAG .set 1\n.if FLAG\nMOV r1, r2\n.endif"
        result = pp.process_text(src)
        assert result == ["MOV r1, r2"]

    def test_if_false(self):
        pp = make_pp()
        src = "FLAG .set 0\n.if FLAG\nMOV r1, r2\n.endif"
        result = pp.process_text(src)
        assert result == []

    def test_if_else_true_branch(self):
        pp = make_pp()
        src = "FLAG .set 1\n.if FLAG\nMOV r1, r2\n.else\nMOV r3, r4\n.endif"
        result = pp.process_text(src)
        assert result == ["MOV r1, r2"]

    def test_if_else_false_branch(self):
        pp = make_pp()
        src = "FLAG .set 0\n.if FLAG\nMOV r1, r2\n.else\nMOV r3, r4\n.endif"
        result = pp.process_text(src)
        assert result == ["MOV r3, r4"]

    def test_if_without_define_is_false(self):
        pp = make_pp()
        src = ".if UNDEF\nMOV r1, r2\n.endif"
        result = pp.process_text(src)
        # 'UNDEF' is an undefined symbol – should not emit
        assert result == []


# ---------------------------------------------------------------------------
# .macro / .endm
# ---------------------------------------------------------------------------

class TestMacros:
    def test_simple_macro_no_args(self):
        pp = make_pp()
        src = ".macro NOP_MACRO\nNOP\n.endm\nNOP_MACRO"
        result = pp.process_text(src)
        assert result == ["NOP"]

    def test_macro_with_args(self):
        pp = make_pp()
        src = ".macro MYMOV dst, src\nMOV dst, src\n.endm\nMYMOV r1, r2"
        result = pp.process_text(src)
        assert result == ["MOV r1, r2"]

    def test_macro_multi_line(self):
        pp = make_pp()
        src = textwrap.dedent("""\
            .macro PUSH reg
            SUB r0, r0, 4
            SBBO &reg, r0, 0, 4
            .endm
            PUSH r5
        """)
        result = pp.process_text(src)
        assert result == ["SUB r0, r0, 4", "SBBO &r5, r0, 0, 4"]

    def test_macro_not_emitted_on_definition(self):
        pp = make_pp()
        src = ".macro NOTHING\nNOP\n.endm"
        result = pp.process_text(src)
        assert result == []


# ---------------------------------------------------------------------------
# .include
# ---------------------------------------------------------------------------

class TestInclude:
    def test_include_file(self):
        pp = make_pp()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".inc", delete=False, encoding="utf-8"
        ) as tf:
            tf.write("; included file\nMOV r1, 0\n")
            tf_name = tf.name
        try:
            pp.include_paths.insert(0, os.path.dirname(tf_name))
            src = f'.include "{os.path.basename(tf_name)}"\nADD r2, r1, 1'
            result = pp.process_text(src)
            assert "MOV r1, 0" in result
            assert "ADD r2, r1, 1" in result
        finally:
            os.unlink(tf_name)

    def test_include_strips_comments(self):
        pp = make_pp()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".inc", delete=False, encoding="utf-8"
        ) as tf:
            tf.write("; just a comment\n")
            tf_name = tf.name
        try:
            pp.include_paths.insert(0, os.path.dirname(tf_name))
            src = f'.include "{os.path.basename(tf_name)}"'
            result = pp.process_text(src)
            assert result == []
        finally:
            os.unlink(tf_name)


# ---------------------------------------------------------------------------
# .struct / .ends
# ---------------------------------------------------------------------------

class TestStructs:
    def test_struct_basic(self):
        pp = make_pp()
        src = textwrap.dedent("""\
            .struct MyStruct
            .u8 field_a
            .u8 field_b
            .u16 field_c
            .u32 field_d
            .ends
        """)
        pp.process_text(src)
        assert pp.defines["MyStruct.field_a"] == "0"
        assert pp.defines["MyStruct.field_b"] == "1"
        assert pp.defines["MyStruct.field_c"] == "2"
        assert pp.defines["MyStruct.field_d"] == "4"

    def test_struct_offsets_substituted(self):
        pp = make_pp()
        src = textwrap.dedent("""\
            .struct Foo
            .u32 x
            .u32 y
            .ends
            LBBO &r1, r2, Foo.y, 4
        """)
        result = pp.process_text(src)
        assert result == ["LBBO &r1, r2, 4, 4"]

    def test_struct_entries_in_structs_dict(self):
        pp = make_pp()
        src = ".struct S\n.u8 a\n.ends"
        pp.process_text(src)
        assert "S.a" in pp.structs
        assert pp.structs["S.a"] == 0
