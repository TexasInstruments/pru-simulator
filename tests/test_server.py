"""Tests for dashboard REST endpoints."""
import pytest
from fastapi.testclient import TestClient
from ui.server import app
from ui.trace_log import TraceLogManager
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


def test_repeated_state_reuses_unchanged_source_payload(fresh_sim):
    """Instruction text need not cross the websocket on every state tick."""
    assert fresh_sim.load("pru0", _NOP_LOOP) == []

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "get_state", "core": "pru0"})
        first = ws.receive_json()
        ws.send_json({"action": "step", "core": "pru0", "count": 1})
        second = ws.receive_json()

    assert first["instructions"]
    assert "labels" in first
    assert "instructions" not in second
    assert "labels" not in second


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


def test_step_capture_emits_one_instruction_and_virtual_time(fresh_sim):
    assert fresh_sim.load("pru0", _NOP_LOOP) == []
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "step", "core": "pru0", "count": 1,
                      "capture": True})
        capture = ws.receive_json()
        state = ws.receive_json()

    assert capture["type"] == "capture"
    assert len(capture["samples"]) == len(capture["captured_at_ms"]) == 1
    assert len(capture["samples"][0]) == 7
    assert state["type"] == "state"
    assert state["captured"] is True
    assert state["captured_at_ms"] == capture["captured_at_ms"][-1]


def test_trace_log_websocket_lifecycle_and_download(fresh_sim, monkeypatch, tmp_path):
    import ui.server as srv

    monkeypatch.setattr(srv, "trace_logs", TraceLogManager(tmp_path))
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "trace_log_start"})
        started = ws.receive_json()
        assert started["type"] == "trace_log_state"
        assert started["active"] is True

        ws.send_json({"action": "step", "core": "pru0", "count": 1,
                      "capture": True})
        capture = ws.receive_json()
        assert capture["type"] == "capture"
        assert capture["sequences"] == [0]
        assert ws.receive_json()["type"] == "state"

        ws.send_json({"action": "trace_log_stop"})
        stopped = ws.receive_json()

    assert stopped["active"] is False
    assert stopped["sample_count"] == 1
    assert stopped["download_url"].endswith("/download")
    session_id = stopped["session_id"]
    page = client.get(f"/trace-logs/{session_id}/samples?after=-1&limit=10")
    download = client.get(f"/trace-logs/{session_id}/download")
    assert page.status_code == 200
    assert page.json()["samples"][0]["sequence"] == 0
    assert download.status_code == 200
    assert "text/csv" in download.headers["content-type"]


def test_trace_log_finalizes_before_a_capture_mode_transition(monkeypatch, tmp_path):
    import asyncio
    import ui.server as srv

    manager = TraceLogManager(tmp_path)
    monkeypatch.setattr(srv, "trace_logs", manager)
    monkeypatch.setattr(srv, "_io_mode", lambda _core: "perif")

    class FakeWebSocket:
        _trace_owner = "socket-1"

        def __init__(self):
            self.messages = []

        async def send_json(self, message):
            self.messages.append(message)

    ws = FakeWebSocket()
    state = manager.start(ws._trace_owner)
    batch = {
        "core": "pru0",
        "mode": "perif",
        "samples": [
            [0, 0, 0, 0, 0, 0, 0],
            [1, 1, 0, 0, 0, 0, 1],
        ],
        "captured_at_ms": [0.0, 1.0],
        "_sample_modes": ["gpio", "perif"],
    }

    asyncio.run(srv._publish_capture_batches(ws, [batch]))

    assert ws.messages[0]["type"] == "trace_log_state"
    assert ws.messages[0]["active"] is False
    assert ws.messages[0]["reason"] == "mode_change"
    assert ws.messages[0]["sample_count"] == 1
    assert batch["sequences"] == [0]
    page = manager.page(state["session_id"], after=-1, limit=10)
    assert [row["mode"] for row in page["samples"]] == ["gpio"]


def test_trace_log_page_rejects_ambiguous_cursors(monkeypatch, tmp_path):
    import ui.server as srv

    monkeypatch.setattr(srv, "trace_logs", TraceLogManager(tmp_path))
    response = client.get("/trace-logs/not-real/samples?before=2&after=1")
    assert response.status_code == 400


def test_trace_log_page_rejects_oversized_limit(monkeypatch, tmp_path):
    import ui.server as srv

    monkeypatch.setattr(srv, "trace_logs", TraceLogManager(tmp_path))
    response = client.get(
        "/trace-logs/not-real/samples?after=-1&limit=50001"
    )
    assert response.status_code == 400


def test_trace_log_unknown_session_is_not_a_filesystem_path(monkeypatch, tmp_path):
    import ui.server as srv

    monkeypatch.setattr(srv, "trace_logs", TraceLogManager(tmp_path))
    response = client.get("/trace-logs/../../memory.cfg/download")
    assert response.status_code in {400, 404}
    assert not (tmp_path / "memory.cfg").exists()


def test_trace_log_disconnect_finalizes_downloadable_session(
    fresh_sim, monkeypatch, tmp_path
):
    import ui.server as srv

    manager = TraceLogManager(tmp_path)
    monkeypatch.setattr(srv, "trace_logs", manager)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "trace_log_start"})
        started = ws.receive_json()
        session_id = started["session_id"]

    assert manager.session(session_id).active is False
    response = client.get(f"/trace-logs/{session_id}/download")
    assert response.status_code == 200


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
