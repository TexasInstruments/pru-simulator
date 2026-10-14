"""Motor Control flow over a real dashboard WebSocket session."""
import asyncio
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pru_io import foc_control_abi as abi
from simulator import Simulator
from ui.server import app

ROOT = Path(__file__).parents[1]
FIRMWARE = (ROOT / "source" / "foc_open_loop.asm").read_text(encoding="utf-8")
client = TestClient(app)


@pytest.fixture
def fresh_sim(monkeypatch):
    import ui.server as srv
    fresh = Simulator(config_path=srv.config_path)
    monkeypatch.setattr(srv, "sim", fresh)
    srv._clear_history()
    return fresh


class Session:
    """Sends one action and drains every message the server pushes for it."""

    def __init__(self, ws):
        self.ws = ws
        self.state = None
        self.errors = []

    def send(self, **action):
        self.ws.send_json({"core": "pru0", **action})
        # Each action ends with state pushes (one per core for step_back) or
        # a foc_samples reply; errors come first.
        final = 4 if action["action"] == "step_back" else 1
        messages, finished = [], 0
        while finished < final:
            message = self.ws.receive_json()
            messages.append(message)
            if message["type"] == "error":
                self.errors.append(message)
            elif message["type"] == "state":
                if message["core"] == "pru0":
                    self.state = message
                finished += 1
            elif message["type"] == "foc_samples":
                finished += 1
        return messages

    def motor(self):
        return next(device for device in self.state["io"]["device_bus"]["devices"]
                    if device["model"] == "pmsm")

    def run_until(self, periods, chunk=20_000):
        while self.motor()["pwm_periods"] < periods:
            self.send(action="run", max_steps=chunk)


def _start(session, **reference):
    session.send(action="device_attach", profile="foc_motor")
    session.send(action="load", source=FIRMWARE, filename="foc_open_loop.asm")
    assert not session.errors
    session.send(action="foc_set_reference", enable=True, **reference)
    assert not session.errors


def test_motor_control_flow_through_the_normal_run_loop(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        _start(session, speed_rpm=400, vd_pu=0.0, vq_pu=0.25, accel_rpm_s=300_000)
        assert session.state["labels"]["control_update"] > 0

        session.run_until(4)
        first = session.motor()
        config = session.state["io"]["foc_config"]
        assert config["status"] & abi.STATUS_RUNNING
        assert config["ack_generation"] == config["requested_generation"] == 1
        session.run_until(12)
        second = session.motor()

        # The ramp climbs toward the 400 rpm reference and drags the rotor along.
        assert 0 < first["commanded_speed_rpm"] < second["commanded_speed_rpm"] <= 400
        assert 0 < first["rotor_speed_rpm"] < second["rotor_speed_rpm"]
        assert second["pwm_frequency_hz"] == pytest.approx(16_000, rel=0.002)
        assert any(abs(current) > 0.5 for current in second["phase_currents_a"])
        assert abs(second["angle_error_rad"]) <= math.pi
        assert second["rotor_angle_rad"] != first["rotor_angle_rad"]
        assert session.state["io"]["foc_config"]["status"] & abi.STATUS_RUNNING

        # Samples since an index: increasing indexes, nothing new after the tail.
        samples = session.send(action="foc_state", since=0)[-1]
        assert samples["type"] == "foc_samples"
        indexes = [row[0] for row in samples["samples"]]
        assert indexes == list(range(len(indexes))) and len(indexes) >= 8
        assert samples["next_index"] == second["sample_index"]
        assert session.send(action="foc_state", since=samples["next_index"])[-1]["samples"] == []
        columns = dict(zip(samples["fields"], zip(*samples["samples"])))
        assert all(0 <= duty <= 1 for duty in columns["duty_a"])
        assert list(columns["time_s"]) == sorted(columns["time_s"])

        # Disable through the ABI and run on: the output returns to neutral.
        session.send(action="foc_enable", enable=False)
        session.run_until(second["pwm_periods"] + 3)
        stopped = session.motor()
        assert session.state["io"]["foc_config"]["status"] == 0
        assert stopped["duty_cycles"] == pytest.approx([0.5, 0.5, 0.5], abs=0.002)
        assert not session.errors


def test_motor_physics_change_live_and_bad_values_are_rejected(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        _start(session, speed_rpm=400, vd_pu=0.0, vq_pu=0.25, accel_rpm_s=300_000)
        session.run_until(3)

        session.send(action="foc_set_motor", parameters={"load_torque_nm": 0.4, "dc_bus_v": 24})
        assert not session.errors
        parameters = session.motor()["parameters"]
        assert (parameters["load_torque_nm"], parameters["dc_bus_v"]) == (0.4, 24)
        session.run_until(6)
        session.send(action="foc_set_motor", parameters={"flux_linkage_vs": 0.0})
        assert session.motor()["parameters"]["flux_linkage_vs"] == 0.0

        before = session.motor()["parameters"]
        for bad in ({"resistance_ohm": -1}, {"pole_pairs": 0}, {"dc_bus_v": float("nan")},
                    {"load_torque_nm": 1.0, "inductance_h": 0}):
            session.send(action="foc_set_motor", parameters=bad)
        assert len(session.errors) == 4
        assert all(error["tag"] == "motor" for error in session.errors)
        assert session.motor()["parameters"] == before, "rejected values change nothing"

        session.send(action="foc_set_reference", vq_pu=3)
        assert len(session.errors) == 5
        assert session.state["io"]["foc_config"]["vq_ref_q15"] == round(0.25 * 32768)


def test_breakpoints_and_step_back_work_with_the_motor_attached(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        _start(session, speed_rpm=400, vd_pu=0.0, vq_pu=0.25, accel_rpm_s=300_000)
        session.send(action="toggle_breakpoint", addr=session.state["labels"]["svgen"])
        # The ordinary run loop stops at the breakpoint, motor attached or not.
        while not session.state["at_breakpoint"]:
            session.send(action="run", max_steps=10_000)
        assert session.state["pc"] == session.state["labels"]["svgen"]
        session.send(action="clear_breakpoints")
        session.run_until(2)

        # Step-back restores the plant and its sample ring coherently.
        before = session.motor()
        ring_before = fresh_sim.device_bus.get_device("foc_motor").samples_since(0)
        session.send(action="step", count=1)
        session.send(action="step", count=1)
        assert session.motor()["cycles"] > before["cycles"]
        session.send(action="step_back")
        session.send(action="step_back")
        restored = session.motor()
        assert restored["cycles"] == before["cycles"]
        assert restored["phase_currents_a"] == before["phase_currents_a"]
        assert restored["sample_index"] == before["sample_index"]
        assert fresh_sim.device_bus.get_device("foc_motor").samples_since(0) == ring_before
        assert not session.errors


def test_firmware_counters_stay_instruction_accurate_during_a_session(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        _start(session, speed_rpm=100, vd_pu=0.0, vq_pu=0.1, accel_rpm_s=1000)
        session.run_until(3)
        counters = fresh_sim.cores["pru0"].counters
        assert counters.cycles == counters.instruction_count + counters.stall_cycles
        assert session.state["cycles"] == counters.cycles


@pytest.mark.parametrize("action", ["reset", "hard_reset"])
def test_normal_motor_reset_starts_a_new_sample_history(fresh_sim, action):
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        _start(session, speed_rpm=100, vd_pu=0.0, vq_pu=0.1, accel_rpm_s=1000)
        for _ in range(10):
            if session.motor()["pwm_periods"] >= 3:
                break
            session.send(action="run", max_steps=20_000)
        assert session.motor()["pwm_periods"] >= 3, session.state
        before = session.send(action="foc_state", since=0)[-1]
        assert before["next_index"] > 0
        session.send(action=action)
        assert session.motor()["sample_index"] == 0
        reset = session.send(action="foc_state", since=before["next_index"])[-1]
        assert reset["next_index"] == 0 and reset["samples"] == []
        session.send(action="foc_set_reference", enable=True, speed_rpm=100, vq_pu=0.1)
        for _ in range(10):
            if session.motor()["pwm_periods"] >= 3:
                break
            session.send(action="run", max_steps=20_000)
        assert session.motor()["pwm_periods"] >= 3, session.state
        resumed = session.send(action="foc_state", since=0)[-1]
        assert [row[0] for row in resumed["samples"]] == list(range(resumed["next_index"]))
        times = [row[1] for row in resumed["samples"]]
        assert times and all(a < b for a, b in zip(times, times[1:]))
        assert not session.errors


def test_overflowed_json_motor_clock_returns_validation_error_and_keeps_session_alive(fresh_sim):
    from starlette.websockets import WebSocketDisconnect
    from tests.test_server import _ActionWebSocket
    import ui.server as srv

    class RawWebSocket(_ActionWebSocket):
        async def receive_text(self):
            try:
                return next(self._actions)
            except StopIteration:
                raise WebSocketDisconnect(code=1000)

    before = fresh_sim.device_bus.snapshot()
    ws = RawWebSocket([
        '{"action":"device_attach","core":"pru0","profile":"foc_motor",'
        '"config":{"core_clock_hz":1e309}}',
        '{"action":"get_state","core":"pru0"}',
    ])
    asyncio.run(srv.websocket_endpoint(ws))
    assert [message["type"] for message in ws.sent] == ["error", "state", "state"]
    assert ws.sent[0]["tag"] == "device"
    assert "core_clock_hz" in ws.sent[0]["errors"][0]
    assert fresh_sim.device_bus.snapshot() == before


@pytest.mark.parametrize("pins,expected", [([0, 1], 3), ([0, 0], 0)])
def test_pending_gpio_pin_clicks_apply_against_current_state(fresh_sim, pins, expected):
    fresh_sim.set_gpio_drive_mask("pru0", 0)
    fresh_sim.cores["pru0"].registers.regs[30] = 0x12345
    with client.websocket_connect("/ws") as ws:
        for pin in pins:
            ws.send_json({"action": "set_gpio_drive_mask", "core": "pru0", "pin": pin})
        states = [ws.receive_json() for _ in pins]
        assert all(state["type"] == "state" for state in states)
        assert states[-1]["io"]["gpo_drive_mask"] == expected
    assert fresh_sim.cores["pru0"].io_port.gpo_drive_mask == expected
    assert fresh_sim.cores["pru0"].registers.regs[30] == 0x12345


def test_pending_gpio_owned_pin_rejection_does_not_lose_other_pin_intent(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        session.send(action="device_attach", profile="ssi_encoder")
        before = fresh_sim.device_bus.snapshot()["output_leases"]
        fresh_sim.cores["pru0"].registers.regs[30] = 0x12345
        for pin in (0, 5, 5, 6):
            ws.send_json({"action": "set_gpio_drive_mask", "core": "pru0", "pin": pin})
        error = ws.receive_json()
        assert error["type"] == "error" and error["tag"] == "gpio"
        states = [ws.receive_json() for _ in range(4)]
        assert states[-1]["io"]["gpo_drive_mask"] == 0x41
        assert fresh_sim.device_bus.snapshot()["output_leases"] == before
        assert fresh_sim.cores["pru0"].registers.regs[30] == 0x12345


@pytest.mark.parametrize("fields", [
    {"pin": True}, {"pin": -1}, {"pin": 20}, {"pin": 1.5}, {"pin": "1"},
    {"pin": None}, {"pin": 0, "mask": 1},
])
def test_invalid_gpio_pin_intent_preserves_mask_and_r30(fresh_sim, fields):
    from tests.test_server import _run_websocket_actions

    fresh_sim.set_gpio_drive_mask("pru0", 7)
    fresh_sim.cores["pru0"].registers.regs[30] = 0x12345
    messages = _run_websocket_actions([{"action": "set_gpio_drive_mask", **fields},
                                      {"action": "get_state"}])
    assert [message["type"] for message in messages] == ["error", "state", "state"]
    assert messages[0]["tag"] == "gpio"
    assert fresh_sim.cores["pru0"].io_port.gpo_drive_mask == 7
    assert fresh_sim.cores["pru0"].registers.regs[30] == 0x12345
