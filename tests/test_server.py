"""Tests for dashboard REST endpoints."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from ui.server import app
from simulator import Simulator
from pru_io import foc_control_abi as foc_abi


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


@pytest.mark.parametrize("replace_clock", [False, True])
def test_config_replacement_clears_rtu1_step_history(
    tmp_path, monkeypatch, replace_clock
):
    import ui.server as srv

    cfg_file = tmp_path / "memory.cfg"
    original = client.get("/config").text
    cfg_file.write_text(original)
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    histories = {core: [] for core in ("pru0", "rtu0", "pru1", "rtu1")}
    histories["rtu1"].append({"stale": True})
    monkeypatch.setattr(srv, "_history", histories)

    if replace_clock:
        response = client.put("/config/clock_speed", json={"mhz": 250})
    else:
        response = client.put("/config", content=original)

    assert response.status_code == 200
    assert all(not history for history in srv._history.values())


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


def test_core_reset_invalidates_all_global_step_histories(fresh_sim):
    import ui.server as srv

    srv._clear_history()
    for history in srv._history.values():
        history.append({"stale": True})
    srv._history_order.append(("pru0", {"stale": True}))

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()

    assert all(not history for history in srv._history.values())
    assert srv._history_order == []


def test_fault_is_reported_in_state_and_restored_by_step_back(fresh_sim):
    import ui.server as srv

    errors = fresh_sim.load(
        "pru0", "ldi r1, 0x4000\nlbbo &r0, r1, 0, 4\nhalt"
    )
    assert errors == []
    snapshot = srv._snapshot("pru0")
    fresh_sim.step("pru0", count=2)

    class WebSocketSink:
        async def send_json(self, payload):
            self.payload = payload

    sink = WebSocketSink()
    asyncio.run(srv._send_state(sink, "pru0"))
    assert sink.payload["fault"]["opcode"] == "LBBO"

    srv._restore("pru0", snapshot)
    assert fresh_sim.cores["pru0"].fault is None


class _ActionWebSocket:
    def __init__(self, actions):
        self._actions = iter(actions)
        self.sent = []

    async def accept(self):
        pass

    async def receive_text(self):
        try:
            return json.dumps(next(self._actions))
        except StopIteration:
            raise WebSocketDisconnect(code=1000)

    async def send_json(self, payload):
        self.sent.append(payload)


def _run_websocket_actions(actions):
    import ui.server as srv

    websocket = _ActionWebSocket(actions)
    asyncio.run(srv.websocket_endpoint(websocket))
    return websocket.sent


def test_state_preserves_other_core_fault_until_reset(fresh_sim):
    fault = {"opcode": "LBBO", "address": 12, "error": "Unmapped memory"}
    fresh_sim.cores["pru0"].fault = fault
    sent = _run_websocket_actions([
        {"action": "get_state", "core": "pru0"},
        {"action": "get_state", "core": "rtu1"},
        {"action": "reset", "core": "pru0"},
    ])
    assert sent[0]["core_faults"] == {"pru0": fault}
    assert sent[1]["fault"] is None
    assert sent[1]["core_faults"] == {"pru0": fault}
    assert sent[2]["core_faults"] == {}


def test_websocket_attaches_updates_and_detaches_ssi_encoder(fresh_sim):
    import ui.server as srv

    for history in srv._history.values():
        history.append({"stale": True})
    srv._history_order.append(("pru1", {"stale": True}))

    sent = _run_websocket_actions([
        {"action": "device_attach", "core": "pru1", "profile": "ssi_encoder",
         "config": {"name": "axis", "clock_pin": 0, "data_pin": 8,
                    "position": 17, "resolution": 8}},
        {"action": "ssi_set_position", "core": "pru1", "name": "axis",
         "position": 42},
        {"action": "device_detach", "core": "pru1", "name": "axis"},
    ])

    attached = next(message for message in sent if message.get("type") == "state")
    device = attached["io"]["device_bus"]["devices"][0]
    assert device["core"] == "pru1"
    assert device["position"] == 17
    assert fresh_sim.device_bus.devices == []
    assert fresh_sim.io("pru1")["gpo_drive_mask"] == (1 << 20) - 1
    assert all(not history for history in srv._history.values())
    assert srv._history_order == []

    detached = [message for message in sent if message.get("type") == "state"][-1]
    assert "device_bus" not in detached["io"]


def test_websocket_lists_ssi_presets_for_the_device_panel(fresh_sim):
    sent = _run_websocket_actions([{"action": "device_discover", "core": "pru1"}])

    profiles = next(message for message in sent
                    if message.get("type") == "device_profiles")["profiles"]
    presets = profiles["ssi_encoder"]["presets"]
    assert len(presets) == 12
    assert presets["AFS_AFM60_MULTITURN_30BIT"]["resolution"] == 33
    assert presets["TTK70"]["error_bits"] == 2


def test_websocket_attaches_ssi_preset_and_sets_position_and_error(fresh_sim):
    import ui.server as srv

    srv._history["pru1"].append({"stale": True})
    sent = _run_websocket_actions([
        {"action": "device_attach", "core": "pru1", "profile": "ssi_encoder",
         "config": {"name": "axis", "preset": "AFS_AFM60_SINGLETURN",
                    "position": 0x2AAAA, "error_value": 1}},
        {"action": "ssi_set_error", "core": "pru1", "name": "axis", "error": 5},
        {"action": "ssi_set_position", "core": "pru1", "name": "axis",
         "position": 7},
    ])

    assert not any(message.get("type") == "error" for message in sent)
    states = [message for message in sent if message.get("type") == "state"]
    first = states[0]["io"]["device_bus"]["devices"][0]
    assert (first["preset"], first["resolution"], first["error_bits"],
            first["error"]) == ("AFS_AFM60_SINGLETURN", 21, 3, 1)
    last = states[-1]["io"]["device_bus"]["devices"][0]
    assert (last["error"], last["position"]) == (5, 7)
    assert fresh_sim.device_bus.get_device("axis").pack_frame(7, 5) == (7 << 3) | 5
    assert not srv._history["pru1"]


def test_websocket_rejects_invalid_ssi_error_updates(fresh_sim):
    sent = _run_websocket_actions([
        {"action": "device_attach", "core": "pru1", "profile": "ssi_encoder",
         "config": {"name": "axis", "resolution": 8, "error_bits": 2,
                    "error_value": 1}},
        {"action": "device_attach", "core": "pru1", "profile": "ssi_encoder",
         "config": {"name": "plain", "data_pin": 9, "resolution": 8}},
        {"action": "ssi_set_error", "core": "pru1", "name": "axis", "error": 4},
        {"action": "ssi_set_error", "core": "pru1", "name": "plain", "error": 1},
        {"action": "ssi_set_error", "core": "pru0", "name": "axis", "error": 1},
        {"action": "ssi_set_error", "core": "pru1", "name": "missing", "error": 1},
        {"action": "ssi_set_error", "core": "pru1", "name": "axis"},
    ])

    errors = [message for message in sent if message.get("type") == "error"]
    assert len(errors) == 5
    assert all(error["tag"] == "device" for error in errors)
    assert fresh_sim.device_bus.get_device("axis").error == 1
    assert fresh_sim.device_bus.get_device("plain").error == 0


def test_websocket_set_error_rejects_non_ssi_device(fresh_sim):
    sent = _run_websocket_actions([
        {"action": "device_attach", "core": "pru0", "profile": "foc_motor"},
        {"action": "ssi_set_error", "core": "pru0", "name": "foc_motor", "error": 1},
    ])

    error = next(message for message in sent if message.get("type") == "error")
    assert "not an SSI encoder" in error["errors"][0]


def test_websocket_foc_apply_writes_control_abi_and_sd_routes(fresh_sim):
    sent = _run_websocket_actions([
        {"action": "device_attach", "core": "pru0", "profile": "foc_motor"},
        {"action": "foc_apply", "core": "pru0",
         "config": {"alpha_q15": -4096, "beta_q15": 2048,
                    "phase_increment_q32": 0x10000000,
                    "modulation_q15": 12000, "initial_phase_q32": 0x20000000},
         "routes": [3, 4]},
    ])

    config = foc_abi.unpack_config(
        fresh_sim.memory_read(foc_abi.CONTROL_ADDRESS, foc_abi.CONFIG_SIZE)
    )
    assert config["alpha_q15"] == -4096
    assert config["beta_q15"] == 2048
    assert config["phase_increment_q32"] == 0x10000000
    assert config["modulation_q15"] == 12000
    assert config["initial_phase_q32"] == 0x20000000
    assert fresh_sim.cores["pru0"].io_port.sd_filter.input_routes[:2] == [3, 4]

    state = [message for message in sent if message.get("type") == "state"][-1]
    assert state["io"]["foc_config"]["alpha_q15"] == -4096
    assert state["io"]["device_bus"]["devices"][0]["model"] == "three_phase_rl"


def test_websocket_rejects_invalid_foc_route_without_partial_update(fresh_sim):
    original_config = foc_abi.pack_config(alpha_q15=321, beta_q15=-654)
    fresh_sim.memory.write(foc_abi.CONTROL_ADDRESS, original_config)
    sd = fresh_sim.cores["pru0"].io_port.sd_filter
    sd.route_input(0, 7)
    sd.route_input(1, 8)

    sent = _run_websocket_actions([
        {"action": "device_attach", "core": "pru0", "profile": "foc_motor"},
        {"action": "foc_apply", "core": "pru0",
         "config": {"alpha_q15": 1000, "beta_q15": 2000},
         "routes": [9, 20]},
    ])

    error = next(message for message in sent
                 if message.get("type") == "error" and message.get("tag") == "device")
    assert "routes" in error["errors"][0]
    assert fresh_sim.memory_read(foc_abi.CONTROL_ADDRESS, foc_abi.CONFIG_SIZE) == original_config
    assert sd.input_routes[:2] == [7, 8]


def test_websocket_swaps_foc_routes_with_unequal_clocks_atomically(fresh_sim):
    fresh_sim.set_sd_modulator("pru0", 0, sd_clock_mhz=10)
    sent = _run_websocket_actions([
        {"action": "device_attach", "core": "pru0", "profile": "foc_motor"},
        {"action": "foc_apply", "core": "pru0", "config": {}, "routes": [3, 4]},
        {"action": "foc_apply", "core": "pru0", "config": {}, "routes": [4, 3]},
    ])
    assert not any(message.get("type") == "error" for message in sent)
    assert fresh_sim.cores["pru0"].io_port.sd_filter.input_routes[:2] == [4, 3]
    motor = fresh_sim.device_bus.get_device("foc_motor")
    assert motor.current_a_clock_hz == 20_000_000
    assert motor.current_b_clock_hz == 10_000_000


def test_websocket_rejects_conflicting_foc_clocks_and_keeps_session(fresh_sim):
    original_config = foc_abi.pack_config(alpha_q15=321)
    fresh_sim.memory.write(foc_abi.CONTROL_ADDRESS, original_config)
    fresh_sim.set_sd_modulator("pru0", 0, sd_clock_mhz=10)
    sd = fresh_sim.cores["pru0"].io_port.sd_filter
    sd.route_input(2, 4)
    sent = _run_websocket_actions([
        {"action": "device_attach", "core": "pru0", "profile": "foc_motor"},
        {"action": "foc_apply", "core": "pru0", "config": {}, "routes": [3, 3]},
        {"action": "foc_apply", "core": "pru0", "config": {}, "routes": [3, 4]},
        {"action": "set_sd_modulator", "core": "pru0", "channel": 2,
         "params": {"sd_clock_mhz": 10}},
        {"action": "set_sd_modulator", "core": "pru0", "channel": 0,
         "params": {"sd_clock_mhz": 15}},
    ])
    errors = [message for message in sent if message.get("type") == "error"]
    assert len(errors) == 2
    assert all("different clocks" in error["errors"][0] for error in errors)
    error_index = sent.index(errors[0])
    unchanged = sent[error_index + 1]
    assert unchanged["io"]["foc_config"]["alpha_q15"] == 321
    assert sd.input_routes == [3, 4, 4]
    assert sd.modulators[2].sd_clock_mhz == 20
    assert sd.modulators[0].sd_clock_mhz == 15
