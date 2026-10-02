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
    model.phase_currents_a[0] = phase_current_a
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
