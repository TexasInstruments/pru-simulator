"""Simulator-tested XFRDMA broadside model.

TRM SPRUIM2J §6.4.6.3.2 and Table 6-60 map the four XFR2PSI copies to
global broadside IDs 0x50..0x53.  Section 6.4.6.3.2.1 makes their XIDs
block-local: 0x50 selects status (XID 0), while 0x51, 0x52, and 0x53 select
PSI-L threads 1, 2, and 3.  Table 6-108 defines status as two words:
rx_ready[3:0] then tx_ready[3:0].  Tables 6-109 and 6-110 describe the
sideband/data layout used by XOUT and XIN.

This model deliberately does not model the sideband word, ``rx_bytes``,
``tx_data_type`` packing, or PSI-L arbitration.  ``tx_max`` is a simulator
constructor parameter, not a TRM-derived FIFO depth.

Two behaviours here are simulator policy rather than documented silicon, and
are called out so a run is not mistaken for evidence about either:

* Table 6-108 says only ``rx_ready[3:0]``/``tx_ready[3:0]``, "one bit per PSI-L
  thread", without naming the bit order.  This model uses ``bit == thread``, so
  the threads reachable through 0x51..0x53 report in bits 1..3 and bit 0 is
  always clear.  Firmware that assumes thread 1 lands in bit 0 will disagree.
* Section 6.4.6.3.2.1.1 defines XID 0 for XIN only and says nothing about
  writing it.  An XOUT or XCHG to 0x50 is therefore discarded and counted in
  :attr:`XFRDMAAccelerator.status_writes` rather than raised, because a trap
  would be this simulator's policy presented as hardware behaviour.
"""

from __future__ import annotations

import logging
from collections import deque

from xfr.accelerator import Accelerator


logger = logging.getLogger(__name__)


XFRDMA_STATUS_ID = 0x50
XFRDMA_THREAD_IDS = (0x51, 0x52, 0x53)


class XFRDMABridge:
    """Shared, deterministic FIFO state for the four XFRDMA broadside IDs."""

    def __init__(self, tx_max: int = 8) -> None:
        if tx_max < 1:
            raise ValueError("tx_max must be at least one packet")
        self.tx_max = tx_max
        self.rx_fifos: dict[int, deque[bytearray]] = {thread: deque() for thread in range(1, 4)}
        self.tx_fifos: dict[int, deque[bytes]] = {thread: deque() for thread in range(1, 4)}

    def inject_rx(self, thread: int, packet: bytes) -> None:
        """Append a deterministic receive packet to *thread*."""
        self._check_thread(thread)
        self.rx_fifos[thread].append(bytearray(packet))

    def drain_tx(self, thread: int) -> list[bytes]:
        """Return and clear queued transmit packets for *thread*."""
        self._check_thread(thread)
        packets = list(self.tx_fifos[thread])
        self.tx_fifos[thread].clear()
        return packets

    def fill_tx(self, thread: int, packet: bytes = b"") -> None:
        """Fill a transmit slot deterministically; intended for test setup."""
        self._check_thread(thread)
        if len(self.tx_fifos[thread]) >= self.tx_max:
            raise ValueError("transmit FIFO is full")
        self.tx_fifos[thread].append(bytes(packet))

    def rx_depth(self, thread: int) -> int:
        self._check_thread(thread)
        return len(self.rx_fifos[thread])

    def _check_thread(self, thread: int) -> None:
        if thread not in self.rx_fifos:
            raise ValueError("XFRDMA thread must be 1, 2, or 3")

    def reset(self) -> None:
        for fifo in (*self.rx_fifos.values(), *self.tx_fifos.values()):
            fifo.clear()


class XFRDMAAccelerator(Accelerator):
    """One XFRDMA global-ID endpoint backed by an :class:`XFRDMABridge`."""

    DEVICE_ID = XFRDMA_STATUS_ID

    def __init__(self, bridge: XFRDMABridge, device_id: int = XFRDMA_STATUS_ID) -> None:
        if device_id not in (XFRDMA_STATUS_ID, *XFRDMA_THREAD_IDS):
            raise ValueError("invalid XFRDMA broadside ID")
        self.bridge = bridge
        self.device_id = device_id
        self.thread = device_id - XFRDMA_STATUS_ID
        self.hold_pc = False
        self.status_writes: dict[str, int] = {}

    def xin(self, start_reg: int, length: int, start_byte: int = 0) -> bytes:
        self.hold_pc = False
        if self.thread == 0:
            return self._status()[:length].ljust(length, b"\0")
        fifo = self.bridge.rx_fifos[self.thread]
        if not fifo:
            self.hold_pc = True
            return bytes(length)
        return self._consume_rx(length)

    def xout(self, start_reg: int, data: bytes, start_byte: int = 0) -> None:
        self.hold_pc = False
        if self.thread == 0:
            self._note_status_write("XOUT")
            return
        fifo = self.bridge.tx_fifos[self.thread]
        if len(fifo) >= self.bridge.tx_max:
            self.hold_pc = True
            return
        fifo.append(bytes(data))

    def xchg(self, start_reg: int, data: bytes, start_byte: int = 0) -> bytes:
        self.hold_pc = False
        if self.thread == 0:
            self._note_status_write("XCHG")
            return bytes(data)
        rx_fifo = self.bridge.rx_fifos[self.thread]
        tx_fifo = self.bridge.tx_fifos[self.thread]
        # Check both sides before consuming RX: a held XCHG is side-effect free.
        if not rx_fifo or len(tx_fifo) >= self.bridge.tx_max:
            self.hold_pc = True
            return bytes(data)
        received = self._consume_rx(len(data))
        tx_fifo.append(bytes(data))
        return received

    def reset(self) -> None:
        self.hold_pc = False
        self.status_writes.clear()
        self.bridge.reset()

    def _status(self) -> bytes:
        rx_ready = sum(1 << thread for thread, fifo in self.bridge.rx_fifos.items() if fifo)
        tx_ready = sum(
            1 << thread
            for thread, fifo in self.bridge.tx_fifos.items()
            if len(fifo) < self.bridge.tx_max
        )
        return rx_ready.to_bytes(4, "little") + tx_ready.to_bytes(4, "little")

    def _consume_rx(self, length: int) -> bytes:
        result = bytearray()
        fifo = self.bridge.rx_fifos[self.thread]
        while len(result) < length and fifo:
            packet = fifo[0]
            take = min(length - len(result), len(packet))
            result.extend(packet[:take])
            del packet[:take]
            if not packet:
                fifo.popleft()
        return bytes(result).ljust(length, b"\0")

    def _note_status_write(self, opcode: str) -> None:
        """Record a write to the status XID, which the TRM only defines for XIN.

        SPRUIM2J 6.4.6.3.2.1.1 gives XID 0 a read-only status meaning and says
        nothing about writing it, so this simulator discards the transfer rather
        than trapping -- a trap would be a simulator policy presented as silicon
        behaviour.  The event is recorded once per opcode so a run that depends
        on it is still auditable.
        """
        if opcode in self.status_writes:
            self.status_writes[opcode] += 1
            return
        self.status_writes[opcode] = 1
        logger.warning(
            "%s to XFRDMA status ID 0x%02X is not defined by SPRUIM2J "
            "6.4.6.3.2.1.1; the transfer is discarded. Later writes are counted "
            "in XFRDMAAccelerator.status_writes but not logged again.",
            opcode,
            self.device_id,
        )
