"""Acceptance tests for the deterministic headless JSON runner."""

import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def invoke(*arguments):
    result = subprocess.run(
        [sys.executable, "-m", "tools.headless_runner", "--json", *map(str, arguments)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, json.loads(result.stdout)


def test_assembly_halts_with_deterministic_json(tmp_path):
    source = tmp_path / "program.asm"
    source.write_text("ldi r0, 42\nhalt\n", encoding="utf-8")

    first, payload = invoke("--assembly", source, "--core", "pru1", "--max-steps", 4)
    second, _ = invoke("--assembly", source, "--core", "pru1", "--max-steps", 4)

    assert first.returncode == 0
    assert first.stderr == ""
    assert first.stdout == second.stdout
    assert payload["success"] is True
    assert payload["reason"] == "halted"
    assert payload["core"] == "pru1"
    assert payload["state"]["registers"][0] == "0x0000002a"


def test_register_condition_can_finish_before_halt(tmp_path):
    source = tmp_path / "condition.asm"
    source.write_text("ldi r3, 0x55\nqba 0\n", encoding="utf-8")

    result, payload = invoke(
        "--assembly", source, "--until", "reg:r3==0x55", "--max-steps", 10
    )

    assert result.returncode == 0
    assert payload["reason"] == "condition_met"
    assert payload["state"]["steps"] == 1


def test_tracked_reference_elf_executes_to_halt():
    result, payload = invoke(
        "--elf", ROOT / "references" / "isa_execution_test.out",
        "--core", "pru0", "--max-steps", 20_000,
    )

    assert result.returncode == 0
    assert payload["input"]["format"] == "elf"
    assert payload["reason"] == "halted"
    assert payload["state"]["steps"] == 560
    assert payload["state"]["cycles"] == 670


def test_elf_entry_point_is_applied(tmp_path):
    elf = bytearray((ROOT / "references" / "isa_execution_test.out").read_bytes())
    struct.pack_into("<I", elf, 24, 573 * 4)
    path = tmp_path / "entry-at-halt.out"
    path.write_bytes(elf)

    result, payload = invoke("--elf", path, "--max-steps", 2)

    assert result.returncode == 0
    assert payload["reason"] == "halted"
    assert payload["state"]["pc"] == 573
    assert payload["state"]["steps"] == 1


def test_elf_rejects_non_executable_and_truncated_section_table(tmp_path):
    fixture = (ROOT / "references" / "isa_execution_test.out").read_bytes()
    cases = []
    non_exec = bytearray(fixture)
    struct.pack_into("<H", non_exec, 16, 1)
    cases.append(("non-exec.out", non_exec, "ET_EXEC"))
    truncated = bytearray(fixture)
    struct.pack_into("<I", truncated, 32, len(truncated) - 1)
    cases.append(("truncated.out", truncated, "section header table is truncated"))

    for filename, contents, message in cases:
        path = tmp_path / filename
        path.write_bytes(contents)
        result, payload = invoke("--elf", path)

        assert result.returncode == 2
        assert result.stderr == ""
        assert result.stdout.count("\n") == 1
        assert payload["reason"] == "load_error"
        assert message in payload["errors"][0]


def test_step_budget_is_a_failing_condition(tmp_path):
    source = tmp_path / "loop.asm"
    source.write_text("loop: qba loop\n", encoding="utf-8")

    result, payload = invoke("--assembly", source, "--max-steps", 3)

    assert result.returncode == 3
    assert payload["success"] is False
    assert payload["reason"] == "step_budget_exceeded"
    assert payload["state"]["steps"] == 3


def test_cycle_budget_is_hard_even_when_condition_is_unmet(tmp_path):
    source = tmp_path / "loop.asm"
    source.write_text("loop: qba loop\n", encoding="utf-8")

    result, payload = invoke(
        "--assembly", source, "--until", "cycles>=9", "--max-cycles", 2
    )

    assert result.returncode == 3
    assert payload["reason"] == "cycle_budget_pre_instruction_refusal"
    assert payload["state"]["cycles"] == 2
    assert payload["state"]["steps"] == 2
    assert payload["budget_refusal"] == {
        "cycles_before": 2,
        "kind": "pre_instruction",
        "next_instruction_cycles": {"maximum": 1, "minimum": 1},
        "remaining_cycles": 0,
    }


def test_program_end_without_requested_condition_is_unmet(tmp_path):
    source = tmp_path / "falls_off.asm"
    source.write_text("ldi r0, 1\n", encoding="utf-8")

    result, payload = invoke("--assembly", source)

    assert result.returncode == 3
    assert payload["reason"] == "condition_unmet"


def test_invalid_assembly_and_elf_are_load_errors(tmp_path):
    assembly = tmp_path / "bad.asm"
    assembly.write_text("ldi r0\n", encoding="utf-8")
    elf = tmp_path / "bad.out"
    elf.write_bytes(b"not an elf")

    asm_result, asm_payload = invoke("--assembly", assembly)
    elf_result, elf_payload = invoke("--elf", elf)

    assert asm_result.returncode == 2
    assert asm_payload["reason"] == "load_error"
    assert asm_payload["errors"]
    assert elf_result.returncode == 2
    assert elf_payload["reason"] == "load_error"
    assert elf_payload["errors"]


def test_invalid_arguments_still_emit_one_json_object():
    result, payload = invoke("--core", "pru0")

    assert result.returncode == 2
    assert result.stderr == ""
    assert payload["reason"] == "input_error"
    assert payload["errors"]


def test_help_does_not_leak_non_json_text_to_stdout():
    result, payload = invoke("--help")

    assert result.returncode == 2
    assert result.stderr == ""
    assert payload["reason"] == "input_error"
    assert "usage:" in payload["errors"][0]


def test_rtu1_headless_selection_and_unsupported_target(tmp_path):
    source = tmp_path / "rtu.asm"
    source.write_text("halt\n")
    result, payload = invoke("--assembly", source, "--core", "rtu1")
    assert result.returncode == 0
    assert payload["success"] is True
    config = tmp_path / "unsupported.cfg"
    config.write_text("[device]\ntarget = AM263x\n")
    result, payload = invoke("--assembly", source, "--core", "rtu1", "--config", config)
    assert result.returncode == 2
    assert payload["reason"] == "input_error"
    assert "unavailable" in payload["errors"][0]
    config.write_text("[device]\ntarget = am234x\n")
    result, payload = invoke("--assembly", source, "--core", "rtu1", "--config", config)
    assert result.returncode == 2
    assert payload["reason"] == "input_error"
    assert "am234x" in payload["errors"][0]


def test_cycle_budget_counts_the_misaligned_store_penalty(tmp_path):
    """PR #78 review: a misaligned 4-byte store to Shared RAM spans two words
    and costs 2 + W = 4 cycles (#77).  With 3 cycles left the runner must
    refuse it rather than run past --max-cycles."""
    source = tmp_path / "store.asm"
    source.write_text(
        "ldi32 r1, 0x00010001\nsbbo &r4, r1, 0, 4\nhalt\n", encoding="utf-8"
    )

    result, payload = invoke("--assembly", source, "--max-cycles", 5)

    assert result.returncode == 3
    assert payload["reason"] == "cycle_budget_pre_instruction_refusal"
    assert payload["state"]["cycles"] == 2
    assert payload["budget_refusal"]["next_instruction_cycles"] == {
        "maximum": 4, "minimum": 4,
    }

    # The prediction is what the simulator then charges.
    result, payload = invoke("--assembly", source, "--max-cycles", 6)
    assert payload["state"]["cycles"] == 6


@pytest.mark.parametrize("budget", [26, 30, 36])
def test_cycle_budget_counts_the_msram_line_crossing_cost(tmp_path, budget):
    """A PRU0 4-byte read of MSRAM at a 64 B line boundary costs 35 cycles
    (#69), not 22.  Budgets between those must refuse it before it runs."""
    source = tmp_path / "cross.asm"
    source.write_text(
        "ldi32 r1, 0x7000003f\nlbbo &r2, r1, 0, 4\nhalt\n", encoding="utf-8"
    )
    config = ROOT / "config" / "memory_am243x.cfg"

    result, payload = invoke("--assembly", source, "--core", "pru0",
                             "--config", config, "--max-cycles", budget)

    assert result.returncode == 3
    assert payload["reason"] == "cycle_budget_pre_instruction_refusal"
    assert payload["state"]["cycles"] == 2
    assert payload["budget_refusal"]["next_instruction_cycles"] == {
        "maximum": 35, "minimum": 35,
    }

    result, payload = invoke("--assembly", source, "--core", "pru0",
                             "--config", config, "--max-cycles", 38)
    assert payload["state"]["cycles"] == 38
