"""MCP inspection tools: multicore run-until, disassembly, breakpoints, config, peripherals."""

import json
from pathlib import Path

import pytest

from mcp_server.server import PRUSimulatorMCP

ELF_FIXTURE = Path(__file__).parents[1] / "references" / "pru_encoding_test.out"
LOOP = "ldi r1, 5\nloop:\nsub r1, r1, 1\nqbne loop, r1, 0\nhalt"


def fresh_mcp() -> PRUSimulatorMCP:
    return PRUSimulatorMCP(config_path="nonexistent.cfg")


def two_core(lead_src=LOOP, follow_src="ldi r2, 7\nldi r3, 1\nhalt"):
    mcp = fresh_mcp()
    assert mcp.pru_load(lead_src, core="pru0")["success"]
    assert mcp.pru_load(follow_src, core="pru1")["success"]
    return mcp


# ---- pru_run_until_multicore ---------------------------------------------

def test_multicore_until_halt_runs_both_cores():
    result = two_core().pru_run_until_multicore()
    assert result["success"] and result["reason"] == "halted"
    assert result["lead"]["halted"] and result["lead"]["core"] == "pru0"
    assert result["follow"]["core"] == "pru1" and result["follow"]["pc"] > 0
    assert result["lead_steps"] > 0


def test_multicore_reg_condition_on_follow_core():
    mcp = two_core(lead_src="nop\njmp 0")
    result = mcp.pru_run_until_multicore(
        condition="reg:r2==7", condition_core="pru1")
    assert result["reason"] == "condition_met" and result["success"]
    assert mcp.sim.cores["pru1"].registers.read_full(2) == 7


def test_multicore_gpo_pin_condition_on_lead():
    mcp = two_core(lead_src="nop\nset r30, r30, 3\nhalt")
    result = mcp.pru_run_until_multicore(condition="gpo:3==1")
    assert result["reason"] == "condition_met"
    assert mcp.sim.io("pru0")["gpo_pins"][3] == 1


def test_multicore_breakpoint_on_follow_core():
    mcp = two_core(lead_src="nop\njmp 0", follow_src="nop\nnop\nnop\njmp 0")
    mcp.pru_breakpoint(core="pru1", address=2)
    result = mcp.pru_run_until_multicore(max_steps=100)
    assert result["reason"] == "breakpoint"
    assert result["breakpoint_core"] == "pru1"
    assert result["follow"]["pc"] == 2 and not result["success"]


def test_multicore_breakpoint_on_lead_core():
    mcp = two_core()
    mcp.pru_breakpoint(core="pru0", address=2)
    result = mcp.pru_run_until_multicore()
    assert result["reason"] == "breakpoint" and result["breakpoint_core"] == "pru0"
    assert result["lead"]["pc"] == 2


def test_multicore_max_steps():
    mcp = two_core(lead_src="nop\njmp 0")
    result = mcp.pru_run_until_multicore(max_steps=5)
    assert result["reason"] == "max_steps" and result["lead_steps"] == 5
    assert not result["success"]


@pytest.mark.parametrize("bad", ["", "bogus", "reg:r40==1", "reg:x1==1",
                                 "pin:30==1", "gpo:1==2", "cycles>abc", "mem:zz!=1"])
def test_multicore_bad_condition_rejected_without_state_change(bad):
    mcp = two_core()
    before = mcp.pru_status()
    with pytest.raises(ValueError):
        mcp.pru_run_until_multicore(condition=bad)
    assert mcp.pru_status() == before


def test_multicore_rejects_same_core_and_bad_args():
    mcp = two_core()
    with pytest.raises(ValueError, match="different cores"):
        mcp.pru_run_until_multicore(lead="pru0", follow="pru0")
    with pytest.raises(ValueError):
        mcp.pru_run_until_multicore(max_steps=True)
    with pytest.raises(KeyError):
        mcp.pru_run_until_multicore(condition_core="nope")


def test_single_core_run_until_shares_parser():
    mcp = fresh_mcp()
    mcp.pru_load(LOOP)
    with pytest.raises(ValueError):
        mcp.pru_run_until(condition="bogus")
    assert mcp.pru_run_until(condition="reg:r1==3")["reason"] == "condition_met"


# ---- pru_disassemble -------------------------------------------------------

def test_disassemble_around_pc_marks_pc_label_breakpoint():
    mcp = fresh_mcp()
    mcp.pru_load(LOOP)
    mcp.pru_step(count=2)
    mcp.pru_breakpoint(address=3)
    out = mcp.pru_disassemble(before=1, after=1)
    assert out["pc"] == 2 and out["program_length"] == 4
    assert [e["address"] for e in out["instructions"]] == [1, 2, 3]
    assert [e["is_pc"] for e in out["instructions"]] == [False, True, False]
    assert [e["breakpoint"] for e in out["instructions"]] == [False, False, True]
    first = out["instructions"][0]
    assert first["label"] == "loop" and first["opcode"] == "SUB" and first["line"] == 3
    assert "sub" in first["text"]


def test_disassemble_clamps_both_ends_and_explicit_address():
    mcp = fresh_mcp()
    mcp.pru_load(LOOP)
    out = mcp.pru_disassemble(address=0, before=50, after=50)
    assert [e["address"] for e in out["instructions"]] == [0, 1, 2, 3]
    out = mcp.pru_disassemble(address=100, before=2, after=2)
    assert out["instructions"][-1]["address"] == 3
    assert mcp.pru_disassemble(address=2, before=0, after=0)["instructions"][0]["address"] == 2


def test_disassemble_no_program_and_bad_args():
    mcp = fresh_mcp()
    out = mcp.pru_disassemble()
    assert out["instructions"] == [] and out["program_length"] == 0
    with pytest.raises(ValueError):
        mcp.pru_disassemble(before=-1)
    with pytest.raises(ValueError):
        mcp.pru_disassemble(address=True)


@pytest.mark.skipif(not ELF_FIXTURE.exists(), reason="ELF fixture not present")
def test_disassemble_after_elf_load():
    mcp = fresh_mcp()
    assert mcp.pru_elf_load(path=str(ELF_FIXTURE))["success"]
    out = mcp.pru_disassemble(before=0, after=4)
    assert out["program_length"] > 0 and out["instructions"]
    assert all(e["text"] and e["opcode"] for e in out["instructions"])


# ---- breakpoints -----------------------------------------------------------

def test_breakpoint_list_remove_clear():
    mcp = fresh_mcp()
    mcp.pru_breakpoint(core="pru0", address=4)
    mcp.pru_breakpoint(core="pru0", address=2)
    mcp.pru_breakpoint(core="pru1", address=7)
    listed = mcp.pru_breakpoint_list()["breakpoints"]
    assert listed["pru0"] == [2, 4] and listed["pru1"] == [7]
    assert mcp.pru_breakpoint_list(core="pru1")["breakpoints"] == {"pru1": [7]}
    assert mcp.pru_breakpoint_remove(core="pru0", address=2) == {
        "removed": True, "breakpoints": [4]}
    assert mcp.pru_breakpoint_remove(core="pru0", address=2)["removed"] is False
    assert mcp.pru_breakpoint_clear(core="pru0")["cleared"] == 1
    assert mcp.pru_breakpoint_list()["breakpoints"]["pru1"] == [7]
    assert mcp.pru_breakpoint_clear()["cleared"] == 1
    assert all(v == [] for v in mcp.pru_breakpoint_list()["breakpoints"].values())
    with pytest.raises(KeyError):
        mcp.pru_breakpoint_list(core="nope")
    with pytest.raises(ValueError):
        mcp.pru_breakpoint_remove(address="1")


# ---- pru_config / pru_peripherals -------------------------------------------

def test_pru_config():
    mcp = fresh_mcp()
    cfg = mcp.pru_config()
    assert cfg["processor"] == mcp.sim.target
    assert cfg["pru_clock_mhz"] == mcp.sim._pru_clock_mhz
    assert cfg["iep_clock_mhz"] > 0
    assert cfg["available_cores"] == list(mcp.sim.cores)
    assert cfg["config_path"] == "nonexistent.cfg"


def test_pru_peripherals_shape_is_json_serialisable():
    mcp = fresh_mcp()
    out = mcp.pru_peripherals()
    json.dumps(out)
    assert out["core"] == "pru0" and out["io_mode"] in ("gpio", "perif", "sd")
    for key in ("mux_sel", "iep", "mac", "crc", "spad", "constant_table",
                "xfr_shift_en", "sd", "perif", "i2c", "device_bus"):
        assert key in out
    assert len(out["constant_table"]) == 32
    assert {"count", "clock_mhz", "compare", "capture"} <= out["iep"].keys()
    assert "bank0" in out["spad"]
    with pytest.raises(KeyError):
        mcp.pru_peripherals(core="nope")


def test_pru_peripherals_reflects_gpcfg_mode_and_i2c():
    mcp = fresh_mcp()
    mcp.sim.gpcfg_write("pru0", 3)
    assert mcp.pru_peripherals()["io_mode"] == "sd"
    mcp.pru_i2c_attach(core="pru0", enabled=True)
    assert mcp.pru_peripherals()["i2c"] is not None


# A slow lead instruction (MS_RAM load, 20+ cycles) lets the follower run
# several fast instructions inside one lead step.
def slow_lead_mcp(follow_src):
    mcp = PRUSimulatorMCP(config_path=str(Path(__file__).parents[1] / "memory.cfg"))
    lead_src = "ldi32 r4, 0x70000000\nlbbo &r5, r4, 0, 4\nnop\njmp 1"
    assert mcp.pru_load(lead_src, core="pru0")["success"]
    assert mcp.pru_load(follow_src, core="pru1")["success"]
    return mcp


def test_multicore_breakpoint_on_follow_core_inside_a_slow_lead_step():
    mcp = slow_lead_mcp("nop\nnop\nnop\nnop\nnop\nnop\nnop\njmp 0")
    mcp.pru_breakpoint(core="pru1", address=4)
    result = mcp.pru_run_until_multicore(max_steps=100)
    assert result["reason"] == "breakpoint"
    assert result["breakpoint_core"] == "pru1"
    assert result["follow"]["pc"] == 4 and not result["success"]


def test_multicore_gpo_condition_set_then_cleared_by_follow_inside_a_slow_lead_step():
    mcp = slow_lead_mcp("nop\nnop\nnop\nset r30, r30, 3\nnop\nclr r30, r30, 3\nhalt")
    result = mcp.pru_run_until_multicore(
        condition="gpo:3==1", condition_core="pru1", max_steps=100)
    assert result["reason"] == "condition_met" and result["success"]
    assert mcp.sim.io("pru1")["gpo_pins"][3] == 1


def test_multicore_lead_condition_beats_follow_breakpoint_during_catch_up():
    mcp = slow_lead_mcp("nop\nnop\nnop\nnop\nnop\nnop\nnop\njmp 0")
    mcp.pru_breakpoint(core="pru1", address=4)
    result = mcp.pru_run_until_multicore(
        condition="cycles>10", condition_core="pru0", max_steps=100)
    assert result["reason"] == "condition_met" and result["success"]


def test_multicore_follow_condition_beats_breakpoint_at_same_check():
    mcp = slow_lead_mcp("nop\nnop\nnop\nset r30, r30, 3\nnop\nnop\nhalt")
    mcp.pru_breakpoint(core="pru1", address=4)
    result = mcp.pru_run_until_multicore(
        condition="gpo:3==1", condition_core="pru1", max_steps=100)
    assert result["reason"] == "condition_met" and result["success"]
