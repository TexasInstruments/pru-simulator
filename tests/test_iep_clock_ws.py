"""Runtime IEP counter clock selection through the dashboard WebSocket."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from simulator import Simulator
from ui.server import app

client = TestClient(app)
CNT_ENABLE_INC1 = 0x11   # CNT_ENABLE | DEFAULT_INC = 1


@pytest.fixture
def fresh_sim(monkeypatch):
    import ui.server as srv
    fresh = Simulator(config_path=srv.config_path)
    monkeypatch.setattr(srv, "sim", fresh)
    monkeypatch.setattr(srv, "_iep_clock_override", None)
    srv._clear_history()
    return fresh


def _send(ws, **action):
    ws.send_json({"core": "pru0", **action})
    messages = []
    while True:
        message = ws.receive_json()
        messages.append(message)
        if message["type"] == "state" and message["core"] == "pru0":
            return messages


def test_state_reports_the_default_iep_clock(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        state = _send(ws, action="get_state")[-1]
    assert state["iep"] == {"clock_mhz": 200.0, "external_mhz": 200.0, "core_clock": False,
                            "choices_mhz": [200, 225, 250, 300, 333]}


@pytest.mark.parametrize("mhz", [225, 250, 300, 333])
def test_set_iep_clock_changes_the_counter_rate_without_losing_time(fresh_sim, mhz):
    iep = fresh_sim.iep
    iep.global_cfg = CNT_ENABLE_INC1
    core_hz = float(iep.core_clock_hz("pru0"))
    with client.websocket_connect("/ws") as ws:
        iep.observe_core_cycles("pru0", 1000)
        before = iep.count
        assert before == round(1000 * 200e6 / core_hz)
        state = _send(ws, action="set_iep_clock", mhz=mhz)[-1]
        assert state["iep"]["clock_mhz"] == float(mhz)
        assert iep.count == before, "switching the clock does not move the counter"
        iep.observe_core_cycles("pru0", 3000)
    assert iep.count - before == round(2000 * mhz * 1e6 / core_hz)


@pytest.mark.parametrize("bad", [0, 275, "300", None, True, -1])
def test_set_iep_clock_rejects_other_values(fresh_sim, bad):
    with client.websocket_connect("/ws") as ws:
        messages = _send(ws, action="set_iep_clock", mhz=bad)
    assert messages[0]["type"] == "error" and messages[0]["tag"] == "iep"
    assert messages[-1]["iep"]["clock_mhz"] == 200.0


def test_iep_clock_is_runtime_only_and_never_written_to_the_config(fresh_sim):
    import ui.server as srv
    before = Path(srv.config_path).read_bytes()
    with client.websocket_connect("/ws") as ws:
        _send(ws, action="set_iep_clock", mhz=300)
    assert Path(srv.config_path).read_bytes() == before
    assert Simulator(config_path=srv.config_path).iep.external_clock_hz == 200_000_000


def test_core_clock_selected_by_firmware_is_reported_not_overridden(fresh_sim):
    fresh_sim.iep.write_iepclk(1)
    with client.websocket_connect("/ws") as ws:
        state = _send(ws, action="set_iep_clock", mhz=300)[-1]
    assert state["iep"]["core_clock"] is True
    assert state["iep"]["external_mhz"] == 300.0
    assert state["iep"]["clock_mhz"] == float(fresh_sim.iep.ocp_clock_hz / 1_000_000)


def test_the_choice_survives_a_core_clock_change_but_not_a_saved_config(fresh_sim, tmp_path, monkeypatch):
    import ui.server as srv
    cfg = tmp_path / "memory.cfg"
    cfg.write_text(Path(srv.config_path).read_text())
    monkeypatch.setattr(srv, "config_path", str(cfg))
    with client.websocket_connect("/ws") as ws:
        _send(ws, action="set_iep_clock", mhz=300)
    assert client.put("/config/clock_speed", json={"mhz": 250}).status_code == 200
    assert srv.sim.iep.active_clock_mhz == 300
    assert client.put("/config", content=cfg.read_text()).status_code == 200
    assert srv.sim.iep.active_clock_mhz == 200


def test_step_back_restore_keeps_the_chosen_clock(fresh_sim):
    import ui.server as srv
    snapshot = srv._snapshot("pru0")           # taken while the clock was still 200 MHz
    with client.websocket_connect("/ws") as ws:
        _send(ws, action="set_iep_clock", mhz=250)
    srv._restore("pru0", snapshot)
    assert fresh_sim.iep.active_clock_mhz == 250


def test_iep_333_mhz_is_exact_and_reported(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        state = _send(ws, action="set_iep_clock", mhz=333)[-1]
    assert state["iep"]["clock_mhz"] == 333.0
    assert fresh_sim.iep.external_clock_hz == 333_000_000
