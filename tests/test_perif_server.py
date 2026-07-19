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


def _receive_states(ws, cores):
    """Receive one state message per core; return {core: state}."""
    out = {}
    for _ in cores:
        st = ws.receive_json()
        out[st["core"]] = st
    assert set(out) == set(cores)
    return out


def test_run_multicore_paces_perif_demo():
    """Regression: the drift demo must capture the clean counter pattern
    when driven through the multicore run action."""
    from pathlib import Path
    src = Path(__file__).parent.parent / "source"
    with client.websocket_connect("/ws") as ws:
        for core in ("pru0", "pru1"):
            ws.send_json({"action": "reset", "core": core})
            ws.receive_json()
            ws.send_json({"action": "gpcfg_write", "core": core, "mux_sel": 1})
            ws.receive_json()
        ws.send_json({"action": "write_perif_register", "core": "pru0",
                      "addr": 0x260E4, "value": 0x00070010})
        ws.receive_json()
        ws.send_json({"action": "write_perif_register", "core": "pru1",
                      "addr": 0x26100, "value": 0x0007001F})
        ws.receive_json()
        # CH0CFG0 = 0 (continuous mode) is a documented host prerequisite of
        # the TX firmware — set explicitly, earlier tests may have changed it.
        ws.send_json({"action": "write_perif_register", "core": "pru0",
                      "addr": 0x260E8, "value": 0})
        ws.receive_json()
        ws.send_json({"action": "perif_loopback", "core": "pru0",
                      "channel": 0, "enabled": True})
        ws.receive_json(); ws.receive_json()
        ws.send_json({"action": "load", "core": "pru0",
                      "source": (src / "perif_tx_pattern.asm").read_text()})
        ws.receive_json()
        ws.send_json({"action": "load", "core": "pru1",
                      "source": (src / "perif_rx_capture.asm").read_text()})
        ws.receive_json()

        for _ in range(60):
            ws.send_json({"action": "run_multicore", "core": "pru0",
                          "partner": "pru1", "max_steps": 1000})
            _receive_states(ws, ("pru0", "pru1"))

        ws.send_json({"action": "read_memory", "addr": 0x2000, "length": 32,
                      "tag": "mem1"})
        mem = ws.receive_json()
        assert mem["data"] == [i & 0xFF for i in range(32)]

        # Restore shared server state.
        ws.send_json({"action": "perif_loopback", "core": "pru0",
                      "channel": 0, "enabled": False})
        ws.receive_json(); ws.receive_json()
        for core in ("pru0", "pru1"):
            ws.send_json({"action": "write_perif_register", "core": core,
                          "addr": 0x260E4 if core == "pru0" else 0x26100,
                          "value": 0})
            ws.receive_json()
            ws.send_json({"action": "gpcfg_write", "core": core, "mux_sel": 0})
            ws.receive_json()
            ws.send_json({"action": "reset", "core": core})
            ws.receive_json()


def test_io_window_mode_follows_firmware_gpcfg_write():
    """Firmware writing GPCFG must flip io.mode in the next state push."""
    prog = (
        "start:\n"
        "        ldi  r0, 0x0000\n"
        "        ldi  r0.w2, 0x0400\n"
        "        ldi  r1, 0x6008\n"
        "        ldi  r1.w2, 0x0002\n"
        "        sbbo r0, r1, 0, 4\n"
        "        halt\n"
    )
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "load", "core": "pru0", "source": prog})
        st = ws.receive_json()
        assert st["io"]["mode"] == "gpio"
        ws.send_json({"action": "run", "core": "pru0", "max_steps": 20})
        st = ws.receive_json()
        assert st["io"]["mode"] == "perif"
        assert st["io"]["mux_sel"] == 1
        # Restore GP mode + reset so shared server state doesn't leak.
        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 0})
        ws.receive_json()
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()


def test_run_multicore_stops_at_lead_breakpoint():
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "reset", "core": "pru1"})
        ws.receive_json()
        prog = "start:\n        add r2, r2, 1\n        jmp start\n"
        for core in ("pru0", "pru1"):
            ws.send_json({"action": "load", "core": core, "source": prog})
            ws.receive_json()
        ws.send_json({"action": "toggle_breakpoint", "core": "pru0", "addr": 1})
        ws.receive_json()
        ws.send_json({"action": "run_multicore", "core": "pru0",
                      "partner": "pru1", "max_steps": 1000})
        states = _receive_states(ws, ("pru0", "pru1"))
        assert states["pru0"]["at_breakpoint"] is True
        assert states["pru0"]["pc"] == 1
        # Cleanup: clear breakpoint (toggle again) + reset both cores.
        ws.send_json({"action": "toggle_breakpoint", "core": "pru0", "addr": 1})
        ws.receive_json()
        for core in ("pru0", "pru1"):
            ws.send_json({"action": "reset", "core": core})
            ws.receive_json()
