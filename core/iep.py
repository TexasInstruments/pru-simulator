"""Deterministic AM243x ICSSG IEP0 counter and register windows."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import gcd, lcm
from typing import Callable

from mem.regions import MemoryRegion


def _mhz_to_hz(clock_mhz: float | str) -> Fraction:
    """Convert a configured MHz value without introducing float drift."""
    return Fraction(str(clock_mhz)) * 1_000_000


@dataclass
class _CoreTimeline:
    cycle_units: int
    base_cycles: int = 0
    base_time_units: int = 0
    last_cycles: int = 0


class IEPTimebase:
    """One shared 64-bit IEP counter driven by deterministic core time."""

    MASK = (1 << 64) - 1
    GLOBAL_CFG_RESET = 0x00000550
    IEPCLK_RESET = 0x00000000
    IEPCLK_OCP_EN = 1 << 0

    def __init__(
        self,
        *,
        external_clock_mhz: float,
        ocp_clock_mhz: float,
        core_clocks_mhz: dict[str, float],
    ):
        self.external_clock_hz = _mhz_to_hz(external_clock_mhz)
        self.ocp_clock_hz = _mhz_to_hz(ocp_clock_mhz)
        core_clocks_hz = {
            name: _mhz_to_hz(clock_mhz)
            for name, clock_mhz in core_clocks_mhz.items()
        }
        # Time is represented as integer multiples of 1/_time_units_per_second.
        # The LCM makes every configured PRU cycle an exact integer number of
        # units, including rational clock rates, without per-instruction objects.
        self._time_units_per_second = lcm(
            *(clock_hz.numerator for clock_hz in core_clocks_hz.values())
        )
        self._core_timelines = {
            name: _CoreTimeline(
                clock_hz.denominator
                * (self._time_units_per_second // clock_hz.numerator)
            )
            for name, clock_hz in core_clocks_hz.items()
        }
        self.counter = 0
        self.global_cfg = self.GLOBAL_CFG_RESET
        self.iepclk = self.IEPCLK_RESET
        self.latched_high = 0
        self._global_time_units = 0
        self._tick_numerator = 0
        self._tick_denominator = 1
        self._ticks_per_time_unit = 0
        self._tick_remainder = 0
        self._counter_observers: list[Callable[[int], None]] = []
        self._refresh_tick_rate()

    @property
    def count(self) -> int:
        return self.counter

    @property
    def enabled(self) -> bool:
        return bool(self.global_cfg & 1)

    @property
    def default_increment(self) -> int:
        return (self.global_cfg >> 4) & 0xF

    @property
    def ocp_enabled(self) -> bool:
        return bool(self.iepclk & self.IEPCLK_OCP_EN)

    @property
    def active_clock_hz(self) -> Fraction:
        return self.ocp_clock_hz if self.ocp_enabled else self.external_clock_hz

    def core_clock_hz(self, core: str) -> Fraction:
        """Return the configured PRU clock represented by *core*'s timeline."""
        timeline = self._core_timelines[core]
        return Fraction(self._time_units_per_second, timeline.cycle_units)

    @property
    def observer_count(self) -> int:
        """Number of callbacks notified when the counter advances."""
        return len(self._counter_observers)

    def add_counter_observer(self, callback: Callable[[int], None]) -> None:
        """Register a callback for counter advances.

        The empty-observer path is deliberately checked in
        :meth:`observe_core_cycles`, so a stopped producer does not add a
        per-instruction callback cost to the simulator.
        """
        if not callable(callback):
            raise TypeError("counter observer must be callable")
        if callback not in self._counter_observers:
            self._counter_observers.append(callback)

    def remove_counter_observer(self, callback: Callable[[int], None]) -> None:
        """Remove a previously registered counter observer."""
        try:
            self._counter_observers.remove(callback)
        except ValueError:
            pass

    def observe_core_cycles(self, core: str, cycles: int) -> None:
        """Advance from a core's cumulative cycles without double counting peers."""
        if cycles < 0:
            raise ValueError("PRU cycle counters cannot be negative")
        timeline = self._core_timelines[core]
        if cycles < timeline.last_cycles:
            # A caller reset the core directly. Treat cycle zero as occurring at
            # the current global instant so the first post-reset cycle counts.
            timeline.base_cycles = 0
            timeline.base_time_units = self._global_time_units
        timeline.last_cycles = cycles
        core_time_units = timeline.base_time_units + (
            cycles - timeline.base_cycles
        ) * timeline.cycle_units
        if core_time_units <= self._global_time_units:
            return

        elapsed_units = core_time_units - self._global_time_units
        self._global_time_units = core_time_units
        if self._tick_numerator == 0:
            return
        if self._ticks_per_time_unit:
            whole_ticks = elapsed_units * self._ticks_per_time_unit
        else:
            whole_ticks, self._tick_remainder = divmod(
                self._tick_remainder + elapsed_units * self._tick_numerator,
                self._tick_denominator,
            )
        self.counter = (self.counter + whole_ticks) & self.MASK
        if whole_ticks and self._counter_observers:
            for callback in tuple(self._counter_observers):
                callback(self.counter)

    def _refresh_tick_rate(self) -> None:
        increment = self.default_increment
        if not self.enabled or increment == 0:
            self._tick_numerator = 0
            self._tick_denominator = 1
            self._ticks_per_time_unit = 0
            return

        active_clock = self.active_clock_hz
        numerator = active_clock.numerator * increment
        denominator = active_clock.denominator * self._time_units_per_second
        divisor = gcd(numerator, denominator)
        numerator //= divisor
        denominator //= divisor
        self._tick_numerator = numerator
        self._tick_denominator = denominator
        self._ticks_per_time_unit = (
            numerator // denominator if numerator % denominator == 0 else 0
        )

    def rebase_core(self, core: str, cycles: int = 0) -> None:
        """Place a reset core at the current global instant."""
        timeline = self._core_timelines[core]
        timeline.base_cycles = cycles
        timeline.last_cycles = cycles
        timeline.base_time_units = self._global_time_units

    def write_global_cfg(self, value: int) -> None:
        self.global_cfg = value & 0xFFFF_FFFF
        self._tick_remainder = 0
        self._refresh_tick_rate()

    def write_iepclk(self, value: int) -> None:
        self.iepclk = value & 0xFFFF_FFFF
        self._tick_remainder = 0
        self._refresh_tick_rate()

    def write_count(self, *, low: int | None = None, high: int | None = None) -> None:
        current_low = self.counter & 0xFFFF_FFFF
        current_high = (self.counter >> 32) & 0xFFFF_FFFF
        if low is not None:
            current_low = low & 0xFFFF_FFFF
        if high is not None:
            current_high = high & 0xFFFF_FFFF
        self.counter = ((current_high << 32) | current_low) & self.MASK
        self._tick_remainder = 0

    def latch_high(self, snapshot: int) -> None:
        self.latched_high = (snapshot >> 32) & 0xFFFF_FFFF

    def hardware_reset(self) -> None:
        """Restore IEP/IEPCLK reset values and a zeroed global timeline."""
        self.counter = 0
        self.global_cfg = self.GLOBAL_CFG_RESET
        self.iepclk = self.IEPCLK_RESET
        self.latched_high = 0
        self._global_time_units = 0
        self._tick_remainder = 0
        self._refresh_tick_rate()
        for timeline in self._core_timelines.values():
            timeline.base_cycles = 0
            timeline.last_cycles = 0
            timeline.base_time_units = 0


class IEPRegisterRegion(MemoryRegion):
    """AM243x IEP0 local register window reached through constant-table c26."""

    BASE_ADDR = 0x0002E000
    WINDOW_SIZE = 0x100
    GLOBAL_CFG_OFFSET = 0x00
    COUNT_LO_OFFSET = 0x10
    COUNT_HI_OFFSET = 0x14

    def __init__(self, timebase: IEPTimebase):
        super().__init__("ICSSG_IEP0", self.BASE_ADDR, self.WINDOW_SIZE, 2, 1, 0)
        self._timebase = timebase

    @staticmethod
    def _overlaps(local: int, length: int, offset: int, size: int = 4) -> bool:
        return local < offset + size and local + length > offset

    @staticmethod
    def _overlay_word(data: bytearray, local: int, offset: int, value: int) -> None:
        word = (value & 0xFFFF_FFFF).to_bytes(4, "little")
        start = max(local, offset)
        end = min(local + len(data), offset + 4)
        for absolute in range(start, end):
            data[absolute - local] = word[absolute - offset]

    def read(self, addr: int, length: int) -> bytes:
        self._check_bounds(addr, length)
        local = addr - self.BASE_ADDR
        data = bytearray(super().read(addr, length))
        snapshot = self._timebase.count

        # TI's 64-bit mode latches COUNT_HI when COUNT_LO is read. For an
        # 8-byte LBCO starting at +0x10, both words therefore come from this
        # one snapshot; a later separate +0x14 read returns the same latch.
        if self._overlaps(local, length, self.COUNT_LO_OFFSET):
            self._timebase.latch_high(snapshot)
        if self._overlaps(local, length, self.GLOBAL_CFG_OFFSET):
            self._overlay_word(
                data, local, self.GLOBAL_CFG_OFFSET, self._timebase.global_cfg
            )
        if self._overlaps(local, length, self.COUNT_LO_OFFSET):
            self._overlay_word(data, local, self.COUNT_LO_OFFSET, snapshot)
        if self._overlaps(local, length, self.COUNT_HI_OFFSET):
            self._overlay_word(
                data, local, self.COUNT_HI_OFFSET, self._timebase.latched_high
            )
        return bytes(data)

    def write(self, addr: int, data: bytes) -> None:
        self._check_bounds(addr, len(data))
        local = addr - self.BASE_ADDR
        super().write(addr, data)

        if self._overlaps(local, len(data), self.GLOBAL_CFG_OFFSET):
            merged = bytearray(self._timebase.global_cfg.to_bytes(4, "little"))
            self._merge_write(merged, local, data, self.GLOBAL_CFG_OFFSET)
            self._timebase.write_global_cfg(int.from_bytes(merged, "little"))

        low = high = None
        if self._overlaps(local, len(data), self.COUNT_LO_OFFSET):
            merged = bytearray((self._timebase.count & 0xFFFF_FFFF).to_bytes(4, "little"))
            self._merge_write(merged, local, data, self.COUNT_LO_OFFSET)
            low = int.from_bytes(merged, "little")
        if self._overlaps(local, len(data), self.COUNT_HI_OFFSET):
            merged = bytearray(((self._timebase.count >> 32) & 0xFFFF_FFFF).to_bytes(4, "little"))
            self._merge_write(merged, local, data, self.COUNT_HI_OFFSET)
            high = int.from_bytes(merged, "little")
        if low is not None or high is not None:
            self._timebase.write_count(low=low, high=high)

    @staticmethod
    def _merge_write(target: bytearray, local: int, data: bytes, offset: int) -> None:
        start = max(local, offset)
        end = min(local + len(data), offset + 4)
        for absolute in range(start, end):
            target[absolute - offset] = data[absolute - local]


class IEPClockRegisterRegion(MemoryRegion):
    """ICSS CFG IEPCLK register at c4 + 0x30; bit 0 selects OCP clock."""

    BASE_ADDR = 0x00026030

    def __init__(self, timebase: IEPTimebase):
        super().__init__("ICSSG_IEPCLK", self.BASE_ADDR, 4, 2, 1, 0)
        self._timebase = timebase

    def read(self, addr: int, length: int) -> bytes:
        self._check_bounds(addr, length)
        local = addr - self.BASE_ADDR
        return self._timebase.iepclk.to_bytes(4, "little")[local : local + length]

    def write(self, addr: int, data: bytes) -> None:
        self._check_bounds(addr, len(data))
        local = addr - self.BASE_ADDR
        merged = bytearray(self._timebase.iepclk.to_bytes(4, "little"))
        merged[local : local + len(data)] = data
        self._timebase.write_iepclk(int.from_bytes(merged, "little"))
