"""Tests for dashboard REST endpoints."""
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


# ---- run_multicore captures both cores ------------------------------------

# Programs long enough to exceed CAPTURE_STRIDE_GP=10 so the server
# actually produces capture samples.
_NOP_LOOP = """
    .global main
    .sect ".text"
main:
    ldi r1, 101
_loop:
    sub r1, r1, 1
    qbne _loop, r1, 0
    halt
"""


def test_run_multicore_sends_state_for_both_cores(fresh_sim):
    """run_multicore must send a state message for each core."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "load", "core": "pru0", "source": _NOP_LOOP})
        ws.receive_json()  # state
        ws.send_json({"action": "load", "core": "pru1", "source": _NOP_LOOP})
        ws.receive_json()  # state

        ws.send_json({
            "action": "run_multicore",
            "core": "pru0",
            "partner": "pru1",
            "max_steps": 500,
            "capture": False,
        })

        messages = []
        for _ in range(20):
            msg = ws.receive_json()
            messages.append(msg)
            if sum(1 for m in messages if m["type"] == "state") >= 2:
                break

        state_cores = {m["core"] for m in messages if m["type"] == "state"}
        assert "pru0" in state_cores
        assert "pru1" in state_cores


def test_run_multicore_capture_sends_samples_for_lead(fresh_sim):
    """run_multicore with capture=True sends at least one capture with samples for lead."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "load", "core": "pru0", "source": _NOP_LOOP})
        ws.receive_json()
        ws.send_json({"action": "load", "core": "pru1", "source": _NOP_LOOP})
        ws.receive_json()

        ws.send_json({
            "action": "run_multicore",
            "core": "pru0",
            "partner": "pru1",
            "max_steps": 500,
            "capture": True,
        })

        messages = []
        for _ in range(20):
            msg = ws.receive_json()
            messages.append(msg)
            if sum(1 for m in messages if m["type"] == "state") >= 2:
                break

        captures = [m for m in messages if m["type"] == "capture"]
        assert len(captures) > 0, "expected at least one capture message"
        # Every capture message that arrives must have samples
        for m in captures:
            assert len(m.get("samples", [])) > 0, \
                f"capture message for core={m.get('core')} has no samples"
        # The lead core (pru0) must have a capture
        assert any(m["core"] == "pru0" for m in captures)


# ---- GPIO wire cross-core propagation ------------------------------------

def test_gpio_wire_add_and_list(fresh_sim):
    """add_wire over WS creates wire visible in list_gpio_wires."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "add_wire",
                      "src_core": "pru0", "src_pin": 0,
                      "dst_core": "pru1", "dst_pin": 16})
        msg = ws.receive_json()
        assert msg["type"] == "wires"
        wires = msg["wires"]
        assert any(w["src_core"] == "pru0" and w["src_pin"] == 0
                   and w["dst_core"] == "pru1" and w["dst_pin"] == 16
                   for w in wires)


def test_gpio_wire_propagates_gpo_to_gpi(fresh_sim):
    """GPO0 on pru0 propagates to GPI16 on pru1 when wire is added."""
    sim = fresh_sim
    sim.add_gpio_wire("pru0", 0, "pru1", 16)

    sim.cores["pru0"].io_port.write_r30(1 << 0)  # set GPO0 high
    assert sim.cores["pru1"].io_port.get_gpi_pins()[16] == 1

    sim.cores["pru0"].io_port.write_r30(0)        # set GPO0 low
    assert sim.cores["pru1"].io_port.get_gpi_pins()[16] == 0


def test_gpio_wires_propagate_reciprocally_between_cores(fresh_sim):
    """Independent cross-core wires carry both clock and data directions."""
    sim = fresh_sim
    sim.add_gpio_wire("pru0", 0, "pru1", 16)
    sim.add_gpio_wire("pru1", 0, "pru0", 8)

    sim.cores["pru0"].io_port.write_r30(1 << 0)
    sim.cores["pru1"].io_port.write_r30(1 << 0)
    assert sim.cores["pru1"].io_port.get_gpi_pins()[16] == 1
    assert sim.cores["pru0"].io_port.get_gpi_pins()[8] == 1

    sim.cores["pru0"].io_port.write_r30(0)
    sim.cores["pru1"].io_port.write_r30(0)
    assert sim.cores["pru1"].io_port.get_gpi_pins()[16] == 0
    assert sim.cores["pru0"].io_port.get_gpi_pins()[8] == 0


def test_memory_read_echoes_panel_request_and_absolute_region(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "action": "read_memory", "addr": 0x00000000,
            "length": 4, "tag": "mem1", "request_id": 11,
        })
        first = ws.receive_json()
        assert first["tag"] == "mem1"
        assert first["request_id"] == 11
        assert first["addr"] == 0x00000000

        ws.send_json({
            "action": "read_memory", "addr": 0x00002000,
            "length": 4, "tag": "mem1", "request_id": 12,
        })
        second = ws.receive_json()
        assert second["tag"] == "mem1"
        assert second["request_id"] == 12
        assert second["addr"] == 0x00002000

        ws.send_json({
            "action": "read_memory", "addr": 0x00010000,
            "length": 4, "tag": "mem2", "request_id": 13,
        })
        shared = ws.receive_json()
        assert shared["tag"] == "mem2"
        assert shared["request_id"] == 13
        assert shared["addr"] == 0x00010000
