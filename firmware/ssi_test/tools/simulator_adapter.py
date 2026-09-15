"""Adapter for running the SSI rebuild on the checked-in PRU simulator.

The adapter deliberately keeps the simulator boundary small.  The simulator
executes the actual assembly and owns GPIO wires, shared DRAM, memory stalls,
and the shared AM243x IEP timebase; this module only supplies deterministic
multi-core scheduling and records externally useful events.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Iterable


SSI_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_ROOT = SSI_ROOT.parents[1]
SIMULATOR_CONFIG = SIMULATOR_ROOT / "config" / "memory_am243x.cfg"

if str(SIMULATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(SIMULATOR_ROOT))

from simulator import Simulator  # noqa: E402


_REGION_BASE = {"dram0": 0x0000, "dram1": 0x2000}
_DEFAULT_ABI = {
    "SSI_PRU0_POSITION_OFF": 0x00,
    "SSI_PRU0_READY_OFF": 0x04,
    "SSI_PRU0_DONE_OFF": 0x08,
    "SSI_PRU0_FRAMES_OFF": 0x0C,
    "SSI_PRU0_RESYNCS_OFF": 0x10,
    "SSI_PRU0_ABORTS_OFF": 0x14,
    "SSI_PRU0_LAST_POSITION_OFF": 0x18,
    "SSI_PRU1_PERIOD_OFF": 0x00,
    "SSI_PRU1_FIRST_BOUNDARY_OFF": 0x04,
    "SSI_PRU1_GRID_READY_OFF": 0x08,
    "SSI_PRU1_ARM_READY_OFF": 0x0C,
    "SSI_PRU1_STOP_OFF": 0x10,
    "SSI_PRU1_TIMER_READY_OFF": 0x14,
    "SSI_PRU1_TIMER_DONE_OFF": 0x18,
    "SSI_PRU1_READER_READY_OFF": 0x1C,
    "SSI_PRU1_READER_DONE_OFF": 0x20,
    "SSI_PRU1_TIMER_MISSED_OFF": 0x24,
    "SSI_PRU1_TIMER_MAX_LATE_OFF": 0x28,
    "SSI_PRU1_TIMER_EMITTED_OFF": 0x2C,
    "SSI_PRU1_READER_FRAMES_OFF": 0x40,
    "SSI_PRU1_READER_RAW_LO_OFF": 0x44,
    "SSI_PRU1_READER_RAW_HI_OFF": 0x48,
    "SSI_PRU1_READER_POSITION_OFF": 0x4C,
    "SSI_PRU1_READER_ERRORS_OFF": 0x50,
    "SSI_PRU1_READER_ABORTS_OFF": 0x54,
}


def _source_text(source: str | Path) -> tuple[str, Path | None]:
    if isinstance(source, Path) or ("\n" not in str(source) and len(str(source)) < 4096):
        path = Path(source)
        if path.is_file():
            return path.read_text(encoding="utf-8"), path
    return str(source), None


class SsiSimulation:
    """Run the three actual SSI assembly images in simulated parallel time."""

    def __init__(
        self,
        profile: dict[str, int],
        *,
        include_dir: str | Path | None = None,
        record_events: bool = True,
        record_position_stores: bool = True,
    ):
        self.profile = profile
        self.record_events = bool(record_events)
        self.record_position_stores = bool(record_position_stores)
        self.abi = dict(_DEFAULT_ABI)
        self.abi.update(profile.get("abi", {}))
        self.include_dir = Path(include_dir or profile.get("_include_dir", SSI_ROOT / "include"))
        self.sim = Simulator(config_path=str(SIMULATOR_CONFIG))
        self._clock_wire = ("pru1", 0, "pru0", 8)
        self._data_wire = ("pru0", 0, "pru1", 16)
        self._clock_connected = False
        self._events: list[dict] = []
        self._pending_latches: deque[dict] = deque()
        self._active_frame_start: dict | None = None
        self._wall_cycle = 0
        self._order = ("rtu1", "pru1", "pru0")
        self._labels: dict[str, dict[str, int]] = {}
        self._source_paths: dict[str, Path] = {}

        # The generic AM243x simulator's IEP observer follows whichever core
        # has advanced furthest.  That is useful for independent-core tests,
        # but this harness has one shared 300 MHz slot timeline: a stalled or
        # unloaded core must not stop the physical IEP clock.  The harness
        # therefore advances the IEP once per scheduler slot below.
        for core in self.sim.cores.values():
            core._cycle_observer = None

    @property
    def cycle(self) -> int:
        """Return the low IEP word used as the event timestamp."""
        return int(self.sim.iep.count) & 0xFFFF_FFFF

    @property
    def wall_cycle(self) -> int:
        """Return the deterministic scheduler's elapsed 300 MHz cycles."""
        return self._wall_cycle

    @property
    def source_hashes(self) -> dict[str, str]:
        return {
            core: hashlib.sha256(path.read_bytes()).hexdigest()
            for core, path in self._source_paths.items()
        }

    def load(
        self,
        emulator: str | Path,
        reader: str | Path,
        timer: str | Path,
    ) -> None:
        """Load the emulator, reader, and RTU_PRU1 timer source files."""
        sources = {"pru0": emulator, "pru1": reader, "rtu1": timer}
        include_paths = [
            str(self.include_dir),
            str(Path(emulator).parent) if isinstance(emulator, Path) and Path(emulator).is_file() else str(SSI_ROOT / "pru0_ssi_emulator"),
            str(Path(reader).parent) if isinstance(reader, Path) and Path(reader).is_file() else str(SSI_ROOT / "pru1_ssi_reader"),
            str(Path(timer).parent) if isinstance(timer, Path) and Path(timer).is_file() else str(SSI_ROOT / "rtu1_tick"),
        ]
        self.sim.hard_reset()
        self._events.clear()
        self._pending_latches.clear()
        self._active_frame_start = None
        self._wall_cycle = 0
        self._labels.clear()
        self._source_paths.clear()
        for core, source in sources.items():
            text, path = _source_text(source)
            errors = self.sim.load(core, text, include_paths)
            if errors:
                raise ValueError(f"{core} assembly did not load: {errors}")
            if path is not None:
                self._source_paths[core] = path
            self._labels[core] = dict(self.sim.cores[core]._parser.labels)

        self.sim.add_gpio_wire(*self._clock_wire)
        self.sim.add_gpio_wire(*self._data_wire)
        self._clock_connected = True
        self._clear_mailboxes()

    def _clear_mailboxes(self) -> None:
        for region, size in (("dram0", 0x20), ("dram1", 0x58)):
            base = _REGION_BASE[region]
            self.sim.memory.write(base, bytes(size))

    def read32(self, region: str, offset: int) -> int:
        """Read one aligned word from the selected local DRAM region."""
        base = _REGION_BASE.get(region)
        if base is None:
            raise ValueError(f"unknown SSI memory region {region!r}")
        if offset < 0 or offset & 3:
            raise ValueError("SSI memory offsets must be non-negative and 4-byte aligned")
        return int.from_bytes(self.sim.memory_read(base + offset, 4), "little")

    def write32(self, region: str, offset: int, value: int) -> None:
        """Write one host word and record scalar position publications."""
        base = _REGION_BASE.get(region)
        if base is None:
            raise ValueError(f"unknown SSI memory region {region!r}")
        if offset < 0 or offset & 3:
            raise ValueError("SSI memory offsets must be non-negative and 4-byte aligned")
        value &= 0xFFFF_FFFF
        self.sim.memory.write(base + offset, value.to_bytes(4, "little"))
        if (
            self.record_events
            and self.record_position_stores
            and region == "dram0"
            and offset == self.abi["SSI_PRU0_POSITION_OFF"]
        ):
            self._record_event({
                "cycle": self.cycle,
                "kind": "position_store",
                "position": value,
            })

    def set_clock_connected(self, connected: bool) -> None:
        """Connect/disconnect the reader clock wire without changing data."""
        connected = bool(connected)
        if connected == self._clock_connected:
            return
        if connected:
            self.sim.add_gpio_wire(*self._clock_wire)
        else:
            self.sim.remove_gpio_wire(*self._clock_wire)
        self._clock_connected = connected

    def set_clock_level(self, level: int) -> None:
        """Drive the emulator clock input while the physical wire is open."""
        if self._clock_connected:
            raise RuntimeError("clock level override requires a disconnected clock wire")
        self.sim.set_input("pru0", 8, bool(level))

    def step(self, cycles: int = 1) -> None:
        """Advance all non-halted loaded cores through *cycles* global slots.

        A lowest-cycle-first scheduler preserves the simulator's instruction
        and memory-stall timing while ensuring that GPIO transitions are seen
        by the other core before it advances past the same physical interval.
        """
        if cycles < 0:
            raise ValueError("cycle count cannot be negative")
        for _ in range(cycles):
            target = self._wall_cycle + 1
            while True:
                active = [
                    name for name in self._order
                    if self.sim.cores[name].instructions
                    and not self.sim.cores[name].halted
                    and self.sim.cores[name].counters.cycles < target
                ]
                if not active:
                    break
                name = min(
                    active,
                    key=lambda core: (
                        self.sim.cores[core].counters.cycles,
                        self._order.index(core),
                    ),
                )
                self._step_core(name)
            self._wall_cycle = target
            self._advance_shared_iep()

    def _advance_shared_iep(self) -> None:
        """Advance the shared IEP by one physical scheduler slot."""
        if not self.sim.iep.enabled:
            return
        self.sim.iep.counter = (
            self.sim.iep.counter + self.sim.iep.default_increment
        ) & self.sim.iep.MASK

    def _step_core(self, name: str) -> None:
        core = self.sim.cores[name]
        before_gpo = core.io_port.gpo
        latch_pc = self._labels.get("pru0", {}).get("l_frame_start")
        publish_pc = self._labels.get("pru1", {}).get("l_reader_frame_published")
        period_pc = self._labels.get("rtu1", {}).get("l_period_store")
        is_latch = name == "pru0" and core.pc == latch_pc
        is_frame_publish = name == "pru1" and core.pc == publish_pc
        is_period_store = name == "rtu1" and core.pc == period_pc

        core.step()
        after_gpo = core.io_port.gpo
        if name == "pru1":
            self._record_wire_changes("clock", 0, before_gpo, after_gpo, core.counters.cycles)
        elif name == "pru0":
            self._record_wire_changes("data", 0, before_gpo, after_gpo, core.counters.cycles)

        if is_latch and not core.fault:
            # The source deliberately loads the scalar into r2 at this PC;
            # the following MOV copies it into the packing register r0.
            position = core.registers.read_full(2)
            event = {
                "cycle": self.cycle,
                "kind": "position_latch",
                "position": position,
                "core_cycle": core.counters.cycles,
            }
            if self.record_events:
                self._record_event(event)
                self._pending_latches.append(event)
                self._active_frame_start = event

        if is_frame_publish and not core.fault:
            raw_lo = core.registers.read_full(0)
            raw_hi = core.registers.read_full(1)
            latch = self._pending_latches.popleft() if self.record_events and self._pending_latches else None
            self._record_event({
                "cycle": self.cycle,
                "kind": "frame",
                "raw": (raw_hi << 32) | raw_lo,
                "latched_position": None if latch is None else latch["position"],
                "start_cycle": None if latch is None else latch["cycle"],
                "end_cycle": self.cycle,
                "raw_lo": raw_lo,
                "raw_hi": raw_hi,
            })
            if self.record_events:
                self._active_frame_start = None

        if is_period_store and not core.fault:
            self._record_event({
                "cycle": self.cycle,
                "kind": "period_store",
                "period": core.registers.read_full(4),
            })

    def _record_wire_changes(
        self, kind: str, pin: int, before: int, after: int, core_cycle: int
    ) -> None:
        if ((before ^ after) >> pin) & 1:
            self._record_event({
                "cycle": self.cycle,
                "core_cycle": core_cycle,
                "kind": kind,
                "pin": pin,
                "level": (after >> pin) & 1,
            })

    def _record_event(self, event: dict) -> None:
        if self.record_events:
            self._events.append(event)

    def events(self) -> list[dict]:
        """Return a copy of the event stream collected so far."""
        return [dict(event) for event in self._events]

    def status(self) -> dict:
        """Expose the underlying simulator status for failure diagnostics."""
        return self.sim.status()

    def simulator_revision(self) -> str:
        """Return the checked-out simulator commit, or ``unknown``."""
        try:
            return subprocess.run(
                ["git", "-C", str(SIMULATOR_ROOT), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return "unknown"


def firmware_paths() -> tuple[Path, Path, Path]:
    """Return the three source files used by both simulator and CCS builds."""
    return (
        SSI_ROOT / "pru0_ssi_emulator" / "pru0_main.asm",
        SSI_ROOT / "pru1_ssi_reader" / "pru1_main.asm",
        SSI_ROOT / "rtu1_tick" / "rtu_pru1_main.asm",
    )
