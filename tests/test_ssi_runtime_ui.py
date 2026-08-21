"""Tests for the browser-facing generic SSI runtime contract."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ui import server as srv
from simulator import Simulator
from pru_io import ssi_config_abi as abi
from pru_io.ssi_runtime import SSIRuntime


STATIC_DIR = Path(__file__).parents[1] / "ui" / "static"
client = TestClient(srv.app)


@pytest.fixture
def fresh_sim(monkeypatch):
    fresh = Simulator(config_path=srv.config_path)
    monkeypatch.setattr(srv, "sim", fresh)
    monkeypatch.setattr(srv, "_ssi_runtime", None)
    return fresh


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
        "ssi-runtime-mailbox",
        "ssi-runtime-trace",
    ):
        assert f'id="{element_id}"' in html, element_id

    for action in (
        "ssi_runtime_load",
        "ssi_runtime_stage",
        "ssi_runtime_frames",
        "ssi_runtime_positions",
        "ssi_runtime_apply",
        "ssi_runtime_read",
    ):
        assert action in js, action


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
        ws.send_json({"action": "ssi_runtime_load"})
        loaded = ws.receive_json()
        assert loaded["type"] == "ssi_runtime_state"
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


def test_dashboard_websocket_rejects_frame_that_exceeds_staged_width(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "ssi_runtime_load"})
        ws.receive_json()
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
