"""Tests for the PRU core clock speed REST endpoint."""
import configparser
import json

import pytest
from fastapi.testclient import TestClient
from ui.server import app

client = TestClient(app)


def test_get_clock_speed_returns_current_value(tmp_path, monkeypatch):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    cfg_file.write_text(
        "[device]\n"
        "target = AM243x\n"
        "pru_clock_mhz = 200\n"
        "\n"
        "[DRAM0]\n"
        "base = 0x00000000\n"
        "size = 0x2000\n"
    )
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    monkeypatch.setattr(srv, "sim", srv.Simulator(config_path=str(cfg_file)))

    response = client.get("/config/clock_speed")
    assert response.status_code == 200
    assert response.json()["mhz"] == 200


def test_put_clock_speed_valid_updates_both_keys(tmp_path, monkeypatch):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    cfg_file.write_text(
        "[device]\n"
        "target = AM243x\n"
        "pru_clock_mhz = 200\n"
        "pru1_clock_mhz = 200.1\n"
        "\n"
        "[DRAM0]\n"
        "base = 0x00000000\n"
        "size = 0x2000\n"
    )
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    monkeypatch.setattr(srv, "sim", srv.Simulator(config_path=str(cfg_file)))

    response = client.put("/config/clock_speed", json={"mhz": 250})
    assert response.status_code == 200
    assert response.json()["ok"] is True

    text = cfg_file.read_text()
    assert "pru_clock_mhz = 250" in text
    assert "pru1_clock_mhz = 250" in text
    assert "200.1" not in text


def test_put_clock_speed_inserts_missing_pru1_key(tmp_path, monkeypatch):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    cfg_file.write_text(
        "[device]\n"
        "target = AM243x\n"
        "pru_clock_mhz = 200\n"
        "\n"
        "[DRAM0]\n"
        "base = 0x00000000\n"
        "size = 0x2000\n"
    )
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    monkeypatch.setattr(srv, "sim", srv.Simulator(config_path=str(cfg_file)))

    response = client.put("/config/clock_speed", json={"mhz": 300})
    assert response.status_code == 200

    text = cfg_file.read_text()
    assert "pru_clock_mhz = 300" in text
    assert "pru1_clock_mhz = 300" in text
    # Key was inserted inside [device], not appended after [DRAM0]
    device_block = text.split("[DRAM0]")[0]
    assert "pru1_clock_mhz = 300" in device_block
    # Blank line separating [device] from [DRAM0] must be preserved
    assert "pru1_clock_mhz = 300\n\n[DRAM0]" in text


def test_put_clock_speed_rejects_disallowed_value(tmp_path, monkeypatch):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    original = (
        "[device]\n"
        "target = AM243x\n"
        "pru_clock_mhz = 200\n"
        "\n"
        "[DRAM0]\n"
        "base = 0x00000000\n"
        "size = 0x2000\n"
    )
    cfg_file.write_text(original)
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    monkeypatch.setattr(srv, "sim", srv.Simulator(config_path=str(cfg_file)))

    response = client.put("/config/clock_speed", json={"mhz": 275})
    assert response.status_code == 400
    assert "error" in response.json()
    assert cfg_file.read_text() == original


@pytest.mark.parametrize("group", [None, 0, 1])
def test_put_clock_speed_invalid_config_leaves_file_and_simulator_untouched(
    tmp_path, monkeypatch, group
):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    valid = (
        "[device]\n"
        "target = AM243x\n"
        "pru_clock_mhz = 200\n"
        "\n"
        "[DRAM0]\n"
        "base = 0x00000000\n"
        "size = 0x2000\n"
    )
    cfg_file.write_text(valid)
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    live = srv.Simulator(config_path=str(cfg_file))
    monkeypatch.setattr(srv, "sim", live)
    monkeypatch.setattr(srv, "_history", {core: [] for core in srv._history})
    srv._history["pru0"].append({"stale": True})
    # The clock edit itself succeeds on this text, but the Simulator cannot load it.
    cfg_file.write_bytes((valid + "\n[DRAM1\nbase = 0\n").encode("utf-8"))
    before = cfg_file.read_bytes()

    body = {"mhz": 250}
    if group is not None:
        body["group"] = group
    response = client.put("/config/clock_speed", json=body)
    assert response.status_code == 400
    assert repr(str(cfg_file))[1:-1] in response.json()["error"]
    assert cfg_file.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["memory.cfg"]
    assert srv.sim is live
    assert srv._history["pru0"] == [{"stale": True}]


@pytest.fixture
def clock_config(tmp_path, monkeypatch):
    import ui.server as srv

    def make(device):
        path = tmp_path / "memory.cfg"
        path.write_text(
            "[device]\n" + device + "\n[DRAM0]\nbase = 0\nsize = 0x2000\n"
        )
        monkeypatch.setattr(srv, "config_path", str(path))
        monkeypatch.setattr(srv, "sim", srv.Simulator(config_path=str(path)))
        monkeypatch.setattr(srv, "_iep_clock_override", None)
        monkeypatch.setattr(srv, "_history", {name: [] for name in srv.sim.cores})
        monkeypatch.setattr(srv, "_history_order", [])
        return path

    return make


@pytest.mark.parametrize("device,rates,members", [
    ("target = AM243x\n", [200, 200], ["pru1", "rtu1"]),
    ("target = AM243x\npru_clock_mhz = 225\n", [225, 225], ["pru1", "rtu1"]),
    ("target = AM263x\npru_clock_mhz = 211.25\npru1_clock_mhz = 299.5\n",
     [211.25, 299.5], ["pru1"]),
])
def test_clock_readout_reports_actual_groups_and_presets(clock_config, device, rates, members):
    path = clock_config(device)
    original = path.read_bytes()
    expected = {
        "mhz": rates[0],
        "groups": [
            {"id": 0, "mhz": rates[0], "cores": ["pru0", "rtu0"]},
            {"id": 1, "mhz": rates[1], "cores": members},
        ],
        "choices_mhz": [200, 225, 250, 300, 333],
    }
    assert client.get("/config/clock_speed").json() == expected
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "get_state", "core": "pru0"})
        assert ws.receive_json()["clocks"] == expected
    assert path.read_bytes() == original


@pytest.mark.parametrize("group,rates", [(0, [300, 211.25]), (1, [225, 300]),
                                         (1.0, [225, 300])])
def test_group_edit_changes_only_members_of_selected_group(clock_config, group, rates):
    import ui.server as srv

    path = clock_config(
        "target = AM243x\npru_clock_mhz = 225\npru1_clock_mhz = 211.25\n"
        "iep_clock_mhz = 217.5\n"
    )
    before = path.read_text()
    srv._set_iep_clock(333.333)
    assert client.put("/config/clock_speed", json={"group": group, "mhz": 300}).status_code == 200
    untouched_key = "pru1_clock_mhz = 211.25" if group == 0 else "pru_clock_mhz = 225"
    changed_key = "pru_clock_mhz" if group == 0 else "pru1_clock_mhz"
    assert path.read_text() == before.replace(
        f"{changed_key} = {225 if group == 0 else 211.25}", f"{changed_key} = 300"
    )
    assert untouched_key in path.read_text()
    for name, rate in zip(["pru0", "rtu0", "pru1", "rtu1"], [rates[0]] * 2 + [rates[1]] * 2):
        assert srv.sim.iep.core_clock_hz(name) == rate * 1_000_000
        assert srv.sim.cores[name].io_port.sd_filter.pru_clock_mhz == rate
    assert srv.sim.iep.external_clock_hz == 333_333_000
    assert srv._configured_iep_mhz() == 217.5
    assert srv._iep_clock_override == 333.333
    assert [g["mhz"] for g in client.get("/config/clock_speed").json()["groups"]] == rates


@pytest.mark.parametrize("device,old_rate", [
    ("target = AM243x\npru_clock_mhz = 211.25\n", 211.25),
    ("target = AM243x\n", 200),
])
def test_group_zero_edit_preserves_inherited_group_one(clock_config, device, old_rate):
    import ui.server as srv

    path = clock_config(device)
    assert client.put("/config/clock_speed", json={"group": 0, "mhz": 300}).status_code == 200
    cfg = configparser.ConfigParser()
    cfg.read(path)
    assert cfg.getfloat("device", "pru_clock_mhz") == 300
    assert cfg.getfloat("device", "pru1_clock_mhz") == old_rate
    assert srv.sim.iep.core_clock_hz("pru1") == old_rate * 1_000_000
    assert srv.sim.iep.core_clock_hz("rtu1") == old_rate * 1_000_000


def test_group_one_edit_does_not_materialize_default_group_zero(clock_config):
    path = clock_config("target = AM263x\n")
    assert client.put("/config/clock_speed", json={"group": 1, "mhz": 333}).status_code == 200
    cfg = configparser.ConfigParser()
    cfg.read(path)
    assert "pru_clock_mhz" not in cfg["device"]
    assert client.get("/config/clock_speed").json()["groups"] == [
        {"id": 0, "mhz": 200, "cores": ["pru0", "rtu0"]},
        {"id": 1, "mhz": 333, "cores": ["pru1"]},
    ]


def test_group_edit_reloads_programs_and_clears_step_history(clock_config):
    import ui.server as srv

    clock_config("target = AM243x\npru_clock_mhz = 200\npru1_clock_mhz = 225\n")
    live = srv.sim
    for name in live.cores:
        assert live.load(name, "LDI r0, 42\nHALT") == []
    live.iep.write_iepclk(1)
    srv._record_step("pru0")
    live.cores["pru0"].step()
    assert client.put("/config/clock_speed", json={"group": 1, "mhz": 300}).status_code == 200
    assert srv.sim is not live
    for core in srv.sim.cores.values():
        assert core.instructions == []
        assert core.pc == 0
        assert core.registers.read_full(0) == 0
    assert all(not history for history in srv._history.values())
    assert srv._history_order == []
    # Like saved config reloads, the rebuild resets firmware's IEP source choice.
    assert srv.sim.iep.iepclk == 0
    assert srv.sim.iep.active_clock_mhz == 200


def test_unchanged_group_zero_does_not_materialize_inherited_group_one(clock_config):
    path = clock_config("target = AM243x\npru_clock_mhz = 225\n")
    before = path.read_bytes()
    assert client.put("/config/clock_speed", json={"group": 0, "mhz": 225}).status_code == 200
    assert path.read_bytes() == before


def test_failed_atomic_replace_preserves_config_simulator_and_history(clock_config, monkeypatch):
    import ui.server as srv

    path = clock_config("target = AM243x\npru_clock_mhz = 225\npru1_clock_mhz = 250\n")
    before = path.read_bytes()
    live = srv.sim
    srv._record_step("pru0")
    history = list(srv._history["pru0"])
    order = list(srv._history_order)

    def fail_replace(source, destination):
        raise OSError("replacement denied")

    monkeypatch.setattr(srv.os, "replace", fail_replace)
    response = client.put("/config/clock_speed", json={"group": 1, "mhz": 300})
    assert response.status_code == 400
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]
    assert srv.sim is live
    assert srv._history["pru0"] == history
    assert srv._history_order == order


@pytest.mark.parametrize("body", [
    {"group": True, "mhz": 300}, {"group": False, "mhz": 300},
    {"group": "0", "mhz": 300}, {"group": None, "mhz": 300},
    {"group": -1, "mhz": 300}, {"group": 2, "mhz": 300},
    {"group": 0.5, "mhz": 300}, {"group": [], "mhz": 300},
    {"group": {}, "mhz": 300}, {"mhz": True}, {"mhz": "300"},
    {"mhz": []}, {"mhz": {}}, {"mhz": None}, {}, [], "300", None,
])
def test_invalid_clock_edits_are_non_mutating(clock_config, body):
    import ui.server as srv

    path = clock_config("target = AM243x\npru_clock_mhz = 225\npru1_clock_mhz = 250\n")
    before = path.read_bytes()
    live = srv.sim
    # Explicit JSON null is distinct from TestClient's json=None (an empty body).
    response = client.put("/config/clock_speed", content=json.dumps(body),
                          headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert "error" in response.json()
    assert path.read_bytes() == before
    assert srv.sim is live


def test_malformed_json_clock_edit_is_non_mutating(clock_config):
    import ui.server as srv

    path = clock_config("target = AM243x\n")
    before = path.read_bytes()
    live = srv.sim
    response = client.put("/config/clock_speed", content="{")
    assert response.status_code == 400
    assert path.read_bytes() == before
    assert srv.sim is live
