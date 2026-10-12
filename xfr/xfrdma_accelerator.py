"""XFRDMA (XFR2PSI) broadside model, checked against LP-AM243 silicon.

TRM SPRUIM2J §6.4.6.3.2.1 makes the XFRDMA XIDs block-local: XID 0 is
status and "XID N is for the N-th thread".  How many threads exist, and which
can transmit, the TRM leaves to configuration.  TI's own
``icss_xfer_defines.inc`` (MCU+ SDK, AM243x) names them for this device:

    XFR2PSI_STAT_XID 80 (0x50)   status
    XFR2PSI_RD0_XID  81 (0x51)   read widget 0     XFR2PSI_WR0_XID 81 (0x51)
    XFR2PSI_RD1_XID  82 (0x52)   read widget 1
    XFR2PSI_RD2_XID  83 (0x53)   read widget 2
    XFR2PSI_RD3_XID  84 (0x54)   read widget 3

so there are four receive threads at 0x51..0x54 and one transmit thread, at
0x51.  This model uses that map.  Table 6-108 defines status as two words,
rx_ready[3:0] then tx_ready[3:0], "one bit per PSI-L thread"; thread *n*
(XID 0x50 + n) reports in bit *n - 1*.  Tables 6-109 and 6-110 describe the
sideband/data layout used by XOUT and XIN.

Silicon (LP-AM243, ICSSG0 PRU0 and RTU_PRU0, no PSI-L peer configured) agrees:

* ``XIN 0x50`` before any traffic returns rx_ready = 0 and tx_ready = ``0x1``
  -- one transmit thread, in bit 0.
* ``XIN 0x54`` answers (zero data, no stall) like 0x51..0x53.
* An XIN on an empty receive thread returns all zeros on both cores, except
  that on PRU0 the second word read back from 0x52 and 0x54 was 1.  That word
  is not modelled; with no peer configured its meaning is unknown.
* **XOUT never stalls without a peer.**  tx_ready falls to 0 within the first
  12 XOUTs to 0x51 and stays there (nothing drains the thread), yet 24 further
  XOUTs retire without a stall, on both cores.  XOUTs to 0x52..0x54 also
  retire without a stall and change no status bit.  This model therefore
  accepts ``tx_max`` packets, then reports tx_ready = 0 and drops further
  writes, counting them in :attr:`XFRDMABridge.tx_dropped`.  Whether silicon
  queues or drops them is not observable without a peer.  The exact point
  where tx_ready falls (1..12) was not isolated, so ``tx_max`` stays a
  constructor parameter.  The TRM's "without stalling" wording implies a
  configured thread does stall; that is available as ``stall_when_full=True``
  but is not measured.

This model deliberately does not model sideband-word interpretation,
``tx_data_type`` packing, or PSI-L arbitration.  An empty XIN returns all
zero bytes, so its unmodelled ``rx_bytes`` field is zero by construction.
``tx_max`` is a simulator constructor parameter, not a TRM-derived FIFO
depth (silicon bounds it to 1..12, see above).

Two behaviours here are simulator policy rather than documented silicon, and
are called out so a run is not mistaken for evidence about either:

* Table 6-108 does not name the bit order.  Thread 1 in bit 0 is what
  silicon shows for the transmit thread; the receive bits are assumed to
  follow the same order (not measured: no receive traffic without a peer).
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
#: Receive threads RD0..RD3 (TI icss_xfer_defines.inc), thread n at 0x50 + n.
XFRDMA_THREAD_IDS = (0x51, 0x52, 0x53, 0x54)
#: The one transmit thread, WR0, shares 0x51 with RD0.
XFRDMA_TX_THREAD = 1
RX_THREADS = (1, 2, 3, 4)


class XFRDMABridge:
    """Shared, deterministic FIFO state for the four XFRDMA broadside IDs."""

    def __init__(self, tx_max: int = 8, stall_when_full: bool = False) -> None:
        if tx_max < 1:
            raise ValueError("tx_max must be at least one packet")
        self.tx_max = tx_max
        #: False (default): silicon with no PSI-L peer -- a full transmit
        #: thread drops writes.  True: the TRM-implied stall, unmeasured.
        self.stall_when_full = stall_when_full
        #: Writes dropped because the transmit thread was full.
        self.tx_dropped = 0
        #: Writes to receive-only threads 0x52..0x54, discarded (silicon:
        #: no stall, no status change).
        self.rx_thread_writes = 0
        self.rx_fifos: dict[int, deque[bytearray]] = {thread: deque() for thread in RX_THREADS}
        self.tx_fifos: dict[int, deque[bytes]] = {XFRDMA_TX_THREAD: deque()}

    def inject_rx(self, thread: int, packet: bytes) -> None:
        """Append a deterministic receive packet to *thread*."""
        self._check_thread(thread)
        self.rx_fifos[thread].append(bytearray(packet))

    def drain_tx(self, thread: int = XFRDMA_TX_THREAD) -> list[bytes]:
        """Return and clear queued transmit packets for *thread*."""
        self._check_tx_thread(thread)
        packets = list(self.tx_fifos[thread])
        self.tx_fifos[thread].clear()
        return packets

    def fill_tx(self, thread: int = XFRDMA_TX_THREAD, packet: bytes = b"") -> None:
        """Fill a transmit slot deterministically; intended for test setup."""
        self._check_tx_thread(thread)
        if len(self.tx_fifos[thread]) >= self.tx_max:
            raise ValueError("transmit FIFO is full")
        self.tx_fifos[thread].append(bytes(packet))

    def rx_depth(self, thread: int) -> int:
        self._check_thread(thread)
        return len(self.rx_fifos[thread])

    def _check_thread(self, thread: int) -> None:
        if thread not in self.rx_fifos:
            raise ValueError("XFRDMA receive thread must be 1, 2, 3 or 4 (XID 0x51..0x54)")

    def _check_tx_thread(self, thread: int) -> None:
        if thread not in self.tx_fifos:
            raise ValueError(
                f"XFRDMA transmit thread must be {XFRDMA_TX_THREAD} (XID 0x51, "
                "XFR2PSI_WR0_XID); the device has no other write widget"
            )

    def reset(self) -> None:
        for fifo in (*self.rx_fifos.values(), *self.tx_fifos.values()):
            fifo.clear()
        self.tx_dropped = 0
        self.rx_thread_writes = 0


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
            return bytes(length)
        return self._consume_rx(length)

    def xout(self, start_reg: int, data: bytes, start_byte: int = 0) -> None:
        self.hold_pc = False
        if self.thread == 0:
            self._note_status_write("XOUT")
            return
        if not self._is_tx():
            return
        fifo = self.bridge.tx_fifos[self.thread]
        if len(fifo) >= self.bridge.tx_max:
            if self.bridge.stall_when_full:
                self.hold_pc = True
            else:
                self.bridge.tx_dropped += 1
            return
        fifo.append(bytes(data))

    def xchg(self, start_reg: int, data: bytes, start_byte: int = 0) -> bytes:
        self.hold_pc = False
        if self.thread == 0:
            self._note_status_write("XCHG")
            return bytes(data)
        rx_fifo = self.bridge.rx_fifos[self.thread]
        if not self._is_tx():
            return self._consume_rx(len(data)) if rx_fifo else bytes(len(data))
        tx_fifo = self.bridge.tx_fifos[self.thread]
        # With stall_when_full a full TX FIFO stalls before either side moves.
        # Empty RX retires as a blank XIN transfer (SPRUIM2J 6.4.6.3.2.3.1).
        full = len(tx_fifo) >= self.bridge.tx_max
        if full and self.bridge.stall_when_full:
            self.hold_pc = True
            return bytes(data)
        received = self._consume_rx(len(data)) if rx_fifo else bytes(len(data))
        if full:
            self.bridge.tx_dropped += 1
        else:
            tx_fifo.append(bytes(data))
        return received

    def reset(self) -> None:
        self.hold_pc = False
        self.status_writes.clear()
        self.bridge.reset()

    def _status(self) -> bytes:
        # Table 6-108, thread n in bit n - 1 (silicon: tx_ready = 0x1).
        rx_ready = sum(1 << (thread - 1) for thread, fifo in self.bridge.rx_fifos.items() if fifo)
        tx_ready = sum(
            1 << (thread - 1)
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

    def _is_tx(self) -> bool:
        """True for the transmit thread (0x51).  A write to a receive-only
        thread is discarded and counted: silicon retires it with no stall and
        no status change."""
        if self.thread in self.bridge.tx_fifos:
            return True
        self.bridge.rx_thread_writes += 1
        return False

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
