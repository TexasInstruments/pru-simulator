"""DeviceModel — one contract for anything wired to the PRU's pins.

WHY
---
The simulator models the PRU well and the *wire* not at all. `IOPort` holds
`gpo`/`gpi` words, and the single external device it supports is bolted on:
`attach_i2c_device()` takes exactly one TCA9538 and hardcodes its SDA/SCL bit
positions. Every other device we have grew its own shape:

    BissCEncoder.next_bit() / .line_at(t_ns)     clock-edge driven, returns a level
    TCA9538Device.step(scl, sda_master) -> sda   pin-level, wired-AND
    Loopback.sample(channel, t_ns)               a wire, not a device

So nothing can be attached generically to a block under test, and adding an
encoder means editing IOPort again.

This module is that missing contract. It deliberately does NOT extend
`perif.peripheral_interface.PeripheralInterface`: despite the name, that class
models the AM243x *on-chip* PERIF SCU — it decodes R30/R31 for a hardware block
inside the PRU subsystem. A device on the far end of a pin is a different thing
and shares none of its register interface.

It does follow this codebase's established convention for attachable models —
`reset()` / `snapshot()` / `restore()` / `get_state()`, as `SigmaDeltaFilter`,
`TCA9538Device` and `Loopback` all do — so the UI's step-back and state views
keep working without special-casing.

ELECTRICAL MODEL
----------------
`tick()` returns what the device *drives*, not the resulting bus level: a
`(drive_mask, drive_values)` pair. The bus resolves them, because with more than
one device on a net only the bus can. Two net types:

    OPEN_DRAIN  a device may only pull low; the net is the wired-AND of every
                driver, with a pull-up supplying the idle high. I2C, 1-Wire,
                I3C, and the SDA half of anything bidirectional.
    PUSH_PULL   one driver at a time. Two devices driving opposite levels is
                contention, and it is recorded as a fault rather than silently
                resolved — that is a real short on real hardware, and a model
                that quietly picks a winner hides a design error.

FAULTS ARE THE POINT
--------------------
A device model that can only produce plausible waveforms is decoration. Its
value is `faults()`: the things it saw that the protocol forbids. A model that
cannot reject a wrong firmware is not an oracle, and every model here ships a
test that drives it with deliberately broken firmware and requires a fault.
"""
from __future__ import annotations

import abc

OPEN_DRAIN = "open_drain"
PUSH_PULL = "push_pull"

_MASK_20 = (1 << 20) - 1


class DeviceModel(abc.ABC):
    """A device on the far end of one or more PRU pins.

    Subclasses see the pins the PRU drives and say what they drive back. What
    `tick()` receives is the PRU's output for THIS cycle resolved against every
    device's drive from the previous one — so a device reacts to the PRU
    immediately, but never to the result of its own drive within the same cycle.
    """

    #: Human-readable, appears in fault text and UI state.
    name: str = "device"

    #: Net type per pin: {pin_index: OPEN_DRAIN | PUSH_PULL}. Pins absent from
    #: this map are inputs to the device only — it observes, never drives them.
    nets: dict[int, str] = {}

    #: Does this device change state on its own, with no pin activity?
    #:
    #: False (default) means purely reactive: it is ticked when a driver
    #: changes the pins, and nothing else. That is right for bus slaves —
    #: I2C, SPI, 1-Wire — and it matters, because ticking an edge-detecting
    #: state machine repeatedly between pin changes feeds it its own output as
    #: fresh input and derails it.
    #:
    #: True means the device also advances with time: an encoder rotating, a
    #: free-running oscillator, a sensor sampling on its own clock. Those get
    #: an additional tick every core cycle.
    time_driven: bool = False

    @abc.abstractmethod
    def tick(self, cycle: int, bus: int) -> tuple[int, int]:
        """Advance one PRU cycle.

        *bus* is the resolved 20-bit pin state as the device sees it: the PRU's
        output for this cycle, combined with every device's drive from the
        previous settle.

        Returns ``(drive_mask, drive_values)``. Bits set in *drive_mask* are
        driven by this device to the corresponding bit of *drive_values*; bits
        clear are released (high-Z). Returning ``(0, 0)`` means "driving
        nothing this cycle", which is the correct idle for an open-drain device.
        """

    def events(self) -> list[dict]:
        """Protocol-level decode: what this device believes it received.

        Each event is a dict with at least ``{"cycle": int, "kind": str}``.
        This is what gets diffed against the independent bus-cosim decoder —
        two implementations agreeing is worth far more than one asserting.
        """
        return []

    def faults(self) -> list[str]:
        """Protocol violations observed. Non-empty means the firmware is wrong
        (or this model is — investigate, do not assume)."""
        return []

    # -- codebase convention: reset / snapshot / restore / get_state ---------

    def reset(self) -> None:
        """Hardware reset. Must clear events and faults."""

    def snapshot(self) -> dict:
        """Full internal state for step-back."""
        return {}

    def restore(self, snap: dict) -> None:
        """Inverse of snapshot()."""

    def get_state(self) -> dict:
        """UI-facing summary. Deliberately coarser than snapshot()."""
        return {"name": self.name, "faults": len(self.faults())}


class DeviceBus:
    """Resolves the PRU's outputs against every attached device's drive.

    Attach to an IOPort and call `settle(cycle, gpo)` once per PRU cycle; the
    returned word is what R31 should sample.
    """

    def __init__(self) -> None:
        self.devices: list[DeviceModel] = []
        self.contentions: list[str] = []
        self._last_drives: list = []
        self._bus = _MASK_20          # idle: pull-ups high
        self._pru_drive_mask = _MASK_20

    def attach(self, device: DeviceModel) -> DeviceModel:
        """Attach a device. Never enabled by default anywhere else — the caller
        decides, per the same rule as IOPort.attach_i2c_device."""
        self.devices.append(device)
        return device

    def detach_all(self) -> None:
        self.devices.clear()

    def set_pru_drive_mask(self, mask: int) -> None:
        """Which pins the PRU is actually driving (the rest are inputs).

        Defaults to all 20, matching IOPort's current behaviour where `gpo` is
        taken as the driven state. A block that configures pins as inputs should
        narrow this, otherwise the PRU fights every device on those pins.
        """
        self._pru_drive_mask = mask & _MASK_20

    def settle(self, cycle: int, gpo: int) -> int:
        """One cycle of bus resolution. Returns the level every input samples.

        Two-phase, because the ordering matters and the obvious single phase is
        wrong in one direction or the other:

          1. Resolve the PRU's CURRENT output against every device's PREVIOUS
             drive, and show that to the devices. A device must see what the
             PRU is doing this cycle - an I2C slave that noticed SDA a cycle
             late would miss a START.
          2. Re-resolve with the drives the devices just produced, and return
             that as what R31 samples.

        So a device sees the PRU immediately but never the result of its own
        drive within the same cycle, which is the part real silicon cannot do
        either.
        """
        provisional = self._resolve(cycle, gpo, self._last_drives,
                                    record_contention=False)
        drives = [(d, d.tick(cycle, provisional)) for d in self.devices]
        self._last_drives = drives

        bus = self._resolve(cycle, gpo, drives, record_contention=True)
        self._bus = bus
        return bus

    def _resolve(self, cycle, gpo, drives, record_contention):
        bus = _MASK_20
        for pin in range(20):
            bit = 1 << pin
            net = self._net_type(pin)
            drivers = []
            if self._pru_drive_mask & bit:
                drivers.append(("PRU", (gpo >> pin) & 1))
            for dev, (mask, values) in drives:
                if mask & bit:
                    drivers.append((dev.name, (values >> pin) & 1))

            if not drivers:
                level = 1                      # pull-up
            elif net == OPEN_DRAIN:
                level = 0 if any(v == 0 for _, v in drivers) else 1
            else:
                level = drivers[0][1]
                if record_contention and len({v for _, v in drivers}) > 1:
                    who = ", ".join(f"{n}={v}" for n, v in drivers)
                    self.contentions.append(
                        f"cycle {cycle}: push-pull contention on pin {pin} ({who})")

            if level:
                bus |= bit
            else:
                bus &= ~bit

        return bus

    def _net_type(self, pin: int) -> str:
        """A pin is open-drain if ANY attached device declares it so — one
        open-drain device makes the whole net open-drain, which is how a real
        bus behaves."""
        for dev in self.devices:
            if dev.nets.get(pin) == OPEN_DRAIN:
                return OPEN_DRAIN
        return PUSH_PULL

    # -- aggregate views -----------------------------------------------------

    def events(self) -> list[dict]:
        out = []
        for dev in self.devices:
            for ev in dev.events():
                out.append({**ev, "device": dev.name})
        return sorted(out, key=lambda e: e.get("cycle", 0))

    def faults(self) -> list[str]:
        out = list(self.contentions)
        for dev in self.devices:
            out.extend(f"{dev.name}: {f}" for f in dev.faults())
        return out

    def reset(self) -> None:
        self.contentions.clear()
        self._last_drives = []
        self._bus = _MASK_20
        for dev in self.devices:
            dev.reset()

    def snapshot(self) -> dict:
        return {"bus": self._bus, "contentions": list(self.contentions),
                "devices": [d.snapshot() for d in self.devices]}

    def restore(self, snap: dict) -> None:
        self._bus = snap["bus"]
        self.contentions = list(snap["contentions"])
        for dev, ds in zip(self.devices, snap["devices"]):
            dev.restore(ds)

    def get_state(self) -> dict:
        return {"bus": self._bus,
                "contentions": len(self.contentions),
                "devices": [d.get_state() for d in self.devices]}
