"""Integration tests for multi-file include path threading."""
import tempfile
from pathlib import Path

import pytest

from core.preprocessor import Preprocessor
from core.parser import Parser
from simulator import Simulator


def test_preprocessor_include_paths_resolves_include():
    """process_text resolves .include when include_paths contains the file's dir."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "constants.inc").write_text("MY_VAL .set 42\n", encoding="utf-8")
        source = '.include "constants.inc"\nldi r0, MY_VAL\nhalt\n'
        pp = Preprocessor()
        lines = pp.process_text(source, include_paths=[tmpdir])
        joined = " ".join(lines).lower()
        assert "ldi" in joined and "42" in joined


def test_preprocessor_include_paths_does_not_mutate_state():
    """Passing include_paths to process_text must not permanently alter self.include_paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pp = Preprocessor()
        original = list(pp.include_paths)
        pp.process_text("ldi r0, 1\nhalt\n", include_paths=[tmpdir])
        assert pp.include_paths == original


def test_parser_include_paths_assembles_included_file():
    """parse_text with include_paths assembles source that .include's a define file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "defs.inc").write_text("MAGIC .set 0x55\n", encoding="utf-8")
        source = '.include "defs.inc"\nldi r1, MAGIC\nhalt\n'
        parser = Parser()
        instrs = parser.parse_text(source, include_paths=[tmpdir])
        assert len(instrs) == 2
        assert instrs[0].opcode == "LDI"


def test_simulator_load_include_paths_succeeds():
    """Simulator.load passes include_paths so included defines are resolved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "reg_vals.inc").write_text("BASE .set 10\n", encoding="utf-8")
        source = '.include "reg_vals.inc"\nldi r0, BASE\nhalt\n'
        sim = Simulator()
        errors = sim.load("pru0", source, include_paths=[tmpdir])
        assert errors == []
        assert sim.cores["pru0"].instructions[0].opcode == "LDI"


def test_simulator_load_without_filename_still_works():
    """Omitting include_paths leaves existing single-file behavior unchanged."""
    sim = Simulator()
    errors = sim.load("pru0", "ldi r0, 1\nhalt\n")
    assert errors == []
    assert len(sim.cores["pru0"].instructions) == 2
