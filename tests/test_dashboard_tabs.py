"""Two browser connections observe one simulator without polling or re-running it."""
import json
from types import SimpleNamespace

import anyio
import pytest
from fastapi.testclient import TestClient

from simulator import Simulator
from ui import server as srv


def test_missing_crc_accelerator_is_not_presented_as_a_real_zero_result():
    assert srv._read_crc(SimpleNamespace(accelerators={})) == {"mode": "—", "value": "—"}


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    fresh = Simulator(srv.config_path)
    cfg = tmp_path / "memory.cfg"
    cfg.write_bytes(open(srv.config_path, "rb").read())
    monkeypatch.setattr(srv, "config_path", str(cfg))
    monkeypatch.setattr(srv, "sim", fresh)
    monkeypatch.setattr(srv, "_iep_clock_override", None)
    srv._clear_history()
    with TestClient(srv.app) as client:
        yield client


def receive(ws):
    async def bounded():
        with anyio.fail_after(3):
            return await ws._send_rx.receive()
    message = ws.portal.call(bounded)
    assert message["type"] == "websocket.send", message
    return json.loads(message["text"])


def until(ws, predicate):
    for _ in range(40):
        message = receive(ws)
        if predicate(message):
            return message
    pytest.fail("expected live update was not received")


def state(ws, core="pru0"):
    return until(ws, lambda msg: msg["type"] == "state" and msg["core"] == core)


def test_load_step_input_and_reset_are_visible_in_an_idle_second_tab(dashboard):
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        control.send_json({"action": "load", "source": "ldi r0, 42\nhalt"})
        loaded = state(control)
        assert state(view)["instructions"] == loaded["instructions"]
        control.send_json({"action": "step"})
        stepped = state(control)
        live = state(view)
        assert live["pc"] == stepped["pc"] == 1
        assert live["cycles"] == stepped["cycles"]
        assert live["registers"][0] == "0x0000002A"
        control.send_json({"action": "set_input", "pin": 16, "value": 1})
        state(control)
        assert state(view)["io"]["gpi_pins"][16] == 1
        # Either tab may change the shared simulator.
        view.send_json({"action": "reset"})
        reset = state(view)
        assert state(control)["pc"] == reset["pc"] == 0


@pytest.mark.parametrize("name,partners", [
    ("SSI encoder (single core)", []),
    ("SSI multi-core (emulator + reader)", ["pru1"]),
])
def test_ssi_signal_capture_and_mailbox_reach_a_recording_observer(dashboard, name, partners):
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        view.send_json({"action": "subscribe_capture", "enabled": True})
        assert receive(view)["type"] == "capture_subscription"
        control.send_json({"action": "scenario_load", "name": name})
        until(control, lambda msg: msg["type"] == "scenario_loaded")
        for core in ["pru0", *partners]:
            state(control, core)
        state(view)
        control.send_json({"action": "run_multicore" if partners else "run",
                           "partners": partners, "max_steps": 20_000, "stride": None,
                           "capture": False, "run_id": 77})
        capture = until(view, lambda msg: msg["type"] == "capture")
        assert {sample[1] & 1 for sample in capture["samples"]} == {0, 1}, (len(capture["samples"]), capture["samples"][:5])
        steps = [sample[0] for sample in capture["samples"]]
        assert all(b - a <= 2 for a, b in zip(steps, steps[1:])), "SSI capture must retain the example sampling rate across tabs"
        assert capture.get("partners", []) == partners
        live = state(view)
        assert "run_id" not in live, "another tab's reply must not release a local pending command"
        assert live["captured"] is True
        assert live["io"]["ssi_demo"]["position"] == 2748
        assert live["io"]["ssi_demo"]["frame_count"] >= 2
        current = state(control)
        assert current["cycles"] == live["cycles"]
        assert current["registers"] == live["registers"]


def test_clock_config_change_reaches_open_tabs_without_reload(dashboard):
    with dashboard.websocket_connect("/ws") as view:
        response = dashboard.put("/config/clock_speed", json={"mhz": 250, "group": 0})
        assert response.status_code == 200
        assert state(view)["clocks"]["groups"][0]["mhz"] == 250


def test_memory_updates_use_each_tabs_own_address(dashboard):
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        view.send_json({"action": "read_memory", "addr": 16, "length": 4, "tag": "mem2"})
        assert receive(view)["data"] == [0, 0, 0, 0]
        control.send_json({"action": "write_memory", "addr": 16, "data": [1, 2, 3, 4]})
        assert receive(control)["type"] == "memory_written"
        live = until(view, lambda msg: msg["type"] == "memory")
        assert (live["tag"], live["addr"], live["data"]) == ("mem2", 16, [1, 2, 3, 4])


def test_run_status_and_stop_are_shared_and_second_tab_cannot_double_run(dashboard):
    assert srv.sim.load("pru0", "add r0, r0, 1\nqba 0") == []
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        control.send_json({"action": "start_execution", "mode": "run"})
        assert receive(control) == {"type": "execution", "running": True, "owner": True, "mode": "run"}
        shared = until(view, lambda msg: msg["type"] == "execution")
        assert shared["running"] and not shared["owner"]
        view.send_json({"action": "run", "max_steps": 10, "run_id": 1})
        until(view, lambda msg: msg["type"] == "error")
        reply = state(view)
        assert reply["cycles"] == 0 and reply["run_id"] == 1
        control.send_json({"action": "run", "managed": True, "max_steps": 10, "run_id": 1})
        progressed = state(control)
        assert progressed["instruction_count"] == 10
        assert state(view)["instruction_count"] == 10
        with dashboard.websocket_connect("/ws") as late:
            late.send_json({"action": "get_state"})
            joined = state(late)
            assert joined["instruction_count"] == 10
            assert joined["registers"] == progressed["registers"]
            assert srv.sim.cores["pru0"].counters.instruction_count == 10
        view.send_json({"action": "stop_execution"})
        assert until(control, lambda msg: msg["type"] == "execution")["running"] is False
        assert until(view, lambda msg: msg["type"] == "execution")["running"] is False
        # A chunk queued by the previous timer must not restart a stopped run.
        control.send_json({"action": "run", "managed": True, "max_steps": 10, "run_id": 2})
        assert state(control)["instruction_count"] == 10


def test_breakpoint_status_is_the_same_in_both_views(dashboard):
    assert srv.sim.load("pru0", "nop\nhalt") == []
    srv.sim.cores["pru0"].breakpoints.add(1)
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        control.send_json({"action": "run"})
        stopped = state(control)
        observed = state(view)
        assert stopped["at_breakpoint"] is observed["at_breakpoint"] is True
        assert stopped["pc"] == observed["pc"] == 1


def test_new_tab_sees_running_status_and_owner_disconnect_stops_it(dashboard):
    with dashboard.websocket_connect("/ws") as view:
        with dashboard.websocket_connect("/ws") as control:
            control.send_json({"action": "start_execution", "mode": "sim"})
            assert receive(control)["running"] is True
            until(view, lambda msg: msg["type"] == "execution")
            with dashboard.websocket_connect("/ws") as late:
                late.send_json({"action": "get_state"})
                assert state(late)["execution"] == {"type": "execution", "running": True, "owner": False, "mode": "sim"}
        assert until(view, lambda msg: msg["type"] == "execution")["running"] is False
