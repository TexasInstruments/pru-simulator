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
from pru_io.spi_link import (ADDR_MAX, C_RX, C_TX, CMD_READ, CMD_RW, CMD_WRITE, P_RX, P_TX,
                             PATTERNS, SpiConfig, SpiLinkRuntime, controller_delay_ok,
                             frame_edges, load_supported, measure_timing,
                             run_test_sequence)
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


SPI_SCENARIO = "SPI multi-core (controller + peripheral)"
_SPI_CONFIG_FIELDS = {"mode", "spi_mhz", "miso_delay"}
_SPI_CONFIG_REQUIRED = {"mode", "spi_mhz"}
_SPI_DEFAULT = (1, 25.0)
_SPI_UI = {"view": "io", "io_tab": "spi", "capture_stride": 1, "graph_window": 4096}


def spi_row(mode, spi_mhz) -> dict:
    """The supported-rate row for (mode, spi_mhz); ValueError if none."""
    if isinstance(mode, bool) or mode not in (0, 1):
        raise ValueError(f"SPI mode must be 0 or 1, got {mode!r}")
    if (isinstance(spi_mhz, bool) or not isinstance(spi_mhz, (int, float))
            or spi_mhz != spi_mhz):
        raise ValueError(f"spi_mhz must be a number, got {spi_mhz!r}")
    rows = load_supported()[mode]
    for row in rows:
        if abs(row["spi_mhz"] - spi_mhz) < 5e-4:
            return row
    raise ValueError(f"mode {mode} supports no {spi_mhz} MHz SPI clock; supported: "
                     f"{[round(r['spi_mhz'], 3) for r in rows]}")


def spi_default_row() -> dict:
    """Ruling R3: mode 1 at 25 MHz, else the fastest supported mode-1 rate below it."""
    rows = [r for r in load_supported()[1] if r["spi_mhz"] <= _SPI_DEFAULT[1] + 5e-4]
    if not rows:
        raise ValueError("no supported mode-1 rate at or below 25 MHz")
    return rows[-1]


def spi_config_row(config: dict | None) -> dict:
    """The supported-rate row for an SPI scenario config, with ``miso_delay``
    replaced by the config's optional ``miso_delay`` (Ruling R17: any value
    ``controller_delay_ok`` accepts at the row's half period; default the
    row's chosen value). ValueError before anything changes."""
    if config is None:
        return spi_default_row()
    if not isinstance(config, dict):
        raise ValueError("SPI scenario config must be an object")
    if any(not isinstance(key, str) for key in config):
        raise ValueError("SPI scenario config keys must be strings")
    unsupported = set(config) - _SPI_CONFIG_FIELDS
    if unsupported:
        raise ValueError(f"unsupported SPI scenario config fields: {sorted(unsupported)}")
    if not _SPI_CONFIG_REQUIRED <= set(config):
        raise ValueError("SPI scenario config needs mode and spi_mhz")
    row = spi_row(config["mode"], config["spi_mhz"])
    delay = config.get("miso_delay")
    if delay is None:
        return row
    if not controller_delay_ok(row["half_k"], delay):
        raise ValueError(
            f"miso_delay {delay!r} is not realisable at {row['spi_mhz']:g} MHz "
            f"(k = {row['half_k']}): it must be a non-negative integer, not "
            f"0 or {row['half_k']} mod {2 * row['half_k']}, at most 6 bits pipelined")
    return {**row, "miso_delay": delay}


def spi_session_config(sim) -> dict | None:
    """The loaded SPI demo's config (mode, spi_mhz, miso_delay), for re-applying
    it (Ruling R21); None when the SPI scenario is not loaded."""
    runtime = getattr(sim, "_scenario_runtime", None)
    if not isinstance(runtime, SpiLinkRuntime):
        return None
    cfg = runtime.cfg
    return {"mode": cfg.mode, "spi_mhz": cfg.spi_mhz, "miso_delay": cfg.miso_delay}


def _spi_multicore(sim, device_api, row) -> SpiLinkRuntime:
    for core in ("pru0", "pru1"):
        if sim.cores[core].clock_mhz != row["core_mhz"]:
            raise ValueError(
                f"{row['spi_mhz']:g} MHz mode {row['mode']} needs a {row['core_mhz']} MHz core "
                f"clock; {core} runs at {sim.cores[core].clock_mhz:g} MHz")
    cfg = SpiConfig(row["mode"], row["core_mhz"], row["half_k"], row["miso_delay"])
    runtime = SpiLinkRuntime(sim, cfg)
    try:
        runtime.load_controller()
        runtime.load_peripheral()
        runtime.wire()
        names = list(PATTERNS)
        for block in range(ADDR_MAX + 1):
            runtime.preload_peripheral_tx(block, PATTERNS[names[block % len(names)]][1])
    except Exception:
        runtime.close()
        raise
    runtime.row = row            # supported-rate row (p1_fetch_cycles for the tIDLE row)
    runtime.last = None          # {"cmd", "addr", "pattern", "match"} of the last send
    runtime.sequence = None
    runtime._view = None         # cached (edge count, cfg id) -> frame/timing
    return runtime


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
    Scenario(
        SPI_SCENARIO,
        "Bit-banged SPI link: PRU0 controller drives PRU1 edge-polling peripheral.",
        ("pru0", "pru1"), "pru0", True,
        {"pru0": "spi/spi_controller.asm", "pru1": "spi/spi_peripheral.asm"},
        _SPI_UI, _spi_multicore),
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
    if name == SPI_SCENARIO:
        row = spi_config_row(config)           # validated before anything is torn down
        _clear_for_scenario(sim, device_api)
        sim._scenario_runtime = scenario.setup(sim, device_api, row)
        sim._scenario = scenario
        return scenario
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
    _clear_for_scenario(sim, device_api)
    sim.memory.write(ssi_abi.CONFIG_ADDRESS, bytes(
        ssi_abi.EMULATOR_ADDRESS + ssi_abi.EMULATOR_SIZE - ssi_abi.CONFIG_ADDRESS))
    # Hardware reset clears IEP registers, keeping the selected external rate.
    sim._scenario_runtime = scenario.setup(sim, device_api, encoder_options,
                                           clock_delay_loops)
    sim._scenario = scenario
    return scenario


def _clear_for_scenario(sim, device_api) -> None:
    """Close the previous scenario and leave a hard-reset simulator with the
    scenario GPIO setup (all 20 pins driven, no mux, no loopback)."""
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


def leave_spi_scenario(sim) -> None:
    """Close an SPI runtime (GPI delay 0, wires, masks, monitor) and drop the scenario."""
    runtime = getattr(sim, "_scenario_runtime", None)
    if isinstance(runtime, SpiLinkRuntime):
        runtime.close()
        sim._scenario_runtime = None
        sim._scenario = None


def _spi_runtime(sim) -> SpiLinkRuntime:
    runtime = getattr(sim, "_scenario_runtime", None)
    if not isinstance(runtime, SpiLinkRuntime):
        raise ValueError("Load the SPI example first")
    return runtime


def spi_send(sim, cmd, addr, pattern) -> None:
    """One frame: PRU1 TX block ``addr`` gets the pattern's peripheral bytes,
    PRU0 sends the pattern's controller bytes; records the result for the state."""
    runtime = _spi_runtime(sim)
    for name, v in (("cmd", cmd), ("addr", addr)):
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 0xFF:
            raise ValueError(f"{name} must be an integer 0..255, got {v!r}")
    if not isinstance(pattern, str) or pattern not in PATTERNS:
        raise ValueError(f"pattern must be one of {list(PATTERNS)}, got {pattern!r}")
    c_tx, p_tx = PATTERNS[pattern]
    if addr <= ADDR_MAX:
        runtime.preload_peripheral_tx(addr, p_tx)
    runtime.transfer(cmd, addr, c_tx)
    runtime.last = {"cmd": cmd, "addr": addr, "pattern": pattern}
    runtime.sequence = None


def spi_sequence(sim) -> None:
    """Run the spec section 9 acceptance sequence; the result goes into the state.

    It runs synchronously: the dashboard event loop stalls for about a second
    while it executes (about 25 frames of 20k simulated cycles each)."""
    runtime = _spi_runtime(sim)
    result = run_test_sequence(runtime)
    runtime.last = None
    runtime.sequence = result


def _hex(data) -> list[str]:
    return [f"{b:02X}" for b in data]


def _spi_view(runtime: SpiLinkRuntime) -> dict:
    """Frame (with its edges) and timing rows of the last complete frame, cached
    until the monitor sees a new edge or the configuration changes."""
    monitor = runtime.monitor
    key = (len(monitor.edges), runtime.cfg.id)
    if runtime._view is not None and runtime._view[0] == key:
        return runtime._view[1]
    frames = monitor.frames(runtime.cfg.mode)
    if frames:
        f = frames[-1]
        edges = frame_edges(monitor.edges, f)
        frame = {"start": f["start"], "end": f["end"], "bits": f["bits"],
                 "aborted": f["aborted"], "mosi": _hex(f["mosi"]),
                 "miso": _hex(f["miso"]), "edges": edges}
        timing = measure_timing(monitor.edges, runtime.cfg, hop_delay=runtime.hop_delay,
                                p1_fetch_cycles=runtime.row.get("p1_fetch_cycles"))
    else:
        frame = {"start": None, "end": None, "bits": 0, "aborted": False,
                 "mosi": [], "miso": [], "edges": []}
        timing = []
    view = {"frame": frame, "timing": timing}
    runtime._view = (key, view)
    return view


def spi_demo_state(sim) -> dict | None:
    """State of the SPI scenario for the dashboard; None when it is not loaded.

    Read it only between transfers: PRU1 writes its status block over several
    stores, so mid-frame the counters can be torn.
    """
    scenario = getattr(sim, "_scenario", None)
    runtime = getattr(sim, "_scenario_runtime", None)
    if scenario is None or not isinstance(runtime, SpiLinkRuntime) or runtime.monitor is None:
        return None
    cfg = runtime.cfg
    view = _spi_view(runtime)
    last = runtime.last
    addr = last["addr"] if last else 0
    memory = {"c_tx": _hex(sim.memory_read(C_TX, 16)), "c_rx": _hex(sim.memory_read(C_RX, 16)),
              "p_rx": None, "p_tx": None}
    match = {"mosi": None, "miso": None}
    if addr <= ADDR_MAX:
        p_rx = runtime.read_peripheral_rx(addr)
        p_tx = bytes(sim.memory_read(P_TX + 16 * addr, 16))
        memory["p_rx"], memory["p_tx"] = _hex(p_rx), _hex(p_tx)
        if last:
            c_tx = bytes(sim.memory_read(C_TX, 16))
            c_rx = bytes(sim.memory_read(C_RX, 16))
            match = {"mosi": sum(a == b for a, b in zip(c_tx, p_rx)),
                     "miso": sum(a == b for a, b in zip(p_tx, c_rx))}
    return {"name": scenario.name,
            "config": {"mode": cfg.mode, "spi_mhz": cfg.spi_mhz, "half_k": cfg.half_k,
                       "miso_delay": cfg.miso_delay, "core_mhz": cfg.core_mhz, "id": cfg.id},
            "core_mhz": cfg.core_mhz, "hop_delay": runtime.hop_delay,
            "last": last,
            "expect": ({"mosi": last["cmd"] in (CMD_WRITE, CMD_RW),
                        "miso": last["cmd"] in (CMD_READ, CMD_RW)} if last else None),
            "frame": view["frame"], "memory": memory, "match": match,
            "timing": view["timing"], "status": runtime.peripheral_status(),
            "faults": runtime.monitor.faults(), "sequence": runtime.sequence}


def ssi_demo_state(sim) -> dict | None:
    """Read the demo reader's mailbox without advancing either PRU.

    The dashboard executes and reads on one thread. An odd sequence still
    means the reader stopped partway through publication, so hide that frame.
    Runtime ownership stays with this simulator until the next example load.
    """
    scenario = getattr(sim, "_scenario", None)
    runtime = getattr(sim, "_scenario_runtime", None)
    if scenario is None or runtime is None or isinstance(runtime, SpiLinkRuntime):
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
