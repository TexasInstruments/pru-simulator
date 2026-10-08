"""Type validation for MCP tool parameters: ints reject bool/str, bools reject str/int."""

import pytest

from mcp_server.server import PRUSimulatorMCP

SOURCE = "ldi r0, 1\nldi r1, 2\nhalt"


def fresh_mcp() -> PRUSimulatorMCP:
    mcp = PRUSimulatorMCP(config_path="nonexistent.cfg")
    assert mcp.pru_load(SOURCE, core="pru0")["success"]
    assert mcp.pru_load(SOURCE, core="pru1")["success"]
    return mcp


def uart_inject(mcp, **overrides):
    kwargs = {"source": "halt", "payload": [0x41], "max_steps": 10}
    kwargs.update(overrides)
    return mcp.pru_uart_inject(**kwargs)


def with_tmp_path(kwargs, tmp_path):
    """Point any VCD path at tmp_path so a regression never writes into the repo root."""
    if "path" in kwargs:
        return {**kwargs, "path": str(tmp_path / "unused.vcd")}
    return kwargs


# (tool, kwargs) pairs that must raise instead of coercing the argument.
INVALID_INT_CALLS = [
    ("pru_step", {"count": True}),
    ("pru_step", {"count": "2"}),
    ("pru_step", {"count": 1.0}),
    ("pru_memory", {"addr": True, "length": 4}),
    ("pru_memory", {"addr": 0, "length": True}),
    ("pru_memory", {"addr": "0", "length": 4}),
    ("pru_memory", {"addr": 0, "length": "4"}),
    ("pru_breakpoint", {"address": True}),
    ("pru_breakpoint", {"address": "3"}),
    ("pru_run_until", {"max_steps": True}),
    ("pru_run_until", {"max_steps": "5"}),
    ("pru_run_until", {"max_cycles": True}),
    ("pru_run_until", {"max_cycles": "5"}),
    ("pru_step_multicore", {"count": True}),
    ("pru_step_multicore", {"count": "1"}),
    ("pru_step_multicore", {"guard_ns": True}),
    ("pru_step_multicore", {"guard_ns": "0"}),
    ("pru_i2c_attach", {"address": True}),
    ("pru_i2c_attach", {"address": "0x23"}),
    ("pru_set_input", {"pin": True}),
    ("pru_set_input", {"pin": "5"}),
    ("pru_vcd_export", {"path": "unused.vcd", "max_steps": True}),
    ("pru_vcd_export", {"path": "unused.vcd", "max_steps": "10"}),
]

INVALID_BOOL_CALLS = [
    ("pru_set_input", {"pin": 5, "value": "false"}),
    ("pru_set_input", {"pin": 5, "value": "true"}),
    ("pru_set_input", {"pin": 5, "value": "0"}),
    ("pru_set_input", {"pin": 5, "value": 1}),
    ("pru_set_input", {"pin": 5, "value": 0}),
    ("pru_set_input", {"pin": 5, "value": None}),
    ("pru_i2c_attach", {"enabled": "false"}),
    ("pru_i2c_attach", {"enabled": "true"}),
    ("pru_i2c_attach", {"enabled": 1}),
    ("pru_vcd_export", {"path": "unused.vcd", "include_gpi": "false"}),
    ("pru_vcd_export", {"path": "unused.vcd", "include_gpi": 1}),
]


@pytest.mark.parametrize("tool, kwargs", INVALID_INT_CALLS,
                         ids=[f"{t}-{next(iter(k))}-{type(next(reversed(k.values()))).__name__}"
                              for t, k in INVALID_INT_CALLS])
def test_integer_parameters_reject_bool_and_non_int(tool, kwargs, tmp_path):
    mcp = fresh_mcp()
    kwargs = with_tmp_path(kwargs, tmp_path)
    with pytest.raises(ValueError, match="must be (an integer|a number)"):
        getattr(mcp, tool)(**kwargs)


@pytest.mark.parametrize("tool, kwargs", INVALID_BOOL_CALLS,
                         ids=[f"{t}-{type(next(reversed(k.values()))).__name__}-{next(reversed(k.values()))}"
                              for t, k in INVALID_BOOL_CALLS])
def test_boolean_parameters_accept_only_real_bools(tool, kwargs, tmp_path):
    mcp = fresh_mcp()
    kwargs = with_tmp_path(kwargs, tmp_path)
    with pytest.raises(ValueError, match="must be a boolean"):
        getattr(mcp, tool)(**kwargs)


@pytest.mark.parametrize("overrides", [
    {"baudrate": True}, {"baudrate": "4000000"},
    {"frames": True}, {"frames": "1"},
    {"pin": True}, {"pin": "0"},
    {"dram0_offset": True}, {"dram0_offset": "0"},
    {"max_steps": True}, {"max_steps": "10"},
    {"payload": "A"}, {"payload": [True]}, {"payload": ["65"]}, {"payload": 65},
])
def test_uart_inject_rejects_bad_types(overrides):
    with pytest.raises(ValueError, match="must be (an integer|a list of integers)"):
        uart_inject(fresh_mcp(), **overrides)


def test_set_input_false_string_does_not_drive_the_pin_high():
    mcp = fresh_mcp()
    with pytest.raises(ValueError):
        mcp.pru_set_input(pin=5, value="false")
    assert mcp.pru_io()["gpi_pins"][5] == 0


def test_rejected_step_does_not_advance_the_core():
    mcp = fresh_mcp()
    with pytest.raises(ValueError):
        mcp.pru_step(count=True)
    assert mcp.sim.cores["pru0"].pc == 0


def test_valid_calls_still_work():
    mcp = fresh_mcp()

    assert mcp.pru_step(core="pru0", count=2)["instruction_text"]
    assert mcp.pru_memory(addr=0, length=4)["hex_dump"] == "00000000"
    assert mcp.pru_breakpoint(address=5) == {"id": 1}
    assert mcp.pru_run_until(max_steps=10, max_cycles=0)["reason"] == "halted"

    assert mcp.pru_set_input(pin=5, value=True) == {"ok": True}
    assert mcp.pru_io()["gpi_pins"][5] == 1
    assert mcp.pru_set_input(pin=5, value=False) == {"ok": True}
    assert mcp.pru_io()["gpi_pins"][5] == 0

    attached = mcp.pru_i2c_attach(enabled=True, address=0x23)
    assert attached["success"] is True and attached["enabled"] is True
    assert mcp.pru_i2c_attach(enabled=False)["enabled"] is False

    result = mcp.pru_step_multicore(count=1, guard_ns=0)
    assert result["success"] is True
    assert "lead" in mcp.pru_step_multicore(count=1, guard_ns=0.5)

    assert uart_inject(mcp)["status"] == "success"


def test_valid_vcd_export_still_works(tmp_path):
    mcp = fresh_mcp()
    result = mcp.pru_vcd_export(path=str(tmp_path / "ok.vcd"), pins="0-1",
                                max_steps=10, include_gpi=False)
    assert result["success"] is True
