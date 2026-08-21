"""Acceptance tests for the independent-oracle MCP surface (G1-G3)."""

from pathlib import Path

import pytest

from mcp_server.server import PRUSimulatorMCP


ELF_FIXTURE = Path(__file__).parents[1] / "references" / "pru_encoding_test.out"


def fresh_mcp() -> PRUSimulatorMCP:
    return PRUSimulatorMCP(config_path="nonexistent.cfg")


def test_elf_load_reports_metadata_and_resets_halted_core():
    mcp = fresh_mcp()
    assert mcp.pru_load("ldi r0, 1\nhalt")["success"]
    assert mcp.pru_run_until()["reason"] == "halted"

    result = mcp.pru_elf_load(path=str(ELF_FIXTURE))

    assert result["success"] is True
    assert result["entry"] is not None
    assert result["sections"]
    state = mcp.sim.cores["pru0"]
    assert (state.pc, state.counters.cycles, state.halted) == (0, 0, False)


@pytest.mark.parametrize(
    ("offset", "replacement", "message"),
    [
        (16, b"\x00\x00", "ET_EXEC"),
        (40, b"\x00\x00", "header size"),
    ],
)
def test_elf_load_rejects_malformed_header(offset, replacement, message):
    data = bytearray(ELF_FIXTURE.read_bytes())
    data[offset:offset + len(replacement)] = replacement

    import base64
    result = fresh_mcp().pru_elf_load(b64=base64.b64encode(data).decode("ascii"))

    assert result["success"] is False
    assert result["entry"] is None
    assert result["sections"] == []
    assert message in result["errors"][0]


def test_multicore_default_advances_both_short_programs():
    mcp = fresh_mcp()
    source = "ldi r0, 1\nldi r1, 2\nhalt"
    assert mcp.pru_load(source, core="pru0")["success"]
    assert mcp.pru_load(source, core="pru1")["success"]

    result = mcp.pru_step_multicore(count=1)

    assert result["lead"]["pc"] == 1
    assert result["follow"]["pc"] == 1
    assert result["lead"]["cycles"] > 0
    assert result["follow"]["cycles"] > 0


def test_pin_predicates_select_one_direction_only():
    mcp = fresh_mcp()
    assert mcp.pru_load("ldi r30, 1\nhalt")["success"]
    mcp.pru_step()

    assert mcp.pru_run_until(condition="gpo:0==1", max_steps=0)["condition_met"]
    assert mcp.pru_run_until(condition="pin:0==0", max_steps=0)["condition_met"]
    assert not mcp.pru_run_until(condition="pin:0==1", max_steps=0)["condition_met"]


def test_cycle_budget_has_stable_pass_and_fail_shape():
    source = "ldi r0, 1\nadd r0, r0, 1\nhalt"

    passing = fresh_mcp()
    assert passing.pru_load(source)["success"]
    passed = passing.pru_run_until(max_cycles=3)
    assert passed == {
        "pc": 2,
        "cycles": 3,
        "reason": "halted",
        "budget_exceeded": False,
        "condition_met": True,
    }

    failing = fresh_mcp()
    assert failing.pru_load(source)["success"]
    failed = failing.pru_run_until(max_cycles=2)
    assert failed["reason"] == "budget_exceeded"
    assert failed["budget_exceeded"] is True
    assert failed["condition_met"] is False


@pytest.mark.parametrize("kwargs", [{"max_steps": -1}, {"max_cycles": -1}])
def test_run_until_rejects_negative_limits(kwargs):
    with pytest.raises(ValueError, match="non-negative"):
        fresh_mcp().pru_run_until(**kwargs)
