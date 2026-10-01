"""ICSSG Industrial Ethernet Peripheral (IEP) timer model.

Implements the counter and compare subset of IEP0 that PRU firmware polls
through the constant table (typically C26). Written from the AM243x TRM
(SPRUIM2J §6.4.13, PRU_ICSSG IEP), NOT ported from any other simulator.

Register map (offsets from the IEP base), per the TRM:

    0x00  IEP_GLOBAL_CFG_REG   [0] CNT_ENABLE, [7:4] DEFAULT_INC
    0x04  IEP_GLOBAL_STATUS_REG
    0x0C  IEP_SLOW_COMPEN_REG  (NOT the counter - see below)
    0x10  IEP_COUNT_REG0       counter, lower 32 bits
    0x14  IEP_COUNT_REG1       counter, upper 32 bits
    0x18  IEP_CAP_CFG_REG
    0x1C  IEP_CAP_STATUS_REG
    0x20  IEP_CAPR0_REG0       capture registers 0x20..0x6C
    0x70  IEP_CMP_CFG_REG      [0] CMP0_RST_CNT_EN, [16:1] CMP_EN (bit 1 -> CMP0)
    0x74  IEP_CMP_STATUS_REG   [15:0] CMP_STATUS, write 1 to clear
    0x78  IEP_CMP0_REG0        compare 0, lower 32 bits
    0x7C  IEP_CMP0_REG1        compare 0, upper 32 bits
    0x80  IEP_CMP1_REG0        ... CMPj_REG0 at 0x78 + 8*j

Offsets are from Table 14-10902, TRM pages 6902/6905. An earlier revision of this
file used 0x0C for the counter and 0x40/0x44/0x48 for the compare block - the
AM335x-era PRU-ICSS layout, not ICSSG. 0x0C is SLOW_COMPEN and 0x40-0x6C are
capture registers, so that firmware polls the wrong registers entirely.

Two TRM details are easy to get wrong and are called out because at least one
other PRU simulator gets them wrong:

  * The compare registers are **64-bit pairs** - "16x 64-bit compare registers:
    IEP_CMPj_REG0/ IEP_CMPj_REG1 (where j = 0 to 15)". So 0x4C is the UPPER
    half of CMP0, not CMP1. CMP1_REG0 lives at 0x50.
  * A CMP0 hit resets the counter only when **IEP_CMP_CFG_REG[0]
    CMP0_RST_CNT_EN** is set, and a compare only fires when its CMP_EN bit is
    set - and CMP_EN starts at bit **1**, so CMP0's enable is bit 1, not bit 0.
    Auto-reset is configuration, not implicit behaviour.

CAPTURE

  0x18  IEP_CAP_CFG_REG      [n]    CAPnR_EN     capture enable, event n
                             [n+7]  CAPnR_1ST_LAST  0 = first, 1 = last
  0x1C  IEP_CAP_STATUS_REG   [n]    CAPnR_VALID, write 1 to clear
  0x20  IEP_CAPR0_REG0       captured counter, 64-bit pairs, CAPRn at 0x20+8n

Capture exists so firmware can timestamp an asynchronous external event
against the counter - the mechanism every encoder protocol uses when it has to
relate a host trigger to a line or clock phase.

`capture_event(n)` latches the CURRENT counter into slot n, subject to the
enable and to first-vs-last:

  * first mode - the slot holds the FIRST event since the valid bit was
    cleared; later events are ignored until software clears it. This is what a
    protocol wants when it must timestamp a trigger and not have a later,
    spurious edge overwrite it.
  * last mode - every event overwrites.

The enable/mode bit positions above are the modelled convention. The TRM's
exact CAP_CFG field packing varies between ICSS generations, and firmware that
depends on a particular packing should be checked against the device TRM rather
than against this model. What IS faithful, and what firmware actually depends
on, is the latch semantics: enable gating, first-vs-last, and a valid bit that
software clears.

TIMING

The IEP is shared by every configured core and advances to the furthest exact
core time seen. Core rates come from [device] `pru_clock_mhz` and
`pru1_clock_mhz`; absent values retain the simulator's 200 MHz fallback. The
IEP uses `iep_clock_mhz` (200 MHz by default) while ICSS CFG IEPCLK bit 0 is
clear, and the PRU0 OCP/core clock while it is set. Rational clock periods are
represented with integer time units, so fractional MHz values and mixed core
rates do not accumulate float drift. Memory and wait stalls count as elapsed
core cycles. Resetting one core rebases it at the current shared time; a full
hardware reset clears the timer registers and timeline.

Not modelled: shadow mode (IEP_CMP_CFG_REG[17] SHADOW_EN), slow compensation,
sync/EHRPWM counter reset, interrupt routing, and the pin/event routing that
decides WHICH external signal drives capture event n - here the event is raised
by the harness or by another model calling `capture_event()`. Reads of
unimplemented offsets return zero and writes are ignored, so firmware touching
them does not fault - it simply sees a counter that ignores those features.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import gcd, lcm

GLOBAL_CFG = 0x00
GLOBAL_STATUS = 0x04
COUNT_REG0 = 0x10
COUNT_REG1 = 0x14
CMP_CFG = 0x70
CMP_STATUS = 0x74
CAP_CFG = 0x18
CAP_STATUS = 0x1C
CAPR0_REG0 = 0x20
CMP0_REG0 = 0x78

NUM_COMPARE = 16
# 0x20..0x6C is 0x50 bytes = ten 64-bit pairs.
NUM_CAPTURE = 10
IEP_SIZE = 0x100

_MASK32 = 0xFFFFFFFF
_MASK64 = 0xFFFFFFFFFFFFFFFF


def _mhz_to_hz(clock_mhz: float | str) -> Fraction:
    """Convert a configured MHz value without introducing float drift."""
    return Fraction(str(clock_mhz)) * 1_000_000


@dataclass
class _CoreTimeline:
    cycle_units: int
    base_cycles: int = 0
    base_time_units: int = 0
    last_cycles: int = 0


class IepTimer:
    """Counter + compare subset of the ICSSG IEP.

    `tick()` advances the counter by DEFAULT_INC when CNT_ENABLE is set, then
    evaluates the enabled compares against the new count.
    """

    IEPCLK_OCP_EN = 1 << 0

    def __init__(self, clock_mhz: float | str = 200.0,
                 ocp_clock_mhz: float | str = 250.0,
                 core_clocks_mhz: dict[str, float | str] | None = None):
        core_clocks_mhz = core_clocks_mhz or {"pru0": ocp_clock_mhz}
        core_clocks_hz = {
            name: _mhz_to_hz(rate) for name, rate in core_clocks_mhz.items()
        }
        if any(rate <= 0 for rate in core_clocks_hz.values()):
            raise ValueError("PRU core clocks must be positive")
        self._time_units_per_second = lcm(
            *(rate.numerator for rate in core_clocks_hz.values())
        )
        self._core_timelines = {
            name: _CoreTimeline(
                rate.denominator * (self._time_units_per_second // rate.numerator)
            )
            for name, rate in core_clocks_hz.items()
        }
        self._global_time_units = 0
        self._tick_remainder = 0
        self._tick_numerator = 0
        self._tick_denominator = 1
        self._ticks_per_time_unit = 0
        self.external_clock_hz = _mhz_to_hz(clock_mhz)
        self.ocp_clock_hz = _mhz_to_hz(ocp_clock_mhz)
        if self.external_clock_hz <= 0:
            raise ValueError(f"IEP clock must be positive, got {clock_mhz}")
        if self.ocp_clock_hz <= 0:
            raise ValueError(f"OCP clock must be positive, got {ocp_clock_mhz}")
        self.iepclk = 0
        self._global_cfg = 0
        self.reset()

    def reset(self) -> None:
        self._global_cfg = 0
        self.global_status = 0
        self.count = 0                       # 64-bit
        self.cmp_cfg = 0
        self.cmp_status = 0                  # 16 bits, write-1-to-clear
        self.compare = [0] * NUM_COMPARE     # each 64-bit
        self.cap_cfg = 0
        self.cap_status = 0                  # valid bits, write-1-to-clear
        self.capture = [0] * NUM_CAPTURE     # each 64-bit
        self._tick_remainder = 0
        self._refresh_tick_rate()

    def hardware_reset(self) -> None:
        """Reset both IEP registers and the shared hardware timeline."""
        self.reset()
        self.iepclk = 0
        self._global_time_units = 0
        self._tick_remainder = 0
        self._refresh_tick_rate()
        for timeline in self._core_timelines.values():
            timeline.base_cycles = 0
            timeline.last_cycles = 0
            timeline.base_time_units = 0

    @property
    def clock_mhz(self) -> Fraction:
        return self.active_clock_mhz

    @property
    def active_clock_hz(self) -> Fraction:
        return self.ocp_clock_hz if self.iepclk & self.IEPCLK_OCP_EN else self.external_clock_hz

    @property
    def active_clock_mhz(self) -> Fraction:
        return self.active_clock_hz / 1_000_000

    def set_clock_mhz(self, clock_mhz: float | str) -> None:
        """Set the external IEP clock rate while preserving elapsed time."""
        rate = _mhz_to_hz(clock_mhz)
        if rate <= 0:
            raise ValueError(f"IEP clock must be positive, got {clock_mhz}")
        self.external_clock_hz = rate
        self._tick_remainder = 0
        self._refresh_tick_rate()

    def write_iepclk(self, value: int) -> None:
        self.iepclk = value & _MASK32
        self._tick_remainder = 0
        self._refresh_tick_rate()

    @property
    def global_time_units(self) -> int:
        return self._global_time_units

    @property
    def time_units_per_second(self) -> int:
        return self._time_units_per_second

    @property
    def now_ns(self) -> Fraction:
        return Fraction(self._global_time_units * 1_000_000_000,
                        self._time_units_per_second)

    def core_clock_hz(self, core: str) -> Fraction:
        timeline = self._core_timelines[core]
        return Fraction(self._time_units_per_second, timeline.cycle_units)

    def core_time_units(self, core: str, cycles: int) -> int:
        """Return *core*'s absolute virtual time for its cumulative cycle count."""
        timeline = self._core_timelines[core]
        return timeline.base_time_units + (
            cycles - timeline.base_cycles
        ) * timeline.cycle_units

    def core_time_ns(self, core: str, cycles: int) -> Fraction:
        """Return *core*'s absolute virtual time in nanoseconds."""
        return Fraction(
            self.core_time_units(core, cycles) * 1_000_000_000,
            self._time_units_per_second,
        )

    def nanoseconds_to_units(self, nanoseconds: float | str) -> Fraction:
        return Fraction(str(nanoseconds)) * self._time_units_per_second / 1_000_000_000

    def time_units_to_ns(self, units: int | Fraction) -> Fraction:
        return Fraction(units) * 1_000_000_000 / self._time_units_per_second

    def rebase_core(self, core: str, cycles: int = 0) -> None:
        """Place a reset core at the current shared time without rewinding it."""
        timeline = self._core_timelines[core]
        timeline.base_cycles = cycles
        timeline.last_cycles = cycles
        timeline.base_time_units = self._global_time_units

    def observe_core_cycles(self, core: str, cycles: int) -> None:
        """Advance the shared IEP to the furthest core time seen so far."""
        if cycles < 0:
            raise ValueError("PRU cycle counters cannot be negative")
        timeline = self._core_timelines[core]
        if cycles < timeline.last_cycles:
            self.rebase_core(core)
        timeline.last_cycles = cycles
        core_time_units = self.core_time_units(core, cycles)
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
        self._advance_ticks(whole_ticks)

    @property
    def global_cfg(self) -> int:
        return self._global_cfg

    @global_cfg.setter
    def global_cfg(self, value: int) -> None:
        self._global_cfg = value & _MASK32
        self._tick_remainder = 0
        self._refresh_tick_rate()

    def _refresh_tick_rate(self) -> None:
        if not self.count_enabled:
            self._tick_numerator = 0
            self._tick_denominator = 1
            self._ticks_per_time_unit = 0
            return
        active_clock = self.active_clock_hz
        numerator = active_clock.numerator
        denominator = active_clock.denominator * self._time_units_per_second
        divisor = gcd(numerator, denominator)
        numerator //= divisor
        denominator //= divisor
        self._tick_numerator = numerator
        self._tick_denominator = denominator
        self._ticks_per_time_unit = (
            numerator // denominator if numerator % denominator == 0 else 0
        )

    def _advance_ticks(self, ticks: int) -> None:
        if ticks <= 0 or not self.count_enabled:
            return
        if not (self.cmp_cfg & 0x1FFFE):
            self.count = (self.count + ticks * self.default_inc) & _MASK64
            return
        for _ in range(ticks):
            self.tick()

    # -- configuration views --------------------------------------------
    @property
    def count_enabled(self) -> bool:
        return bool(self.global_cfg & 0x1)

    @property
    def default_inc(self) -> int:
        """IEP_GLOBAL_CFG_REG[7:4]. A DEFAULT_INC of 0 does not advance."""
        return (self.global_cfg >> 4) & 0xF

    @property
    def cmp0_rst_cnt_en(self) -> bool:
        return bool(self.cmp_cfg & 0x1)

    def cmp_enabled(self, j: int) -> bool:
        """IEP_CMP_CFG_REG[16:1]: CMP_EN bit 1 maps to CMP0."""
        return bool(self.cmp_cfg & (1 << (j + 1)))

    def cap_enabled(self, n: int) -> bool:
        return bool(self.cap_cfg & (1 << n))

    def cap_last_mode(self, n: int) -> bool:
        return bool(self.cap_cfg & (1 << (n + 7)))

    def cap_valid(self, n: int) -> bool:
        return bool(self.cap_status & (1 << n))

    # -- capture ---------------------------------------------------------
    def capture_event(self, n: int) -> bool:
        """Latch the current counter into capture slot *n*.

        Returns True if a value was stored. A disabled slot stores nothing, and
        in first mode an already-valid slot is left alone - which is the whole
        point of first mode, so a later edge cannot overwrite the trigger the
        firmware is trying to timestamp.
        """
        if not 0 <= n < NUM_CAPTURE:
            raise ValueError(f"capture slot {n} out of range")
        if not self.cap_enabled(n):
            return False
        if self.cap_valid(n) and not self.cap_last_mode(n):
            return False
        self.capture[n] = self.count
        self.cap_status |= 1 << n
        return True

    # -- timeline --------------------------------------------------------
    def tick(self) -> None:
        """Advance one ICSSG_IEP_CLK cycle."""
        if not self.count_enabled:
            return
        self.count = (self.count + self.default_inc) & _MASK64

        hit0 = False
        for j in range(NUM_COMPARE):
            if self.cmp_enabled(j) and self.count == self.compare[j]:
                self.cmp_status |= 1 << j
                if j == 0:
                    hit0 = True

        # "IEP_CMP_CFG_REG[0] CMP0_RST_CNT_EN, if enabled, will reset the
        # controller counter on the next ICSSG_IEP_CLK/ICSSG_ICLK cycle."
        if hit0 and self.cmp0_rst_cnt_en:
            self.count = 0

    # -- register access -------------------------------------------------
    def read32(self, offset: int) -> int:
        if offset == GLOBAL_CFG:
            return self.global_cfg
        if offset == GLOBAL_STATUS:
            return self.global_status
        if offset == COUNT_REG0:
            return self.count & _MASK32
        if offset == COUNT_REG1:
            return (self.count >> 32) & _MASK32
        if offset == CMP_CFG:
            return self.cmp_cfg
        if offset == CMP_STATUS:
            return self.cmp_status
        if offset == CAP_CFG:
            return self.cap_cfg
        if offset == CAP_STATUS:
            return self.cap_status
        if CAPR0_REG0 <= offset < CAPR0_REG0 + NUM_CAPTURE * 8:
            n, half = divmod(offset - CAPR0_REG0, 8)
            v = self.capture[n]
            return v & _MASK32 if half == 0 else (v >> 32) & _MASK32
        if CMP0_REG0 <= offset < CMP0_REG0 + NUM_COMPARE * 8:
            j, half = divmod(offset - CMP0_REG0, 8)
            v = self.compare[j]
            return v & _MASK32 if half == 0 else (v >> 32) & _MASK32
        return 0

    def write32(self, offset: int, value: int) -> None:
        value &= _MASK32
        if offset == GLOBAL_CFG:
            self.global_cfg = value
        elif offset == GLOBAL_STATUS:
            self.global_status = value
        elif offset == COUNT_REG0:
            self.count = (self.count & ~_MASK32) | value
            self._tick_remainder = 0
        elif offset == COUNT_REG1:
            self.count = (self.count & _MASK32) | (value << 32)
            self._tick_remainder = 0
        elif offset == CMP_CFG:
            self.cmp_cfg = value
        elif offset == CMP_STATUS:
            # 16 status bits, write 1h to clear.
            self.cmp_status &= ~(value & 0xFFFF)
        elif offset == CAP_CFG:
            self.cap_cfg = value
        elif offset == CAP_STATUS:
            # Valid bits, write 1 to clear - the same convention as CMP_STATUS.
            self.cap_status &= ~value
        elif CAPR0_REG0 <= offset < CAPR0_REG0 + NUM_CAPTURE * 8:
            # Capture registers are read-only in hardware; a write is ignored
            # rather than silently corrupting a timestamp.
            pass
        elif CMP0_REG0 <= offset < CMP0_REG0 + NUM_COMPARE * 8:
            j, half = divmod(offset - CMP0_REG0, 8)
            if half == 0:
                self.compare[j] = (self.compare[j] & ~_MASK32) | value
            else:
                self.compare[j] = (self.compare[j] & _MASK32) | (value << 32)
        # Unimplemented offsets are ignored rather than faulting.

    # -- byte-addressed access used by the memory bus --------------------
    def read(self, offset: int, length: int) -> bytes:
        out = bytearray()
        for i in range(length):
            byte_off = offset + i
            word = self.read32(byte_off & ~0x3)
            out.append((word >> ((byte_off & 0x3) * 8)) & 0xFF)
        return bytes(out)

    def write(self, offset: int, data: bytes) -> None:
        # Group by 32-bit word so write-1-to-clear and the 64-bit halves see a
        # whole word, which is how firmware always accesses these registers.
        words: dict[int, list[int | None]] = {}
        for i, b in enumerate(data):
            byte_off = offset + i
            w = byte_off & ~0x3
            words.setdefault(w, [None, None, None, None])[byte_off & 0x3] = b
        for w, parts in words.items():
            if any(p is None for p in parts):
                cur = self.read32(w)
                parts = [p if p is not None else (cur >> (k * 8)) & 0xFF
                         for k, p in enumerate(parts)]
            self.write32(w, parts[0] | (parts[1] << 8) | (parts[2] << 16) | (parts[3] << 24))

    def snapshot(self) -> dict:
        return {
            "global_cfg": self.global_cfg,
            "global_status": self.global_status,
            "count": self.count,
            "cmp_cfg": self.cmp_cfg,
            "cmp_status": self.cmp_status,
            "compare": list(self.compare),
            "cap_cfg": self.cap_cfg,
            "cap_status": self.cap_status,
            "capture": list(self.capture),
            "iepclk": self.iepclk,
            "external_clock_hz": self.external_clock_hz,
            "ocp_clock_hz": self.ocp_clock_hz,
            "active_clock_hz": self.active_clock_hz,
            "global_time_units": self._global_time_units,
            "tick_remainder": self._tick_remainder,
            "core_timelines": {
                name: {
                    "base_cycles": timeline.base_cycles,
                    "base_time_units": timeline.base_time_units,
                    "last_cycles": timeline.last_cycles,
                }
                for name, timeline in self._core_timelines.items()
            },
        }

    def restore(self, snapshot: dict) -> None:
        """Restore IEP registers and shared-time bookkeeping from a snapshot."""
        self._global_cfg = snapshot["global_cfg"] & _MASK32
        self.global_status = snapshot.get("global_status", 0) & _MASK32
        self.count = snapshot["count"] & _MASK64
        self.cmp_cfg = snapshot["cmp_cfg"] & _MASK32
        self.cmp_status = snapshot["cmp_status"] & 0xFFFF
        self.compare = list(snapshot["compare"])
        self.cap_cfg = snapshot.get("cap_cfg", 0) & _MASK32
        self.cap_status = snapshot.get("cap_status", 0) & _MASK32
        self.capture = list(snapshot.get("capture", [0] * NUM_CAPTURE))
        self.iepclk = snapshot.get("iepclk", self.iepclk) & _MASK32
        self.external_clock_hz = snapshot.get("external_clock_hz", self.external_clock_hz)
        self.ocp_clock_hz = snapshot.get("ocp_clock_hz", self.ocp_clock_hz)
        self._global_time_units = snapshot.get("global_time_units", 0)
        self._tick_remainder = snapshot.get("tick_remainder", 0)
        for name, state in snapshot.get("core_timelines", {}).items():
            timeline = self._core_timelines.get(name)
            if timeline is None:
                continue
            timeline.base_cycles = state["base_cycles"]
            timeline.base_time_units = state["base_time_units"]
            timeline.last_cycles = state["last_cycles"]
        self._refresh_tick_rate()
