"""XFR bus providing inter-core scratchpad access (XIN/XOUT/XCHG)."""

import logging

from xfr.scratchpad import Scratchpad

log = logging.getLogger(__name__)

SPAD_BANK0 = 10
SPAD_BANK1 = 11
SPAD_BANK2 = 12
IPC_SPAD = 15


class XFRBus:
    """Models the PRU XFR bus for scratchpad-based inter-core communication."""

    def __init__(self):
        self._pads: dict[int, Scratchpad] = {
            SPAD_BANK0: Scratchpad(120),  # R0–R29, 30 × 4 bytes
            SPAD_BANK1: Scratchpad(120),
            SPAD_BANK2: Scratchpad(120),
            IPC_SPAD:   Scratchpad(32),   # R2–R9,  8 × 4 bytes
        }
        # ICSSG_SPP_REG[1] XFR_SHIFT_EN — enables optional XIN/XOUT shift
        self.xfr_shift_en: bool = False
        # Device IDs already warned about, so a polling loop over an
        # unmodelled widget does not emit one warning per iteration.
        self._warned_unknown: set[int] = set()

    def reset(self) -> None:
        """Clear all scratchpad banks and reset XFR configuration to defaults."""
        for pad in self._pads.values():
            pad.data[:] = bytes(len(pad.data))
        self.xfr_shift_en = False
        self._warned_unknown.clear()

    def _get_pad(self, device_id: int) -> Scratchpad | None:
        return self._pads.get(device_id)

    def supports(self, device_id: int) -> bool:
        """Return whether *device_id* is a scratchpad owned by this bus.

        PRUCore also owns accelerator and shifted-SPAD dispatch. Keeping this
        query narrow lets the core recognise a transfer that reaches neither,
        and report it, while still returning the zero/no-op result that an
        unconnected broadside ID produces on hardware.
        """
        return device_id in self._pads

    def _warn_unknown_once(self, opcode: str, device_id: int, effect: str) -> None:
        """Warn about an unmodelled *device_id*, at most once per bus instance."""
        if device_id in self._warned_unknown:
            return
        self._warned_unknown.add(device_id)
        log.warning("%s: unknown device_id %d (0x%02X), %s", opcode, device_id,
                    device_id, effect)

    def xin(self, device_id: int, offset: int, length: int) -> bytes:
        """Read *length* bytes from scratchpad *device_id* at *offset*.

        Returns zeros for unknown device IDs.
        """
        pad = self._get_pad(device_id)
        if pad is None:
            self._warn_unknown_once("XIN", device_id, "returning zeros")
            return bytes(length)
        return pad.read(offset, length)

    def xout(self, device_id: int, offset: int, data: bytes) -> None:
        """Write *data* to scratchpad *device_id* at *offset*.

        Logs a warning for unknown device IDs.
        """
        pad = self._get_pad(device_id)
        if pad is None:
            self._warn_unknown_once("XOUT", device_id, "data discarded")
            return
        pad.write(offset, data)

    def xchg(self, device_id: int, offset: int, data: bytes) -> bytes:
        """Atomically exchange *data* with scratchpad *device_id* at *offset*.

        Logs a warning and returns zeros for unknown device IDs.
        """
        pad = self._get_pad(device_id)
        if pad is None:
            self._warn_unknown_once("XCHG", device_id, "data discarded")
            return bytes(len(data))
        return pad.exchange(offset, data)
