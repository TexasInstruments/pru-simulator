"""Fault-reporting SSI encoder model for the RM08 and SICK absolute encoders.

The modeled edge convention is the one in SICK IM0100079, section 2.2 (p. 5):
the first high-to-low transition loads the position, the first low-to-high
edge presents the MSB, each later rising edge presents the next bit, and new
position values are loaded after the monoflop time tm once the master stops
clocking. The model measures tm from the latest falling edge. The document does
not say which edge the master samples on; the model allows sampling during the
high phase after a rising edge.

Defaults follow the RM08 data sheet (RM08D01_18, p. 10): clock up to 4 MHz and
tm up to 20.5 us. ``pru_io.ssi_presets`` lists the encoders used and where each
frame comes from.

This is a DeviceModel: it observes the clock pin, drives only the data pin,
and advances its monoflop during elapsed simulator cycles. It deliberately
does not share SSI decoding or timing code with the reader firmware.

A frame is ``resolution`` clocked bits (up to 64) holding a position field and
an optional error field. The SICK SSI document (IM0100079, section 2.3) sends
error bits after the position, so by default the error occupies the low bits;
``pru_io.ssi_presets`` lists SICK families that use this layout.
"""
from __future__ import annotations

from fractions import Fraction
from math import ceil

from pru_io.device_model import PUSH_PULL, DeviceModel
from pru_io.ssi_presets import preset_fields


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
    wire representation of the position field only; Gray-coded frames are
    reported decoded in frame events while ``raw_value`` preserves the
    transmitted bits. ``position_bits``/``position_offset`` and
    ``error_bits``/``error_offset`` place the fields inside the
    ``resolution``-bit word (offset 0 is the last bit sent). Unset layout
    fields default to the error bits last: ``position_bits`` fills what the
    error leaves, ``error_offset`` is 0 and ``position_offset`` is
    ``error_bits`` (0 if only ``error_offset`` is given).
    """

    name = "ssi_encoder"
    time_driven = True

    def __init__(self, clock_pin: int = 0, data_pin: int = 8,
                 position: int = 0, resolution: int = 12,
                 encoding: str = "binary", f_max_hz=4_000_000,
                 monoflop_us=20.5, core_clock_hz=250_000_000,
                 idle_value: int = 1, name: str = "ssi_encoder",
                 position_bits: int | None = None,
                 position_offset: int | None = None, error_bits: int = 0,
                 error_offset: int | None = None, error_value: int = 0) -> None:
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
        self._set_layout(position_bits, position_offset, error_bits,
                         error_offset)
        self.preset: str | None = None
        self.position = self._position(position)
        self.error = self._error(error_value)
        self.f_max_hz = _positive_fraction(f_max_hz, "f_max_hz")
        self.monoflop_us = _positive_fraction(monoflop_us, "monoflop_us")
        self.core_clock_hz = _positive_fraction(core_clock_hz, "core_clock_hz")
        if (isinstance(idle_value, bool) or not isinstance(idle_value, int)
                or idle_value not in (0, 1)):
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

    @classmethod
    def from_preset(cls, preset: str, **options) -> "SSIEncoderModel":
        """Build a model from a ``pru_io.ssi_presets`` frame; options override it."""
        model = cls(**{**preset_fields(preset), **options})
        model.preset = preset
        return model

    def _set_layout(self, position_bits, position_offset, error_bits,
                    error_offset) -> None:
        def field(value, name, low, high):
            if (isinstance(value, bool) or not isinstance(value, int)
                    or not low <= value <= high):
                raise ValueError(f"{name} must be an integer from {low} to {high}")
            return value

        width = self.resolution
        self.error_bits = field(error_bits, "error_bits", 0, width - 1)
        if position_bits is None:
            position_bits = width - self.error_bits
        self.position_bits = field(position_bits, "position_bits", 1, width)
        if error_offset is None:
            error_offset = 0
            if position_offset is None:
                position_offset = self.error_bits
        elif position_offset is None:
            position_offset = 0
        self.position_offset = field(
            position_offset, "position_offset", 0, width - self.position_bits)
        self.error_offset = field(
            error_offset, "error_offset", 0, width - self.error_bits)
        self._position_mask = (1 << self.position_bits) - 1
        self._error_mask = (1 << self.error_bits) - 1
        if (self._position_mask << self.position_offset
                & self._error_mask << self.error_offset):
            raise ValueError("position and error fields overlap")

    def _position(self, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("position must be an unsigned integer")
        if not 0 <= value <= self._position_mask:
            raise ValueError(f"position must fit the {self.position_bits}-bit position field")
        return value

    def _error(self, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("error must be an unsigned integer")
        if not 0 <= value <= self._error_mask:
            raise ValueError(f"error must fit the {self.error_bits}-bit error field")
        return value

    def set_position(self, position: int) -> None:
        """Set the position latched by the next frame."""
        self.position = self._position(position)

    def set_error(self, error: int) -> None:
        """Set the error field latched by the next frame."""
        self.error = self._error(error)

    def pack_frame(self, position: int, error: int = 0) -> int:
        """Return the ``resolution``-bit wire word for a position and error."""
        position = self._position(position)
        error = self._error(error)
        if self.encoding == "gray":
            position ^= position >> 1
        return (position << self.position_offset) | (error << self.error_offset)

    def decode_frame(self, raw_value: int) -> tuple[int, int]:
        """Return the (position, error) fields carried by a wire word."""
        position = (raw_value >> self.position_offset) & self._position_mask
        if self.encoding == "gray":
            shift = position >> 1
            while shift:
                position ^= shift
                shift >>= 1
        return position, (raw_value >> self.error_offset) & self._error_mask

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
            else:
                # The first fall after the Nth rising edge is the optional
                # closing edge used by the reader firmware. Its following
                # rise returns CLK to idle high; neither edge adds a data bit.
                if not self._post_word_fall_seen:
                    self._post_word_fall_seen = True
                    self._last_falling_cycle = cycle
                    self._output_value = 0
                else:
                    self._last_falling_cycle = cycle
                    self._fault(cycle, "clocked past the end of the SSI word")
        elif rising:
            if self._state == "active":
                self._clock_bit(cycle)

        mask = 1 << self.data_pin
        values = self._output_value << self.data_pin
        return mask, values

    def _start_frame(self, cycle: int, valid: bool) -> None:
        self._state = "active"
        self._frame_valid = valid
        self._latched_position = self.position
        self._latched_raw = self.pack_frame(self.position, self.error)
        self._bits_clocked = 0
        self._received_raw = 0
        self._last_falling_cycle = cycle
        self._last_rising_cycle = None
        self._frame_started_cycle = cycle
        self._frame_completed_cycle = None
        self._post_word_fall_seen = False
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
            self._state = "guard"

    def _expire(self, cycle: int) -> None:
        if self._state == "idle" or self._last_falling_cycle is None:
            return
        if cycle - self._last_falling_cycle < self.monoflop_cycles:
            return

        if self._state == "guard":
            if self._frame_valid:
                position, error = self.decode_frame(self._received_raw)
                event = {
                    "cycle": self._frame_completed_cycle,
                    "kind": "frame",
                    "raw_value": self._received_raw,
                    "position": position,
                    "resolution": self.resolution,
                    "encoding": self.encoding,
                }
                if self.error_bits:
                    event["error"] = error
                self._events.append(event)
                self.frames_captured += 1
        else:
            detail = (f"monoflop timeout after {self._bits_clocked} of "
                      f"{self.resolution} SSI bits")
            self._fault(cycle, detail)
        self._state = "idle"
        self._last_falling_cycle = None
        self._output_value = self.idle_value

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
        self._latched_raw = self.pack_frame(self.position, self.error)
        self._received_raw = 0
        self._bits_clocked = 0
        self._frame_valid = True
        self._post_word_fall_seen = False
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
            "post_word_fall_seen": self._post_word_fall_seen,
            "position": self.position,
            "error": self.error,
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
        self._post_word_fall_seen = snap.get("post_word_fall_seen", False)
        self.position = snap["position"]
        self.error = snap["error"]
        self._events = [dict(event) for event in snap["events"]]
        self._faults = list(snap["faults"])
        self.frames_captured = snap["frames_captured"]

    def get_state(self) -> dict:
        return {
            "name": self.name,
            "clock_pin": self.clock_pin,
            "data_pin": self.data_pin,
            "position": self.position,
            "error": self.error,
            "preset": self.preset,
            "resolution": self.resolution,
            "position_bits": self.position_bits,
            "position_offset": self.position_offset,
            "error_bits": self.error_bits,
            "error_offset": self.error_offset,
            "encoding": self.encoding,
            "state": self._state,
            "bits_clocked": self._bits_clocked,
            "frames_captured": self.frames_captured,
            "f_max_hz": float(self.f_max_hz),
            "monoflop_cycles": self.monoflop_cycles,
            "core_clock_hz": float(self.core_clock_hz),
            "faults": len(self._faults),
        }
