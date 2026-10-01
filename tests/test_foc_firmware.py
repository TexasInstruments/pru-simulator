"""PRU instruction level FOC firmware and physical pin regression tests."""

from pathlib import Path

import pytest

from mcp_server.server import PRUSimulatorMCP
from pru_io import foc_control_abi as abi


ROOT = Path(__file__).parents[1]
FIRMWARE = (ROOT / "source" / "foc_open_loop.asm").read_text(encoding="utf-8")
PHASE_BOUNDARIES_Q32 = (
    0x00000000,
    0x2AAAAAAB,
    0x55555555,
    0x80000000,
    0xAAAAAAAA,
    0xD5555555,
)
SECTOR_DUTIES = (
    (0.875, 0.125, 0.125),
    (0.875, 0.875, 0.125),
    (0.125, 0.875, 0.125),
    (0.125, 0.875, 0.875),
    (0.125, 0.125, 0.875),
    (0.875, 0.125, 0.875),
)


def _new_simulator(config: dict, config_path: str | None = None) -> PRUSimulatorMCP:
    mcp = PRUSimulatorMCP(config_path=config_path or str(ROOT / "memory.cfg"))
    mcp.sim.memory.write(abi.CONTROL_ADDRESS, abi.pack_config(**config))
    result = mcp.pru_load(FIRMWARE, include_paths=[str(ROOT / "source")])
    assert result["success"], result["errors"]
    return mcp


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


@pytest.mark.parametrize(
    ("config", "expected_duties"),
    [
        *[
            (
                {
                    "phase_increment_q32": 1,
                    "modulation_q15": 16384,
                    "initial_phase_q32": (boundary + 0x01000000 - 1) & 0xFFFFFFFF,
                },
                duties,
            )
            for boundary, duties in zip(PHASE_BOUNDARIES_Q32, SECTOR_DUTIES)
        ],
        ({"alpha_q15": 0, "beta_q15": 16384}, (0.5, 0.933013, 0.066987)),
        ({"alpha_q15": 0, "beta_q15": -16384}, (0.5, 0.066987, 0.933013)),
        ({"alpha_q15": -16384, "beta_q15": 0}, (0.125, 0.875, 0.875)),
        # Outside the linear modulation region, centered phases saturate at
        # sqrt(3)/4 while retaining enough low time for the next control update.
        ({"alpha_q15": 32767, "beta_q15": 0}, (0.933013, 0.066987, 0.066987)),
    ],
)
def test_foc_firmware_matches_independent_ti_svgen_vectors(
    tmp_path, config, expected_duties
):
    mcp = _new_simulator(config)
    path = tmp_path / "foc.vcd"
    result = mcp.pru_vcd_export(
        path=str(path), max_steps=20_000, pins="0-2", include_gpi=True
    )
    assert result["steps_executed"] == 20_000
    events = _vcd_events(path)
    rises = _edges(events["gpo_0"], 1)
    assert len(rises) >= 2
    assert rises[1] - rises[0] == pytest.approx(62_500_000, abs=50_000)
    for pin, expected in enumerate(expected_duties):
        rising, falling = _first_pulse(events[f"gpo_{pin}"])
        period_ps = 62_500_000
        observed = (falling - rising) / period_ps
        assert observed == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize(
    "config",
    [
        *[
            {
                "phase_increment_q32": 1,
                "modulation_q15": 16384,
                "initial_phase_q32": (boundary + 0x01000000 - 1) & 0xFFFFFFFF,
            }
            for boundary in PHASE_BOUNDARIES_Q32
        ],
        {"alpha_q15": 32767, "beta_q15": 0},
    ],
)
def test_foc_control_update_fits_measured_pwm_low_window(config, sim_config):
    mcp = _new_simulator(
        config,
        sim_config(pru_clock_mhz=250, pru1_clock_mhz=250, iep_clock_mhz=200),
    )
    core = mcp.sim.cores["pru0"]
    wait_period_pc = core._parser.labels["wait_period"]
    all_off_cycle = None
    all_off_stalls = None
    update_budget = None

    for _ in range(30_000):
        previous_gpo = core.io_port.gpo & 0x7
        core.step()
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
    assert update_stalls <= 12


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
        abi.pack_config(alpha_q15=16384, beta_q15=0),
    )
    result = mcp.pru_load(FIRMWARE, include_paths=[str(ROOT / "source")])
    assert result["success"], result["errors"]

    path = tmp_path / "foc-current.vcd"
    capture = mcp.pru_vcd_export(
        path=str(path), max_steps=35_000, pins="0-4", include_gpi=True
    )
    assert capture["steps_executed"] == 35_000
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
