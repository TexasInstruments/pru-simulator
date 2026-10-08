"""One-click example setups: devices, wiring, ABI blocks and firmware for a live Simulator.

``apply_scenario`` leaves the simulator ready to Run, with no manual
configuration. ``device_api`` is the ``PRUSimulatorMCP`` bound to the same
simulator; it owns device attach/detach so GPIO drive masks stay consistent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from pathlib import Path
from typing import Callable

from pru_io import foc_control, foc_control_abi
from pru_io import ssi_config_abi as ssi_abi
from pru_io.ssi_runtime import SSIEmulatorRuntime, SSIRuntime, load_reader

_SOURCE = Path(__file__).resolve().parents[1] / "source"
_FOC_FIRMWARE = "foc_open_loop.asm"
_READER_FIRMWARE = "ssi_generic_reader/ssi_generic_reader.asm"
_EMULATOR_FIRMWARE = "ssi_generic_emulator.asm"

_PRESET = "RM08_12BIT_4MHZ"
_POSITION = 0xABC
_CLOCK_DELAY_LOOPS = 20    # 4 MHz encoder limit holds from 200 to 333 MHz cores
_IEP_CLOCK_MHZ = 200       # the FOC PWM period is counted in IEP ticks
_PIN_MASK = (1 << 20) - 1
_CLOCK_PIN = 0
_DATA_PIN = 16
# The SSI signals toggle every ~45 instructions, so the graph must sample far
# more often than its default 1 in 100; 8192 samples span a frame plus its idle.
_SSI_UI = {"view": "simulator", "capture_stride": 2, "graph_window": 8192,
           "memory_addr": ssi_abi.MAILBOX_ADDRESS, "memory_length": ssi_abi.MAILBOX_SIZE}


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    cores: tuple[str, ...]
    lead: str
    multicore: bool
    firmware: dict[str, str]          # core -> path below source/
    ui: dict
    setup: Callable = field(repr=False, compare=False, default=None)

    def describe(self) -> dict:
        return {"name": self.name, "description": self.description,
                "cores": list(self.cores), "lead": self.lead,
                "multicore": self.multicore, "firmware": dict(self.firmware),
                "ui": dict(self.ui)}


def _ssi_single_core(sim, device_api) -> None:
    SSIRuntime(sim, core="pru0", position=_POSITION,
               preset=_PRESET).load(clock_delay_loops=_CLOCK_DELAY_LOOPS)


def _ssi_multicore(sim, device_api) -> None:
    emulator = SSIEmulatorRuntime(sim, core="pru1", position=_POSITION,
                                  preset=_PRESET,
                                  core_clock_hz=sim.iep.core_clock_hz("pru0"))
    emulator.load()
    # Idle past the monoflop time, as SSIRuntime.load does for the single core.
    idle_delay_loops = ceil(emulator.layout.monoflop_cycles / 2) + 8
    load_reader(sim, "pru0", emulator.layout.resolution, _CLOCK_DELAY_LOOPS,
                idle_delay_loops)
    sim.add_gpio_wire("pru0", _CLOCK_PIN, "pru1", _CLOCK_PIN)
    sim.add_gpio_wire("pru1", _DATA_PIN, "pru0", _DATA_PIN)
    sim.set_gpio_drive_mask("pru0", _PIN_MASK & ~(1 << _DATA_PIN))
    sim.set_gpio_drive_mask("pru1", _PIN_MASK & ~(1 << _CLOCK_PIN))


def _foc_motor(sim, device_api) -> None:
    motor = device_api.pru_device_attach(profile="foc_motor", core="pru0")
    pole_pairs = motor["device"]["parameters"]["pole_pairs"]
    sd = sim.cores["pru0"].io_port.sd_filter
    sd.route_inputs([3, 4] + sd.input_routes[2:])   # motor current pins -> SD0/SD1
    errors = sim.load("pru0", (_SOURCE / _FOC_FIRMWARE).read_text(encoding="utf-8"),
                      include_paths=[str(_SOURCE)])
    if errors:
        raise ValueError("FOC firmware assembly failed: " + "; ".join(errors))
    foc_control.stage_control(
        sim.memory, enable=1,
        speed_ref_q28=foc_control.speed_rpm_to_q28(400, pole_pairs),
        ramp_rate_q28=foc_control.ramp_rpm_s_to_q28(300_000, pole_pairs),
        vq_ref_q15=foc_control.voltage_pu_to_q15(0.25))


SCENARIOS = {scenario.name: scenario for scenario in (
    Scenario(
        "SSI encoder (single core)",
        "SSI reader on PRU0 reading a simulated 12-bit encoder at position 0xABC.",
        ("pru0",), "pru0", False, {"pru0": _READER_FIRMWARE}, _SSI_UI,
        _ssi_single_core),
    Scenario(
        "SSI multi-core (emulator + reader)",
        "SSI reader on PRU0 clocking frames out of the SSI emulator firmware on PRU1.",
        ("pru0", "pru1"), "pru0", True,
        {"pru0": _READER_FIRMWARE, "pru1": _EMULATOR_FIRMWARE},
        {**_SSI_UI, "graph_window": 4096},  # its graph panel is narrow: fewer samples, wider pulses
        _ssi_multicore),
    Scenario(
        "FOC motor (open loop)",
        "Open-loop FOC firmware on PRU0 driving a simulated PMSM at 400 rpm.",
        ("pru0",), "pru0", False, {"pru0": _FOC_FIRMWARE}, {"view": "motor"},
        _foc_motor),
)}


def apply_scenario(sim, name: str, device_api) -> Scenario:
    """Reset the simulator and set up scenario ``name``; raises ValueError if unknown."""
    scenario = SCENARIOS.get(name)
    if scenario is None:
        raise ValueError(f"unknown scenario {name!r}; available: {list(SCENARIOS)}")
    for device in list(sim.device_bus.devices):
        device_api.pru_device_detach(device.name)
    for wire in sim.list_gpio_wires():
        sim.remove_gpio_wire(**wire)
    sim.hard_reset()
    for core in sim.cores:
        sim.set_gpio_drive_mask(core, _PIN_MASK)
        for group in range(5):
            sim.set_loopback(core, group, False)
    sim.memory.write(ssi_abi.CONFIG_ADDRESS, bytes(
        ssi_abi.EMULATOR_ADDRESS + ssi_abi.EMULATOR_SIZE - ssi_abi.CONFIG_ADDRESS))
    sim.memory.write(foc_control_abi.CONTROL_ADDRESS,
                     bytes(foc_control_abi.CONFIG_SIZE))
    sim.iep.set_clock_mhz(_IEP_CLOCK_MHZ)
    scenario.setup(sim, device_api)
    return scenario
