"""UART Frame Generator — produces bit-level waveform on a GPI pin.

Pre-computes a timeline of pin transitions for 8N1 UART frames.
Used to test PRU UART receiver assembly code.
"""

from __future__ import annotations


class UARTFrameGenerator:
    """Generates UART bit waveform events for testing PRU UART RX.

    Pre-computes all pin transitions at construction time.
    Call start() to arm the generator at a specific cycle,
    then get_pin_value(cycle) to query the pin state at any cycle.
    """

    def __init__(
        self,
        pin: int = 0,
        payload: list[int] | None = None,
        baudrate: int = 4_000_000,
        pru_clock_mhz: float = 200.0,
        frames: int = 1,
        idle_gap_bits: int = 2,
    ):
        if pin < 0 or pin > 19:
            raise ValueError(f"GPI pin {pin} out of range 0-19")
        self.pin = pin
        self.payload = payload if payload is not None else []
        self.baudrate = baudrate
        self.pru_clock_mhz = pru_clock_mhz
        self.bit_period = int(pru_clock_mhz * 1_000_000 / baudrate)  # cycles per bit
        self.frames = frames
        self.idle_gap_bits = idle_gap_bits  # idle bits between repeated frames

        self._trigger_cycle: int | None = None
        self._transitions: list[tuple[int, int]] = []  # (cycle, pin_value)
        self._stop_bit_indices: list[int] = []  # index into _transitions for each byte's stop bit
        self._idle_value = 1  # UART idle is HIGH
        self._frame_end_cycle = 0
        self._io_port = None

    def start(self, trigger_cycle: int = 0) -> None:
        """Arm the generator: pre-compute all transitions starting at trigger_cycle."""
        self._trigger_cycle = trigger_cycle
        self._transitions = []
        self._stop_bit_indices = []
        cycle = trigger_cycle

        for frame_idx in range(self.frames):
            for byte_val in self.payload:
                # START bit (LOW)
                self._transitions.append((cycle, 0))
                cycle += self.bit_period

                # 8 data bits, LSB first
                for bit_idx in range(8):
                    bit = (byte_val >> bit_idx) & 1
                    self._transitions.append((cycle, bit))
                    cycle += self.bit_period

                # STOP bit (HIGH)
                self._transitions.append((cycle, 1))
                self._stop_bit_indices.append(len(self._transitions) - 1)
                cycle += self.bit_period

            # Idle gap between frames (HIGH for idle_gap_bits bit periods)
            if frame_idx < self.frames - 1:
                self._transitions.append((cycle, 1))
                cycle += self.bit_period * self.idle_gap_bits

        self._frame_end_cycle = cycle

    def inject_framing_error(self, byte_index: int) -> None:
        """Corrupt the stop bit of the specified byte (make it LOW instead of HIGH).

        Must be called AFTER start(). Modifies the pre-computed timeline in place.
        byte_index: 0-based index within the ENTIRE payload across all frames.
        """
        if self._trigger_cycle is None:
            raise RuntimeError("Call start() before inject_framing_error()")
        if byte_index < 0 or byte_index >= len(self._stop_bit_indices):
            raise ValueError(f"byte_index {byte_index} out of range (0-{len(self._stop_bit_indices)-1})")
        stop_idx = self._stop_bit_indices[byte_index]
        cycle, _ = self._transitions[stop_idx]
        self._transitions[stop_idx] = (cycle, 0)  # Force stop bit LOW

    def attach(self, io_port) -> None:
        """Attach this generator to an IOPort. tick() will update the port's GPI."""
        self._io_port = io_port

    def tick(self, cycle: int) -> None:
        """Update the attached IOPort's GPI pin to the correct value for this cycle.

        Must be called BEFORE the PRU instruction executes so R31 reads see the correct bit.
        """
        if self._io_port is None:
            return
        pin_val = self.get_pin_value(cycle)
        if pin_val:
            self._io_port.gpi |= (1 << self.pin)
        else:
            self._io_port.gpi &= ~(1 << self.pin)

    def get_pin_value(self, cycle: int) -> int:
        """Return pin value at the given absolute cycle.

        Before trigger_cycle: returns idle (HIGH).
        After frame_end: returns idle (HIGH).
        During frame: returns the bit value active at that cycle.
        """
        if self._trigger_cycle is None:
            return self._idle_value
        if cycle < self._trigger_cycle:
            return self._idle_value
        if cycle >= self._frame_end_cycle:
            return self._idle_value

        # Find the last transition at or before this cycle
        pin_val = self._idle_value
        for trans_cycle, val in self._transitions:
            if trans_cycle <= cycle:
                pin_val = val
            else:
                break
        return pin_val
