# pru_io/io_port.py
"""IOPort — models the PRU's R30 (GPO) and R31 (GPI) 20-bit I/O registers.

When the SD filter is attached and sd_en is active, R30/R31 are routed
through the sigma-delta filter interface instead of raw GPIO.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Callable

from pru_io.device_model import DeviceBus

if TYPE_CHECKING:
    from pru_io.sd_filter import SigmaDeltaFilter
    from pru_io.tca9538 import TCA9538Device

_MASK_20 = 0x000FFFFF
_I2C_SCL_BIT = 0
_I2C_SDA_BIT = 1


class IOPort:
    """20-bit general-purpose output (GPO) and input (GPI) port with SD mode."""

    def __init__(self) -> None:
        self.gpo: int = 0             # driven by writes to R30
        self.gpi: int = 0             # sampled by reads from R31
        self.sd_filter: SigmaDeltaFilter | None = None
        self.perif = None             # type: PeripheralInterface | None
        self.loopback_mask: int = 0   # which GPO bits feed back to GPI (5 groups × 4 bits)
        self.uart_generator = None  # type: UARTFrameGenerator | None
        self.i2c_device = None        # type: TCA9538Device | None
        # Generic device bus. It stays inert until a device or wire is attached.
        self.device_bus = DeviceBus()
        self._device_endpoint: str | None = None
        self._device_pin_mask = 0
        self.gpo_drive_mask: int = 0
        self._device_cycle = 0        # last cycle seen, for write-triggered settles

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
        old_gpo = self.gpo
        self.gpo = value & _MASK_20
        if self.perif is not None:
            self.perif.process_r30(value, wstrb)
        if self.sd_filter is not None:
            self.sd_filter.process_r30(value)
        if not (value & (1 << 25)) and self.loopback_mask:
            self.gpi = (self.gpi & ~self.loopback_mask) | (self.gpo & self.loopback_mask)
        if self.i2c_device is not None:
            scl = bool(value & (1 << _I2C_SCL_BIT))
            sda_master = bool(value & (1 << _I2C_SDA_BIT))
            bus_sda = self.i2c_device.step(scl, sda_master)
            if bus_sda:
                self.gpi |= (1 << _I2C_SDA_BIT)
            else:
                self.gpi &= ~(1 << _I2C_SDA_BIT)
        # Reactive models see actual pin changes; repeated writes of the same
        # value must not manufacture protocol edges.
        if old_gpo != self.gpo and self.device_bus.active:
            self.tick_devices(self._device_cycle)

    # ------------------------------------------------------------------
    # Generic device bus
    # ------------------------------------------------------------------

    def attach_device(self, device):
        """Attach a DeviceModel to the pins it declares in `nets`.

        The generic replacement for attach_i2c_device: any number of devices,
        on any pins, sharing nets. Opt-in, like everything else here.
        """
        return self.device_bus.attach(device, port=self._device_endpoint,
                                      cycle=self._device_cycle)

    def bind_device_bus(self, bus: DeviceBus, endpoint: str) -> None:
        """Join a simulator-wide electrical bus under this core's name."""
        self.device_bus = bus
        self._device_endpoint = endpoint
        bus.register_port(endpoint, self)

    def set_gpo_drive_mask(self, mask: int) -> None:
        """Set which pins this core is currently driving as GPIO outputs."""
        self.gpo_drive_mask = mask & _MASK_20
        if self._device_endpoint is None:
            self.device_bus.set_pru_drive_mask(self.gpo_drive_mask)
            if self.device_bus.active:
                self.tick_devices(self._device_cycle)
        else:
            self.device_bus.set_core_drive_mask(
                self._device_endpoint, self.gpo_drive_mask,
                cycle=self._device_cycle)

    def tick_devices(self, cycle: int, time_only: bool = False) -> None:
        """One bus settle.

        Called from `write_r30` when a pin changes. Time-driven models advance
        through `advance_devices()` with the elapsed cycle count. Reactive
        devices are settled only on pin changes:
        a bus slave detects edges, so ticking it repeatedly between changes
        feeds it its own last output as fresh input and derails the state
        machine.

        Only pins some attached device actually drives are written back into
        GPI. Pins nothing drives keep whatever loopback, the UART generator or
        `set_gpi_pin` put there - attaching a device must not quietly take over
        the whole port.
        """
        if not self.device_bus.active:
            return
        if time_only:
            self.advance_devices(1)
            return
        bus = self.device_bus.settle(cycle, self.gpo, port=self._device_endpoint)
        if self._device_endpoint is None:
            self._update_device_gpi(bus)

    def advance_devices(self, elapsed_cycles: int,
                        after_cycle: Callable[[int, int], None] | None = None) -> None:
        """Advance time-driven models and optionally observe each elapsed cycle.

        `after_cycle` receives the new device-cycle number and resolved GPI word
        after the bus has advanced. It is used by other generic cycle-based
        peripherals that need the pin state for every elapsed cycle.
        """
        if elapsed_cycles <= 0:
            return
        has_time_driven = self.device_bus.has_time_driven(self._device_endpoint)
        if not has_time_driven and after_cycle is None:
            self._device_cycle += elapsed_cycles
            return
        for _ in range(elapsed_cycles):
            cycle = self._device_cycle + 1
            if has_time_driven:
                self.device_bus.advance_cycles(
                    self._device_endpoint, 1, first_cycle=cycle)
                if self._device_endpoint is None:
                    self._update_device_gpi(self.device_bus._bus)
            self._device_cycle = cycle
            if after_cycle is not None:
                after_cycle(cycle, self.gpi)

    def detach_device(self, device) -> None:
        """Detach one model and release its previous output pins."""
        self.device_bus.detach(device)
        if self._device_endpoint is None:
            self._update_device_gpi(self.device_bus._bus)

    def _update_device_gpi(self, bus: int) -> None:
        driven = 0
        for device in self.device_bus.devices:
            for pin in device.nets:
                driven |= 1 << pin
        managed = self._device_pin_mask | driven
        self.gpi = ((self.gpi & ~managed) | (bus & managed)) & _MASK_20
        self._device_pin_mask = driven

    def attach_i2c_device(self, device: "TCA9538Device | None") -> None:
        """Attach (or detach with None) an I2C slave model on SCL=bit0/SDA=bit1.

        Opt-in: bits 0/1 already mean SCLK/MOSI to spi_master_tx.asm, so an
        attached device must never be on by default.
        """
        self.i2c_device = device

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
        self._device_cycle = 0
        if self.perif is not None:
            self.perif.reset()
        # Devices stay attached across a reset (they model external hardware,
        # which does not vanish), but their state and any recorded faults do
        # not survive - a stale fault from the previous run would be read as a
        # finding about this one.
        self.device_bus.reset_port(self._device_endpoint, gpo=self.gpo)
        if self._device_endpoint is None:
            self._update_device_gpi(self.device_bus._bus)

    # ------------------------------------------------------------------
    # Pin-list helpers
    # ------------------------------------------------------------------

    def get_gpo_pins(self) -> list[int]:
        """Return a list of 20 integers (0 or 1) representing each GPO pin."""
        return [(self.gpo >> i) & 1 for i in range(20)]

    def get_gpi_pins(self) -> list[int]:
        """Return a list of 20 integers (0 or 1) representing each GPI pin."""
        return [(self.gpi >> i) & 1 for i in range(20)]
