"""RTU_PRU XFR2TR ring accelerator -- broadside IDs 0x70, 0x71 and 0x72.

Hardware reference: AM64x/AM243x TRM SPRUIM2J (MAY 2020 - REVISED APRIL 2026).

    * **Table 6-60** "Hardware Module Broadside ID Mapping" carries the row
      ``XFR2SHORT_DMA | 0x70/71/72 | 2 copies: RTU_PRU1/0``.  Table 6-60 names
      the block XFR2SHORT_DMA; section 6.4.6.3.3 names the same broadside IDs
      the XFR2TR ring accelerator.  Both agree it exists on the two RTU_PRUs
      and on no other core.
    * **Section 6.4.6.3.3** "PRU_ICSSG XFR2TR Ring Accelerator" -- prose and
      the supported / not-supported feature lists.
    * **Table 6-112** "RTU_PRU to XFR2TR Ring Interface" -- the register map.
    * **Section 6.4.6.3.3.1** "XFR2TR Programming Model" -- the worked example.

What the block does, in the TRM's own words (6.4.6.3.3): "XFR2TR creates a
Send list by doing a local memory copy of fixed (preconfigured) TR receive
list to the Send list.  This is accomplished by XOUT commands to define which
predefined TRs to add to the list."  So the copy itself is performed by the
accelerator, autonomously, once IDs have been submitted -- it is the thing
being accelerated.  :meth:`XFR2TRRing.tick` is that engine and is driven from
``PRUCore.step()``, one tick per core cycle, exactly like the IEP timer.

TWO STRUCTURES, NOT ONE
-----------------------
Section 6.4.6.3.3's supported-feature list names them separately:

    * "1 ring, with wrap around support along with observation of write
      pointer and reset write pointer" -- sized by ``tr_rsrc_nums`` at up to
      4096 entries, observable only through ``tr_rsrc_wrt_ptr``.
    * "8-level deep FIFO for TR IDs, upto 8 IDs per XOUT" -- the submit FIFO.

Table 6-112 scopes both status fields to the *FIFO*, not to the ring:

    * ``tr_rsrc_busy``     "It will be busy until the FIFO is empty AND the
      last write completed /data has landed."
    * ``tr_rscr_fifo_occ`` "The number of elements in the submit FIFO."

They are therefore modelled here as two distinct objects: :attr:`_fifo` (the
8-deep submit FIFO of TR IDs) and the ring, which lives in memory and is
tracked only by :attr:`wrt_ptr`.  ``fifo_occ`` falls back to zero as the
engine drains while ``wrt_ptr`` keeps climbing -- see
``test_fifo_occupancy_and_ring_write_pointer_are_independent``.

"Hardware Ring completion (managed by firmware)" in the not-supported list is
about *completion signalling* to the downstream consumer -- it sits alongside
"Hardware Door Bell (firmware will accomplish the Door Bell)".  It does not
mean firmware performs the TR copy; the copy is the accelerator's entire
purpose per the sentence quoted above.

WHAT THE TRM DOES NOT SAY (inferences, flagged as such)
-------------------------------------------------------
1. **Copy latency.**  The TRM gives no cycle count for one TR copy.
   :data:`TR_COPY_CYCLES` is an INFERENCE.  Its only load-bearing property is
   that it is >= 1, which is what makes the documented "busy until the FIFO is
   empty AND the last write completed" ordering observable at all.
2. **How the ID count of one 0x72 XOUT is encoded.**  Table 6-112 places each
   12-bit ID in its own 16-bit lane (``R2[11-0]``, ``R2[27-16]``,
   ``R3[11-0]`` ...), so byte pair ``2k..2k+1`` of the transfer holds ID *k*
   and a 2N-byte XOUT carries exactly N IDs.  That reading is DERIVED from the
   lane layout; the TRM states only "1 to 8 IDs per XOUT / Left packed / No
   holes or offsets" and never names the count encoding.  It is the only
   reading found that can express the odd ID counts the worked example uses
   (it submits 5 IDs, which a whole-register length cannot express).
3. **FIFO load order.**  The worked example in 6.4.6.3.3.1 submits
   ``XOUT 0,1,2,6,3`` and lands them in the ring as ``3,6,2,1,0`` -- reverse
   of the submitted order, and its second example agrees.  The feature list
   nevertheless calls the structure a FIFO.  This model reconciles the two by
   pushing ID(N-1)..ID0 into a genuine FIFO and draining in FIFO order, which
   reproduces both examples.  The TRM states neither the load order nor the
   pop order normatively; the reconciliation is an INFERENCE.
4. **``tr_rscr_reset`` scope.**  Table 6-112 says "This reset the ring
   pointer" -- so the write pointer is cleared.  That the submit FIFO is *not*
   cleared is an inference from the TRM's silence, consistent with
   6.4.6.3.3.1, which only ever hits reset after "Wait for busy".
5. **FIFO overflow.**  Undocumented for this block.  (6.4.6.3.2.3.1 documents
   XOUT stalls for XFRDMA, a different block; nothing licenses copying that
   behaviour here.)  This model raises rather than silently dropping IDs --
   SIMULATOR POLICY, in the spirit of the fail-loud unsupported-XFR handling
   this file's base commit added.
6. **Base-address qualifiers.**  Table 6-112 annotates ``tr_msrc_base`` with
   "Needs to be mode 0x4" and ``tr_rsrc_base`` with "Needs to be mode 0x40 or
   0x08".  Those mode qualifiers are NOT modelled; the 18-bit bases are used
   directly as byte addresses on the core's memory bus.  6.4.6.3.3 places both
   lists in "PRU_ICSSG shared RAM (Data RAM2)".
7. **Write-pointer units.**  6.4.6.3.3.1 lists successive WR addresses as
   0,1,2,3,4 then 5,6,7,... for successive TRs, i.e. an element index rather
   than a byte address.  DERIVED from that example.
8. **Ring-size rule not enforced.**  Table 6-112 requires ``tr_rsrc_nums`` to
   be "larger than maximum submitted TRs".  The TRM phrases this as a
   constraint on firmware, not as a hardware check, so nothing here enforces
   it.

Not modelled at all: external memory for TRs or the ring, and the hardware
doorbell -- both are in the TRM's own not-supported list.

Note on TRM spelling: Table 6-112 is internally inconsistent, writing
``tr_rsrc_base`` / ``tr_rsrc_busy`` / ``tr_rsrc_nums`` / ``tr_rsrc_wrt_ptr``
but ``tr_rscr_reset`` and ``tr_rscr_fifo_occ``.  Quotations above preserve the
TRM's spelling; Python identifiers here use the ``tr_rsrc``/``fifo_occ`` form
throughout.
"""

from __future__ import annotations

from collections import deque

from mem.memory_bus import MemoryBus
from xfr.accelerator import Accelerator

# --- Table 6-60 / section 6.4.6.3.3 broadside IDs -------------------------
XFR2TR_SRC_CFG = 0x70    # XOUT only: tr_msrc_base, tr_size
XFR2TR_RING_CFG = 0x71   # XOUT: ring base / reset / nums.  XIN: busy / occ / wrt_ptr
XFR2TR_SUBMIT = 0x72     # XOUT only: 1..8 TR IDs

XFR2TR_DEVICE_IDS = (XFR2TR_SRC_CFG, XFR2TR_RING_CFG, XFR2TR_SUBMIT)

#: Table 6-60: "XFR2SHORT_DMA 0x70/71/72 -- 2 copies: RTU_PRU1/0".  The TRM
#: spells the cores RTU_PRU0/RTU_PRU1; this simulator spells its RTU core
#: "RTU0" (see ``simulator.py``), so those are the names checked here.  PRU0,
#: PRU1, TX_PRU0 and TX_PRU1 have no XFR2TR ring and must be refused.
ALLOWED_CORE_NAMES = frozenset({"RTU0", "RTU1"})

#: 6.4.6.3.3 supported features: "8-level deep FIFO for TR IDs, upto 8 IDs per
#: XOUT".  Both the depth and the per-XOUT cap come from that one line.
SUBMIT_FIFO_DEPTH = 8
MAX_IDS_PER_XOUT = 8

#: Table 6-112, 0x72 XOUT: IDs are 12 bits wide (``R2[11-0]`` and friends).
TR_ID_MASK = 0xFFF

#: Table 6-112, 0x70 XOUT ``tr_size``: "0h: 8 Bytes / 1h: 64 Bytes".
TR_SIZE_BYTES = {0: 8, 1: 64}

#: Table 6-112, 0x71 XOUT ``tr_rsrc_nums``: "0 = 1 TR / 4095 = 4096 TRs".
TR_RSRC_NUMS_MASK = 0xFFF
MAX_RING_ENTRIES = 4096

#: Table 6-112: ``tr_msrc_base[17-0]``, ``tr_rsrc_base[17-0]`` and
#: ``tr_rsrc_wrt_ptr`` in ``R8[17-0]`` -- all 18 bits wide.
ADDR_MASK = 0x3FFFF

#: INFERRED (module docstring note 1) -- the TRM gives no copy latency.  Core
#: cycles from an ID leaving the submit FIFO to its TR landing in the ring.
#: Must be >= 1 for "busy until ... the last write completed /data has landed"
#: to be distinguishable from "busy until the FIFO is empty".
TR_COPY_CYCLES = 2


class XFR2TRRing:
    """State and copy engine of one RTU_PRU's XFR2TR ring accelerator.

    One instance backs all three broadside IDs, because 0x70/0x71/0x72 are
    three views of a single block (Table 6-60 counts *one* XFR2SHORT_DMA per
    RTU_PRU, not three).
    """

    def __init__(self, memory: MemoryBus) -> None:
        self._memory = memory
        self.reset()

    def reset(self) -> None:
        """Power-on defaults."""
        # --- Table 6-112, BS ID 0x70 XOUT ---
        self.tr_msrc_base = 0        # R6[17-0]
        self.tr_size_sel = 0         # R7[0]    -- 0h: 8 Bytes, 1h: 64 Bytes
        # --- Table 6-112, BS ID 0x71 XOUT ---
        self.tr_rsrc_base = 0        # R6[17-0]
        self.tr_rsrc_nums = 0        # R7[19-8] -- 0 => a 1-entry ring
        # --- Table 6-112, BS ID 0x71 XIN ---
        self.wrt_ptr = 0             # R8[17-0] tr_rsrc_wrt_ptr
        # --- 6.4.6.3.3: the 8-level deep submit FIFO, distinct from the ring
        self._fifo: deque[int] = deque()
        # --- the single TR copy the engine currently has in flight
        self._in_flight: int | None = None
        self._cycles_left = 0

    # -- Table 6-112 observables -------------------------------------------

    @property
    def tr_size(self) -> int:
        """Bytes per TR element.  Table 6-112, 0x70 XOUT ``tr_size``."""
        return TR_SIZE_BYTES[self.tr_size_sel]

    @property
    def ring_entries(self) -> int:
        """Ring capacity.  Table 6-112: ``tr_rsrc_nums`` 0 = 1 TR."""
        return self.tr_rsrc_nums + 1

    @property
    def fifo_occ(self) -> int:
        """``tr_rscr_fifo_occ`` -- "The number of elements in the submit FIFO".

        A property of the FIFO only.  It is deliberately *not* derived from
        the ring: ring occupancy is not an observable this interface exposes
        at all (Table 6-112 gives the ring only ``tr_rsrc_wrt_ptr``).
        """
        return len(self._fifo)

    @property
    def busy(self) -> bool:
        """``tr_rsrc_busy`` -- "0h: Not Busy / 1x: Busy".

        Table 6-112: "It will be busy until the FIFO is empty AND the last
        write completed /data has landed."  Both terms are required: the
        second is what keeps busy asserted after the final ID has left the
        FIFO but before its TR has landed in the ring.
        """
        return bool(self._fifo) or self._in_flight is not None

    # -- 0x70 / 0x71 XOUT configuration ------------------------------------

    def configure_source(self, words: dict[int, int]) -> None:
        """Table 6-112, BS ID 0x70 XOUT: ``tr_msrc_base`` and ``tr_size``."""
        if 6 in words:
            self.tr_msrc_base = words[6] & ADDR_MASK               # R6[17-0]
        if 7 in words:
            self.tr_size_sel = words[7] & 0x1                      # R7[0]

    def configure_ring(self, words: dict[int, int]) -> None:
        """Table 6-112, BS ID 0x71 XOUT: ring base, reset bit and ring size."""
        if 6 in words:
            self.tr_rsrc_base = words[6] & ADDR_MASK               # R6[17-0]
        if 7 in words:
            control = words[7]
            # R7[0] is "Reserved" in Table 6-112 -- deliberately not decoded.
            self.tr_rsrc_nums = (control >> 8) & TR_RSRC_NUMS_MASK  # R7[19-8]
            if (control >> 1) & 0x1:                                # R7[1]
                # "This reset the ring pointer".  Pointer only -- the submit
                # FIFO and any in-flight copy are left alone (docstring 4).
                self.wrt_ptr = 0

    # -- 0x72 XOUT submit --------------------------------------------------

    def submit(self, ids: list[int]) -> None:
        """Push 1..8 TR IDs into the submit FIFO (Table 6-112, BS ID 0x72)."""
        if not 1 <= len(ids) <= MAX_IDS_PER_XOUT:
            raise ValueError(
                f"XFR2TR 0x72 XOUT carries 1 to {MAX_IDS_PER_XOUT} TR IDs "
                f"(SPRUIM2J Table 6-112); got {len(ids)}"
            )
        if len(self._fifo) + len(ids) > SUBMIT_FIFO_DEPTH:
            # SIMULATOR POLICY, not silicon: overflow is undocumented for this
            # block (module docstring note 5).  Fail loud rather than drop.
            raise RuntimeError(
                f"XFR2TR submit FIFO overflow: {len(self._fifo)} of "
                f"{SUBMIT_FIFO_DEPTH} entries occupied, {len(ids)} more "
                "submitted.  SPRUIM2J does not document XFR2TR FIFO-full "
                "behaviour, so the simulator refuses rather than guessing; "
                "poll tr_rscr_fifo_occ (0x71 XIN, R7[11-8]) before the XOUT."
            )
        # INFERRED order (module docstring note 3): reversed, so that the
        # FIFO-order drain reproduces the 6.4.6.3.3.1 worked example in which
        # XOUT 0,1,2,6,3 lands in the ring as 3,6,2,1,0.
        for tr_id in reversed(ids):
            self._fifo.append(tr_id & TR_ID_MASK)

    # -- the autonomous copy engine ----------------------------------------

    def tick(self) -> None:
        """Advance the TR copy engine by one core cycle.

        Called from ``PRUCore.step()``.  This is what makes the accelerator
        autonomous: firmware submits IDs and polls ``tr_rsrc_busy``, and the
        ring drains because time passes -- not because anything else pokes it.
        """
        if self._in_flight is not None:
            self._cycles_left -= 1
            if self._cycles_left <= 0:
                self._land(self._in_flight)
                self._in_flight = None
        if self._in_flight is None and self._fifo:
            self._in_flight = self._fifo.popleft()
            self._cycles_left = TR_COPY_CYCLES

    def _land(self, tr_id: int) -> None:
        """Copy one TR from the source list into the ring and bump wrt_ptr.

        6.4.6.3.3: "a local memory copy of fixed (preconfigured) TR receive
        list to the Send list".  6.4.6.3.3.1 shows the source list indexed
        linearly by TR ID and the destination indexed by the write pointer.
        """
        size = self.tr_size
        src = self.tr_msrc_base + tr_id * size
        dst = self.tr_rsrc_base + self.wrt_ptr * size
        try:
            payload, _stalls = self._memory.read(src, size)
            self._memory.write(dst, payload)
        except ValueError as exc:
            raise RuntimeError(
                f"XFR2TR copy of TR ID {tr_id} failed: {exc}.  Check "
                f"tr_msrc_base=0x{self.tr_msrc_base:05X} / "
                f"tr_rsrc_base=0x{self.tr_rsrc_base:05X} (SPRUIM2J Table "
                "6-112); 6.4.6.3.3 places both lists in PRU_ICSSG shared RAM."
            ) from exc
        # "1 ring, with wrap around support" -- wrap at the configured size.
        self.wrt_ptr = (self.wrt_ptr + 1) % self.ring_entries


class TaskRingAccelerator(Accelerator):
    """One broadside view (0x70, 0x71 or 0x72) onto a shared :class:`XFR2TRRing`."""

    DEVICE_ID = XFR2TR_SRC_CFG

    def __init__(self, ring: XFR2TRRing, device_id: int, core_name: str) -> None:
        if device_id not in XFR2TR_DEVICE_IDS:
            raise ValueError(f"0x{device_id:02X} is not an XFR2TR broadside ID")
        if core_name not in ALLOWED_CORE_NAMES:
            raise PermissionError(
                f"XFR2TR device 0x{device_id:02X} exists on RTU_PRU cores only "
                "(SPRUIM2J Table 6-60, 'XFR2SHORT_DMA 0x70/71/72, 2 copies: "
                f"RTU_PRU1/0'); core '{core_name}' is not one of "
                f"{sorted(ALLOWED_CORE_NAMES)}"
            )
        self.DEVICE_ID = device_id
        self.core_name = core_name
        self.ring = ring

    # -- Accelerator interface ---------------------------------------------

    def xout(self, start_reg: int, data: bytes, start_byte: int = 0) -> None:
        if self.DEVICE_ID == XFR2TR_SUBMIT:
            self.ring.submit(self._decode_submit(start_reg, data, start_byte))
            return
        words = self._decode_config(start_reg, data, start_byte)
        if self.DEVICE_ID == XFR2TR_SRC_CFG:
            self.ring.configure_source(words)
        else:
            self.ring.configure_ring(words)

    def xin(self, start_reg: int, length: int, start_byte: int = 0) -> bytes:
        if self.DEVICE_ID != XFR2TR_RING_CFG:
            # Table 6-112 lists no XIN row for 0x70 or for 0x72.
            raise ValueError(
                f"XFR2TR device 0x{self.DEVICE_ID:02X} is XOUT-only "
                "(SPRUIM2J Table 6-112 lists no XIN access for it)"
            )
        if start_reg != 7 or start_byte != 0 or length not in (4, 8):
            raise ValueError(
                "XFR2TR 0x71 XIN must be `xin 0x71, &r7, 4` (status) or "
                "`xin 0x71, &r7, 8` (status + tr_rsrc_wrt_ptr): Table 6-112 "
                "puts tr_rsrc_busy / tr_rscr_fifo_occ in R7 and "
                "tr_rsrc_wrt_ptr in R8"
            )
        # Table 6-112, BS ID 0x71 XIN: R7[0] busy, R7[11-8] fifo_occ.
        status = (int(self.ring.busy) & 0x1) | ((self.ring.fifo_occ & 0xF) << 8)
        payload = status.to_bytes(4, "little")
        payload += (self.ring.wrt_ptr & ADDR_MASK).to_bytes(4, "little")
        return payload[:length]

    def xchg(self, start_reg: int, data: bytes, start_byte: int = 0) -> bytes:
        # Table 6-112's Access Type column contains only XIN and XOUT rows.
        raise ValueError(
            f"XFR2TR device 0x{self.DEVICE_ID:02X} has no XCHG access "
            "(SPRUIM2J Table 6-112)"
        )

    def reset(self) -> None:
        self.ring.reset()

    # -- decoding helpers --------------------------------------------------

    def _decode_config(self, start_reg: int, data: bytes, start_byte: int) -> dict[int, int]:
        """Split an ``&r6``-based XOUT into ``{register index: word}``.

        Table 6-112 puts both 0x70 and 0x71 configuration in R6 and R7, so a
        4-byte transfer writes R6 alone and an 8-byte transfer writes both.
        """
        if start_reg != 6 or start_byte != 0 or len(data) not in (4, 8):
            raise ValueError(
                f"XFR2TR 0x{self.DEVICE_ID:02X} XOUT must be `xout "
                f"0x{self.DEVICE_ID:02X}, &r6, 4` or `..., &r6, 8`: Table "
                "6-112 places its configuration in R6 (base) and R7 (control)"
            )
        return {
            6 + index: int.from_bytes(data[4 * index:4 * index + 4], "little")
            for index in range(len(data) // 4)
        }

    @staticmethod
    def _decode_submit(start_reg: int, data: bytes, start_byte: int) -> list[int]:
        """Unpack left-packed 12-bit TR IDs from an ``&r2``-based XOUT.

        Table 6-112 gives ID *k* its own 16-bit lane in R5:R2 -- ``R2[11-0]``,
        ``R2[27-16]``, ``R3[11-0]``, ... -- so ID *k* is bytes ``2k..2k+1`` of
        the transfer and N IDs occupy 2N bytes.  "Left packed / No holes or
        offsets" is why the transfer must start at R2.b0.  See module
        docstring note 2: this count encoding is derived, not quoted.
        """
        if start_reg != 2 or start_byte != 0:
            raise ValueError(
                "XFR2TR 0x72 XOUT must start at &r2.b0: Table 6-112 packs the "
                "TR IDs into R5:R2 left packed, 'No holes or offsets'"
            )
        if len(data) == 0 or len(data) % 2 != 0 or len(data) > 2 * MAX_IDS_PER_XOUT:
            raise ValueError(
                f"XFR2TR 0x72 XOUT length must be 2..{2 * MAX_IDS_PER_XOUT} "
                "bytes and even: Table 6-112 gives each of the 1 to 8 TR IDs "
                "a 16-bit lane in R5:R2"
            )
        return [
            int.from_bytes(data[2 * k:2 * k + 2], "little") & TR_ID_MASK
            for k in range(len(data) // 2)
        ]


def attach_task_ring(core) -> XFR2TRRing | None:
    """Give *core* an XFR2TR ring if Table 6-60 places one on it.

    Returns the ring (which the caller stores on ``core.task_ring``) or
    ``None`` for cores that have no XFR2TR block -- whose XFR accesses to
    0x70-0x72 then fall through to the simulator's fail-loud
    unsupported-device-ID path.
    """
    if core.name not in ALLOWED_CORE_NAMES:
        return None
    ring = XFR2TRRing(core.memory)
    for device_id in XFR2TR_DEVICE_IDS:
        core.accelerators[device_id] = TaskRingAccelerator(ring, device_id, core.name)
    return ring
