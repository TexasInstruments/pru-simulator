# pru_io/io_port.py
"""IOPort — models the PRU's R30 (GPO) and R31 (GPI) 20-bit I/O registers.

When the SD filter is attached and sd_en is active, R30/R31 are routed
through the sigma-delta filter interface instead of raw GPIO.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pru_io.sd_filter import SigmaDeltaFilter

_MASK_20 = 0x000FFFFF


class IOPort:
    """20-bit general-purpose output (GPO) and input (GPI) port with SD mode."""

    def __init__(self) -> None:
        self.gpo: int = 0             # driven by writes to R30
        self.gpi: int = 0             # sampled by reads from R31
        self.sd_filter: SigmaDeltaFilter | None = None
        self.perif = None             # type: PeripheralInterface | None
        self.loopback_mask: int = 0   # which GPO bits feed back to GPI (5 groups × 4 bits)
        self.uart_generator = None  # type: UARTFrameGenerator | None

    # ------------------------------------------------------------------
    # R30 / GPO
    # ------------------------------------------------------------------

    def write_r30(self, value: int, wstrb: int = 0xF) -> None:
        """Set GPO state from *value*. If SD filter attached, also process R30.

        *wstrb* is the 4-bit byte write-strobe mask (which bytes of R30 the
        instruction wrote); the Peripheral Interface uses it to gate FIFO
        pushes / clock-mode / RX-enable latches.

        When sd_en (R30 bit 25) is clear, applies loopback_mask: masked GPO bits
        are immediately reflected into GPI (combinatorial, matching hardware).
        """
        self.gpo = value & _MASK_20
        if self.perif is not None:
            self.perif.process_r30(value, wstrb)
        if self.sd_filter is not None:
            self.sd_filter.process_r30(value)
        if not (value & (1 << 25)) and self.loopback_mask:
            self.gpi = (self.gpi & ~self.loopback_mask) | (self.gpo & self.loopback_mask)

    def set_loopback_group(self, group: int, enabled: bool) -> None:
        """Enable/disable GPO→GPI loopback for a 4-bit group (0–4).

        group 0 → bits 3:0, group 1 → bits 7:4, ..., group 4 → bits 19:16.
        Raises ValueError if group is out of range 0-4.
        """
        if group < 0 or group > 4:
            raise ValueError(f"Loopback group {group} out of range 0-4")
        mask = 0xF << (group * 4)
        if enabled:
            self.loopback_mask |= mask
        else:
            self.loopback_mask &= ~mask

    # ------------------------------------------------------------------
    # R31 / GPI
    # ------------------------------------------------------------------

    def read_r31(self) -> int:
        """Return R31 value: Peripheral status if perif mode active, else SD
        status if sd_en active, else GPI masked to 20 bits."""
        if self.perif is not None and self.perif.enabled:
            return self.perif.get_r31_status()
        if self.sd_filter is not None and self.sd_filter.sd_en:
            return self.sd_filter.get_r31_status()
        return self.gpi & _MASK_20

    def write_r31(self, value: int) -> None:
        """Process R31 write as a Peripheral command (if perif mode active),
        else as an SD command (if SD mode active)."""
        if self.perif is not None and self.perif.enabled:
            self.perif.process_r31_command(value)
            return
        if self.sd_filter is not None and self.sd_filter.sd_en:
            self.sd_filter.process_r31_command(value)

    def set_gpi_pin(self, pin: int, value: bool) -> None:
        """Set or clear a single GPI pin (0-19).

        Raises ValueError if *pin* is out of range.
        """
        if pin < 0 or pin > 19:
            raise ValueError(f"GPI pin index {pin} out of range 0-19")
        if value:
            self.gpi |= 1 << pin
        else:
            self.gpi &= ~(1 << pin)
        self.gpi &= _MASK_20

    def set_gpi_word(self, value: int) -> None:
        """Set all GPI bits at once, masked to 20 bits."""
        self.gpi = value & _MASK_20

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear driven-output state and reset the attached Peripheral Interface.

        Called from `PRUCore.reset()`: R30 is zeroed by the register-file reset,
        so GPO follows it, and the peripheral drops its latched status bits
        (overrun/underrun, RX valid/overflow, busy) and FIFOs.  GPI is left
        untouched — it models external stimulus (UI input pins, injected
        frames), not core state.
        """
        self.gpo = 0
        if self.perif is not None:
            self.perif.reset()

    # ------------------------------------------------------------------
    # Pin-list helpers
    # ------------------------------------------------------------------

    def get_gpo_pins(self) -> list[int]:
        """Return a list of 20 integers (0 or 1) representing each GPO pin."""
        return [(self.gpo >> i) & 1 for i in range(20)]

    def get_gpi_pins(self) -> list[int]:
        """Return a list of 20 integers (0 or 1) representing each GPI pin."""
        return [(self.gpi >> i) & 1 for i in range(20)]
