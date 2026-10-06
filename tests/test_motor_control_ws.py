"""Motor Control flow over a real dashboard WebSocket session."""
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
