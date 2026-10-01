"""SSI Encoder Generator â€” emulates an absolute SSI encoder's data line.

Unlike the UART frame generator (which plays a fixed baud-timed waveform), an
SSI slave is *clock-driven*: the master (the PRU reader) drives the SSI clock,
and the encoder shifts its position word out one bit per clock edge. So this
generator is reactive â€” on every step it watches the reader's clock output
(a GPO bit) for edges and drives the reader's data input (a GPI bit)
accordingly. It holds no fixed timeline and works at any clock frequency.

Protocol modelled (matches the reader firmware and the ssi_data_out reference):
  * Idle: clock HIGH, data HIGH.
  * Frame start: the first FALLING clock edge (master pulls clock low).
  * Each subsequent RISING edge shifts out the next bit, MSB-first.
  * The master samples data during the high phase (before the next falling edge).
  * Frame ends on the falling edge after the last bit; data returns to idle HIGH.
  * Data is straight BINARY (no Gray coding).
"""

from __future__ import annotations


class SSIEncoderGenerator:
    """Drives an SSI encoder's data line on a GPI pin in response to a clock GPO.

    Attach to an IOPort, then tick() every instruction BEFORE it executes so a
    same-instruction R31 read sees the freshly-shifted bit. The generator reads
    the clock level from io_port.gpo[clk_pin] and writes the data bit to
    io_port.gpi[data_pin].
    """

    def __init__(
        self,
        clk_pin: int = 0,
        data_pin: int = 8,
        value: int = 0,
        bits: int = 12,
        msb_first: bool = True,
        idle_high: bool = True,
    ):
        if clk_pin < 0 or clk_pin > 19:
            raise ValueError(f"clk_pin {clk_pin} out of range 0-19")
        if data_pin < 0 or data_pin > 19:
            raise ValueError(f"data_pin {data_pin} out of range 0-19")
        if clk_pin == data_pin:
            raise ValueError("clk_pin and data_pin must differ")
        if bits < 1 or bits > 32:
            raise ValueError(f"bits {bits} out of range 1-32")

        self.clk_pin = clk_pin
        self.data_pin = data_pin
        self.value = value & ((1 << bits) - 1)
        self.bits = bits
        self.msb_first = msb_first
        self.idle_value = 1 if idle_high else 0

        self._io_port = None
        # Runtime state (also captured by snapshot/restore for step-back)
        self._prev_clk = self.idle_value
        self._state = "idle"          # "idle" | "active"
        self._bit_index = 0           # next bit to present within the frame
        self._latched = self.value    # value frozen at frame start
        self.frames_captured = 0

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def attach(self, io_port) -> None:
        """Attach to an IOPort and prime edge detection + idle data level."""
        self._io_port = io_port
        # Seed prev_clk with the actual current clock level to avoid a spurious
        # first edge, and drive the data line to its idle level.
        self._prev_clk = (io_port.gpo >> self.clk_pin) & 1
        self._drive_data(self.idle_value)

    def set_value(self, value: int) -> None:
        """Update the position word presented on the NEXT frame."""
        self.value = value & ((1 << self.bits) - 1)

    # ------------------------------------------------------------------
    # Per-step update
    # ------------------------------------------------------------------

    def tick(self, cycle: int = 0) -> None:
        """Advance the encoder one instruction: detect clock edges and shift.

        Call BEFORE the PRU instruction executes so R31 reads see the new bit.
        The *cycle* argument is accepted for symmetry with UARTFrameGenerator
        but is unused â€” this generator is purely edge-driven.
        """
        if self._io_port is None:
            return

        clk = (self._io_port.gpo >> self.clk_pin) & 1
        rising = self._prev_clk == 0 and clk == 1
        falling = self._prev_clk == 1 and clk == 0
        self._prev_clk = clk

        if self._state == "idle":
            if falling:                       # master pulled clock low -> frame start
                self._state = "active"
                self._bit_index = 0
                self._latched = self.value
        else:  # active
            if rising:
                if self._bit_index < self.bits:
                    self._drive_data(self._bit_at(self._bit_index))
                    self._bit_index += 1
            elif falling:
                if self._bit_index >= self.bits:   # last bit already shifted
                    self._state = "idle"
                    self.frames_captured += 1
                    self._drive_data(self.idle_value)

    def _bit_at(self, index: int) -> int:
        """Return the bit to present at frame position *index* (0 = first out)."""
        if self.msb_first:
            shift = self.bits - 1 - index
        else:
            shift = index
        return (self._latched >> shift) & 1

    def _drive_data(self, level: int) -> None:
        if level:
            self._io_port.gpi |= (1 << self.data_pin)
        else:
            self._io_port.gpi &= ~(1 << self.data_pin)

    # ------------------------------------------------------------------
    # Step-back / UI support
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Capture mutable state so PRUCore step-back can restore it exactly."""
        return {
            "prev_clk": self._prev_clk,
            "state": self._state,
            "bit_index": self._bit_index,
            "latched": self._latched,
            "value": self.value,
            "frames_captured": self.frames_captured,
        }

    def restore(self, snap: dict) -> None:
        """Restore state captured by snapshot()."""
        self._prev_clk = snap["prev_clk"]
        self._state = snap["state"]
        self._bit_index = snap["bit_index"]
        self._latched = snap["latched"]
        self.value = snap["value"]
        self.frames_captured = snap["frames_captured"]

    def get_state(self) -> dict:
        """Return a JSON-friendly view for the UI."""
        return {
            "clk_pin": self.clk_pin,
            "data_pin": self.data_pin,
            "value": self.value,
            "bits": self.bits,
            "msb_first": self.msb_first,
            "state": self._state,
            "bit_index": self._bit_index,
            "frames_captured": self.frames_captured,
        }
