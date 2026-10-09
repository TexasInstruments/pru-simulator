"""Scenario picker actions over a real dashboard WebSocket session."""
import pytest
from fastapi.testclient import TestClient

from pru_io import ssi_config_abi as abi
from pru_io.scenarios import SCENARIOS
from simulator import Simulator
from ui.server import app

client = TestClient(app)
SINGLE, MULTI, FOC = SCENARIOS


@pytest.fixture
def fresh_sim(monkeypatch):
    import ui.server as srv
    fresh = Simulator(config_path=srv.config_path)
    monkeypatch.setattr(srv, "sim", fresh)
    monkeypatch.setattr(srv, "_device_api", srv.PRUSimulatorMCP(
        config_path=srv.config_path, simulator=fresh))
    monkeypatch.setattr(srv, "_iep_clock_override", None)
    srv._clear_history()
    return fresh


def _load(ws, name, states):
    ws.send_json({"action": "scenario_load", "name": name})
    loaded = ws.receive_json()
    return [loaded, *[ws.receive_json() for _ in range(
        states if loaded['type'] == 'scenario_loaded' else 1)]]


def _mailbox(sim):
    return abi.unpack_mailbox(sim.memory_read(abi.MAILBOX_ADDRESS, abi.MAILBOX_SIZE))


def test_scenario_list_describes_every_scenario(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "scenario_list"})
        message = ws.receive_json()
    assert message["type"] == "scenarios"
    by_name = {scenario["name"]: scenario for scenario in message["scenarios"]}
    assert list(by_name) == list(SCENARIOS)
    assert by_name[MULTI]["multicore"] is True and by_name[MULTI]["lead"] == "pru0"
    assert by_name[MULTI]["cores"] == ["pru0", "pru1"]
    assert by_name[FOC]["ui"]["view"] == "motor"
    assert all(scenario["description"] for scenario in by_name.values())


def test_unknown_scenario_is_a_normal_error(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "scenario_load", "name": "nope"})
        error = ws.receive_json()
        assert error["type"] == "error" and error["tag"] == "scenario"
        assert "unknown scenario" in error["errors"][0]
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"action": "scenario_load"})   # missing name
        assert ws.receive_json()["tag"] == "scenario"
        ws.receive_json()
        ws.send_json({"action": "get_state", "core": "pru0"})   # socket still alive
        assert ws.receive_json()["core"] == "pru0"


def test_single_core_ssi_scenario_runs_and_captures_the_clock(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        loaded, state = _load(ws, SINGLE, 1)
        assert loaded["type"] == "scenario_loaded" and loaded["name"] == SINGLE
        assert state["core"] == "pru0" and len(state["instructions"]) > 0
        assert state["io"]["device_bus"]["devices"][0]["position"] == 0xABC
        samples = []
        for _ in range(8):
            ws.send_json({"action": "run", "core": "pru0", "max_steps": 1000,
                          "capture": True, "stride": loaded["ui"]["capture_stride"]})
            messages = [ws.receive_json(), ws.receive_json()]
            samples += next(m for m in messages if m["type"] == "capture")["samples"]
    clock = [(sample[1] >> 0) & 1 for sample in samples]
    data = [(sample[2] >> 16) & 1 for sample in samples]
    assert sum(a != b for a, b in zip(clock, clock[1:])) >= 12
    assert 0 < sum(data) < len(data)
    assert _mailbox(fresh_sim)["raw_frame_lo"] == 0xABC


def test_multicore_scenario_refreshes_both_cores_and_runs_together(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        loaded, *states = _load(ws, MULTI, 2)
        assert loaded["multicore"] is True
        assert [state["core"] for state in states] == ["pru0", "pru1"]
        assert all(len(state["instructions"]) > 0 for state in states)
        for _ in range(40):
            ws.send_json({"action": "run_multicore", "core": "pru0",
                          "partners": ["pru1"], "max_steps": 1000})
            ws.receive_json(), ws.receive_json()
    assert _mailbox(fresh_sim)["frame_count"] >= 3
    assert _mailbox(fresh_sim)["raw_frame_lo"] == 0xABC


def test_foc_scenario_fills_the_motor_samples_on_the_first_run(fresh_sim):
    import ui.server as srv
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "set_iep_clock", "mhz": 250})
        ws.receive_json()
        loaded, state = _load(ws, FOC, 1)
        assert loaded["ui"]["view"] == "motor"
        assert state["io"]["foc_config"]["enable"] == 1
        assert state["io"]["sd"]["input_routes"][:2] == [3, 4]
        assert state["io"]["foc_clocks"]["iep_hz"] == 250e6
        assert srv._iep_clock_override == 250
        for _ in range(120):
            ws.send_json({"action": "run", "core": "pru0", "max_steps": 1000})
            ws.receive_json()
        ws.send_json({"action": "foc_state", "core": "pru0", "since": 0})
        samples = ws.receive_json()
    columns = dict(zip(samples["fields"], zip(*samples["samples"])))
    assert len(samples["samples"]) >= 3
    assert any(abs(current) > 0.5 for phase in ("ia", "ib", "ic") for current in columns[phase])
    assert any(duty != 0.5 for duty in columns["duty_a"])


def _run_capture(ws, message):
    ws.send_json(message)
    capture = ws.receive_json()
    assert capture["type"] == "capture", capture
    return capture


def test_multicore_capture_carries_both_ends_of_each_wire(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        _load(ws, MULTI, 2)
        capture = _run_capture(ws, {
            "action": "run_multicore", "core": "pru0", "partners": ["pru1"],
            "max_steps": 40000, "capture": True, "stride": 2})
    assert capture["core"] == "pru0" and capture["partners"] == ["pru1"]
    samples = capture["samples"]
    assert all(len(s) == 8 for s in samples)   # 6 lead ints + [r30, gpi] of pru1
    bit = lambda s, idx, n: (s[idx] >> n) & 1
    # clock: PRU0 GPO 0 == PRU1 GPI 0; data: PRU1 GPO 16 == PRU0 GPI 16
    assert any(bit(s, 1, 0) for s in samples) and any(bit(s, 7, 0) for s in samples)
    assert any(bit(s, 6, 16) for s in samples) and any(bit(s, 2, 16) for s in samples)
    assert all(bit(s, 1, 0) == bit(s, 7, 0) for s in samples)
    assert all(bit(s, 6, 16) == bit(s, 2, 16) for s in samples)


def test_single_core_capture_shape_is_unchanged(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        _load(ws, SINGLE, 1)
        capture = _run_capture(ws, {
            "action": "run", "core": "pru0", "max_steps": 2000,
            "capture": True, "stride": 2})
    assert "partners" not in capture
    assert all(len(s) == 6 for s in capture["samples"])

@pytest.mark.parametrize('name, states', [(SINGLE, 1), (MULTI, 2)])
def test_ssi_demo_readout_and_next_position(fresh_sim, name, states):
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'action': 'set_iep_clock', 'mhz': 271.25})
        ws.receive_json()
        loaded, *initial = _load(ws, name, states)
        assert loaded['type'] == 'scenario_loaded', loaded
        demo = initial[0]['io']['ssi_demo']
        assert demo['requested_position'] == 2748
        assert demo['position'] is None and demo['frame_count'] == 0
        assert initial[0]['iep']['override_mhz'] == 271.25
        run = {'action': 'run_multicore' if states == 2 else 'run',
               'core': 'pru0', 'partners': ['pru1'], 'max_steps': 20_000}
        ws.send_json(run)
        replies = [ws.receive_json() for _ in range(states)]
        assert replies[0]['io']['ssi_demo']['position'] == 2748
        assert replies[0]['io']['ssi_demo']['error'] == 0
        assert replies[0]['io']['ssi_demo']['frame_count'] >= 2
        ws.send_json({'action': 'ssi_demo_position', 'position': 1234})
        state = ws.receive_json()
        assert state['io']['ssi_demo']['requested_position'] == 1234
        ws.send_json(run)
        replies = [ws.receive_json() for _ in range(states)]
        assert replies[0]['io']['ssi_demo']['position'] == 1234
        assert replies[0]['io']['ssi_demo']['reader_status'] == 0
        assert all(m['fault'] is None for m in replies)
        ws.send_json({'action': 'ssi_demo_position', 'position': 4096})
        assert ws.receive_json()['tag'] == 'ssi_demo'
        assert ws.receive_json()['io']['ssi_demo']['requested_position'] == 1234


def test_demo_hides_torn_mailbox_and_disappears_when_other_firmware_loads(fresh_sim):
    with client.websocket_connect('/ws') as ws:
        loaded, state = _load(ws, SINGLE, 1)
        assert loaded['type'] == 'scenario_loaded', loaded
        fresh_sim.memory.write(abi.MAILBOX_ADDRESS, (1).to_bytes(4, 'little'))
        ws.send_json({'action': 'get_state'})
        demo = ws.receive_json()['io']['ssi_demo']
        assert demo['coherent'] is False
        assert demo['position'] is None and demo['frame_count'] is None
        ws.send_json({'action': 'load', 'source': 'HALT'})
        assert 'ssi_demo' not in ws.receive_json()['io']
