"""Acceptance tests for MCP multicore pacing and run-until control."""

import pytest

from mcp_server.server import PRUSimulatorMCP


def fresh_mcp() -> PRUSimulatorMCP:
    return PRUSimulatorMCP(config_path="nonexistent.cfg")


def test_multicore_batch_reports_actual_lead_steps_across_catchup_retries(tmp_path):
    config = tmp_path / "memory.cfg"
    config.write_text(
        "[device]\npru_clock_mhz=200\npru1_clock_mhz=200\n"
        "[DRAM0]\nbase=0\nsize=0x2000\nread_latency=2500\n"
        "write_latency=1\njitter=0\n")
    mcp = PRUSimulatorMCP(str(config))
    assert mcp.pru_load("nop\nlbbo &r2, r0, 0, 4\nhalt")["success"]
    assert mcp.pru_load("qba 0", core="pru1")["success"]

    first = mcp.pru_step_multicore(count=3)
    assert first["reason"] == "pacing_incomplete"
    assert first["lead_steps"] == 2
    retry = mcp.pru_step_multicore(count=1)
    assert retry["reason"] == "pacing_incomplete"
    assert retry["lead_steps"] == 0
    final = mcp.pru_step_multicore(count=1)
    assert final["success"]
    assert final["lead_steps"] == 1
    assert final["lead"]["halted"]
    assert mcp.pru_step_multicore(count=0)["lead_steps"] == 0


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


def test_multicore_uses_elapsed_time_when_core_clocks_differ(tmp_path):
    config = tmp_path / "memory.cfg"
    config.write_text(
        "[device]\npru_clock_mhz = 250\npru1_clock_mhz = 200\niep_clock_mhz = 200\n",
        encoding="utf-8",
    )
    mcp = PRUSimulatorMCP(config_path=str(config))
    source = "nop\njmp 0"
    assert mcp.pru_load(source, core="pru0")["success"]
    assert mcp.pru_load(source, core="pru1")["success"]

    result = mcp.pru_step_multicore(count=10)

    assert result["success"] is True
    assert result["lead"]["cycles"] == 10
    assert result["follow"]["cycles"] == 8


def test_multicore_rejects_same_lead_and_follow():
    with pytest.raises(ValueError, match="different cores"):
        fresh_mcp().pru_step_multicore(lead="pru0", follow="pru0")


def test_multicore_normal_follower_halt_preserves_success():
    mcp = fresh_mcp()
    assert mcp.pru_load("nop\njmp 0", core="pru0")["success"]
    assert mcp.pru_load("halt", core="pru1")["success"]
    for _ in range(3):
        result = mcp.pru_step_multicore(count=3)
        assert result["success"] is True
        assert result["follow"]["halted"] is True
    assert result["lead"]["cycles"] == 9


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


def test_multicore_live_follower_without_progress_is_catchup_failure(monkeypatch):
    mcp = fresh_mcp()
    assert mcp.pru_load("nop\njmp 0", core="pru0")["success"]
    assert mcp.pru_load("nop\njmp 0", core="pru1")["success"]
    monkeypatch.setattr(mcp.sim.cores["pru1"], "step", lambda: None)
    result = mcp.pru_step_multicore(count=1)
    assert result["success"] is False
    assert result["reason"] == "pacing_catchup_failed"
    assert result["follow_ns"] < result["target_ns"]


def test_multicore_faulted_follower_is_not_normal_halt():
    mcp = fresh_mcp()
    assert mcp.pru_load("nop\njmp 0", core="pru0")["success"]
    assert mcp.pru_load("halt", core="pru1")["success"]
    follower = mcp.sim.cores["pru1"]
    follower.halted = True
    follower.fault = {"type": "memory", "address": 0xFFFFFFFF}
    result = mcp.pru_step_multicore(count=1)
    assert result["success"] is False
    assert result["follow"]["fault"] == follower.fault


def test_multicore_actual_lead_fault_is_not_normal_completion():
    mcp = fresh_mcp()
    assert mcp.pru_load("ldi32 r0, 0xFFFFFFFF\nlbbo &r1, r0, 0, 4\nhalt", core="pru0")["success"]
    assert mcp.pru_load("nop\njmp 0", core="pru1")["success"]
    result = mcp.pru_step_multicore(count=4)
    assert result["success"] is False
    assert result["reason"] == "core_fault"
    assert result["lead"]["fault"]["address"] == 0xFFFFFFFF
    assert result["lead"]["fault"]["opcode"] == "LBBO"


def test_multicore_normal_lead_halt_is_success():
    mcp = fresh_mcp()
    assert mcp.pru_load("halt", core="pru0")["success"]
    assert mcp.pru_load("nop\njmp 0", core="pru1")["success"]
    result = mcp.pru_step_multicore(count=4)
    assert result["success"] is True
    assert result["lead"]["halted"] is True
    assert result["lead"]["fault"] is None


@pytest.mark.parametrize("guard", [float("nan"), float("inf"), -float("inf")])
def test_multicore_rejects_nonfinite_guards_before_execution(guard):
    mcp = fresh_mcp()
    assert mcp.pru_load("nop\njmp 0", core="pru0")["success"]
    with pytest.raises(ValueError, match="finite"):
        mcp.pru_step_multicore(guard_ns=guard)
    assert mcp.sim.iep.now_ns == 0


def test_multicore_checks_the_guard_target_selected_before_follower_mux_change():
    mcp = fresh_mcp()
    assert mcp.pru_load("nop\njmp 0", core="pru0")["success"]
    assert mcp.pru_load(
        "ldi32 r0, 0x2600C\nldi r1, 0\nsbbo &r1, r0, 0, 4\n"
        "loop: nop\njmp loop", core="pru1"
    )["success"]
    for core in ("pru0", "pru1"):
        mcp.sim.gpcfg_write(core, 1)
    mcp.sim.step("pru0", 10)

    result = mcp.pru_step_multicore(count=1, guard_ns=20)

    assert not mcp.sim._perif["pru1"].enabled
    assert result["follow"]["cycles"] == 7  # 35 ns target, 20 ns behind 55 ns lead
    assert result["success"] is True


def test_multicore_exhausted_follower_is_normal_completion():
    mcp = fresh_mcp()
    assert mcp.pru_load("nop\njmp 0", core="pru0")["success"]
    assert mcp.pru_load("ldi r0, 42", core="pru1")["success"]

    result = mcp.pru_step_multicore(count=3)

    assert result["success"] is True
    assert result["follow"]["pc"] == 1
    assert result["follow"]["halted"] is False
    assert result["follow"]["fault"] is None
    assert mcp.sim.registers("pru1")[0] == 42


def test_multicore_catchup_is_bounded_and_resumes_without_skipping_execution():
    mcp = fresh_mcp()
    assert mcp.pru_load("nop\njmp 0", core="pru0")["success"]
    assert mcp.pru_load("add r0, r0, 1\njmp 0", core="pru1")["success"]
    mcp.sim.step("pru0", 10000)

    result = mcp.pru_step_multicore()

    assert result["success"] is False
    assert result["reason"] == "pacing_incomplete"
    assert 0 < result["follow"]["cycles"] < 10001
    assert result["follow_ns"] < result["target_ns"]
    assert mcp.sim.registers("pru1")[0] == (result["follow"]["cycles"] + 1) // 2
    for _ in range(20):
        result = mcp.pru_step_multicore()
        if result["success"]:
            break
        assert result["reason"] == "pacing_incomplete"

    assert result["success"] is True
    assert result["follow"]["cycles"] == result["lead"]["cycles"]
    assert mcp.sim.registers("pru1")[0] == (result["follow"]["cycles"] + 1) // 2


@pytest.mark.parametrize("finish", ["halt", "nop"])
def test_multicore_resumes_catchup_after_the_lead_finishes(finish):
    mcp = fresh_mcp()
    assert mcp.pru_load(
        f"ldi r0, 10000\nloop: sub r0, r0, 1\nqbne loop, r0, 0\n{finish}",
        core="pru0"
    )["success"]
    assert mcp.pru_load("add r1, r1, 1\njmp 0", core="pru1")["success"]
    mcp.sim.step("pru0", 20001)

    for expected_cycles in (1000, 2000):
        result = mcp.pru_step_multicore(count=5)
        assert result["success"] is False
        assert result["reason"] == "pacing_incomplete"
        assert result["lead"]["cycles"] == 20002
        assert result["lead"]["halted"] == (finish == "halt")
        assert result["lead"]["fault"] is None
        assert result["follow"]["cycles"] == expected_cycles
        assert mcp.sim.registers("pru1")[1] == expected_cycles // 2

    for _ in range(25):
        result = mcp.pru_step_multicore(count=5)
        if result["success"]:
            break
        assert result["reason"] == "pacing_incomplete"

    assert result["success"] is True
    assert result["lead"]["cycles"] == result["follow"]["cycles"] == 20002
    assert mcp.sim.registers("pru1")[1] == 10001
