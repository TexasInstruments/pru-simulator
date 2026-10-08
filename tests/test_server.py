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


def test_put_config_unknown_target_returns_400_and_keeps_the_live_simulator(
    tmp_path, monkeypatch
):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    original = client.get("/config").text
    cfg_file.write_text(original)
    before = cfg_file.read_bytes()
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    live = Simulator(config_path=str(cfg_file))
    monkeypatch.setattr(srv, "sim", live)
    typo = original.replace("target = AM243x", "target = am234x", 1)
    assert typo != original

    response = client.put("/config", content=typo)
    assert response.status_code == 400
    assert "am234x" in response.json()["error"]
    assert cfg_file.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["memory.cfg"]
    assert srv.sim is live

    response = client.put("/config", content=original)
    assert response.status_code == 200
    assert srv.sim is not live
    assert cfg_file.read_text() == original
    assert [p.name for p in tmp_path.iterdir()] == ["memory.cfg"]


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


def test_put_config_invalid_leaves_file_history_and_simulator_untouched(
    tmp_path, monkeypatch
):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    original = client.get("/config").text
    cfg_file.write_bytes(original.encode("utf-8"))
    before = cfg_file.read_bytes()
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    live = Simulator(config_path=str(cfg_file))
    monkeypatch.setattr(srv, "sim", live)
    monkeypatch.setattr(srv, "_history", {core: [] for core in srv._history})
    srv._history["pru0"].append({"stale": True})

    response = client.put("/config", content=original + "\n[DRAM0\nbase = 0\n")
    assert response.status_code == 400
    assert repr(str(cfg_file))[1:-1] in response.json()["error"]
    assert cfg_file.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["memory.cfg"]
    assert srv.sim is live
    assert srv._history["pru0"] == [{"stale": True}]


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
    assert sink.payload["fault"] == fresh_sim.cores["pru0"].fault
    assert sink.payload["fault"] is not fresh_sim.cores["pru0"].fault

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
    assert fresh_sim.io("pru1")["gpo_drive_mask"] == 0
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
         "config": {"enable": 1, "vd_ref_q15": -4096, "vq_ref_q15": 2048,
                    "speed_ref_q28": 0x10000000, "ramp_rate_q28": 12000,
                    "initial_phase_q32": 0x20000000},
         "routes": [3, 4]},
    ])

    config = foc_abi.unpack_config(
        fresh_sim.memory_read(foc_abi.CONTROL_ADDRESS, foc_abi.CONFIG_SIZE)
    )
    assert config["enable"] == 1
    assert config["vd_ref_q15"] == -4096
    assert config["vq_ref_q15"] == 2048
    assert config["speed_ref_q28"] == 0x10000000
    assert config["ramp_rate_q28"] == 12000
    assert config["initial_phase_q32"] == 0x20000000
    assert config["requested_generation"] == 1
    assert fresh_sim.cores["pru0"].io_port.sd_filter.input_routes[:2] == [3, 4]

    state = [message for message in sent if message.get("type") == "state"][-1]
    assert state["io"]["foc_config"]["vd_ref_q15"] == -4096
    assert state["io"]["device_bus"]["devices"][0]["model"] == "pmsm"


def test_websocket_rejects_invalid_foc_route_without_partial_update(fresh_sim):
    original_config = foc_abi.pack_config(vd_ref_q15=321, vq_ref_q15=-654)
    fresh_sim.memory.write(foc_abi.CONTROL_ADDRESS, original_config)
    sd = fresh_sim.cores["pru0"].io_port.sd_filter
    sd.route_input(0, 7)
    sd.route_input(1, 8)

    sent = _run_websocket_actions([
        {"action": "device_attach", "core": "pru0", "profile": "foc_motor"},
        {"action": "foc_apply", "core": "pru0",
         "config": {"vd_ref_q15": 1000, "vq_ref_q15": 2000},
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
    original_config = foc_abi.pack_config(vd_ref_q15=321)
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
    assert unchanged["io"]["foc_config"]["vd_ref_q15"] == 321
    assert sd.input_routes == [3, 4, 4]
    assert sd.modulators[2].sd_clock_mhz == 20
    assert sd.modulators[0].sd_clock_mhz == 15


# ---- Motor control actions -----------------------------------------------

def _foc_block(sim):
    return foc_abi.unpack_config(sim.memory_read(foc_abi.CONTROL_ADDRESS, foc_abi.CONFIG_SIZE))


def test_websocket_foc_reference_converts_units_and_sets_the_display_angle(fresh_sim):
    import math
    import ui.server as srv

    srv._history_order.append(("pru0", {"stale": True}))
    sent = _run_websocket_actions([
        {"action": "device_attach", "core": "pru0", "profile": "foc_motor",
         "config": {"pole_pairs": 2}},
        {"action": "foc_set_reference", "core": "pru0", "speed_rpm": 600,
         "vd_pu": 0.25, "vq_pu": 0.25, "accel_rpm_s": 1200, "enable": True},
    ])
    assert not any(message.get("type") == "error" for message in sent)
    block = _foc_block(fresh_sim)
    # 600 rpm * 2 pole pairs / 60 = 20 Hz electrical = 0.02 pu of the 1 kHz base.
    assert block["speed_ref_q28"] == round(0.02 * 2**28)
    assert block["ramp_rate_q28"] == round(0.04 / 16000 * 2**28)
    assert (block["vd_ref_q15"], block["vq_ref_q15"]) == (8192, 8192)
    assert (block["enable"], block["requested_generation"]) == (1, 1)
    motor = fresh_sim.device_bus.get_device("foc_motor")
    assert motor.parameters()["reference_angle_rad"] == pytest.approx(math.pi / 4)
    assert srv._history_order == []
    state = [message for message in sent if message.get("type") == "state"][-1]
    assert state["io"]["foc_config"]["requested_generation"] == 1
    assert state["io"]["foc_clocks"] == {"pru_hz": 250e6, "iep_hz": 200e6}


def test_websocket_foc_actions_reject_bad_input_without_changing_anything(fresh_sim):
    sent = _run_websocket_actions([
        {"action": "foc_set_reference", "core": "pru0", "speed_rpm": 100},
        {"action": "device_attach", "core": "pru0", "profile": "foc_motor"},
        {"action": "foc_set_reference", "core": "pru0", "speed_rpm": 100, "vq_pu": 0.1},
        {"action": "foc_set_reference", "core": "pru0", "speed_rpm": 1e9},
        {"action": "foc_set_reference", "core": "pru0", "vq_pu": 2},
        {"action": "foc_set_reference", "core": "pru0", "accel_rpm_s": -1},
        {"action": "foc_set_reference", "core": "pru0", "speed_rpm": "fast"},
        {"action": "foc_set_reference", "core": "pru0", "enable": "yes"},
        {"action": "foc_enable", "core": "pru0", "enable": 1},
        {"action": "foc_set_motor", "core": "pru0", "parameters": {"resistance_ohm": 0}},
        {"action": "foc_set_motor", "core": "pru0", "parameters": []},
        {"action": "foc_set_motor", "core": "pru0", "parameters": {"bogus": 1}},
        {"action": "foc_state", "core": "pru0", "since": -1},
    ])
    errors = [message for message in sent if message.get("type") == "error"]
    assert len(errors) == 11
    assert all(error["tag"] == "motor" for error in errors)
    assert "Attach a FOC motor" in errors[0]["errors"][0]
    block = _foc_block(fresh_sim)
    assert (block["speed_ref_q28"], block["requested_generation"]) == (
        round(100 * 4 / 60 / 1000 * 2**28), 1), "only the one valid update was staged"
    assert fresh_sim.device_bus.get_device("foc_motor").parameters()["resistance_ohm"] == 0.5


def test_websocket_foc_set_motor_enable_and_state(fresh_sim):
    import ui.server as srv

    srv._history_order.append(("pru0", {"stale": True}))
    sent = _run_websocket_actions([
        {"action": "device_attach", "core": "pru0", "profile": "foc_motor"},
        {"action": "foc_set_motor", "core": "pru0",
         "parameters": {"load_torque_nm": 0.2, "pole_pairs": 3}},
        {"action": "foc_enable", "core": "pru0", "enable": True},
        {"action": "foc_state", "core": "pru0", "since": 0},
    ])
    assert not any(message.get("type") == "error" for message in sent)
    motor = fresh_sim.device_bus.get_device("foc_motor")
    assert (motor.load_torque_nm, motor.pole_pairs) == (0.2, 3)
    assert _foc_block(fresh_sim)["enable"] == 1
    samples = sent[-1]
    assert samples["type"] == "foc_samples"
    assert samples["fields"][:2] == ["index", "time_s"]
    assert samples["samples"] == [] and samples["next_index"] == 0
    assert srv._history_order == []


def test_websocket_foc_apply_with_empty_config_only_changes_routes(fresh_sim):
    fresh_sim.memory.write(foc_abi.CONTROL_ADDRESS, foc_abi.pack_config(vd_ref_q15=77))
    _run_websocket_actions([
        {"action": "device_attach", "core": "pru0", "profile": "foc_motor"},
        {"action": "foc_apply", "core": "pru0", "config": {}, "routes": [3, 4]},
    ])
    assert fresh_sim.cores["pru0"].io_port.sd_filter.input_routes[:2] == [3, 4]
    assert _foc_block(fresh_sim)["vd_ref_q15"] == 77


@pytest.mark.parametrize('mhz', [333.333, 0.125, 251.5])
def test_custom_iep_clock_preserves_counters_and_time(fresh_sim, monkeypatch, mhz):
    import ui.server as srv
    monkeypatch.setattr(srv, '_iep_clock_override', None)
    fresh_sim.load('pru0', 'nop\nhalt')
    fresh_sim.step('pru0')
    before = (fresh_sim.iep.count, fresh_sim.iep.global_time_units, fresh_sim.cores['pru0'].counters.cycles)
    sent = _run_websocket_actions([{'action': 'set_iep_clock', 'mhz': mhz}])
    assert [p['type'] for p in sent] == ['state']
    assert sent[0]['iep']['override_mhz'] == mhz
    assert sent[0]['iep']['configured_mhz'] == 200
    assert sent[0]['iep']['external_mhz'] == mhz
    assert before == (fresh_sim.iep.count, fresh_sim.iep.global_time_units, fresh_sim.cores['pru0'].counters.cycles)


@pytest.mark.parametrize('mhz', [True, '250', float('nan'), float('inf'), 0, -1])
def test_invalid_iep_clock_is_non_mutating(fresh_sim, monkeypatch, mhz):
    import ui.server as srv
    monkeypatch.setattr(srv, '_iep_clock_override', None)
    before = fresh_sim.iep.snapshot()
    sent = _run_websocket_actions([{'action': 'set_iep_clock', 'mhz': mhz}])
    assert sent[0]['type'] == 'error'
    assert sent[0]['tag'] == 'iep'
    assert fresh_sim.iep.snapshot() == before
    assert srv._iep_clock_override is None


def test_iep_configured_reset_retains_source_and_session_step_back(fresh_sim, monkeypatch):
    import ui.server as srv
    monkeypatch.setattr(srv, '_iep_clock_override', None)
    fresh_sim.iep.set_clock_mhz(211.25)
    fresh_sim.iep.write_iepclk(1)
    fresh_sim.load('pru0', 'nop\nhalt')
    sent = _run_websocket_actions([
        {'action': 'step'}, {'action': 'set_iep_clock', 'mhz': 333.333},
        {'action': 'step_back'}, {'action': 'set_iep_clock', 'mhz': None},
        {'action': 'reset'}, {'action': 'hard_reset'},
    ])
    assert not any(p['type'] == 'error' for p in sent)
    assert sent[1]['iep']['core_clock'] is True
    assert sent[1]['iep']['clock_mhz'] == 250
    assert all(p['iep']['external_mhz'] == 333.333 for p in sent[2:6])
    assert sent[6]['iep']['external_mhz'] == 211.25
    assert sent[6]['iep']['override_mhz'] is None
    assert sent[6]['iep']['core_clock'] is True
    assert sent[-1]['iep']['external_mhz'] == 211.25


def test_gpio_direction_control_preserves_r30_and_reports_ownership(fresh_sim):
    sent = _run_websocket_actions([
        {'action': 'set_register', 'index': 30, 'value': 15},
        {'action': 'set_gpio_drive_mask', 'mask': 7},
        {'action': 'device_attach', 'profile': 'foc_motor'},
        {'action': 'set_gpio_drive_mask', 'mask': 0},
        {'action': 'get_state'},
    ])
    assert sent[1]['io']['gpo_drive_mask'] == 7
    assert sent[1]['registers'][30] == '0x0000000F'
    errors = [p for p in sent if p['type'] == 'error' and p.get('tag') == 'gpio']
    assert len(errors) == 1
    assert sent[-1]['io']['gpo_drive_mask'] == sent[2]['io']['gpo_drive_mask']
    assert sent[-1]['registers'][30] == '0x0000000F'


@pytest.mark.parametrize('mask', [True, -1, 1 << 20, 1.2, '7', None])
def test_gpio_direction_rejects_invalid_masks(fresh_sim, mask):
    sent = _run_websocket_actions([{'action': 'set_gpio_drive_mask', 'mask': mask}])
    assert sent[0]['type'] == 'error'
    assert sent[0]['tag'] == 'gpio'
    assert fresh_sim.cores['pru0'].io_port.gpo_drive_mask == 0


def test_unavailable_core_request_keeps_socket_and_advertises_actual_cores(fresh_sim):
    del fresh_sim.cores['rtu1']
    sent = _run_websocket_actions([
        {'action': 'get_state', 'core': 'rtu1'}, {'action': 'get_state', 'core': 'pru0'},
        {'action': 'run_multicore', 'partners': ['rtu1']},
    ])
    assert sent[0]['type'] == 'error'
    assert sent[1]['type'] == 'state'
    assert sent[1]['core'] == 'pru0'
    assert sent[1]['available_cores'] == ['pru0', 'rtu0', 'pru1']
    assert any(p['type'] == 'error' and 'partners' in p['errors'][0] for p in sent)


@pytest.mark.parametrize('core_source', [False, True])
def test_foc_reference_uses_active_iep_rate(fresh_sim, core_source):
    from pru_io import foc_control
    fresh_sim.iep.set_clock_mhz(333.333)
    fresh_sim.iep.write_iepclk(int(core_source))
    sent = _run_websocket_actions([
        {'action': 'device_attach', 'profile': 'foc_motor'},
        {'action': 'foc_set_reference', 'speed_rpm': 600, 'accel_rpm_s': 1200},
    ])
    assert not any(p['type'] == 'error' for p in sent)
    hz = float(fresh_sim.iep.active_clock_hz) / 12500
    block = _foc_block(fresh_sim)
    assert block['speed_ref_q28'] == foc_control.speed_rpm_to_q28(600, 4, update_hz=hz)
    assert block['ramp_rate_q28'] == foc_control.ramp_rpm_s_to_q28(1200, 4, update_hz=hz)


def test_iep_override_survives_core_reload_saved_config_clears_it(tmp_path, monkeypatch):
    import ui.server as srv
    cfg = tmp_path / 'memory.cfg'
    # Insert into the actual device section, not whichever section ends the file.
    original = srv._set_ini_value(client.get('/config').text, 'device', 'iep_clock_mhz', '211.25')
    cfg.write_text(original)
    monkeypatch.setattr(srv, 'config_path', str(cfg))
    monkeypatch.setattr(srv, 'sim', Simulator(str(cfg)))
    monkeypatch.setattr(srv, '_iep_clock_override', None)
    srv._set_iep_clock(333.333)
    assert client.put('/config/clock_speed', json={'mhz': 300}).status_code == 200
    assert float(srv.sim.iep.external_clock_hz / 1e6) == 333.333
    assert srv._configured_iep_mhz() == 211.25
    srv._set_iep_clock(None)
    assert float(srv.sim.iep.external_clock_hz / 1e6) == 211.25
    srv._set_iep_clock(250)
    assert client.put('/config', content=original).status_code == 200
    assert srv._iep_clock_override is None
    assert float(srv.sim.iep.external_clock_hz / 1e6) == 211.25


def test_am263x_state_advertises_only_supported_cores(tmp_path, monkeypatch):
    import ui.server as srv
    cfg = tmp_path / 'memory.cfg'
    cfg.write_text(srv._set_ini_value(client.get('/config').text, 'device', 'target', 'AM263x'))
    monkeypatch.setattr(srv, 'sim', Simulator(str(cfg)))
    sent = _run_websocket_actions([{'action': 'get_state'},
                                  {'action': 'load', 'core': 'rtu1', 'source': 'halt'},
                                  {'action': 'get_state'}])
    assert sent[0]['available_cores'] == ['pru0', 'rtu0', 'pru1']
    assert sent[1]['type'] == 'error'
    assert sent[-1]['type'] == 'state'


def test_configured_reset_uses_backend_policy_when_iep_rate_is_omitted(tmp_path, monkeypatch):
    import ui.server as srv
    cfg = tmp_path / 'memory.cfg'
    original = client.get('/config').text
    import re
    original = re.sub(r'^iep_clock_mhz\s*=.*\n', '', original, flags=re.M)
    cfg.write_text(original)
    monkeypatch.setattr(srv, 'config_path', str(cfg))
    monkeypatch.setattr(srv, 'sim', Simulator(str(cfg)))
    monkeypatch.setattr(srv, '_iep_clock_override', None)
    configured = float(srv.sim.iep.external_clock_hz / 1_000_000)
    srv._set_iep_clock(333.333)
    srv._set_iep_clock(None)
    assert float(srv.sim.iep.external_clock_hz / 1_000_000) == configured
    assert client.put('/config/clock_speed', json={'mhz': 300}).status_code == 200
    assert float(srv.sim.iep.external_clock_hz / 1_000_000) == float(Simulator(str(cfg)).iep.external_clock_hz / 1_000_000)


def test_ws_ssi_attach_then_load_current_abi_firmware_keeps_session(fresh_sim):
    from pathlib import Path
    source = Path('source/ssi_generic_emulator.asm').read_text()
    sent = _run_websocket_actions([
        {'action': 'device_attach', 'core': 'pru1', 'profile': 'ssi_encoder'},
        {'action': 'load', 'core': 'pru1', 'filename': 'ssi_generic_emulator.asm', 'source': source},
        {'action': 'step', 'core': 'pru1'},
    ])
    assert not any(p['type'] == 'error' for p in sent)
    assert sent[-1]['instructions']
    assert sent[-1]['fault'] is None
    assert sent[-1]['io']['device_bus']['devices'][0]['name'] == 'ssi_encoder'
