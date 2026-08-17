"""Focused regressions for multicore Signal Graph capture timing."""

import pytest
from fastapi.testclient import TestClient

from simulator import Simulator
from ui.server import app


client = TestClient(app)


@pytest.fixture
def fresh_sim(monkeypatch):
    import ui.server as srv

    fresh = Simulator(config_path=srv.config_path)
    monkeypatch.setattr(srv, "sim", fresh)
    return fresh


_LOOP = """
    .global main
    .sect ".text"
main:
    jmp main
"""


def _receive_multicore_messages(ws):
    """Receive one complete successful multicore response."""
    messages = []
    for _ in range(8):
        messages.append(ws.receive_json())
        if sum(message["type"] == "state" for message in messages) == 2:
            return messages
    raise AssertionError(f"multicore response did not finish: {messages!r}")


def _capture_for_core(messages, core):
    return next(
        message for message in messages
        if message["type"] == "capture" and message["core"] == core
    )


def test_multicore_capture_run_steps_are_absolute_across_chunks(fresh_sim):
    """Two run_multicore chunks must not restart their graph time at zero."""
    assert fresh_sim.load("pru0", _LOOP) == []
    assert fresh_sim.load("pru1", _LOOP) == []

    with client.websocket_connect("/ws") as ws:
        captures = []
        for _ in range(2):
            ws.send_json({
                "action": "run_multicore",
                "core": "pru0",
                "partner": "pru1",
                "max_steps": 30,
                "capture": True,
            })
            messages = _receive_multicore_messages(ws)
            captures.append(_capture_for_core(messages, "pru0"))

    first_steps = [sample[-1] for sample in captures[0]["samples"]]
    second_steps = [sample[-1] for sample in captures[1]["samples"]]
    assert first_steps
    assert second_steps
    assert all(left < right for left, right in zip(first_steps, first_steps[1:]))
    assert all(left < right for left, right in zip(second_steps, second_steps[1:]))
    assert second_steps[0] > first_steps[-1]


def test_multicore_run_acknowledges_request_for_ui_backpressure(fresh_sim):
    """A run request can signal completion without changing legacy replies."""
    assert fresh_sim.load("pru0", _LOOP) == []
    assert fresh_sim.load("pru1", _LOOP) == []

    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "action": "run_multicore",
            "core": "pru0",
            "partner": "pru1",
            "max_steps": 10,
            "capture": False,
            "request_id": 7,
        })
        _receive_multicore_messages(ws)
        done = ws.receive_json()

    assert done == {"type": "run_done", "request_id": 7}


def test_gpio_multicore_run_reports_instruction_count_mismatch(fresh_sim):
    """GPIO/SSI capture must reject cores that start at different steps."""
    assert fresh_sim.load("pru0", _LOOP) == []
    assert fresh_sim.load("pru1", _LOOP) == []
    fresh_sim.step("pru0", 3)
    fresh_sim.step("pru1", 1)

    assert fresh_sim.cores["pru0"].counters.instruction_count != \
        fresh_sim.cores["pru1"].counters.instruction_count
    assert fresh_sim.gpcfg_state("pru0")["mux_sel"] == 0
    assert fresh_sim.gpcfg_state("pru1")["mux_sel"] == 0

    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "action": "run_multicore",
            "core": "pru0",
            "partner": "pru1",
            "max_steps": 10,
            "capture": True,
        })
        message = ws.receive_json()

    assert message["type"] == "error"
    errors = " ".join(str(error) for error in message.get("errors", []))
    assert "sync" in errors.lower() or "instruction count" in errors.lower()


def test_peripheral_multicore_run_allows_instruction_drift_at_same_virtual_time(
    fresh_sim,
):
    """Peripheral mode synchronizes by virtual time, not instruction count."""
    assert fresh_sim.load("pru0", _LOOP) == []
    assert fresh_sim.load("pru1", _LOOP) == []
    fresh_sim.step("pru0", 3)
    fresh_sim.step("pru1", 1)
    fresh_sim.gpcfg_write("pru0", 1)
    fresh_sim.gpcfg_write("pru1", 1)
    # The scenario under test is equal peripheral time despite unequal core
    # instruction counts; align the fixture's virtual clocks explicitly.
    fresh_sim._perif["pru1"]._now_ns = fresh_sim._perif["pru0"]._now_ns

    assert fresh_sim.cores["pru0"].counters.instruction_count != \
        fresh_sim.cores["pru1"].counters.instruction_count
    assert fresh_sim._perif["pru0"]._now_ns == fresh_sim._perif["pru1"]._now_ns

    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "action": "run_multicore",
            "core": "pru0",
            "partner": "pru1",
            "max_steps": 1,
            "capture": False,
        })
        messages = _receive_multicore_messages(ws)

    assert {message["type"] for message in messages} == {"state"}
    assert {message["core"] for message in messages} == {"pru0", "pru1"}
