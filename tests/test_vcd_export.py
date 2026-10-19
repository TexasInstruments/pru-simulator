"""Targeted tests for deterministic GPIO VCD export and its MCP surface."""

import hashlib
import json

import pytest

from mcp_server.server import PRUSimulatorMCP
from mcp_server.vcd_export import parse_pin_selection


def test_parse_pin_selection_is_sorted_unique_and_rejects_invalid_ranges():
    assert parse_pin_selection("3,1-2,0x3") == [1, 2, 3]
    with pytest.raises(ValueError, match="ascending"):
        parse_pin_selection("3-1")
    with pytest.raises(ValueError, match="0-19"):
        parse_pin_selection("20")


def test_pru_vcd_export_records_deterministic_gpo_transitions(tmp_path):
    mcp = PRUSimulatorMCP(config_path="nonexistent.cfg")
    source = "ldi r30, 0\nldi r30, 1\nldi r30, 3\nldi r30, 2\nhalt"
    assert mcp.pru_load(source=source)["success"]

    first_path = tmp_path / "first.vcd"
    first = mcp.pru_vcd_export(path=str(first_path), pins="0-1")
    first_bytes = first_path.read_bytes()

    assert first["success"] is True
    assert first["complete"] is True
    assert first["steps_executed"] == 5
    assert first["start_cycle"] == 0
    assert first["end_cycle"] == 5
    assert first["halted"] is True
    assert first["stop_reason"] == "halted"
    assert first["transition_count"] == 3
    assert first["cycle_period_ps"] == 5000
    assert first["signals"] == [
        {"name": "gpo_0", "kind": "gpo", "pin": 0},
        {"name": "gpo_1", "kind": "gpo", "pin": 1},
    ]
    assert first["sha256"] == hashlib.sha256(first_bytes).hexdigest()
    assert "#10000\n1!\n" in first_bytes.decode("ascii")
    assert "#15000\n1\"\n" in first_bytes.decode("ascii")
    assert "#20000\n0!\n" in first_bytes.decode("ascii")
    json.dumps(first)

    mcp.pru_reset()
    assert mcp.pru_load(source=source)["success"]
    second_path = tmp_path / "second.vcd"
    second = mcp.pru_vcd_export(path=str(second_path), pins="0-1")
    assert second_path.read_bytes() == first_bytes
    assert second["sha256"] == first["sha256"]


def test_pru_vcd_export_can_capture_gpi_and_respects_step_budget(tmp_path):
    mcp = PRUSimulatorMCP(config_path="nonexistent.cfg")
    mcp.pru_set_input(pin=2, value=True)
    assert mcp.pru_load(source="ldi r30, 4\nldi r30, 0\nhalt")["success"]

    result = mcp.pru_vcd_export(
        path=str(tmp_path / "budget.vcd"),
        pins="2",
        include_gpi=True,
        max_steps=1,
    )

    assert result["steps_executed"] == 1
    assert result["success"] is False
    assert result["complete"] is False
    assert result["halted"] is False
    assert result["stop_reason"] == "max_steps"
    assert result["transition_count"] == 1
    assert [signal["name"] for signal in result["signals"]] == ["gpo_2", "gpi_2"]
    text = (tmp_path / "budget.vcd").read_text(encoding="ascii")
    assert "0!" in text
    assert "1\"" in text
    assert "#5000\n1!" in text


def test_pru_vcd_export_zero_step_budget_is_incomplete_and_deterministic(tmp_path):
    mcp = PRUSimulatorMCP(config_path="nonexistent.cfg")
    source = "ldi r30, 1\nhalt"
    assert mcp.pru_load(source=source)["success"]

    first_path = tmp_path / "zero-first.vcd"
    first = mcp.pru_vcd_export(path=str(first_path), pins="0", max_steps=0)
    first_bytes = first_path.read_bytes()

    assert first["success"] is False
    assert first["complete"] is False
    assert first["steps_executed"] == 0
    assert first["start_cycle"] == 0
    assert first["end_cycle"] == 0
    assert first["halted"] is False
    assert first["stop_reason"] == "max_steps"
    assert first["transition_count"] == 0
    assert first["sha256"] == hashlib.sha256(first_bytes).hexdigest()

    mcp.pru_reset()
    assert mcp.pru_load(source=source)["success"]
    second_path = tmp_path / "zero-second.vcd"
    second = mcp.pru_vcd_export(path=str(second_path), pins="0", max_steps=0)

    assert second_path.read_bytes() == first_bytes
    assert second["sha256"] == first["sha256"]
