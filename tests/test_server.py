"""Tests for dashboard REST endpoints."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
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


@pytest.mark.parametrize("action,partners", [
    ("run", None),
    ("run_multicore", ["rtu0", "pru1"]),
    ("run_multicore", ["nope"]),
])
def test_run_completion_identifies_only_the_final_state(fresh_sim, action, partners):
    for core in fresh_sim.cores:
        assert fresh_sim.load(core, "loop:\nadd r0, r0, 1\njmp loop") == []
    request = {"action": action, "core": "pru0", "max_steps": 100,
               "capture": True, "run_id": 17}
    if partners is not None:
        request["partners"] = partners
    sent = _run_websocket_actions([
        request, {"action": "get_state", "core": "pru0"},
    ])
    completed = [message for message in sent if message.get("run_id") == 17]
    assert len(completed) == 1
    assert completed[0]["type"] == "state"
    assert completed[0]["core"] == ("pru1" if partners == ["rtu0", "pru1"] else "pru0")
    assert completed[0] is sent[-2], "completion follows every state/capture/error in its chunk"
    assert "run_id" not in sent[-1], "ordinary state pushes cannot acknowledge a run"


def test_invalid_run_core_returns_a_completion_and_keeps_the_socket(fresh_sim):
    sent = _run_websocket_actions([
        {"action": "run", "core": "missing", "run_id": 18},
        {"action": "get_state", "core": "pru0"},
    ])
    assert sent[0]["type"] == "error"
    assert sent[1]["type"] == "state" and sent[1]["run_id"] == 18
    assert sent[2]["type"] == "state" and "run_id" not in sent[2]


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
        {'action': 'device_attach', 'profile': 'ssi_encoder',
         'config': {'data_pin': 16}},
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


@pytest.mark.parametrize("target,has_rtu1", [
    ("AM243x", True), ("AM64x", True), ("AM263x", False),
])
def test_dashboard_core_choices_follow_the_live_target(tmp_path, monkeypatch, target, has_rtu1):
    import ui.server as srv

    cfg_file = tmp_path / "memory.cfg"
    cfg_file.write_text("[device]\ntarget = AM243x\n")
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    monkeypatch.setattr(srv, "sim", Simulator(str(cfg_file)))

    assert "rtu1" in client.get("/cores").json()
    response = client.put("/config", content=f"[device]\ntarget = {target}\n")

    assert response.status_code == 200
    cores = client.get("/cores").json()
    assert set(cores) == set(srv.sim.cores)
    assert ("rtu1" in cores) == has_rtu1
    if has_rtu1:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "load", "core": "rtu1", "source": "ldi r3, 42\nhalt"})
            assert ws.receive_json()["core"] == "rtu1"
            ws.send_json({"action": "step", "core": "rtu1"})
            assert ws.receive_json()["registers"][3] == "0x0000002A"


def test_step_back_restores_all_peers_and_discards_their_later_history(fresh_sim, monkeypatch):
    import ui.server as srv

    monkeypatch.setattr(srv, "_history", {core: [] for core in fresh_sim.cores})
    monkeypatch.setattr(srv, "_history_order", [])
    for core in ("pru0", "pru1"):
        assert fresh_sim.load(core, "nop\njmp 0") == []
    before = fresh_sim.iep.snapshot()
    with client.websocket_connect("/ws") as ws:
        for core in ("pru0", "pru1"):
            ws.send_json({"action": "step", "core": core})
            ws.receive_json()

        ws.send_json({"action": "step_back", "core": "pru0"})

        states = [ws.receive_json() for _ in fresh_sim.cores]
        assert {state["core"] for state in states} == set(fresh_sim.cores)
        assert all(state["cycles"] == 0 for state in states)
        assert all(not history for history in srv._history.values())
        assert srv._history_order == []
        assert fresh_sim.iep.snapshot() == before


def test_multicore_run_reports_incomplete_catchup_without_blocking(fresh_sim):
    for core in ("pru0", "pru1"):
        assert fresh_sim.load(core, "nop\njmp 0") == []
    fresh_sim.step("pru0", 10000)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "run_multicore", "core": "pru0",
                      "partner": "pru1", "max_steps": 5})

        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["reason"] == "pacing_incomplete"
        assert ws.receive_json()["cycles"] == 10001
        assert 0 < ws.receive_json()["cycles"] < 10001
        ws.send_json({"action": "get_state", "core": "pru1"})
        assert ws.receive_json()["type"] == "state"


@pytest.mark.parametrize("finish", ["halt", "nop"])
def test_multicore_run_resumes_catchup_after_the_lead_finishes(fresh_sim, finish):
    assert fresh_sim.load(
        "pru0", f"ldi r0, 10000\nloop: sub r0, r0, 1\nqbne loop, r0, 0\n{finish}"
    ) == []
    assert fresh_sim.load("pru1", "add r1, r1, 1\njmp 0") == []
    fresh_sim.step("pru0", 20001)  # leave HALT/the final NOP for the paced run

    with client.websocket_connect("/ws") as ws:
        for expected_cycles in (1000, 2000):
            ws.send_json({"action": "run_multicore", "core": "pru0",
                          "partner": "pru1", "max_steps": 5})
            error = ws.receive_json()
            assert error["type"] == "error"
            assert error["reason"] == "pacing_incomplete"
            lead = ws.receive_json()
            follow = ws.receive_json()
            assert lead["cycles"] == 20002
            assert lead["halted"] == (finish == "halt")
            assert lead["fault"] is None
            assert follow["cycles"] == expected_cycles
            assert follow["registers"][1] == f"0x{expected_cycles // 2:08X}"

        for _ in range(25):
            ws.send_json({"action": "run_multicore", "core": "pru0",
                          "partner": "pru1", "max_steps": 5})
            lead = ws.receive_json()
            incomplete = lead["type"] == "error"
            if incomplete:
                assert lead["reason"] == "pacing_incomplete"
                lead = ws.receive_json()
            follow = ws.receive_json()
            if not incomplete:
                break

        assert not incomplete
        assert lead["cycles"] == follow["cycles"] == 20002
        assert follow["registers"][1] == "0x00002711"  # all 10001 ADDs executed
