"""Simulator-side tests for the open-loop FOC co-simulation contract."""

import math

from pru_io import foc_abi as abi
from pru_io.foc_motor_model import FocMotorModel
from pru_io.foc_runtime import FocRuntime
from simulator import Simulator


def _enable_iep(sim):
    sim.iep.write_iepclk(1)
    sim.iep.write_global_cfg(0x11)


def _write_rotating_pwm(sim, theta):
    """Write a hand-derived balanced voltage vector to the PWM ABI.

    Phase voltages use the conventional inverse-Clarke mapping shared by the
    firmware and the model.
    """
    amplitude = 0.35 * 48.0
    valpha = amplitude * math.cos(theta)
    vbeta = amplitude * math.sin(theta)
    va = valpha
    vb = -0.5 * valpha + (math.sqrt(3.0) / 2.0) * vbeta
    vc = -0.5 * valpha - (math.sqrt(3.0) / 2.0) * vbeta
    sim.memory.write(
        abi.PWM_OUT_BASE,
        abi.pack_pwm_out(
            seq=2,
            ta_q24=round((0.5 + va / 48.0) * abi.Q_ONE),
            tb_q24=round((0.5 + vb / 48.0) * abi.Q_ONE),
            tc_q24=round((0.5 + vc / 48.0) * abi.Q_ONE),
            valpha_q24=round(0.35 * math.cos(theta) * abi.Q_ONE),
            vbeta_q24=round(0.35 * math.sin(theta) * abi.Q_ONE),
            theta_cmd_u32=round((theta / (2.0 * math.pi)) * (1 << 32)) & 0xFFFFFFFF,
            loop_counter=1,
            timestamp_cycles=0,
        ),
    )


def test_motor_model_starts_with_a_coherent_zero_feedback_snapshot(nominal_config):
    sim = Simulator(nominal_config)
    model = FocMotorModel(sim)

    state = model.state()
    feedback = abi.unpack_motor_fb(sim.memory_read(abi.MOTOR_FB_BASE, abi.MOTOR_FB_SIZE))

    assert state["running"] is False
    assert feedback == {
        "seq": 0,
        "ia_q24": 0,
        "ib_q24": 0,
        "ic_q24": 0,
        "id_meas_q24": 0,
        "iq_meas_q24": 0,
        "rotor_theta_u32": 0,
        "speed_rpm_q24": 0,
        "timestamp": 0,
    }


def test_motor_model_spins_from_a_rotating_duty_set(nominal_config):
    sim = Simulator(nominal_config)
    _enable_iep(sim)
    model = FocMotorModel(sim)
    model.start()

    tick_hz = float(sim.iep.active_clock_hz)
    electrical_hz = 40.0
    step_ticks = 2_000  # 10 us at the configured 200 MHz IEP rate
    for index in range(1, 2501):
        timestamp = index * step_ticks
        theta = 2.0 * math.pi * electrical_hz * timestamp / tick_hz
        _write_rotating_pwm(sim, theta)
        model.advance_to(timestamp)

    state = model.state()

    assert state["rotor_theta_u32"] != 0
    assert 150.0 < state["speed_rpm"] < 900.0
    assert max(abs(state[name]) for name in ("ia", "ib", "ic")) < 100.0
    assert abs(state["ia"] + state["ib"] + state["ic"]) < 1e-6


def test_runtime_stages_q24_references_and_seeds_the_sine_lut(nominal_config):
    sim = Simulator(nominal_config)
    runtime = FocRuntime(sim)

    runtime.set_reference(speed=0.5, id=0.125, iq=0.25, ramp=0.02)
    control = abi.unpack_control(sim.memory_read(abi.CONTROL_BASE, abi.CONTROL_SIZE))
    first = int.from_bytes(sim.memory_read(abi.SINE_LUT_BASE, 4), "little")
    quarter = int.from_bytes(
        sim.memory_read(abi.SINE_LUT_BASE + 512 * 4, 4), "little"
    )

    assert control["enable"] == 0
    assert control["requested_generation"] == 1
    assert control["speed_ref_q24"] == 8_388_608
    assert control["id_ref_q24"] == 2_097_152
    assert control["iq_ref_q24"] == 4_194_304
    assert control["ramp_rate_q24"] == 335_544
    assert first == 0
    assert quarter == abi.Q_ONE


def test_runtime_start_and_stop_own_the_motor_observer(nominal_config):
    sim = Simulator(nominal_config)
    runtime = FocRuntime(sim)
    runtime.set_reference(speed=0.2, id=0.0, iq=0.2, ramp=0.01)

    runtime.start()
    started = abi.unpack_control(sim.memory_read(abi.CONTROL_BASE, abi.CONTROL_SIZE))
    assert started["enable"] == 1
    assert runtime.model.running is True
    assert sim.iep.observer_count == 1

    runtime.stop()
    stopped = abi.unpack_control(sim.memory_read(abi.CONTROL_BASE, abi.CONTROL_SIZE))
    assert stopped["enable"] == 0
    assert runtime.model.running is False
    assert sim.iep.observer_count == 0


def test_simulator_motor_attach_registers_model_reset_hook(nominal_config):
    sim = Simulator(nominal_config)

    model = sim.motor_attach()
    model._theta_mech = 1.0
    model.start()
    sim.hard_reset()

    assert sim.foc_motor_model is model
    assert model.running is False
    assert model.state()["rotor_theta_u32"] == 0
