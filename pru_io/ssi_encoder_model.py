"""Fault-reporting SSI encoder model for Hengstler and Pepperl+Fuchs devices.

The modeled edge convention is documented by Hengstler AC58 datasheet v3
(210125HF, p. 3) and Pepperl+Fuchs AVM78E (p. 5) / AVS36M (p. 4): the first
high-to-low transition latches the position, each rising edge presents the
next MSB-first bit, and monoflop Tm starts at the last falling edge. Sampling
in the master's high phase before its next falling edge is consistent with
those documents.

This is a DeviceModel: it observes the clock pin, drives only the data pin,
and advances its monoflop during elapsed simulator cycles. It deliberately
does not share SSI decoding or timing code with the reader firmware.
"""
from __future__ import annotations

from fractions import Fraction
from math import ceil

from pru_io.device_model import PUSH_PULL, DeviceModel

_MASK_20 = (1 << 20) - 1


def _positive_fraction(value, name: str) -> Fraction:
    try:
        result = Fraction(str(value))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if result <= 0:
        raise ValueError(f"{name} must be a positive number")
    return result


class SSIEncoderModel(DeviceModel):
    """An SSI encoder that latches on falling edges and presents bits on rises.

    ``position`` is the host-side unsigned position. ``encoding`` selects the
    wire representation; Gray-coded frames are reported decoded in frame
    events while ``raw_value`` preserves the transmitted bits.
    """

    name = "ssi_encoder"
    time_driven = True

    def __init__(self, clock_pin: int = 0, data_pin: int = 8,
                 position: int = 0, resolution: int = 12,
                 encoding: str = "binary", f_max_hz=4_000_000,
                 monoflop_us=20.5, core_clock_hz=250_000_000,
                 idle_value: int = 1, name: str = "ssi_encoder") -> None:
        self.clock_pin = self._pin(clock_pin, "clock_pin")
        self.data_pin = self._pin(data_pin, "data_pin")
        if self.clock_pin == self.data_pin:
            raise ValueError("clock_pin and data_pin must differ")
        if isinstance(resolution, bool) or not isinstance(resolution, int):
            raise ValueError("resolution must be an integer from 1 to 64")
        if not 1 <= resolution <= 64:
            raise ValueError("resolution must be an integer from 1 to 64")
        self.resolution = resolution
        self.encoding = str(encoding).lower()
        if self.encoding not in ("binary", "gray"):
            raise ValueError("encoding must be 'binary' or 'gray'")
        self.position = self._position(position)
        self.f_max_hz = _positive_fraction(f_max_hz, "f_max_hz")
        self.monoflop_us = _positive_fraction(monoflop_us, "monoflop_us")
        self.core_clock_hz = _positive_fraction(core_clock_hz, "core_clock_hz")
        if isinstance(idle_value, bool) or idle_value not in (0, 1):
            raise ValueError("idle_value must be 0 or 1")
        self.idle_value = idle_value
        self.name = name
        self.nets = {self.data_pin: PUSH_PULL}
        self.monoflop_cycles = ceil(
            self.core_clock_hz * self.monoflop_us / 1_000_000)
        self.min_rising_period_cycles = ceil(self.core_clock_hz / self.f_max_hz)
        self.reset()

    @staticmethod
    def _pin(value: int, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 20:
            raise ValueError(f"{name} must be an integer from 0 to 19")
        return value

    def _position(self, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("position must be an unsigned integer")
        if not 0 <= value < (1 << self.resolution):
            raise ValueError(f"position must fit the {self.resolution}-bit resolution")
        return value

    def set_position(self, position: int) -> None:
        """Set the position latched by the next frame."""
        self.position = self._position(position)

    def tick(self, cycle: int, bus: int) -> tuple[int, int]:
        clock = (bus >> self.clock_pin) & 1
        self._expire(cycle)

        rising = self._prev_clock == 0 and clock == 1
        falling = self._prev_clock == 1 and clock == 0
        self._prev_clock = clock

        if falling:
            if self._state == "idle":
                self._start_frame(cycle, valid=True)
            elif self._state == "active":
                self._last_falling_cycle = cycle
                if self._bits_clocked == self.resolution:
                    self._state = "guard"
            else:  # another request arrived before Tm expired
                self._fault(cycle, "premature frame restart before monoflop Tm")
                self._start_frame(cycle, valid=False)
        elif rising:
            if self._state == "active":
                self._clock_bit(cycle)
            elif self._state == "guard":
                self._fault(cycle, "clocked past the end of the SSI word before Tm")

        mask = 1 << self.data_pin
        values = self._output_value << self.data_pin
        return mask, values

    def _start_frame(self, cycle: int, valid: bool) -> None:
        self._state = "active"
        self._frame_valid = valid
        self._latched_position = self.position
        if self.encoding == "gray":
            self._latched_raw = self.position ^ (self.position >> 1)
        else:
            self._latched_raw = self.position
        self._bits_clocked = 0
        self._received_raw = 0
        self._last_falling_cycle = cycle
        self._last_rising_cycle = None
        self._frame_started_cycle = cycle
        self._frame_completed_cycle = None
        self._output_value = self.idle_value

    def _clock_bit(self, cycle: int) -> None:
        if self._bits_clocked >= self.resolution:
            self._fault(cycle, "clocked past the end of the SSI word")
            self._output_value = self.idle_value
            return

        if self._last_rising_cycle is not None:
            period = cycle - self._last_rising_cycle
            if period < self.min_rising_period_cycles:
                self._fault(
                    cycle,
                    f"SSI clock exceeds f_max ({period} cycles between rising edges; "
                    f"minimum is {self.min_rising_period_cycles})",
                )

        index = self._bits_clocked
        bit = (self._latched_raw >> (self.resolution - index - 1)) & 1
        self._output_value = bit
        self._received_raw = (self._received_raw << 1) | bit
        self._bits_clocked += 1
        self._last_rising_cycle = cycle
        if self._bits_clocked == self.resolution:
            self._frame_completed_cycle = cycle

    def _expire(self, cycle: int) -> None:
        if self._state == "idle" or self._last_falling_cycle is None:
            return
        if cycle - self._last_falling_cycle < self.monoflop_cycles:
            return

        if self._state == "guard":
            if self._frame_valid:
                self._events.append({
                    "cycle": self._frame_completed_cycle,
                    "kind": "frame",
                    "raw_value": self._received_raw,
                    "position": self._decode(self._received_raw),
                    "resolution": self.resolution,
                    "encoding": self.encoding,
                })
                self.frames_captured += 1
        else:
            if self._bits_clocked == self.resolution:
                detail = "clock stalled after final rising edge before final falling edge"
            else:
                detail = (f"monoflop timeout after {self._bits_clocked} of "
                          f"{self.resolution} SSI bits")
            self._fault(cycle, detail)
        self._state = "idle"
        self._last_falling_cycle = None
        self._output_value = self.idle_value

    def _decode(self, raw_value: int) -> int:
        if self.encoding == "binary":
            return raw_value
        value = raw_value
        shift = raw_value >> 1
        while shift:
            value ^= shift
            shift >>= 1
        return value

    def _fault(self, cycle: int, message: str) -> None:
        self._faults.append(f"cycle {cycle}: {message}")
        self._events.append({"cycle": cycle, "kind": "fault", "message": message})
        if self._state != "idle":
            self._frame_valid = False

    def events(self) -> list[dict]:
        return [dict(event) for event in self._events]

    def faults(self) -> list[str]:
        return list(self._faults)

    def reset(self) -> None:
        self._state = "idle"
        self._output_value = self.idle_value
        self._prev_clock = 1
        self._last_falling_cycle: int | None = None
        self._last_rising_cycle: int | None = None
        self._frame_started_cycle: int | None = None
        self._frame_completed_cycle: int | None = None
        self._latched_position = self.position
        self._latched_raw = self.position
        self._received_raw = 0
        self._bits_clocked = 0
        self._frame_valid = True
        self._events: list[dict] = []
        self._faults: list[str] = []
        self.frames_captured = 0

    def snapshot(self) -> dict:
        return {
            "state": self._state,
            "output_value": self._output_value,
            "prev_clock": self._prev_clock,
            "last_falling_cycle": self._last_falling_cycle,
            "last_rising_cycle": self._last_rising_cycle,
            "frame_started_cycle": self._frame_started_cycle,
            "frame_completed_cycle": self._frame_completed_cycle,
            "latched_position": self._latched_position,
            "latched_raw": self._latched_raw,
            "received_raw": self._received_raw,
            "bits_clocked": self._bits_clocked,
            "frame_valid": self._frame_valid,
            "position": self.position,
            "events": [dict(event) for event in self._events],
            "faults": list(self._faults),
            "frames_captured": self.frames_captured,
        }

    def restore(self, snap: dict) -> None:
        self._state = snap["state"]
        self._output_value = snap["output_value"]
        self._prev_clock = snap["prev_clock"]
        self._last_falling_cycle = snap["last_falling_cycle"]
        self._last_rising_cycle = snap["last_rising_cycle"]
        self._frame_started_cycle = snap["frame_started_cycle"]
        self._frame_completed_cycle = snap["frame_completed_cycle"]
        self._latched_position = snap["latched_position"]
        self._latched_raw = snap["latched_raw"]
        self._received_raw = snap["received_raw"]
        self._bits_clocked = snap["bits_clocked"]
        self._frame_valid = snap["frame_valid"]
        self.position = snap["position"]
        self._events = [dict(event) for event in snap["events"]]
        self._faults = list(snap["faults"])
        self.frames_captured = snap["frames_captured"]

    def get_state(self) -> dict:
        return {
            "name": self.name,
            "clock_pin": self.clock_pin,
            "data_pin": self.data_pin,
            "position": self.position,
            "resolution": self.resolution,
            "encoding": self.encoding,
            "state": self._state,
            "bits_clocked": self._bits_clocked,
            "frames_captured": self.frames_captured,
            "f_max_hz": float(self.f_max_hz),
            "monoflop_cycles": self.monoflop_cycles,
            "core_clock_hz": float(self.core_clock_hz),
            "faults": len(self._faults),
        }
