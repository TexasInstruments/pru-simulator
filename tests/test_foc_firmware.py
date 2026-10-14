"""PRU instruction level FOC firmware and physical pin regression tests."""

import math
import struct
from pathlib import Path

import pytest

from mcp_server.server import PRUSimulatorMCP
from pru_io import foc_control_abi as abi
from tests import foc_reference as ref


ROOT = Path(__file__).parents[1]
FIRMWARE = (ROOT / "source" / "foc_open_loop.asm").read_text(encoding="utf-8")
TABLE_ADDRESS = 0x1000
DUTY_TOLERANCE = 0.002
RUN = {"enable": 1, "requested_generation": 1}


def _q15(value):
    return round(value * 32768)


def _new_simulator(config: dict, config_path: str | None = None) -> PRUSimulatorMCP:
    mcp = PRUSimulatorMCP(config_path=config_path or str(ROOT / "memory.cfg"))
    mcp.sim.memory.write(abi.CONTROL_ADDRESS, abi.pack_config(**config))
    result = mcp.pru_load(FIRMWARE, include_paths=[str(ROOT / "source")])
    assert result["success"], result["errors"]
    return mcp


def _control(mcp) -> dict:
    return abi.unpack_config(mcp.sim.memory_read(abi.CONTROL_ADDRESS, abi.CONFIG_SIZE))


def _poke(mcp, offset_from_control: int, value: int) -> None:
    mcp.sim.memory.write(abi.CONTROL_ADDRESS + offset_from_control,
                         struct.pack("<I", value & 0xFFFFFFFF))


def _capture_periods(mcp, periods: int, on_first_period=None) -> list[dict]:
    """Step the core and measure each PWM period from the real R30 pins."""
    core = mcp.sim.cores["pru0"]
    last = 0
    captured = []
    current = None
    while len(captured) < periods:
        core.step()
        pins = core.io_port.gpo & 7
        if pins == last:
            continue
        cycle = core.counters.cycles
        if not last and pins:
            if current is not None:
                current["length"] = cycle - current["start"]
                captured.append(current)
            elif on_first_period is not None:
                on_first_period()
            current = {"start": cycle, "fall": [None, None, None]}
        if current is not None:
            for phase in range(3):
                if (last >> phase) & 1 and not (pins >> phase) & 1:
                    current["fall"][phase] = cycle
        last = pins
    for period in captured:
        period["duties"] = tuple(
            (fall - period["start"]) / period["length"] for fall in period["fall"])
    return captured


@pytest.mark.parametrize(
    ("config", "expected_duties"),
    [
        # At angle zero, Vd/Vq are the alpha/beta inputs of TI SVGEN_runCom.
        ({"vd_ref_q15": 0, "vq_ref_q15": 16384}, (0.5, 0.933013, 0.066987)),
        ({"vd_ref_q15": 0, "vq_ref_q15": -16384}, (0.5, 0.066987, 0.933013)),
        ({"vd_ref_q15": 16384, "vq_ref_q15": 0}, (0.875, 0.125, 0.125)),
        ({"vd_ref_q15": -16384, "vq_ref_q15": 0}, (0.125, 0.875, 0.875)),
        ({"vd_ref_q15": 8192, "vq_ref_q15": 14189}, (0.875, 0.875, 0.125)),
        # Outside the linear modulation region, centered phases saturate at
        # sqrt(3)/4 while retaining enough low time for the next control update.
        ({"vd_ref_q15": 32767, "vq_ref_q15": 0}, (0.933013, 0.066987, 0.066987)),
    ],
)
def test_foc_firmware_matches_independent_ti_svgen_vectors(config, expected_duties):
    mcp = _new_simulator({**RUN, **config})
    periods = _capture_periods(mcp, 2)
    for period in periods:
        assert period["length"] == pytest.approx(15625, abs=12)
        assert period["duties"] == pytest.approx(expected_duties, abs=DUTY_TOLERANCE)
    status = _control(mcp)["status"]
    assert bool(status & abi.STATUS_SATURATED) == (config["vd_ref_q15"] == 32767)


@pytest.mark.parametrize(
    ("theta_turns", "vd", "vq"),
    [
        (0.0, 0.30, 0.0),
        (0.10, 0.25, 0.10),
        (0.30, 0.0, 0.25),
        (0.55, -0.20, 0.15),
        (0.80, 0.10, -0.30),
        (0.999, -0.35, -0.10),
    ],
)
def test_foc_inverse_park_matches_independent_reference(theta_turns, vd, vq):
    vd_q15, vq_q15 = _q15(vd), _q15(vq)
    mcp = _new_simulator({
        **RUN, "vd_ref_q15": vd_q15, "vq_ref_q15": vq_q15,
        "initial_phase_q32": round(theta_turns * 2**32),
    })
    expected = ref.svgen_duties(*ref.inverse_park(theta_turns, vd_q15 / 32768, vq_q15 / 32768))
    for period in _capture_periods(mcp, 2):
        assert period["duties"] == pytest.approx(expected, abs=DUTY_TOLERANCE)
        measured = ref.vector_angle_from_duties(period["duties"])
        wanted = 2 * math.pi * theta_turns + math.atan2(vq, vd)
        assert math.remainder(measured - wanted, 2 * math.pi) == pytest.approx(0, abs=0.01)


def test_foc_sine_table_is_generated_by_the_firmware():
    mcp = _new_simulator({**RUN})
    core = mcp.sim.cores["pru0"]
    control_update = core._parser.labels["control_update"]
    steps = 0
    while core.pc != control_update:
        core.step()
        steps += 1
        assert steps < 40_000, "firmware did not finish building the sine table"
    table = struct.unpack("<1024i", mcp.sim.memory_read(TABLE_ADDRESS, 4096))
    worst = max(abs(value - math.sin(2 * math.pi * index / 1024) * 32768)
                for index, value in enumerate(table))
    assert worst < 1.0
    assert (table[0], table[256], table[512], table[768]) == (0, 32768, 0, -32768)


@pytest.mark.parametrize(
    ("speed_pu", "ramp_pu", "vd", "vq"),
    [
        (0.5, 0.004, 0.0, 0.25),      # ramping for the whole capture
        (-0.5, 0.004, 0.1, 0.2),      # negative speed
        (0.03, 0.01, 0.0, 0.3),       # reaches the reference after 3 updates
    ],
)
def test_foc_angle_advances_at_the_ramped_speed(speed_pu, ramp_pu, vd, vq):
    updates = 30
    mcp = _new_simulator({
        **RUN, "speed_ref_q28": round(speed_pu * 2**28),
        "ramp_rate_q28": round(ramp_pu * 2**28),
        "vd_ref_q15": _q15(vd), "vq_ref_q15": _q15(vq),
    })
    periods = _capture_periods(mcp, updates)
    angles = ref.ramped_angle_sequence(
        updates, round(speed_pu * 2**28) / 2**28, round(ramp_pu * 2**28) / 2**28)
    for period, angle in zip(periods, angles):
        expected = ref.svgen_duties(*ref.inverse_park(angle, vd, vq))
        assert period["duties"] == pytest.approx(expected, abs=DUTY_TOLERANCE)
    measured = ref.vector_angle_from_duties(periods[-1]["duties"]) - math.atan2(vq, vd)
    assert math.remainder(measured - 2 * math.pi * angles[-1], 2 * math.pi) == pytest.approx(
        0, abs=0.01)
    assert abs(angles[-1] - angles[0]) > 0.001


def test_foc_disabled_output_is_neutral_and_holds_angle():
    mcp = _new_simulator({"requested_generation": 1, "vd_ref_q15": 16384,
                          "vq_ref_q15": 16384, "speed_ref_q28": 2**27})
    for period in _capture_periods(mcp, 3):
        assert period["duties"] == pytest.approx((0.5, 0.5, 0.5), abs=DUTY_TOLERANCE)
    control = _control(mcp)
    assert control["ack_generation"] == 1
    assert control["status"] == 0


def test_foc_generation_handshake_adopts_whole_blocks_only():
    mcp = _new_simulator({**RUN, "vq_ref_q15": _q15(0.25)})
    before = []

    def stage_next_generation():
        control = _control(mcp)
        assert control["ack_generation"] == 1
        assert control["status"] & abi.STATUS_RUNNING
        # A changed field without a new generation must not be adopted.
        _poke(mcp, abi.VD_REF_Q15_OFFSET, _q15(0.3))
        before.append(True)

    periods = _capture_periods(mcp, 3, on_first_period=stage_next_generation)
    assert before
    for period in periods:
        assert period["duties"] == pytest.approx(
            ref.svgen_duties(*ref.inverse_park(0.0, 0.0, 0.25)), abs=DUTY_TOLERANCE)

    _poke(mcp, abi.REQUESTED_GENERATION_OFFSET, 2)
    adopted = _capture_periods(mcp, 3)
    assert _control(mcp)["ack_generation"] == 2
    assert adopted[-1]["duties"] == pytest.approx(
        ref.svgen_duties(*ref.inverse_park(0.0, 0.3, 0.25)), abs=DUTY_TOLERANCE)


@pytest.mark.parametrize(
    ("offset", "value"),
    [
        (abi.ABI_VERSION_OFFSET, 1),
        (abi.CONTROL_PERIOD_TICKS_OFFSET, 12501),
        (abi.PWM_PERIOD_TICKS_OFFSET, 100),
        (abi.ENABLE_OFFSET, 2),
        (abi.SPEED_REF_Q28_OFFSET, 2**28 + 1),
        (abi.SPEED_REF_Q28_OFFSET, -2**28 - 1),
        (abi.RAMP_RATE_Q28_OFFSET, 2**28 + 1),
        (abi.VD_REF_Q15_OFFSET, 32768),
        (abi.VQ_REF_Q15_OFFSET, -32769),
    ],
)
def test_foc_invalid_config_is_neutral_acknowledged_and_recoverable(offset, value):
    mcp = _new_simulator({**RUN, "vq_ref_q15": _q15(0.25)})
    mcp.sim.memory.write(abi.CONTROL_ADDRESS + offset, struct.pack("<I", value & 0xFFFFFFFF))
    for period in _capture_periods(mcp, 2):
        assert period["duties"] == pytest.approx((0.5, 0.5, 0.5), abs=DUTY_TOLERANCE)
    control = _control(mcp)
    assert control["ack_generation"] == 1
    assert control["status"] & abi.STATUS_INVALID_CONFIG
    assert not control["status"] & abi.STATUS_RUNNING

    mcp.sim.memory.write(abi.CONTROL_ADDRESS, abi.pack_config(
        enable=1, requested_generation=2, vq_ref_q15=_q15(0.25)))
    recovered = _capture_periods(mcp, 3)[-1]
    assert recovered["duties"] == pytest.approx(
        ref.svgen_duties(*ref.inverse_park(0.0, 0.0, 0.25)), abs=DUTY_TOLERANCE)
    assert _control(mcp)["status"] == abi.STATUS_RUNNING


@pytest.mark.parametrize(
    ("config", "adopt"),
    [
        ({**RUN, "vd_ref_q15": 16384, "vq_ref_q15": -9000,
          "speed_ref_q28": -(1 << 27), "ramp_rate_q28": 80000}, None),
        ({**RUN, "vd_ref_q15": 32767, "vq_ref_q15": 32767,
          "speed_ref_q28": 1 << 28, "ramp_rate_q28": 1 << 28}, None),
        ({"requested_generation": 1}, None),
        # Worst case: the update that adopts a new generation.
        ({**RUN, "vd_ref_q15": 16384},
         {"enable": 1, "requested_generation": 2, "vd_ref_q15": -32768,
          "vq_ref_q15": 32767, "speed_ref_q28": -(1 << 28), "ramp_rate_q28": 7}),
        ({**RUN, "vd_ref_q15": 16384}, {"enable": 1, "requested_generation": 2}),
    ],
)
def test_foc_control_update_fits_measured_pwm_low_window(config, adopt, sim_config):
    mcp = _new_simulator(
        config,
        sim_config(pru_clock_mhz=250, pru1_clock_mhz=250, iep_clock_mhz=200),
    )
    core = mcp.sim.cores["pru0"]
    wait_period_pc = core._parser.labels["wait_period"]
    all_off_cycle = None
    all_off_stalls = None
    update_budget = None
    started = False

    for _ in range(60_000):
        previous_gpo = core.io_port.gpo & 0x7
        core.step()
        if not started and core.io_port.gpo & 0x7:
            started = True
            if adopt is not None:
                mcp.sim.memory.write(abi.CONTROL_ADDRESS, abi.pack_config(**adopt))
        if previous_gpo and not (core.io_port.gpo & 0x7):
            all_off_cycle = core.counters.cycles
            all_off_stalls = core.counters.stall_cycles
        if all_off_cycle is not None and core.pc == wait_period_pc:
            update_budget = (
                core.counters.cycles - all_off_cycle,
                core.counters.stall_cycles - all_off_stalls,
            )
            break

    assert update_budget is not None, "firmware did not reach the next-period wait"
    update_cycles, update_stalls = update_budget
    max_compare_ticks = 6250 + (14189 * 12500 // 32768)
    available_low_cycles = (12500 - max_compare_ticks) * 250 // 200
    assert update_cycles < available_low_cycles // 2
    assert update_stalls <= 24


def test_foc_run_keeps_core_cycles_equal_to_instructions_plus_stalls():
    mcp = PRUSimulatorMCP(config_path=str(ROOT / "memory.cfg"))
    assert mcp.pru_device_attach(profile="foc_motor")["success"]
    mcp.sim.memory.write(abi.CONTROL_ADDRESS, abi.pack_config(
        **RUN, vq_ref_q15=_q15(0.2), speed_ref_q28=2**26, ramp_rate_q28=2**20))
    assert mcp.pru_load(FIRMWARE, include_paths=[str(ROOT / "source")])["success"]
    mcp.pru_step(count=40_000)
    counters = mcp.sim.cores["pru0"].counters
    assert counters.cycles == counters.instruction_count + counters.stall_cycles
    assert counters.instruction_count == 40_000
    assert mcp.sim.device_bus.devices[0].get_state()["pwm_periods"] >= 1


def test_foc_pwm_period_and_pin_coupled_current_sd_route(tmp_path, monkeypatch):
    mcp = PRUSimulatorMCP(config_path=str(ROOT / "memory.cfg"))
    attached = mcp.pru_device_attach(profile="foc_motor")
    assert attached["success"]
    mcp.pru_sd_route_input(channel=0, pin=3)
    mcp.pru_sd_route_input(channel=1, pin=4)
    sd = mcp.sim.cores["pru0"].io_port.sd_filter

    def internal_modulator_used():
        raise AssertionError("routed SD channels must sample the physical GPI pins")

    monkeypatch.setattr(sd.modulators[0], "next_bit", internal_modulator_used)
    monkeypatch.setattr(sd.modulators[1], "next_bit", internal_modulator_used)
    mcp.sim.memory.write(
        abi.CONTROL_ADDRESS,
        abi.pack_config(**RUN, vd_ref_q15=16384),
    )
    result = mcp.pru_load(FIRMWARE, include_paths=[str(ROOT / "source")])
    assert result["success"], result["errors"]

    path = tmp_path / "foc-current.vcd"
    capture = mcp.pru_vcd_export(
        path=str(path), max_steps=60_000, pins="0-4", include_gpi=True
    )
    assert capture["steps_executed"] == 60_000
    events = _vcd_events(path)
    rises = _edges(events["gpo_0"], 1)
    assert len(rises) >= 2
    assert rises[1] - rises[0] == pytest.approx(62_500_000, abs=50_000)
    assert len(events["gpi_3"]) > 1
    assert len(events["gpi_4"]) > 1
    assert sd.channels[0].acc1 > 0
    assert sd.channels[1].acc1 > 0
    assert sd.get_state()["input_routes"][:2] == [3, 4]
    assert mcp.sim.device_bus.faults() == []
    motor = mcp.sim.device_bus.devices[0]
    assert motor.get_state()["pwm_periods"] >= 2


def test_foc_pwm_period_survives_iep_count_reg0_wrap(tmp_path):
    mcp = _new_simulator({**RUN, "vd_ref_q15": 16384})
    mcp.sim.iep.count = 0xFFFF_FF00

    path = tmp_path / "foc-wrap.vcd"
    capture = mcp.pru_vcd_export(
        path=str(path), max_steps=45_000, pins="0-2", include_gpi=True
    )

    assert capture["steps_executed"] == 45_000
    events = _vcd_events(path)
    rises = _edges(events["gpo_0"], 1)
    assert len(rises) >= 2
    assert rises[1] - rises[0] == pytest.approx(62_500_000, abs=50_000)
    assert _first_pulse(events["gpo_0"])[1] - rises[0] == pytest.approx(
        0.875 * 62_500_000, abs=625_000
    )


def _vcd_events(path: Path) -> dict[str, list[tuple[int, int]]]:
    names_by_identifier = {}
    events: dict[str, list[tuple[int, int]]] = {}
    timestamp_ps = 0
    for line in path.read_text(encoding="ascii").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "$var":
            names_by_identifier[parts[3]] = parts[4]
            events[parts[4]] = []
        elif line.startswith("#"):
            timestamp_ps = int(line[1:])
        elif len(line) >= 2 and line[0] in "01":
            name = names_by_identifier.get(line[1:])
            if name is not None:
                value = int(line[0])
                if not events[name] or events[name][-1][1] != value:
                    events[name].append((timestamp_ps, value))
    return events


def _edges(events: list[tuple[int, int]], edge_value: int) -> list[int]:
    return [
        timestamp
        for (previous_time, previous), (timestamp, value) in zip(events, events[1:])
        if previous == 1 - edge_value and value == edge_value
    ]


def _first_pulse(events: list[tuple[int, int]]) -> tuple[int, int]:
    rises = _edges(events, 1)
    assert rises, "firmware did not raise the PWM output"
    fallings = [timestamp for timestamp, value in events if value == 0]
    falling = next(timestamp for timestamp in fallings if timestamp > rises[0])
    return rises[0], falling


@pytest.mark.parametrize("core_mhz, iep_mhz, core_source, period_cycles", [
    (250, 200, False, 15625),
    (250, 250, False, 12500),
    (300, 200, False, 18750),
    (333.333, 250, False, 16666.65),
    (250, 300, True, 12500),
])
def test_foc_physical_period_uses_the_selected_iep_source(
        tmp_path, core_mhz, iep_mhz, core_source, period_cycles):
    config = tmp_path / "clock.cfg"
    config.write_text((ROOT / "memory.cfg").read_text().replace(
        "pru_clock_mhz = 250", f"pru_clock_mhz = {core_mhz}").replace(
        "[device]", f"[device]\niep_clock_mhz = {iep_mhz}"))
    mcp = _new_simulator({**RUN, "vd_ref_q15": 8192}, str(config))
    mcp.sim.iep.write_iepclk(int(core_source))
    for period in _capture_periods(mcp, 2):
        assert period["length"] == pytest.approx(period_cycles, abs=12)
        assert period["duties"] == pytest.approx((0.6875, 0.3125, 0.3125), abs=DUTY_TOLERANCE)
    core = mcp.sim.cores["pru0"]
    assert core.counters.cycles == core.counters.instruction_count + core.counters.stall_cycles
