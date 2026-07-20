"""Tests for the PRU core clock speed REST endpoint."""
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
