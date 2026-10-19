"""Tests for dashboard REST endpoints."""
import asyncio

import pytest
from fastapi.testclient import TestClient
from ui.server import app
from simulator import Simulator


client = TestClient(app)


def test_get_config_returns_text():
    response = client.get("/config")
    assert response.status_code == 200
    # Default memory.cfg at project root contains DRAM0
    assert "[DRAM0]" in response.text


def test_get_config_content_type_is_text():
    response = client.get("/config")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_put_config_valid_returns_ok(tmp_path, monkeypatch):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    original = client.get("/config").text
    cfg_file.write_text(original)
    monkeypatch.setattr(srv, "config_path", str(cfg_file))

    response = client.put("/config", content=original)
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_put_config_writes_to_disk(tmp_path, monkeypatch):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    # Seed with valid config content
    original = client.get("/config").text
    cfg_file.write_text(original)
    monkeypatch.setattr(srv, "config_path", str(cfg_file))

    new_content = original + "\n; patched\n"
    response = client.put("/config", content=new_content)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "; patched" in cfg_file.read_text()


def test_put_config_unknown_target_returns_400_and_keeps_the_live_simulator(
    tmp_path, monkeypatch
):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    original = client.get("/config").text
    cfg_file.write_text(original)
    before = cfg_file.read_bytes()
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    live = Simulator(config_path=str(cfg_file))
    monkeypatch.setattr(srv, "sim", live)
    typo = original.replace("target = AM243x", "target = am234x", 1)
    assert typo != original

    response = client.put("/config", content=typo)
    assert response.status_code == 400
    assert "am234x" in response.json()["error"]
    assert cfg_file.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["memory.cfg"]
    assert srv.sim is live

    response = client.put("/config", content=original)
    assert response.status_code == 200
    assert srv.sim is not live
    assert cfg_file.read_text() == original
    assert [p.name for p in tmp_path.iterdir()] == ["memory.cfg"]


@pytest.mark.parametrize("replace_clock", [False, True])
def test_config_replacement_clears_rtu1_step_history(
    tmp_path, monkeypatch, replace_clock
):
    import ui.server as srv

    cfg_file = tmp_path / "memory.cfg"
    original = client.get("/config").text
    cfg_file.write_text(original)
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    histories = {core: [] for core in ("pru0", "rtu0", "pru1", "rtu1")}
    histories["rtu1"].append({"stale": True})
    monkeypatch.setattr(srv, "_history", histories)

    if replace_clock:
        response = client.put("/config/clock_speed", json={"mhz": 250})
    else:
        response = client.put("/config", content=original)

    assert response.status_code == 200
    assert all(not history for history in srv._history.values())


def test_put_config_invalid_leaves_file_history_and_simulator_untouched(
    tmp_path, monkeypatch
):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    original = client.get("/config").text
    cfg_file.write_bytes(original.encode("utf-8"))
    before = cfg_file.read_bytes()
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    live = Simulator(config_path=str(cfg_file))
    monkeypatch.setattr(srv, "sim", live)
    monkeypatch.setattr(srv, "_history", {core: [] for core in srv._history})
    srv._history["pru0"].append({"stale": True})

    response = client.put("/config", content=original + "\n[DRAM0\nbase = 0\n")
    assert response.status_code == 400
    assert repr(str(cfg_file))[1:-1] in response.json()["error"]
    assert cfg_file.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["memory.cfg"]
    assert srv.sim is live
    assert srv._history["pru0"] == [{"stale": True}]


# ---- WebSocket helpers -------------------------------------------------


@pytest.fixture
def fresh_sim(monkeypatch):
    import ui.server as srv
    fresh = Simulator(config_path=srv.config_path)
    monkeypatch.setattr(srv, "sim", fresh)
    return fresh


# ---- set_register tests ------------------------------------------------

def test_set_register_updates_value(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()  # consume state
        ws.send_json({"action": "set_register", "core": "pru0", "index": 5, "value": 0xABCD1234})
        state = ws.receive_json()
        assert state["type"] == "state"
        assert state["registers"][5] == "0xABCD1234"


def test_set_register_r30_updates_gpo(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "set_register", "core": "pru0", "index": 30, "value": 0b101})
        state = ws.receive_json()
        assert state["io"]["gpo_pins"][0] == 1
        assert state["io"]["gpo_pins"][1] == 0
        assert state["io"]["gpo_pins"][2] == 1


def test_set_register_r31_updates_gpi(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "set_register", "core": "pru0", "index": 31, "value": 0x00000008})
        state = ws.receive_json()
        assert state["registers"][31] == "0x00000008"
        assert state["io"]["gpi_pins"][3] == 1


# ---- IO sync tests -----------------------------------------------------

def test_r31_display_reflects_set_input(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "set_input", "core": "pru0", "pin": 0, "value": 1})
        state = ws.receive_json()
        assert state["registers"][31] == "0x00000001"


def test_gpo_zero_after_reset(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        # Set R30 to non-zero
        ws.send_json({"action": "set_register", "core": "pru0", "index": 30, "value": 0xF})
        ws.receive_json()
        # Reset — R30 becomes 0, GPO must also become 0
        ws.send_json({"action": "reset", "core": "pru0"})
        state = ws.receive_json()
        assert all(p == 0 for p in state["io"]["gpo_pins"])
        assert state["registers"][30] == "0x00000000"


def test_fault_is_reported_in_state_and_restored_by_step_back(fresh_sim):
    import ui.server as srv

    errors = fresh_sim.load(
        "pru0", "ldi r1, 0x4000\nlbbo &r0, r1, 0, 4\nhalt"
    )
    assert errors == []
    snapshot = srv._snapshot("pru0")
    fresh_sim.step("pru0", count=2)

    class WebSocketSink:
        async def send_json(self, payload):
            self.payload = payload

    sink = WebSocketSink()
    asyncio.run(srv._send_state(sink, "pru0"))
    assert sink.payload["fault"]["opcode"] == "LBBO"
    assert sink.payload["fault"] == fresh_sim.cores["pru0"].fault
    assert sink.payload["fault"] is not fresh_sim.cores["pru0"].fault

    srv._restore("pru0", snapshot)
    assert fresh_sim.cores["pru0"].fault is None


@pytest.mark.parametrize("target,has_rtu1", [
    ("AM243x", True), ("AM64x", True), ("AM263x", False),
])
def test_dashboard_core_choices_follow_the_live_target(tmp_path, monkeypatch, target, has_rtu1):
    import ui.server as srv

    cfg_file = tmp_path / "memory.cfg"
    cfg_file.write_text("[device]\ntarget = AM243x\n")
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    monkeypatch.setattr(srv, "sim", Simulator(str(cfg_file)))

    assert "rtu1" in client.get("/cores").json()
    response = client.put("/config", content=f"[device]\ntarget = {target}\n")

    assert response.status_code == 200
    cores = client.get("/cores").json()
    assert set(cores) == set(srv.sim.cores)
    assert ("rtu1" in cores) == has_rtu1
    if has_rtu1:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "load", "core": "rtu1", "source": "ldi r3, 42\nhalt"})
            assert ws.receive_json()["core"] == "rtu1"
            ws.send_json({"action": "step", "core": "rtu1"})
            assert ws.receive_json()["registers"][3] == "0x0000002A"


def test_step_back_after_peer_progress_reports_error_and_preserves_history(fresh_sim, monkeypatch):
    import ui.server as srv

    monkeypatch.setattr(srv, "_history", {core: [] for core in fresh_sim.cores})
    for core in ("pru0", "pru1"):
        assert fresh_sim.load(core, "nop\njmp 0") == []
    with client.websocket_connect("/ws") as ws:
        for core in ("pru0", "pru1"):
            ws.send_json({"action": "step", "core": core})
            ws.receive_json()
        before = fresh_sim.iep.snapshot()

        ws.send_json({"action": "step_back", "core": "pru0"})

        error = ws.receive_json()
        assert error["type"] == "error"
        assert "peer core has progressed" in error["errors"][0]
        assert ws.receive_json()["cycles"] == 1
        assert len(srv._history["pru0"]) == 1
        assert fresh_sim.iep.snapshot() == before


def test_multicore_run_reports_incomplete_catchup_without_blocking(fresh_sim):
    for core in ("pru0", "pru1"):
        assert fresh_sim.load(core, "nop\njmp 0") == []
    fresh_sim.step("pru0", 10000)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "run_multicore", "core": "pru0",
                      "partner": "pru1", "max_steps": 5})

        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["reason"] == "pacing_incomplete"
        assert ws.receive_json()["cycles"] == 10001
        assert 0 < ws.receive_json()["cycles"] < 10001
        ws.send_json({"action": "get_state", "core": "pru1"})
        assert ws.receive_json()["type"] == "state"


@pytest.mark.parametrize("finish", ["halt", "nop"])
def test_multicore_run_resumes_catchup_after_the_lead_finishes(fresh_sim, finish):
    assert fresh_sim.load(
        "pru0", f"ldi r0, 10000\nloop: sub r0, r0, 1\nqbne loop, r0, 0\n{finish}"
    ) == []
    assert fresh_sim.load("pru1", "add r1, r1, 1\njmp 0") == []
    fresh_sim.step("pru0", 20001)  # leave HALT/the final NOP for the paced run

    with client.websocket_connect("/ws") as ws:
        for expected_cycles in (1000, 2000):
            ws.send_json({"action": "run_multicore", "core": "pru0",
                          "partner": "pru1", "max_steps": 5})
            error = ws.receive_json()
            assert error["type"] == "error"
            assert error["reason"] == "pacing_incomplete"
            lead = ws.receive_json()
            follow = ws.receive_json()
            assert lead["cycles"] == 20002
            assert lead["halted"] == (finish == "halt")
            assert lead["fault"] is None
            assert follow["cycles"] == expected_cycles
            assert follow["registers"][1] == f"0x{expected_cycles // 2:08X}"

        for _ in range(25):
            ws.send_json({"action": "run_multicore", "core": "pru0",
                          "partner": "pru1", "max_steps": 5})
            lead = ws.receive_json()
            incomplete = lead["type"] == "error"
            if incomplete:
                assert lead["reason"] == "pacing_incomplete"
                lead = ws.receive_json()
            follow = ws.receive_json()
            if not incomplete:
                break

        assert not incomplete
        assert lead["cycles"] == follow["cycles"] == 20002
        assert follow["registers"][1] == "0x00002711"  # all 10001 ADDs executed
