"""Acceptance tests for MCP multicore pacing and run-until control."""

import pytest

from mcp_server.server import PRUSimulatorMCP


def fresh_mcp() -> PRUSimulatorMCP:
    return PRUSimulatorMCP(config_path="nonexistent.cfg")


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
    assert result["success"] is True


def test_multicore_rejects_same_lead_and_follow():
    with pytest.raises(ValueError, match="different cores"):
        fresh_mcp().pru_step_multicore(lead="pru0", follow="pru0")


def test_multicore_reports_when_follower_cannot_catch_up(monkeypatch):
    mcp = fresh_mcp()
    assert mcp.pru_load("ldi r0, 1\nhalt", core="pru0")["success"]
    assert mcp.pru_load("ldi r0, 1\nhalt", core="pru1")["success"]
    mcp.sim._perif["pru0"]._now_ns = 100.0
    monkeypatch.setattr(mcp.sim, "step_paced", lambda *args, **kwargs: None)

    result = mcp.pru_step_multicore(count=1)

    assert result["success"] is False
    assert result["reason"] == "pacing_catchup_failed"
    assert result["follow_ns"] < result["target_ns"]


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
    assert failed["cycles"] == 2
    assert failed["pc"] == 2


def test_cycle_budget_does_not_execute_stalling_instruction():
    mcp = fresh_mcp()
    assert mcp.pru_load("lbbo r0, r1, 0, 4\nhalt")["success"]

    result = mcp.pru_run_until(max_cycles=1)

    assert result["reason"] == "budget_exceeded"
    assert result["cycles"] == 0
    assert result["pc"] == 0


def test_run_until_honors_breakpoint_before_execution():
    mcp = fresh_mcp()
    assert mcp.pru_load("ldi r0, 1\nhalt")["success"]
    mcp.pru_breakpoint(address=0)

    result = mcp.pru_run_until()

    assert result["reason"] == "breakpoint"
    assert result["condition_met"] is False
    assert result["pc"] == 0
    assert mcp.pru_registers()["r0"] == "0x00000000"


@pytest.mark.parametrize("kwargs", [{"max_steps": -1}, {"max_cycles": -1}])
def test_run_until_rejects_negative_limits(kwargs):
    with pytest.raises(ValueError, match="non-negative"):
        fresh_mcp().pru_run_until(**kwargs)
