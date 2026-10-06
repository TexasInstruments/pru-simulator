"""Three- and four-core runs through the dashboard WebSocket (run_multicore)."""
import pytest
from fastapi.testclient import TestClient

from simulator import Simulator
from ui.server import app

client = TestClient(app)
LOOP = "start:\n        add r2, r2, 1\n        jmp start\n"
CORES = ("pru0", "rtu0", "pru1", "rtu1")


@pytest.fixture
def loaded(monkeypatch):
    import ui.server as srv
    fresh = Simulator(config_path=srv.config_path)
    monkeypatch.setattr(srv, "sim", fresh)
    srv._clear_history()
    for core in CORES:
        assert fresh.load(core, LOOP) == []
    return fresh


def _states(ws, cores):
    out = {}
    for _ in cores:
        state = ws.receive_json()
        assert state["type"] == "state", state
        out[state["core"]] = state
    assert set(out) == set(cores)
    return out


def test_run_multicore_advances_every_listed_partner(loaded):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "run_multicore", "core": "pru0",
                      "partners": ["rtu0", "pru1", "rtu1"], "max_steps": 200})
        states = _states(ws, CORES)
    assert [states[core]["core"] for core in CORES] == list(CORES)
    cycles = {core: states[core]["cycles"] for core in CORES}
    assert all(count > 0 for count in cycles.values()), cycles
    # All four run at the configured core clock, so pacing keeps them level.
    assert max(cycles.values()) - min(cycles.values()) <= 2, cycles
    assert all(states[core]["at_breakpoint"] is False for core in CORES)


def test_run_multicore_stops_at_a_breakpoint_on_any_partner(loaded):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "toggle_breakpoint", "core": "rtu1", "addr": 1})
        ws.receive_json()
        ws.send_json({"action": "run_multicore", "core": "pru0",
                      "partners": ["rtu0", "pru1", "rtu1"], "max_steps": 1000})
        states = _states(ws, CORES)
    assert states["rtu1"]["at_breakpoint"] is True and states["rtu1"]["pc"] == 1
    assert states["pru0"]["at_breakpoint"] is False
    assert states["pru0"]["cycles"] < 100, "stopped early, did not run the whole chunk"


def test_single_partner_form_still_works(loaded):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "run_multicore", "core": "pru0",
                      "partner": "rtu0", "max_steps": 50})
        states = _states(ws, ("pru0", "rtu0"))
    assert states["rtu0"]["cycles"] > 0


def test_step_and_reset_address_each_core_separately(loaded):
    with client.websocket_connect("/ws") as ws:
        for core in CORES:
            ws.send_json({"action": "step", "core": core, "count": 1})
            assert ws.receive_json()["core"] == core
        assert [loaded.cores[core].pc for core in CORES] == [1, 1, 1, 1]
        for core in CORES:
            ws.send_json({"action": "reset", "core": core})
            assert ws.receive_json()["core"] == core
        assert [loaded.cores[core].pc for core in CORES] == [0, 0, 0, 0]


@pytest.mark.parametrize("partners", [["nope"], ["pru0"], "rtu0", ["rtu0", "x"]])
def test_run_multicore_rejects_bad_partners_without_dropping_the_socket(loaded, partners):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "run_multicore", "core": "pru0", "partners": partners})
        error = ws.receive_json()
        assert error["type"] == "error" and "partners" in error["errors"][0]
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"action": "get_state", "core": "pru0"})   # still alive
        assert ws.receive_json()["core"] == "pru0"
    assert loaded.cores["pru0"].counters.cycles == 0
