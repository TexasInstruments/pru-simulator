"""Tests for the processor selector REST endpoint."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ui.server import app

client = TestClient(app)


@pytest.fixture
def processor_config(tmp_path, monkeypatch):
    """Live server on a temp memory.cfg, so the repo's memory.cfg is never written."""
    import ui.server as srv

    path = tmp_path / "memory.cfg"
    path.write_text(
        "[device]\ntarget = AM243x\npru_clock_mhz = 225\n"
        "\n[DRAM0]\nbase = 0\nsize = 0x2000\n"
    )
    monkeypatch.setattr(srv, "config_path", str(path))
    monkeypatch.setattr(srv, "HOME_CONFIG_PATH", str(path))
    monkeypatch.setattr(srv, "RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(srv, "sim", srv.Simulator(config_path=str(path)))
    monkeypatch.setattr(srv, "_iep_clock_override", None)
    monkeypatch.setattr(srv, "_history", {name: [] for name in srv._history})
    monkeypatch.setattr(srv, "_history_order", [])
    return path


def test_get_processor_lists_the_per_target_configs(processor_config):
    data = client.get("/config/processor").json()
    assert data["processor"] == "AM243x"
    assert set(data["choices"]) == {"AM243x", "AM261x", "AM263x", "AM62x"}


def test_put_processor_switches_target_and_keeps_a_valid_clock(processor_config):
    import ui.server as srv

    live = srv.sim
    response = client.put("/config/processor", json={"processor": "AM261x"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["processor"] == "AM261x"
    assert srv.sim is not live and srv.sim.target == "AM261x"
    assert list(srv.sim.cores) == ["pru0", "pru1"]
    assert srv.sim._pru_clock_mhz == 225
    assert "target = AM261x" not in processor_config.read_text()  # home file untouched
    assert "target = AM261x" in open(srv.config_path).read()
    assert client.get("/config/processor").json()["processor"] == "AM261x"
    assert client.get("/config/clock_speed").json()["mhz"] == 225


def test_put_processor_uses_the_file_clock_when_the_current_one_is_not_a_preset(
        processor_config):
    import ui.server as srv

    processor_config.write_text(
        processor_config.read_text().replace("225", "211.25"))
    srv.sim = srv.Simulator(config_path=str(processor_config))
    assert client.put("/config/processor", json={"processor": "AM263x"}).status_code == 200
    assert srv.sim.target == "AM263x"
    assert srv.sim._pru_clock_mhz == 200  # the shipped file has no clock: the default


def test_put_processor_resets_history_and_websocket_reports_the_new_processor(
        processor_config):
    import ui.server as srv

    srv._record_step("pru0")
    assert srv._history_order
    with client.websocket_connect("/ws") as ws:
        assert client.put("/config/processor", json={"processor": "AM263x"}).status_code == 200
        ws.send_json({"action": "device_discover"})
        while True:
            msg = ws.receive_json()
            if msg.get("type") == "device_profiles":
                break
        assert msg["processor"] == "AM263x"
        ws.send_json({"action": "get_state", "core": "pru0"})
        while True:
            msg = ws.receive_json()
            if msg.get("type") == "state":
                break
        assert msg["processor"] == "AM263x"
        assert msg["available_cores"] == ["pru0", "pru1"]
    assert srv._history_order == []


@pytest.mark.parametrize("body", [
    {"processor": "AM999x"}, {"processor": "am261x"}, {"processor": None},
    {"processor": 5}, {}, {"processor": "AM261x", "extra": 1}, [],
])
def test_put_processor_rejects_unknown_values_without_mutation(processor_config, body):
    import ui.server as srv

    before = processor_config.read_bytes()
    live = srv.sim
    response = client.put("/config/processor", json=body)
    assert response.status_code == 422
    assert "error" in response.json()
    assert processor_config.read_bytes() == before
    assert srv.sim is live


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_home(tmp_path, monkeypatch):
    """Live server on the repo's real memory.cfg, runtime copies in a tmp dir."""
    import ui.server as srv

    home = REPO_ROOT / "memory.cfg"
    monkeypatch.setattr(srv, "config_path", str(home))
    monkeypatch.setattr(srv, "HOME_CONFIG_PATH", str(home))
    monkeypatch.setattr(srv, "RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(srv, "sim", srv.Simulator(config_path=str(home)))
    monkeypatch.setattr(srv, "_iep_clock_override", None)
    monkeypatch.setattr(srv, "_history", {name: [] for name in srv._history})
    monkeypatch.setattr(srv, "_history_order", [])
    return home


def test_round_trip_never_rewrites_memory_cfg(repo_home):
    import ui.server as srv

    before = repo_home.read_bytes()
    home_target = srv.sim.target
    cores = client.get("/cores").json()
    other = next(t for t in client.get("/config/processor").json()["choices"]
                 if t != home_target)
    assert client.put("/config/processor", json={"processor": other}).status_code == 200
    assert srv.sim.target == other
    assert Path(srv.config_path) != repo_home
    assert repo_home.read_bytes() == before
    assert client.put("/config/processor",
                      json={"processor": home_target}).status_code == 200
    assert repo_home.read_bytes() == before
    assert srv.config_path == str(repo_home) and srv.sim.target == home_target
    assert client.get("/cores").json() == cores


def test_clock_change_while_switched_writes_only_the_runtime_copy(repo_home):
    import ui.server as srv

    preset = REPO_ROOT / "config" / "memory_am261x.cfg"
    home_before, preset_before = repo_home.read_bytes(), preset.read_bytes()
    assert client.put("/config/processor", json={"processor": "AM261x"}).status_code == 200
    assert client.put("/config/clock_speed", json={"mhz": 300}).status_code == 200
    assert srv.sim._pru_clock_mhz == 300
    runtime = Path(srv.config_path)
    assert runtime.parent == Path(srv.RUNTIME_DIR)
    assert "pru_clock_mhz = 300" in runtime.read_text()
    assert repo_home.read_bytes() == home_before
    assert preset.read_bytes() == preset_before


def test_get_config_returns_the_active_config_while_switched(repo_home):
    import ui.server as srv

    assert "target = AM261x" not in client.get("/config").text
    assert client.put("/config/processor", json={"processor": "AM261x"}).status_code == 200
    assert "target = AM261x" in client.get("/config").text
    assert client.get("/config").text == Path(srv.config_path).read_text()
    assert srv._device_api_for_current_sim()._config_path == srv.config_path
    assert client.put("/config/processor", json={"processor": "AM243x"}).status_code == 200
    assert "target = AM243x" in client.get("/config").text


def test_put_config_while_switched_writes_the_runtime_copy(repo_home):
    import ui.server as srv

    home_before = repo_home.read_bytes()
    assert client.put("/config/processor", json={"processor": "AM261x"}).status_code == 200
    text = client.get("/config").text
    assert client.put("/config", content=text).status_code == 200
    assert repo_home.read_bytes() == home_before


def test_put_processor_switches_to_am62x(processor_config):
    # AM62x: copied from AM261x, not verified on hardware.
    import ui.server as srv

    assert "AM62x" in client.get("/config/processor").json()["choices"]
    response = client.put("/config/processor", json={"processor": "AM62x"})
    assert response.status_code == 200 and response.json()["processor"] == "AM62x"
    assert srv.sim.target == "AM62x"
    assert list(srv.sim.cores) == ["pru0", "pru1"]
    assert client.get("/config/processor").json()["processor"] == "AM62x"
