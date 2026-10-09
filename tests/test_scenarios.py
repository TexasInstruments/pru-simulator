"""One-click scenarios leave a fresh Simulator functional with no manual setup."""
from math import ceil
from pathlib import Path

import pytest

from mcp_server.server import PRUSimulatorMCP
from pru_io import ssi_config_abi as abi
from pru_io.foc_motor_model import FocMotorModel
from pru_io.scenarios import SCENARIOS, apply_scenario
from simulator import Simulator

ROOT = Path(__file__).parents[1]
SINGLE, MULTI, FOC = SCENARIOS


def _new(config=None):
    config = config or str(ROOT / "memory.cfg")
    sim = Simulator(config)
    return sim, PRUSimulatorMCP(config_path=config, simulator=sim)


def _mailbox(sim):
    return abi.unpack_mailbox(sim.memory_read(abi.MAILBOX_ADDRESS, abi.MAILBOX_SIZE))


def _frames(sim, steps, step):
    """Run ``steps`` instructions and return every distinct frame the mailbox published."""
    seen = {}
    for _ in range(steps // 50):
        step(50)
        mailbox = _mailbox(sim)
        if mailbox["frame_count"]:
            seen[mailbox["frame_count"]] = mailbox["raw_frame_lo"]
    return seen


def test_scenarios_declare_their_metadata():
    assert SCENARIOS[SINGLE].cores == ("pru0",) and not SCENARIOS[SINGLE].multicore
    assert SCENARIOS[MULTI].cores == ("pru0", "pru1") and SCENARIOS[MULTI].multicore
    assert SCENARIOS[FOC].ui["view"] == "motor"
    assert all(scenario.lead in scenario.cores for scenario in SCENARIOS.values())


def test_unknown_scenario_is_a_value_error():
    sim, api = _new()
    with pytest.raises(ValueError, match="unknown scenario"):
        apply_scenario(sim, "nope", api)


@pytest.mark.parametrize("clock_mhz", [200, 300, 333])
def test_single_core_ssi_decodes_the_commanded_position(sim_config, clock_mhz):
    sim, api = _new(sim_config(pru_clock_mhz=clock_mhz, pru1_clock_mhz=clock_mhz))
    apply_scenario(sim, SINGLE, api)
    encoder = sim.device_bus.devices[0]

    frames = _frames(sim, 40_000, lambda count: sim.step("pru0", count))

    assert len(frames) >= 3
    assert {encoder.decode_frame(raw)[0] for raw in frames.values()} == {0xABC}
    encoder.set_position(0x123)
    frames = _frames(sim, 40_000, lambda count: sim.step("pru0", count))
    assert encoder.decode_frame(_mailbox(sim)["raw_frame_lo"])[0] == 0x123
    assert encoder.faults() == [] and sim.device_bus.faults() == []


def test_multicore_reader_receives_frames_from_the_emulator_core():
    sim, api = _new()
    apply_scenario(sim, MULTI, api)
    assert sim.device_bus.devices == []   # no host-side encoder: the emulator core is the slave

    frames = _frames(sim, 40_000, lambda count: sim.step_paced_many("pru0", ["pru1"], count))

    assert len(frames) >= 3
    assert set(frames.values()) == {0xABC}
    assert _mailbox(sim)["status"] == 0
    assert abi.unpack_emulator(sim.memory_read(abi.EMULATOR_ADDRESS, abi.EMULATOR_SIZE))["status"] == 0
    assert sim.cores["pru1"].counters.cycles > 0 and not sim.cores["pru1"].halted


def test_foc_scenario_produces_pwm_and_phase_currents():
    sim, api = _new()
    apply_scenario(sim, FOC, api)
    core = sim.cores["pru0"]
    motor = next(d for d in sim.device_bus.devices if isinstance(d, FocMotorModel))
    pwm_states = set()

    for _ in range(80_000):
        core.step()
        pwm_states.add(core.io_port.gpo & 7)

    state = motor.get_state()
    assert len(pwm_states) > 2
    assert state["pwm_periods"] >= 3
    assert any(abs(current) > 0.5 for current in state["phase_currents_a"])
    assert state["rotor_speed_rpm"] > 0
    assert core.io_port.sd_filter.input_routes[:2] == [3, 4]
    assert sim.device_bus.faults() == []


def test_scenarios_can_be_applied_one_after_another_on_the_same_simulator():
    sim, api = _new()
    for name in (FOC, SINGLE, MULTI, SINGLE, FOC):
        apply_scenario(sim, name, api)
    assert [d.name for d in sim.device_bus.devices] == ["foc_motor"]
    assert sim.list_gpio_wires() == []
    apply_scenario(sim, MULTI, api)
    assert sim.device_bus.devices == [] and len(sim.list_gpio_wires()) == 2
    frames = _frames(sim, 20_000, lambda count: sim.step_paced_many("pru0", ["pru1"], count))
    assert set(frames.values()) == {0xABC}


@pytest.mark.parametrize("name", [SINGLE, MULTI])
def test_ssi_scenarios_idle_the_clock_past_the_monoflop_between_frames(name):
    sim, api = _new()
    scenario = apply_scenario(sim, name, api)
    step = ((lambda: sim.step_paced_many("pru0", ["pru1"], 1)) if scenario.multicore
            else (lambda: sim.step("pru0", 1)))
    core = sim.cores["pru0"]
    monoflop = ceil(12.5e-6 * sim.iep.core_clock_hz("pru0"))
    falls, last = [], 1
    for _ in range(30_000):
        step()
        clock = core.io_port.gpo & 1
        if last and not clock:
            falls.append(core.counters.cycles)
        last = clock

    gaps = [b - a for a, b in zip(falls, falls[1:])]
    idles = [gap for gap in gaps if gap > monoflop]
    assert len(idles) >= 2 and max(gaps) < 2 * monoflop
    assert all(gap < monoflop for gap in gaps if gap not in idles)
    assert (len(falls) - 1 - len(idles)) // len(idles) in (12, 13)   # one fall per clock pulse

@pytest.mark.parametrize('name', [SINGLE, MULTI, FOC])
def test_scenarios_preserve_the_selected_iep_rate(name):
    sim, api = _new()
    sim.iep.set_clock_mhz(271.25)
    apply_scenario(sim, name, api)
    assert sim.iep.external_clock_hz == 271_250_000
    if name == FOC:
        from pru_io import foc_control, foc_control_abi
        cfg = foc_control_abi.unpack_config(sim.memory_read(
            foc_control_abi.CONTROL_ADDRESS, foc_control_abi.CONFIG_SIZE))
        cadence = 271_250_000 / 12500
        assert cfg['speed_ref_q28'] == foc_control.speed_rpm_to_q28(400, 4, update_hz=cadence)
        assert cfg['ramp_rate_q28'] == foc_control.ramp_rpm_s_to_q28(300_000, 4, update_hz=cadence)


def test_reloading_multicore_releases_the_old_emulator_and_leases():
    sim, api = _new()
    for name in (MULTI, MULTI, SINGLE, MULTI):
        apply_scenario(sim, name, api)
    assert len(sim.list_gpio_wires()) == 2
    frames = _frames(sim, 20_000, lambda n: sim.step_paced_many('pru0', ['pru1'], n))
    assert set(frames.values()) == {0xABC}
    assert sim.device_bus.faults() == []

@pytest.mark.parametrize('reader_mhz, encoder_mhz', [(333, 200), (200, 333)])
def test_multicore_demo_with_unequal_core_clocks(sim_config, reader_mhz, encoder_mhz):
    sim, api = _new(sim_config(pru_clock_mhz=reader_mhz, pru1_clock_mhz=encoder_mhz))
    apply_scenario(sim, MULTI, api)
    frames = _frames(sim, 40_000, lambda n: sim.step_paced_many('pru0', ['pru1'], n))
    assert len(frames) >= 3 and set(frames.values()) == {0xABC}
    assert sim.device_bus.faults() == []
