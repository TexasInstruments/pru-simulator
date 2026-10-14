"""TCA9538 as a DeviceModel — the first port onto the generic device contract.

`TCA9538Device` already models the chip's I2C state machine correctly. What it
lacks is a way to be attached to anything except the one hardcoded slot in
`IOPort` (`attach_i2c_device`, with SDA/SCL frozen at pins 1 and 0). This wraps
it in the `DeviceModel` contract so it can sit on arbitrary pins, share a net
with other devices, and report protocol violations.

The wrapper deliberately does not reimplement the state machine — the existing
one is tested (`tests/test_i2c_tca9538_firmware.py`) and reused as-is. What is
added here is the electrical declaration (SDA and SCL are open-drain), event
decode, and the fault checks that make it usable as an oracle.
"""
from __future__ import annotations

from pru_io.device_model import OPEN_DRAIN, DeviceModel
from pru_io.tca9538 import TCA9538Device


class TCA9538Model(DeviceModel):
    """TCA9538 8-bit I2C IO expander on a configurable pin pair."""

    def __init__(self, address: int = 0x23, scl_pin: int = 0, sda_pin: int = 1,
                 name: str = "tca9538"):
        self.name = name
        self.address = address
        self.scl_pin = scl_pin
        self.sda_pin = sda_pin
        # Both I2C lines are open-drain with a pull-up. Declaring SCL as well as
        # SDA is not pedantry: it is what makes clock stretching by any device
        # on this net resolve correctly instead of being overridden by the PRU.
        self.nets = {scl_pin: OPEN_DRAIN, sda_pin: OPEN_DRAIN}

        self.device = TCA9538Device(address=address)
        self._events: list[dict] = []
        self._faults: list[str] = []
        self._prev_scl: int | None = None
        self._prev_sda: int | None = None
        self._driving_low = False
        self._cycle = 0

    # ------------------------------------------------------------------
    # DeviceModel
    # ------------------------------------------------------------------

    def tick(self, cycle: int, bus: int) -> tuple[int, int]:
        self._cycle = cycle
        scl = (bus >> self.scl_pin) & 1
        sda = (bus >> self.sda_pin) & 1

        self._check_faults(cycle, scl, sda)
        self._record_events(cycle, scl, sda)

        # The underlying model returns the RESOLVED wired-AND level, not "am I
        # driving". Those differ exactly when the master is pulling low, and
        # conflating them latches the bus low forever: the slave reports low,
        # that becomes the previous drive, the slave sees low again next cycle
        # and keeps reporting it, so SDA never rises again.
        #
        # Open-drain makes the distinction exact. The slave is driving iff the
        # resolved level came out low while the master had released the line.
        # The underlying model wants sda_MASTER - what the master is driving -
        # while the bus only tells us the resolved level. On an open-drain net
        # those differ exactly while this slave is pulling low, and feeding it
        # the resolved level there makes it read its own ACK as the master
        # driving low, then see a spurious STOP when it releases.
        #
        # A slave only ever drives during its ACK, and the protocol requires the
        # master to have released the line for that. So while we are driving,
        # "master released" is the correct reading - and it is an assumption
        # about the protocol, stated here, not a guess about the waveform.
        sda_master = 1 if self._driving_low else sda

        bus_sda = self.device.step(bool(scl), bool(sda_master))
        self._driving_low = (not bus_sda) and sda_master == 1
        self._prev_scl, self._prev_sda = scl, sda

        if self._driving_low:
            return (1 << self.sda_pin), 0    # pulling SDA low
        return 0, 0                          # released, pull-up wins

    def events(self) -> list[dict]:
        return list(self._events)

    def faults(self) -> list[str]:
        return list(self._faults)

    def get_state(self) -> dict:
        """UI-facing summary: wiring, registers, driven output levels, counts.

        The chip model is write-only (no Input Port register), so the eight
        "LED" levels are the output pins the PRU has enabled: output_reg on
        every pin whose config bit is 0 (output). Input-configured pins are
        reported as not driven.
        """
        chip = self.device.get_state()
        driven = ~chip["config_reg"] & 0xFF
        levels = chip["output_reg"] & driven
        return {
            "name": self.name,
            "model": "tca9538",
            "faults": len(self._faults),
            "address": self.address,
            "scl_pin": self.scl_pin,
            "sda_pin": self.sda_pin,
            "output_reg": chip["output_reg"],
            "polarity_reg": chip["polarity_reg"],
            "config_reg": chip["config_reg"],
            "driven_mask": driven,
            "levels": [(levels >> bit) & 1 for bit in range(8)],
            "protocol_state": self.device.state,
            "saw_start": chip["saw_start"],
            "last_transaction": chip["last_transaction"],
            "events": len(self._events),
        }

    def reset(self) -> None:
        """Reset protocol state and clear findings while keeping the device attached."""
        self.device = TCA9538Device(address=self.address)
        self._events.clear()
        self._faults.clear()
        self._prev_scl = None
        self._prev_sda = None
        self._driving_low = False
        self._cycle = 0

    def snapshot(self) -> dict:
        """Capture wrapper and I2C protocol state for simulator step-back."""
        return {
            "device": self.device.snapshot(),
            "events": [dict(event) for event in self._events],
            "faults": list(self._faults),
            "prev_scl": self._prev_scl,
            "prev_sda": self._prev_sda,
            "driving_low": self._driving_low,
            "cycle": self._cycle,
        }

    def restore(self, snap: dict) -> None:
        self.device.restore(snap["device"])
        self._events = [dict(event) for event in snap["events"]]
        self._faults = list(snap["faults"])
        self._prev_scl = snap["prev_scl"]
        self._prev_sda = snap["prev_sda"]
        self._driving_low = snap["driving_low"]
        self._cycle = snap["cycle"]

    # ------------------------------------------------------------------
    # Decode and violation checks
    # ------------------------------------------------------------------

    def _record_events(self, cycle: int, scl: int, sda: int) -> None:
        if self._prev_scl is None:
            return
        if scl and self._prev_sda == 1 and sda == 0:
            self._events.append({"cycle": cycle, "kind": "start"})
        elif scl and self._prev_sda == 0 and sda == 1:
            self._events.append({"cycle": cycle, "kind": "stop"})

    def _check_faults(self, cycle: int, scl: int, sda: int) -> None:
        """The violations an I2C slave can actually observe on the wire.

        Deliberately narrow: only things that are unambiguously illegal
        regardless of what the master intended. A check that needs to guess the
        master's intent produces false findings, and a noisy oracle gets muted.
        """
        if self._prev_scl is None:
            return

        # SDA may only change while SCL is low. A transition on a high clock is
        # a START or a STOP by definition, so it is illegal in the middle of a
        # byte - that is the classic bit-banged-I2C ordering bug, driving SDA
        # before pulling SCL down.
        if scl and self._prev_scl and sda != self._prev_sda:
            if self.device.state not in ("IDLE",):
                self._faults.append(
                    f"cycle {cycle}: SDA changed while SCL high mid-transfer "
                    f"(state={self.device.state}) - START/STOP inside a byte")


def attach_tca9538(bus, address: int = 0x23, scl_pin: int = 0, sda_pin: int = 1):
    """Convenience constructor mirroring IOPort.attach_i2c_device's intent."""
    return bus.attach(TCA9538Model(address=address, scl_pin=scl_pin,
                                   sda_pin=sda_pin))
