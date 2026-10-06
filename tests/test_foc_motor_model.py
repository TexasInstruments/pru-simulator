import math

import pytest

from pru_io.device_model import PUSH_PULL
from pru_io.device_profiles import create_device, discover_device_profiles
from pru_io.foc_motor_model import FocMotorModel
from simulator import Simulator


def test_foc_motor_is_discoverable_and_uses_push_pull_current_pins():
    profiles = discover_device_profiles()

    assert "foc_motor" in profiles
    assert profiles["foc_motor"]["outputs"] == {
        "current_a_pin": PUSH_PULL,
        "current_b_pin": PUSH_PULL,
    }
    model = create_device("foc_motor")
    assert isinstance(model, FocMotorModel)
    assert model.nets == {3: PUSH_PULL, 4: PUSH_PULL}
    assert model.pru_output_mask == 0x7
    assert model.core_clock_hz == 250_000_000


def test_foc_motor_current_outputs_follow_pwm_pins_and_have_no_shared_feedback():
    model = FocMotorModel()
    # R30 pins 0/1/2 are real phase outputs. Unequal duty changes the modeled
    # phase currents, which the model presents on GPI pins 3/4.
    for cycle in range(1, 1500):
        drive_mask, drive_values = model.tick(cycle, (1 << 0) | (1 << 1))

    assert drive_mask == (1 << 3) | (1 << 4)
    assert isinstance(drive_values, int)
    assert set(model.get_state()["outputs"]) == {"current_a_pin", "current_b_pin"}
    assert not hasattr(model, "feedback_address")
    assert model.faults() == []


def test_foc_motor_profile_rejects_non_pru0_attachment():
    from mcp_server.server import PRUSimulatorMCP

    mcp = PRUSimulatorMCP(config_path="nonexistent.cfg")
    with pytest.raises(ValueError, match="pru0"):
        mcp.pru_device_attach(profile="foc_motor", core="pru1")


def test_foc_motor_profile_rejects_invalid_fields():
    with pytest.raises(ValueError, match="unknown foc_motor config fields"):
        create_device("foc_motor", {"feedback_address": 0x10000})


def test_foc_motor_snapshot_restores_current_pdm_and_period_state():
    model = FocMotorModel()
    for cycle in range(1, 1500):
        model.tick(cycle, 0x3)
    snapshot = model.snapshot()

    expected_drive = model.tick(1500, 0x4)
    expected_state = model.get_state()

    model.restore(snapshot)
    assert model.tick(1500, 0x4) == expected_drive
    assert model.get_state() == expected_state


def test_sd_channel_clock_change_keeps_attached_foc_pdm_rate_aligned():
    sim = Simulator()
    model = create_device("foc_motor")
    sim.device_bus.attach(model, port="pru0")
    sim.cores["pru0"].io_port.sd_filter.route_input(0, model.current_a_pin)

    sim.set_sd_modulator("pru0", 0, sd_clock_mhz=12.5)

    assert model.current_a_clock_hz == 12_500_000
    assert model.current_b_clock_hz == 20_000_000
    assert sim.cores["pru0"].io_port.sd_filter.modulators[0].sd_clock_mhz == 12.5


@pytest.mark.parametrize("core_clock_mhz", [200, 250, 300])
@pytest.mark.parametrize("sample_clock_mhz", [10, 20, 25])
@pytest.mark.parametrize("phase_current_a,expected_range", [
    (0.0, (0.45, 0.55)),
    (10.0, (0.70, 0.80)),
    (-10.0, (0.20, 0.30)),
])
def test_foc_current_pdm_is_sampled_at_the_sd_clock(
        sim_config, core_clock_mhz, sample_clock_mhz, phase_current_a,
        expected_range):
    core_clock_hz = core_clock_mhz * 1_000_000
    sample_clock_hz = sample_clock_mhz * 1_000_000
    sim = Simulator(sim_config(
        pru_clock_mhz=core_clock_mhz,
        pru1_clock_mhz=core_clock_mhz,
    ))
    model = FocMotorModel(
        core_clock_hz=core_clock_hz,
        current_a_clock_hz=sample_clock_hz,
    )
    # A floating-neutral motor carries a balanced set: ia + ib + ic = 0.
    model.phase_currents_a = [phase_current_a, -phase_current_a / 2, -phase_current_a / 2]
    sim.attach_device("pru0", model)
    sim.set_gpio_drive_mask("pru0", 0x7)

    sd = sim.cores["pru0"].io_port.sd_filter
    sd.route_input(0, model.current_a_pin)
    sim.set_sd_modulator("pru0", 0, sd_clock_mhz=sample_clock_mhz)
    samples = []
    channel_tick = sd.channels[0].tick
    sd.channels[0].tick = lambda bit: (samples.append(bit), channel_tick(bit))[1]

    elapsed_cycles = core_clock_mhz * 20
    assert sim.load("pru0", "loop: NOP\nQBA loop\n") == []
    sim.step("pru0", count=elapsed_cycles)
    assert sim.cores["pru0"].counters.cycles == elapsed_cycles

    expected_samples = elapsed_cycles * sample_clock_hz // core_clock_hz
    assert abs(len(samples) - expected_samples) <= 1
    density = sum(samples) / len(samples)
    assert expected_range[0] <= density <= expected_range[1]


@pytest.mark.parametrize("channel,pin", [(0, 4), (1, 3), (2, 9)])
def test_routed_current_clock_follows_output_pin(channel, pin):
    from mcp_server.server import PRUSimulatorMCP
    mcp = PRUSimulatorMCP()
    mcp.pru_device_attach("foc_motor", config={"current_b_pin": 9} if pin == 9 else None)
    motor = mcp.sim.device_bus.devices[0]
    mcp.sim.set_sd_modulator("pru0", channel, sd_clock_mhz=10)
    mcp.pru_sd_route_input(channel, pin)
    sd = mcp.sim.cores["pru0"].io_port.sd_filter
    samples = []
    tick = sd.channels[channel].tick
    sd.channels[channel].tick = lambda bit: (samples.append(bit), tick(bit))[1]
    mcp.pru_load("loop: nop\nqba loop\n")
    mcp.pru_step(count=10000)
    assert motor.phase_currents_a == [0, 0, 0]
    assert 0.45 < sum(samples) / len(samples) < 0.55
    clock = motor.current_a_clock_hz if pin == 3 else motor.current_b_clock_hz
    assert clock == 10_000_000


def test_conflicting_consumers_rejected_without_mutating_route_or_clock():
    from mcp_server.server import PRUSimulatorMCP
    mcp = PRUSimulatorMCP()
    mcp.pru_device_attach("foc_motor")
    mcp.pru_sd_route_input(0, 3)
    mcp.sim.set_sd_modulator("pru0", 1, sd_clock_mhz=10)
    sd = mcp.sim.cores["pru0"].io_port.sd_filter
    with pytest.raises(ValueError, match="different clocks"):
        mcp.pru_sd_route_input(1, 3)
    assert sd.input_routes == [3, None, None]
    mcp.sim.set_sd_modulator("pru0", 1, sd_clock_mhz=20)
    mcp.pru_sd_route_input(1, 3)
    with pytest.raises(ValueError, match="different clocks"):
        mcp.sim.set_sd_modulator("pru0", 0, sd_clock_mhz=10)
    assert sd.modulators[0].sd_clock_mhz == 20


def test_unrouted_explicit_current_clock_is_preserved():
    from mcp_server.server import PRUSimulatorMCP
    mcp = PRUSimulatorMCP()
    mcp.pru_device_attach("foc_motor", config={"current_b_clock_hz": 10_000_000})
    assert mcp.sim.device_bus.devices[0].current_b_clock_hz == 10_000_000


def test_foc_profile_exposes_and_validates_physics_options():
    defaults = discover_device_profiles()["foc_motor"]["defaults"]
    assert {key: defaults[key] for key in (
        "resistance_ohm", "inductance_h", "flux_linkage_vs", "pole_pairs",
        "inertia_kg_m2", "damping_nm_s", "load_torque_nm", "dc_bus_v")} == {
        "resistance_ohm": 0.5, "inductance_h": 0.001, "flux_linkage_vs": 0.05,
        "pole_pairs": 4, "inertia_kg_m2": 0.0005, "damping_nm_s": 0.003,
        "load_torque_nm": 0.0, "dc_bus_v": 48.0}
    model = create_device("foc_motor", {"pole_pairs": 2, "flux_linkage_vs": 0.0,
                                        "load_torque_nm": 0.2, "dc_bus_v": 24})
    assert model.get_state()["parameters"]["pole_pairs"] == 2
    assert model.get_state()["model"] == "pmsm"
    for bad in ({"pole_pairs": 0}, {"inertia_kg_m2": 0}, {"dc_bus_v": -1},
                {"load_torque_nm": "heavy"}, {"flux_linkage_vs": -0.1}):
        with pytest.raises(ValueError):
            create_device("foc_motor", bad)


def test_mcp_attach_with_physics_state_fields_and_runtime_configure():
    from mcp_server.server import PRUSimulatorMCP
    mcp = PRUSimulatorMCP()
    attached = mcp.pru_device_attach("foc_motor", config={
        "pole_pairs": 2, "flux_linkage_vs": 0.08, "load_torque_nm": 0.1,
        "dc_bus_v": 24.0, "inertia_kg_m2": 0.001})
    assert attached["device"]["parameters"] == {
        "resistance_ohm": 0.5, "inductance_h": 0.001, "flux_linkage_vs": 0.08,
        "pole_pairs": 2, "inertia_kg_m2": 0.001, "damping_nm_s": 0.003,
        "load_torque_nm": 0.1, "dc_bus_v": 24.0, "reference_angle_rad": math.pi / 2}
    device = mcp.pru_device_state()["devices"][0]
    for key in ("rotor_angle_rad", "commanded_angle_rad", "angle_error_rad",
                "rotor_speed_rpm", "commanded_speed_rpm", "phase_currents_a",
                "id_a", "iq_a", "duty_cycles", "alpha_beta_v", "pwm_periods",
                "pwm_frequency_hz", "sample_index", "time_s"):
        assert key in device

    result = mcp.pru_device_configure("foc_motor", {"load_torque_nm": 0.25, "pole_pairs": 3})
    assert result["success"] and result["parameters"]["load_torque_nm"] == 0.25
    assert mcp.pru_device_state()["devices"][0]["parameters"]["pole_pairs"] == 3

    with pytest.raises(ValueError):
        mcp.pru_device_configure("foc_motor", {"load_torque_nm": 1.0, "pole_pairs": 0})
    assert mcp.pru_device_state()["devices"][0]["parameters"]["load_torque_nm"] == 0.25
    with pytest.raises(KeyError):
        mcp.pru_device_configure("missing", {"pole_pairs": 1})


def test_configure_is_rejected_by_devices_without_runtime_parameters():
    from mcp_server.server import PRUSimulatorMCP
    mcp = PRUSimulatorMCP()
    mcp.pru_device_attach("tca9538", core="pru1")
    with pytest.raises(ValueError, match="no run-time parameters"):
        mcp.pru_device_configure("tca9538", {"address": 1})


def ref_duties(theta_turns, vd, vq):
    from tests import foc_reference as ref
    return ref.svgen_duties(*ref.inverse_park(theta_turns, vd, vq))


def _firmware_system(garble_shared_ram=False):
    from pathlib import Path
    from mcp_server.server import PRUSimulatorMCP
    from pru_io import foc_control_abi as abi

    root = Path(__file__).parents[1]
    mcp = PRUSimulatorMCP(config_path=str(root / "memory.cfg"))
    mcp.pru_device_attach("foc_motor")
    mcp.sim.memory.write(abi.CONTROL_ADDRESS, abi.pack_config(
        enable=1, requested_generation=1, vq_ref_q15=6554,
        initial_phase_q32=round(0.3 * 2**32)))
    source = (root / "source" / "foc_open_loop.asm").read_text(encoding="utf-8")
    assert mcp.pru_load(source, include_paths=[str(root / "source")])["success"]
    if garble_shared_ram:
        control_end = abi.CONTROL_ADDRESS + abi.CONFIG_SIZE
        noise = bytes((index * 73 + 41) & 0xFF for index in range(0x10000))
        mcp.sim.memory.write(abi.SHARED_BASE, noise[:abi.CONTROL_OFFSET])
        mcp.sim.memory.write(control_end, noise[:abi.SHARED_BASE + 0x10000 - control_end])
    # Table generation (~24.6k cycles) plus four PWM periods.
    mcp.pru_step(count=23_600 + 4 * 11_000)
    return mcp, mcp.sim.device_bus.devices[0]


def test_firmware_pins_drive_the_plant_and_the_plant_reads_no_shared_memory():
    mcp, motor = _firmware_system()
    state = motor.get_state()
    assert state["pwm_periods"] >= 3
    assert state["pwm_frequency_hz"] == pytest.approx(16_000, rel=0.001)
    # Vq = 0.2 pu at 0.3 turns: duties and the commanded angle come from pins.
    expected = ref_duties(0.3, 0.0, 6554 / 32768)
    assert state["duty_cycles"] == pytest.approx(expected, abs=0.002)
    assert state["commanded_angle_rad"] == pytest.approx(2 * math.pi * 0.3, abs=0.01)
    assert math.remainder(state["rotor_angle_rad"], 2 * math.pi) == pytest.approx(0.0, abs=0.01)
    assert state["angle_error_rad"] == pytest.approx(2 * math.pi * 0.3, abs=0.01)
    assert any(abs(current) > 0.1 for current in state["phase_currents_a"])
    samples = motor.samples_since(0)
    assert [sample[0] for sample in samples["samples"]] == list(range(len(samples["samples"])))

    garbled_mcp, garbled = _firmware_system(garble_shared_ram=True)
    assert garbled.snapshot() == motor.snapshot()
    assert garbled_mcp.sim.cores["pru0"].registers.regs == mcp.sim.cores["pru0"].registers.regs
