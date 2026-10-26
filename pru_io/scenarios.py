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

from pru_io import ssi_config_abi as ssi_abi
from pru_io.device_profiles import create_device
from pru_io.ssi_runtime import SSIEmulatorRuntime, SSIRuntime, load_reader

_SOURCE = Path(__file__).resolve().parents[1] / "source"
_READER_FIRMWARE = "ssi_generic_reader/ssi_generic_reader.asm"
_EMULATOR_FIRMWARE = "ssi_generic_emulator.asm"

_PRESET = "RM08_12BIT_4MHZ"
_POSITION = 0xABC
_CLOCK_DELAY_LOOPS = 20    # 4 MHz encoder limit holds from 200 to 333 MHz cores
_SSI_CONFIG_FIELDS = {"preset", "resolution", "position", "position_bits",
                      "error_bits", "error_value", "encoding"}
_PIN_MASK = (1 << 20) - 1
_CLOCK_PIN = 0
_DATA_PIN = 16
# The SSI signals toggle every ~45 instructions, so the graph must sample far
# more often than its default 1 in 100; 8192 samples span a frame plus its idle.
_SSI_UI = {"view": "io", "capture_stride": 2, "graph_window": 8192,
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


def _ssi_single_core(sim, device_api, encoder_options, clock_delay_loops) -> SSIRuntime:
    reader = SSIRuntime(sim, core="pru0", **encoder_options)
    try:
        reader.load(clock_delay_loops=clock_delay_loops)
    except Exception:
        reader.close()
        raise
    return reader


def _ssi_multicore(sim, device_api, encoder_options, clock_delay_loops) -> SSIEmulatorRuntime:
    emulator = SSIEmulatorRuntime(sim, core="pru1", **encoder_options)
    try:
        emulator.load()
        # Idle past the monoflop time, as SSIRuntime.load does for the single core.
        idle_delay_loops = ceil(emulator.layout.monoflop_cycles / 2) + 8
        load_reader(sim, "pru0", emulator.layout.resolution, clock_delay_loops,
                    idle_delay_loops)
        sim.add_gpio_wire("pru0", _CLOCK_PIN, "pru1", _CLOCK_PIN)
        sim.add_gpio_wire("pru1", _DATA_PIN, "pru0", _DATA_PIN)
        sim.set_gpio_drive_mask("pru0", _PIN_MASK & ~(1 << _DATA_PIN))
    except Exception:
        emulator.close()
        raise
    return emulator


SCENARIOS = {scenario.name: scenario for scenario in (
    Scenario(
        "SSI encoder (single core)",
        "SSI reader on PRU0 reading a configurable simulated encoder.",
        ("pru0",), "pru0", False, {"pru0": _READER_FIRMWARE}, _SSI_UI,
        _ssi_single_core),
    Scenario(
        "SSI multi-core (emulator + reader)",
        "SSI reader on PRU0 clocking frames out of the SSI emulator firmware on PRU1.",
        ("pru0", "pru1"), "pru0", True,
        {"pru0": _READER_FIRMWARE, "pru1": _EMULATOR_FIRMWARE},
        {**_SSI_UI, "graph_window": 4096},  # its graph panel is narrow: fewer samples, wider pulses
        _ssi_multicore),
)}


def apply_scenario(sim, name: str, device_api, config: dict | None = None) -> Scenario:
    """Validate SSI settings, then reset and set up the complete scenario.

    Omitting ``config`` retains the standard 12-bit, 0xABC example. An
    explicit config uses device-profile defaults, with ``preset=None`` for
    a custom frame. Pins and core timing belong to the scenario.
    """
    scenario = SCENARIOS.get(name) if isinstance(name, str) else None
    if scenario is None:
        raise ValueError(f"unknown scenario {name!r}; available: {list(SCENARIOS)}")
    if any(core not in sim.cores for core in scenario.cores):
        raise ValueError(f"scenario requires cores {list(scenario.cores)}")
    if config is None:
        config = {"preset": _PRESET, "position": _POSITION}
    if not isinstance(config, dict):
        raise ValueError("SSI scenario config must be an object")
    if any(not isinstance(key, str) for key in config):
        raise ValueError("SSI scenario config keys must be strings")
    unsupported = set(config) - _SSI_CONFIG_FIELDS
    if unsupported:
        raise ValueError(f"unsupported SSI scenario config fields: {sorted(unsupported)}")
    encoder_options = {**config, "clock_pin": _CLOCK_PIN, "data_pin": _DATA_PIN,
                       "core_clock_hz": sim.iep.core_clock_hz(scenario.lead)}
    layout = create_device("ssi_encoder", encoder_options)
    # Each half-period runs a two-instruction delay loop, plus firmware
    # overhead; ignoring that overhead conservatively bounds the clock rate.
    clock_delay_loops = max(_CLOCK_DELAY_LOOPS,
                            ceil(layout.core_clock_hz / (4 * layout.f_max_hz)))
    previous = getattr(sim, "_scenario_runtime", None)
    if previous is not None:
        previous.close()
    sim._scenario_runtime = None
    sim._scenario = None
    for device in list(sim.device_bus.devices):
        device_api.pru_device_detach(device.name)
    for wire in sim.list_gpio_wires():
        sim.remove_gpio_wire(**wire)
    sim.hard_reset()
    for core in sim.cores:
        # Examples own their GPIO setup; a normal reset keeps the UI's mux
        # and external UART stimulus, both incompatible with these SSI pins.
        sim.gpcfg_write(core, 0)
        sim.cores[core].io_port.uart_generator = None
        sim.set_gpio_drive_mask(core, _PIN_MASK)
        for group in range(5):
            sim.set_loopback(core, group, False)
    sim.memory.write(ssi_abi.CONFIG_ADDRESS, bytes(
        ssi_abi.EMULATOR_ADDRESS + ssi_abi.EMULATOR_SIZE - ssi_abi.CONFIG_ADDRESS))
    # Hardware reset clears IEP registers, keeping the selected external rate.
    sim._scenario_runtime = scenario.setup(sim, device_api, encoder_options,
                                           clock_delay_loops)
    sim._scenario = scenario
    return scenario


def ssi_demo_state(sim) -> dict | None:
    """Read the demo reader's mailbox without advancing either PRU.

    The dashboard executes and reads on one thread. An odd sequence still
    means the reader stopped partway through publication, so hide that frame.
    Runtime ownership stays with this simulator until the next example load.
    """
    scenario = getattr(sim, "_scenario", None)
    runtime = getattr(sim, "_scenario_runtime", None)
    if scenario is None or runtime is None:
        return None
    is_model = isinstance(runtime, SSIRuntime)
    if is_model and runtime.encoder not in sim.device_bus.devices:
        return None
    layout = runtime.encoder if is_model else runtime.layout
    mailbox = ssi_abi.unpack_mailbox(sim.memory_read(
        ssi_abi.MAILBOX_ADDRESS, ssi_abi.MAILBOX_SIZE))
    coherent = not mailbox["sequence"] & 1
    raw = mailbox["raw_frame_lo"] | mailbox["raw_frame_hi"] << 32
    received = coherent and mailbox["frame_count"] > 0
    position, error = layout.decode_frame(raw) if received else (None, None)
    emulator_status = None if is_model else runtime.status()
    return {"name": scenario.name, "reader_core": scenario.lead,
            "encoder_core": None if is_model else runtime.core,
            "requested_position": layout.position,
            "position_max": (1 << layout.position_bits) - 1,
            "frame_bits": layout.resolution, "error_bits": layout.error_bits,
            "position": position, "error": error,
            "raw_frame": f"0x{raw:03X}" if received else None,
            "frame_count": mailbox["frame_count"] if coherent else None,
            "coherent": coherent, "reader_status": mailbox["status"],
            "emulator_status": emulator_status,
            "timer_enabled": sim.iep.count_enabled}


def set_ssi_demo_position(sim, position: int) -> None:
    """Set the position latched by the next frame in either SSI demo."""
    if ssi_demo_state(sim) is None:
        raise ValueError("Load an SSI example before setting its position")
    runtime = sim._scenario_runtime
    if isinstance(runtime, SSIRuntime):
        runtime.encoder.set_position(position)
    else:
        runtime.set_position(position)
