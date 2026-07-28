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


def test_run_capture_samples_every_instruction_for_the_graph():
    """The Run button executes up to `max_steps` instructions per websocket
    round-trip. A Signal Graph fed only by the closing state push therefore
    samples once per chunk — hopelessly coarse for a perif bit (2 core cycles
    at the channel-0 N=2 divider), which is why a Run capture of
    `perif_duty_cycle_sweep.asm` looked empty while SIM (one instruction per
    push) traced it fine. With `capture: true` the server must emit a
    per-instruction batch instead, and flag the state push so the client does
    not sample it a second time."""
    from pathlib import Path
    src = Path(__file__).parent.parent / "source"
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        # CH0CFG0 = 0 (continuous mode) is a documented host prerequisite of the
        # sweep firmware — set explicitly, earlier tests may have changed it.
        ws.send_json({"action": "write_perif_register", "core": "pru0",
                      "addr": 0x260E8, "value": 0})
        ws.receive_json()
        ws.send_json({"action": "load", "core": "pru0",
                      "source": (src / "perif_duty_cycle_sweep.asm").read_text()})
        ws.receive_json()

        ws.send_json({"action": "run", "core": "pru0",
                      "max_steps": 400, "capture": True})
        cap = ws.receive_json()
        assert cap["type"] == "capture"
        assert cap["core"] == "pru0"
        # The firmware writes GPCFG itself, so the run ends in Peripheral mode.
        assert cap["mode"] == "perif"

        # One sample per instruction, in order, no gaps — from the instruction
        # that enables peripheral mode onward (the handful of GP-mode setup
        # instructions before the firmware's GPCFG write are decimated away).
        steps = [s[0] for s in cap["samples"]]
        assert steps[0] < 20, "perif mode should be entered early in the setup"
        assert steps == list(range(steps[0], steps[0] + len(steps)))
        assert len(steps) > 100, "run ended too early to trace the sweep"

        # Channel 0's three graph lanes all toggle inside a single batch — the
        # whole point of capturing here rather than once per round-trip.
        out = {s[3] & 1 for s in cap["samples"]}
        oe = {s[4] & 1 for s in cap["samples"]}
        clk = {s[5] & 1 for s in cap["samples"]}
        assert out == {0, 1}, "data lane never toggled within one run chunk"
        assert oe == {0, 1}, "out_en never asserted within one run chunk"
        assert clk == {0, 1}, "clock lane never toggled within one run chunk"

        st = ws.receive_json()
        assert st["type"] == "state"
        assert st["captured"] is True

        # Without capture the batch is absent and the flag is clear, so the
        # client falls back to sampling the state push itself (SIM behavior).
        ws.send_json({"action": "run", "core": "pru0", "max_steps": 10})
        st = ws.receive_json()
        assert st["type"] == "state"
        assert st["captured"] is False


def test_run_capture_decimates_in_gp_mode():
    """GP-mode traces are firmware-paced, so capture keeps the old 100:1 stride
    there. Sampling every instruction would shrink the window's time span 100x
    and break the UART decoder's bit-period detection (Example 5 records a
    115200-baud frame, ~1736 core cycles per bit, over a Run)."""
    from pathlib import Path
    src = Path(__file__).parent.parent / "source"
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 0})
        ws.receive_json()
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "load", "core": "pru0",
                      "source": (src / "uart_tx.asm").read_text()})
        ws.receive_json()

        ws.send_json({"action": "run", "core": "pru0",
                      "max_steps": 1000, "capture": True})
        cap = ws.receive_json()
        assert cap["type"] == "capture"
        assert cap["mode"] == "gpio"
        assert len(cap["samples"]) == 10, "expected 1000 instructions / stride 100"
        assert [s[0] for s in cap["samples"]] == [100 * i for i in range(1, 11)]
        ws.receive_json()   # closing state push

        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 0})
        ws.receive_json()
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()


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


def test_state_carries_tx_clk_pin_for_graph_lanes():
    """The Signal Graph's `perifN_clk` lanes read `tx_clk_pin` off each state
    push (app.js graphSample) — guard that key, and that it actually toggles
    while transmitting so the lane isn't filtered out as inactive."""
    from pathlib import Path
    src = Path(__file__).parent.parent / "source"
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 1})
        state = ws.receive_json()
        ch0 = state["io"]["perif"]["channels"][0]
        assert "tx_clk_pin" in ch0 and "tx_line" in ch0

        ws.send_json({"action": "write_perif_register", "core": "pru0",
                      "addr": 0x260E4, "value": 0x00070010})
        ws.receive_json()
        ws.send_json({"action": "write_perif_register", "core": "pru0",
                      "addr": 0x260E8, "value": 0})      # continuous mode
        ws.receive_json()
        ws.send_json({"action": "load", "core": "pru0",
                      "source": (src / "perif_tx_pattern.asm").read_text()})
        ws.receive_json()

        clk_seen, data_seen = set(), set()
        for _ in range(300):                              # 1 instruction/sample
            ws.send_json({"action": "step", "core": "pru0", "count": 1})
            st = ws.receive_json()
            c = st["io"]["perif"]["channels"][0]
            clk_seen.add(1 if c["tx_clk_pin"] else 0)
            data_seen.add(1 if c["tx_line"] else 0)

        assert clk_seen == {0, 1}, "clock lane never toggled"
        assert data_seen == {0, 1}, "data lane never toggled"

        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 0})
        ws.receive_json()


def test_state_carries_out_en_and_mode_for_perif_graph_lanes():
    """In Peripheral mode the Signal Graph hides the GPO/GPI lanes (the pads
    belong to the perif) and draws out / out_en / tx_clk instead. That filter
    keys off `io.mode`, and the out_en lane reads `tx_out_en` — guard both, and
    that out_en actually asserts while transmitting."""
    from pathlib import Path
    src = Path(__file__).parent.parent / "source"
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 1})
        state = ws.receive_json()
        assert state["io"]["mode"] == "perif"
        assert "tx_out_en" in state["io"]["perif"]["channels"][0]

        ws.send_json({"action": "write_perif_register", "core": "pru0",
                      "addr": 0x260E4, "value": 0x00070010})
        ws.receive_json()
        ws.send_json({"action": "write_perif_register", "core": "pru0",
                      "addr": 0x260E8, "value": 0})      # continuous mode
        ws.receive_json()
        ws.send_json({"action": "load", "core": "pru0",
                      "source": (src / "perif_tx_pattern.asm").read_text()})
        ws.receive_json()

        oe_seen = set()
        for _ in range(300):
            ws.send_json({"action": "step", "core": "pru0", "count": 1})
            st = ws.receive_json()
            assert st["io"]["mode"] == "perif"
            oe_seen.add(1 if st["io"]["perif"]["channels"][0]["tx_out_en"] else 0)

        assert 1 in oe_seen, "out_en never asserted while transmitting"

        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 0})
        ws.receive_json()
