# tests/test_perif_server.py
"""Phase-4: WebSocket wiring for the Peripheral Interface panel."""
from fastapi.testclient import TestClient
from ui.server import app

client = TestClient(app)


def test_gpcfg_switches_mode_and_exposes_perif_state():
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()

        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 1})
        state = ws.receive_json()
        assert state["io"]["mode"] == "perif"
        assert len(state["io"]["perif"]["channels"]) == 3
        assert "loopback" in state["io"]

        # Loopback action -> perif_ok, then a fresh state push.
        ws.send_json({"action": "perif_loopback", "core": "pru0", "channel": 0,
                      "enabled": True, "latency_ns": 5, "drift_ppm": 10})
        ok = ws.receive_json()
        assert ok["type"] == "perif_ok" and ok["enabled"] is True
        state2 = ws.receive_json()
        lb0 = state2["io"]["loopback"]["channels"][0]
        assert lb0["enabled"] is True and lb0["latency_ns"] == 5.0

        # Restore GP mode + loopback so shared server state doesn't leak.
        ws.send_json({"action": "perif_loopback", "core": "pru0", "channel": 0,
                      "enabled": False})
        ws.receive_json(); ws.receive_json()
        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 0})
        st = ws.receive_json()
        assert st["io"]["mode"] == "gpio"


def test_gpcfg_switches_mode_to_sd_without_sd_en():
    """Setting the GPCFG mux to SD (3) switches the IO window to SD view
    immediately, same as Perif mode does for mux_sel=1 - it should not
    require firmware to separately assert sd_en (R30 bit 25) first."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()

        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 3})
        state = ws.receive_json()
        assert state["io"]["mode"] == "sd"
        assert state["io"]["mux_sel"] == 3
        assert state["io"]["sd"]["sd_en"] is False

        # Restore GP mode so shared server state doesn't leak.
        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 0})
        st = ws.receive_json()
        assert st["io"]["mode"] == "gpio"


def test_pru1_state_has_perif_rtu0_does_not():
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "get_state", "core": "pru1"})
        st = ws.receive_json()
        assert st["core"] == "pru1"
        assert "perif" in st["io"]
        ws.send_json({"action": "get_state", "core": "rtu0"})
        st = ws.receive_json()
        assert "perif" not in st["io"]


def test_write_perif_register_via_ws():
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 1})
        ws.receive_json()
        # CH0CFG0 @ 0x260E8: tx_frame_size = 8 (bits [15:11])
        ws.send_json({"action": "write_perif_register", "core": "pru0",
                      "addr": 0x260E8, "value": 8 << 11})
        state = ws.receive_json()
        cfg = state["io"]["perif"]["channels"][0]["config"]
        assert cfg["tx_frame_size"] == 8
        # TXCFG @ 0x260E4 raw value must round-trip into the shared payload
        ws.send_json({"action": "write_perif_register", "core": "pru0",
                      "addr": 0x260E4, "value": 0x00070010})
        state = ws.receive_json()
        sh = state["io"]["perif"]["shared"]
        assert sh["txcfg"] == 0x00070010
        assert sh["base_addr"] == 0x260E0
        # Restore TXCFG so shared server state doesn't leak.
        ws.send_json({"action": "write_perif_register", "core": "pru0",
                      "addr": 0x260E4, "value": 0})
        ws.receive_json()
        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 0})
        ws.receive_json()
