"""TCA9538 expander through the dashboard WebSocket (generic device path)."""
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
    srv._clear_history()
    return fresh


def _send(ws, **action):
    """Send one action and drain what the server pushes: errors, then one state."""
    ws.send_json({"core": "pru0", **action})
    messages = []
    while True:
        message = ws.receive_json()
        messages.append(message)
        if message["type"] == "state" and message["core"] == "pru0":
            return messages


def _devices(messages):
    return messages[-1]["io"].get("device_bus", {}).get("devices", [])


def _errors(messages):
    return [m for m in messages if m["type"] == "error"]


def test_device_attach_adds_tca9538_with_drawable_state(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        messages = _send(ws, action="device_attach", profile="tca9538",
                         config={"address": 0x24, "scl_pin": 2, "sda_pin": 3,
                                 "name": "expander"})
        assert _errors(messages) == []
        (device,) = _devices(messages)
        assert device["model"] == "tca9538"
        assert (device["name"], device["address"]) == ("expander", 0x24)
        assert (device["scl_pin"], device["sda_pin"]) == (2, 3)
        assert device["levels"] == [0] * 8 and device["core"] == "pru0"

        messages = _send(ws, action="device_detach", name="expander")
        assert _errors(messages) == []
        assert _devices(messages) == []


def test_device_attach_rejects_bad_tca9538_config(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        messages = _send(ws, action="device_attach", profile="tca9538",
                         config={"address": 0x80})
        assert _errors(messages)[0]["tag"] == "device"
        assert _devices(messages) == []


def test_legacy_i2c_attach_still_works(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        messages = _send(ws, action="i2c_attach", enabled=True, address=0x23)
        assert _errors(messages) == []
        assert messages[-1]["io"]["i2c"]["address"] == 0x23
        messages = _send(ws, action="i2c_attach", enabled=False)
        assert "i2c" not in messages[-1]["io"]


def test_legacy_i2c_refused_next_to_device_on_the_same_pins(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        _send(ws, action="device_attach", profile="tca9538")
        messages = _send(ws, action="i2c_attach", enabled=True)
        (error,) = _errors(messages)
        assert "pins 0 and 1" in error["errors"][0]
        assert fresh_sim.cores["pru0"].io_port.i2c_device is None


def test_tca9538_device_refused_next_to_legacy_i2c_on_the_same_pins(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        _send(ws, action="i2c_attach", enabled=True)
        messages = _send(ws, action="device_attach", profile="tca9538")
        (error,) = _errors(messages)
        assert "legacy I2C" in error["errors"][0]
        assert "attaching a tca9538 device" in error["errors"][0]
        assert _devices(messages) == []
        # A tca9538 on other pins does not collide with the legacy slot.
        messages = _send(ws, action="device_attach", profile="tca9538",
                         config={"scl_pin": 6, "sda_pin": 7})
        assert _errors(messages) == []
        assert len(_devices(messages)) == 1


def test_any_device_driving_legacy_i2c_pins_is_refused(fresh_sim):
    """The legacy slot drives R31 bits 0/1: an SSI encoder answering on bit 1
    would contend with it, whatever the device profile."""
    with client.websocket_connect("/ws") as ws:
        _send(ws, action="i2c_attach", enabled=True)
        messages = _send(ws, action="device_attach", profile="ssi_encoder",
                         config={"clock_pin": 4, "data_pin": 1})
        (error,) = _errors(messages)
        assert "legacy I2C" in error["errors"][0]
        assert "attaching an ssi_encoder device" in error["errors"][0]
        assert _devices(messages) == []
        # Its clock input on bit 0 is not driven by the device: no conflict.
        messages = _send(ws, action="device_attach", profile="ssi_encoder",
                         config={"clock_pin": 0, "data_pin": 8})
        assert _errors(messages) == []
        assert len(_devices(messages)) == 1
