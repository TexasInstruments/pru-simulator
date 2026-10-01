"""Tests for the browser-facing generic SSI runtime contract."""

from pathlib import Path
import traceback

import pytest
from fastapi.testclient import TestClient

from ui import server as srv
from simulator import Simulator
from pru_io import ssi_config_abi as abi
from pru_io.ssi_runtime import SSIRuntime


STATIC_DIR = Path(__file__).parents[1] / "ui" / "static"
client = TestClient(srv.app)


def test_normal_websocket_disconnect_is_not_reported_as_server_error(monkeypatch):
    reported = []
    monkeypatch.setattr(traceback, "print_exc", lambda: reported.append(True))

    with client.websocket_connect("/ws"):
        pass

    assert reported == []


@pytest.fixture
def fresh_sim(monkeypatch):
    fresh = Simulator(config_path=srv.config_path)
    monkeypatch.setattr(srv, "sim", fresh)
    monkeypatch.setattr(srv, "_ssi_runtime", None)
    return fresh


def _load_runtime_pair(ws):
    """Load the pair and consume the two source-state refresh messages."""
    ws.send_json({"action": "ssi_runtime_load"})
    loaded = ws.receive_json()
    assert loaded["type"] == "ssi_runtime_state"
    states = [ws.receive_json(), ws.receive_json()]
    assert {state["type"] for state in states} == {"state"}
    assert {state["core"] for state in states} == {"pru0", "pru1"}
    assert all(state["instructions"] for state in states)
    return loaded


def test_generic_load_publishes_source_state_for_both_cores(fresh_sim, monkeypatch):
    published = []

    async def record_state(_ws, core, at_breakpoint=False, captured=False):
        published.append(core)

    monkeypatch.setattr(srv, "_send_state", record_state)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "ssi_runtime_load"})
        loaded = ws.receive_json()

    assert loaded["loaded"] is True
    assert published == ["pru0", "pru1"]


def test_generic_ssi_state_exposes_address_labeled_mailbox_debug_data(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        loaded = _load_runtime_pair(ws)

    fields = loaded["mailbox_layout"]["fields"]
    assert fields["sequence"] == abi.MAILBOX_BASE + abi.MAILBOX_SEQ_OFF
    assert fields["raw_frame"] == abi.MAILBOX_BASE + abi.MAILBOX_RAW_FRAME_OFF
    assert fields["raw_position"] == abi.MAILBOX_BASE + abi.MAILBOX_POSITION_VALUE_OFF
    assert fields["position"] == fields["raw_position"]
    assert fields["timestamp"] == abi.MAILBOX_BASE + abi.MAILBOX_TIMESTAMP_CYCLES_OFF
    assert len(loaded["mailbox_display"]["raw_frame"]) == 18
    assert len(loaded["mailbox_display"]["timestamp"]) == 18

    display = srv._ssi_mailbox_display({
        "seq": 0x19E,
        "raw_frame": 0x123456789ABCDEF0,
        "raw_position_value": 0xCC2,
        "position_value": 0xCC2,
        "status_bits": 0,
        "frame_counter": 205,
        "timestamp_cycles": 0x0000000000F53D4,
    })
    assert display["raw_frame"] == "0x123456789ABCDEF0"
    assert display["timestamp"] == "0x00000000000F53D4"


def test_generic_ssi_mailbox_panel_uses_named_addresses_and_seqlock_labels():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "Shared RAM mailbox (seqlock snapshot)" in js
    for label in (
        "sequence",
        "raw frame",
        "raw position",
        "position",
        "status",
        "frame counter",
        "timestamp",
        "Trace counters",
    ):
        assert label in js, label
    assert "mailbox_layout" in js
    assert "trace_layout" in js
    assert "Seqlock" in html or "seqlock" in html


def test_generic_ssi_runtime_panel_exposes_load_configure_and_observe_controls():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    for element_id in (
        "ssi-runtime",
        "ssi-runtime-load",
        "ssi-runtime-profile",
        "ssi-runtime-topology",
        "ssi-runtime-encoding",
        "ssi-runtime-alignment",
        "ssi-runtime-frame-bits",
        "ssi-runtime-position-offset",
        "ssi-runtime-clock-high",
        "ssi-runtime-clock-low",
        "ssi-runtime-sample-delay",
        "ssi-runtime-tv",
        "ssi-runtime-tm",
        "ssi-runtime-tp",
        "ssi-runtime-formation",
        "ssi-runtime-formation-pause",
        "ssi-runtime-hold-mode",
        "ssi-runtime-position-count",
        "ssi-runtime-gray-excess-offset",
        "ssi-runtime-fault-argument",
        "ssi-runtime-fault-repeat",
        "ssi-runtime-frames",
        "ssi-runtime-positions",
        "ssi-runtime-stage",
        "ssi-runtime-apply",
        "ssi-runtime-run",
        "ssi-runtime-refresh",
        "ssi-runtime-status",
        "ssi-runtime-wires",
        "ssi-runtime-mailbox",
        "ssi-runtime-trace",
        "ssi-runtime-producer-mode",
        "ssi-runtime-producer-period",
        "ssi-runtime-producer-trajectory",
        "ssi-runtime-producer-initial",
        "ssi-runtime-producer-velocity",
        "ssi-runtime-producer-start",
        "ssi-runtime-producer-stop",
        "ssi-runtime-producer-step",
        "ssi-runtime-producer-diagnostics",
    ):
        assert f'id="{element_id}"' in html, element_id

    for action in (
        "ssi_runtime_load",
        "ssi_runtime_stage",
        "ssi_runtime_positions",
        "ssi_runtime_apply",
        "ssi_runtime_read",
        "ssi_runtime_producer_configure",
        "ssi_runtime_producer_start",
        "ssi_runtime_producer_stop",
        "ssi_runtime_producer_step",
    ):
        assert action in js, action

    assert 'action: "ssi_runtime_apply"' in js
    assert "frames: frameTokens()" in js
    assert "request_id" in js
    assert "selectGenericSsiPartner" in js
    assert "PRU1:GPO0" in html
    assert "PRU1 DRAM (global 0x00002000)" in html
    assert "capture_group" in js
    assert "memReadInFlight" in js
    assert "abandonedRunRequestIds" in js


def test_dashboard_timestamped_producer_controls_and_diagnostics(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        _load_runtime_pair(ws)
        ws.send_json({
            "action": "ssi_runtime_apply",
            "profile": "CUSTOM_LEGACY_12BIT_4MHZ",
            "overrides": {
                "producer_mode": abi.SSI_PRODUCER_MODE_TIMESTAMPED,
                "producer_sample_age_limit_iep_ticks": 100_000,
                "producer_prediction_horizon_limit_iep_ticks": 100_000,
            },
        })
        applied = ws.receive_json()
        assert applied["active"]["producer_mode"] == 1

        ws.send_json({
            "action": "ssi_runtime_producer_configure",
            "trajectory": "constant",
            "initial_position": "0x345",
            "velocity_counts_per_second": 0,
            "period_iep_ticks": 288,
        })
        configured = ws.receive_json()
        assert configured["producer"]["trajectory"] == "constant"

        ws.send_json({"action": "ssi_runtime_producer_start"})
        started = ws.receive_json()
        assert started["producer"]["running"] is True

        for _ in range(400):
            fresh_sim.step_paced("pru1", "pru0")

        ws.send_json({"action": "ssi_runtime_read"})
        state = ws.receive_json()
        assert state["producer"]["published_count"] > 1
        assert "last_request_timestamp_iep" in state["producer_diagnostics"]

        ws.send_json({"action": "ssi_runtime_producer_stop"})
        stopped = ws.receive_json()
        assert stopped["producer"]["running"] is False


def test_generic_ssi_panel_identifies_the_live_shared_memory_region():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'value="0x00010200">SSI mailbox (global 0x00010200)</option>' in html
    assert 'id="ssi-runtime-memory-hint"' in html
    assert "PRU0/PRU1 DRAM are not written by this pair" in html
    assert '/static/app.js?v=20260914-5' in html


def test_profile_catalog_is_safe_for_ui_and_contains_default_and_documented_profiles():
    profiles = srv._ssi_profile_catalog()
    names = {profile["name"] for profile in profiles}

    assert "CUSTOM_LEGACY_12BIT_4MHZ" in names
    assert "AHS_AHM36_SINGLETURN" in names
    assert "ATM60_90" in names

    default = next(p for p in profiles if p["name"] == "CUSTOM_LEGACY_12BIT_4MHZ")
    assert default["frame_width_bits"] == 12
    assert default["clock_hz"] == 4_000_000
    assert set(default) >= {
        "name",
        "frame_width_bits",
        "position_width_bits",
        "clock_high_cycles",
        "clock_low_cycles",
        "sample_delay_cycles",
        "tv_cycles",
        "tm_pause_outer_iters",
        "tp_pause_outer_iters",
        "max_clock_hz",
    }


def test_ui_frame_parser_accepts_hex_values_and_rejects_invalid_values():
    assert srv._parse_ssi_frame_values(["ABC", "0x12A", "cc2"]) == [0xABC, 0x12A, 0xCC2]

    with pytest.raises(ValueError, match="at least one"):
        srv._parse_ssi_frame_values([])

    with pytest.raises(ValueError, match="invalid SSI frame value"):
        srv._parse_ssi_frame_values(["not-hex"])


def test_runtime_frame_writer_populates_slots_and_clears_the_rest():
    class Memory:
        def __init__(self):
            self.data = bytearray(abi.TRACE_BASE + abi.TRACE_RECORD_SIZE * 16)

        def write(self, address, data):
            self.data[address:address + len(data)] = data

        def read(self, address, length):
            return bytes(self.data[address:address + length])

    class FakeSim:
        def __init__(self):
            self.memory = Memory()

        def memory_read(self, address, length):
            return self.memory.read(address, length)

    runtime = SSIRuntime.__new__(SSIRuntime)
    runtime.sim = FakeSim()
    runtime._staged = {"frame_width_bits": 12}

    runtime.set_raw_frames([0xABC, 0xAAA, 0xBCA, 0x12A, 0xCC2])

    first = int.from_bytes(
        runtime.sim.memory.read(abi.FRAMES_BASE, 8), "little"
    )
    sixth = int.from_bytes(
        runtime.sim.memory.read(abi.FRAMES_BASE + 5 * abi.FRAME_SLOT_SIZE, 8),
        "little",
    )
    assert first == 0xABC
    assert sixth == abi.FRAME_SLOT_UNUSED_SENTINEL
    assert runtime.read_raw_frames() == [0xABC, 0xAAA, 0xBCA, 0x12A, 0xCC2]


def test_dashboard_websocket_runs_generic_ssi_pair_and_applies_raw_frames(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        loaded = _load_runtime_pair(ws)
        assert loaded["loaded"] is True
        assert loaded["selected_profile"] == "CUSTOM_LEGACY_12BIT_4MHZ"
        assert loaded["effective_clock_hz"] == loaded["active"]["effective_clock_hz"]
        assert {"src_core": "pru1", "src_pin": 0, "dst_core": "pru0", "dst_pin": 8} in loaded["wires"]
        assert {"src_core": "pru0", "src_pin": 0, "dst_core": "pru1", "dst_pin": 16} in loaded["wires"]

        ws.send_json({
            "action": "ssi_runtime_stage",
            "profile": "KH53",
        })
        staged_profile = ws.receive_json()
        assert staged_profile["selected_profile"] == "CUSTOM_LEGACY_12BIT_4MHZ"
        assert staged_profile["staged_profile"] == "KH53"

        ws.send_json({
            "action": "ssi_runtime_stage",
            "profile": "CUSTOM_LEGACY_12BIT_4MHZ",
            "overrides": {"capture_mode": 1, "sequence_hold_count": 2},
        })
        staged = ws.receive_json()
        assert staged["type"] == "ssi_runtime_state"
        assert staged["staged"]["sequence_hold_count"] == 2

        ws.send_json({
            "action": "ssi_runtime_frames",
            "frames": ["ABC", "AAA", "BCA", "12A", "CC2"],
        })
        frames = ws.receive_json()
        assert frames["frames"] == [0xABC, 0xAAA, 0xBCA, 0x12A, 0xCC2]

        ws.send_json({
            "action": "ssi_runtime_positions",
            "positions": ["ABC", "12A"],
        })
        positions = ws.receive_json()
        assert positions["type"] == "ssi_runtime_state"
        assert positions["frames"] == [0xABC, 0x12A]

        ws.send_json({"action": "ssi_runtime_apply"})
        applied = ws.receive_json()
        assert applied["active"]["frame_width_bits"] == 12
        assert applied["active"]["capture_mode"] == 1
        assert applied["active"]["requested_generation"] == applied["active"]["pru1_ack_generation"]

        ws.send_json({"action": "ssi_runtime_read"})
        readback = ws.receive_json()
        assert readback["mailbox"]["frame_counter"] >= 0
        assert readback["trace"]["write_index"] >= 0


def test_profile_dropdown_prefers_staged_profile_after_stage():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "const profileName = msg.staged_profile || msg.selected_profile;" in js


def test_dashboard_generic_pair_run_completes_with_reader_as_lead(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        loaded = _load_runtime_pair(ws)
        assert loaded["loaded"] is True

        ws.send_json({
            "action": "run_multicore",
            "core": "pru1",
            "partner": "pru0",
            "max_steps": 20000,
            "capture": False,
            "request_id": "ssi-runtime-general-1",
        })

        messages = []
        while True:
            message = ws.receive_json()
            messages.append(message)
            if message.get("type") == "run_done":
                break

    assert {message["core"] for message in messages if message["type"] == "state"} == {
        "pru0", "pru1",
    }
    assert messages[-1] == {
        "type": "run_done",
        "request_id": "ssi-runtime-general-1",
    }


def test_generic_load_replaces_stale_gpio_wires(fresh_sim):
    fresh_sim.add_gpio_wire("pru0", 1, "pru1", 2)

    with client.websocket_connect("/ws") as ws:
        loaded = _load_runtime_pair(ws)

    assert loaded["wires"] == [
        {"src_core": "pru1", "src_pin": 0, "dst_core": "pru0", "dst_pin": 8},
        {"src_core": "pru0", "src_pin": 0, "dst_core": "pru1", "dst_pin": 16},
    ]


def test_dashboard_apply_is_transactional_when_frame_validation_fails(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        loaded = _load_runtime_pair(ws)
        original_generation = loaded["active"]["requested_generation"]
        original_frames = loaded["frames"]

        ws.send_json({
            "action": "ssi_runtime_apply",
            "profile": "CUSTOM_LEGACY_12BIT_4MHZ",
            "overrides": {
                "frame_width_bits": 8,
                "position_width_bits": 8,
                "singleturn_width_bits": 8,
            },
            "frames": ["ABC"],
        })
        error = ws.receive_json()
        assert error["type"] == "ssi_runtime_error"
        assert "does not fit in 8 bits" in error["error"]

        ws.send_json({"action": "ssi_runtime_read"})
        unchanged = ws.receive_json()
        assert unchanged["active"]["requested_generation"] == original_generation
        assert unchanged["active"]["frame_width_bits"] == 12
        assert unchanged["staged"]["frame_width_bits"] == 12
        assert unchanged["frames"] == original_frames


def test_dashboard_apply_commits_one_complete_transaction(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        loaded = _load_runtime_pair(ws)
        original_generation = loaded["active"]["requested_generation"]

        ws.send_json({
            "action": "ssi_runtime_apply",
            "profile": "CUSTOM_LEGACY_12BIT_4MHZ",
            "overrides": {
                "frame_width_bits": 8,
                "position_width_bits": 8,
                "singleturn_width_bits": 8,
                "multiturn_width_bits": 0,
            },
            "frames": ["12", "A5", "FF"],
        })
        applied = ws.receive_json()

    assert applied["type"] == "ssi_runtime_state"
    assert applied["active"]["frame_width_bits"] == 8
    assert applied["frames"] == [0x12, 0xA5, 0xFF]
    assert applied["active"]["requested_generation"] > original_generation
    assert applied["active"]["requested_generation"] == applied["active"]["pru1_ack_generation"]


def test_dashboard_websocket_rejects_frame_that_exceeds_staged_width(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        _load_runtime_pair(ws)
        ws.send_json({
            "action": "ssi_runtime_stage",
            "profile": "CUSTOM_LEGACY_12BIT_4MHZ",
            "overrides": {
                "frame_width_bits": 8,
                "position_width_bits": 8,
                "singleturn_width_bits": 8,
            },
        })
        ws.receive_json()
        ws.send_json({"action": "ssi_runtime_frames", "frames": ["ABC"]})
        error = ws.receive_json()
        assert error["type"] == "ssi_runtime_error"
        assert "does not fit in 8 bits" in error["error"]


def test_dashboard_only_requests_capture_when_recording():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    run_section = js[js.index("function pumpRun"):js.index("function startRun")]
    sim_section = js[js.index("function startSim"):js.index("function stopSim")]
    step_section = js[js.index("btnStep.addEventListener"):js.index("btnRun.addEventListener")]

    assert "const capture = signalGraph.recording" in run_section
    assert "capture, request_id" in run_section
    assert "capture: signalGraph.recording" in sim_section
    assert 'action: "step", core: currentCore, count: 1 });' in step_section
    assert 'capture: true' not in step_section
    assert 'action: "run_multicore"' in step_section


def test_dashboard_throttles_runtime_and_profile_dom_work_during_runs():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    run_done_section = js[js.index('msg.type === "run_done"'):js.index('msg.type === "uart_inject_ok"')]

    assert "function queueSsiRuntimeRead(force = false)" in js
    assert "const SSI_RUNTIME_READ_THROTTLE_MS = 250;" in js
    assert "queueSsiRuntimeRead(!(running || simRunning))" in run_done_section
    assert "profileSelect.dataset.profileKey === profileKey" in js
