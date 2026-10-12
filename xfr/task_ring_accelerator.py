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

They are therefore modelled here as two distinct objects: :attr:`_slots` (the
8-slot submit FIFO of TR IDs, with its own read and write pointers) and the
ring, which lives in memory and is tracked only by its write index.  ``fifo_occ`` falls back to zero as the
engine drains while ``wrt_ptr`` keeps climbing -- see
``test_fifo_occupancy_and_ring_write_pointer_are_independent``.

"Hardware Ring completion (managed by firmware)" in the not-supported list is
about *completion signalling* to the downstream consumer -- it sits alongside
"Hardware Door Bell (firmware will accomplish the Door Bell)".  It does not
mean firmware performs the TR copy; the copy is the accelerator's entire
purpose per the sentence quoted above.

SILICON-MEASURED BEHAVIOUR (LP-AM243, AM2434 ICSSG0 RTU_PRU0)
-------------------------------------------------------------
Where the TRM is silent or its worked example disagrees with the device, this
model follows measurements taken on an LP-AM243 with the source list and the
ring in ICSSG shared RAM.  Each point is pinned by a test in
``tests/test_task_ring_accelerator.py`` (class ``TestSiliconBehaviour``).

1. **Landing order is submission order.**  The 6.4.6.3.3.1 worked example
   shows ``XOUT 0,1,2,6,3`` landing as ``3,6,2,1,0``.  Silicon lands
   ``0,1,2,6,3``; IDs ``1..8`` land ``1..8``; the second example's
   ``20,4,7,0,1,2`` lands in that order too.  The example's order is not
   reproduced.
2. **``tr_rsrc_wrt_ptr`` is an absolute address.**  ``XIN 0x71`` returns
   ``tr_rsrc_base + index * tr_size`` in R8 (``0x16000``, ``0x16008``, ...
   for 8-byte TRs; ``0x17080`` after two 64-byte TRs at ``0x17000``), not the
   element index that the worked example's "WR address 0,1,2" suggests.
3. **Ring size.**  With ``tr_rsrc_nums = 3`` silicon wraps after **3**
   entries, not the 4 that Table 6-112's "0 = 1 TR" encoding implies.  The
   model uses ``max(tr_rsrc_nums, 1)`` entries; ``tr_rsrc_nums = 0`` was not
   measured, so treating it as one entry is an INFERENCE.
4. **The submit FIFO is 8 slots with 4-bit pointers.**  ``tr_rscr_fifo_occ``
   reads ``(wr - rd) mod 16``, and an ID leaves the count only when its TR
   has landed, so the TR being copied still counts: straight after an 8-ID
   XOUT occupancy reads 8, then falls by one per landed TR.
5. **Overflow is silent.**  Submitting more than fits neither stalls the core
   nor flags anything: the write pointer runs on and overwrites queued slots.
   Submitting 1..8 and then 9..16, with two TRs landed and the third being
   copied, reads occupancy 14 and lands ``1,2,3,12,13,14,15,16,9,10,...,16``:
   16 TRs, with IDs 4..8 lost.  The model reproduces that exactly and counts
   each overwrite of a still-queued ID in :attr:`XFR2TRRing.overflow_events`.
6. **Copy timing.**  One TR costs a shared-RAM read plus a shared-RAM write
   of ``tr_size`` bytes, ``(2 + words) + (1 + words)`` core cycles (7 for 8
   bytes, 35 for 64 bytes), back to back, starting :data:`TR_START_CYCLES`
   after the submitting XOUT.  The ID is latched when its copy starts, which
   is why ID 3 survived the overflow in point 5.  The engine advances by
   elapsed core cycles, stalls included.

WHAT NEITHER THE TRM NOR SILICON PINS DOWN (inferences, flagged as such)
-----------------------------------------------------------------------
1. **How the ID count of one 0x72 XOUT is encoded.**  Table 6-112 places each
   12-bit ID in its own 16-bit lane (``R2[11-0]``, ``R2[27-16]``,
   ``R3[11-0]`` ...), so byte pair ``2k..2k+1`` of the transfer holds ID *k*
   and a 2N-byte XOUT carries exactly N IDs.  That reading is DERIVED from the
   lane layout.  Silicon is consistent with it: 2-, 6-, 10-, 12- and 16-byte
   submits landed 1, 3, 5, 6 and 8 TRs.
2. **``tr_rscr_reset`` scope.**  Table 6-112 says "This reset the ring
   pointer".  Silicon returns the write pointer to the ring base and later TRs
   overwrite from slot 0.  Reset was only exercised while idle, so leaving the
   submit FIFO alone is an inference.
3. **Base-address qualifiers.**  Table 6-112 annotates ``tr_msrc_base`` with
   "Needs to be mode 0x4" and ``tr_rsrc_base`` with "Needs to be mode 0x40 or
   0x08".  Those mode qualifiers are NOT modelled; the 18-bit bases are used
   directly as byte addresses on the core's memory bus.  6.4.6.3.3 places both
   lists in "PRU_ICSSG shared RAM (Data RAM2)".
4. **Ring-size rule not enforced.**  Table 6-112 requires ``tr_rsrc_nums`` to
   be "larger than maximum submitted TRs".  The TRM phrases this as a
   constraint on firmware, not as a hardware check, so nothing here enforces
   it.
5. **Copy timing outside shared RAM** was not measured; the per-TR cost in
   point 6 above is applied whatever the bases point at.

Not modelled at all: external memory for TRs or the ring, and the hardware
doorbell -- both are in the TRM's own not-supported list.

Note on TRM spelling: Table 6-112 is internally inconsistent, writing
``tr_rsrc_base`` / ``tr_rsrc_busy`` / ``tr_rsrc_nums`` / ``tr_rsrc_wrt_ptr``
but ``tr_rscr_reset`` and ``tr_rscr_fifo_occ``.  Quotations above preserve the
TRM's spelling; Python identifiers here use the ``tr_rsrc``/``fifo_occ`` form
throughout.
"""

from __future__ import annotations

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

#: SILICON (module docstring point 4): the 8 submit-FIFO slots are addressed
#: by 4-bit pointers, so occupancy reads ``(wr - rd) mod 16``.
FIFO_PTR_MODULUS = 16

#: SILICON (module docstring point 6): core cycles from the submitting XOUT
#: to the first TR copy starting, when the engine was idle.  Fitted on
#: LP-AM243.
TR_START_CYCLES = 1


def tr_copy_cycles(tr_size: int) -> int:
    """SILICON (module docstring point 6): core cycles to copy one TR.

    A shared-RAM read of ``tr_size`` bytes, ``2 + words``, then a write,
    ``1 + words``: 7 cycles for an 8-byte TR and 35 for a 64-byte TR.
    """
    words = tr_size // 4
    return (2 + words) + (1 + words)


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
        # --- ring write index; XIN 0x71 R8 reports it as an address
        self.wrt_index = 0
        # --- 6.4.6.3.3: the 8-level deep submit FIFO, distinct from the ring.
        # SILICON: 8 slots addressed by 4-bit read/write pointers.
        self._slots = [0] * SUBMIT_FIFO_DEPTH
        self._fifo_wr = 0
        self._fifo_rd = 0
        #: Queued IDs overwritten before any copy latched them (silicon drops
        #: them silently).
        self.overflow_events = 0
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
        """Ring capacity.

        SILICON (module docstring point 3): ``tr_rsrc_nums`` entries, so
        ``nums = 3`` wraps after 3 -- not Table 6-112's "0 = 1 TR" reading.
        ``nums = 0`` is unmeasured and treated as a one-entry ring.
        """
        return max(self.tr_rsrc_nums, 1)

    @property
    def wrt_ptr(self) -> int:
        """``tr_rsrc_wrt_ptr`` as XIN 0x71 returns it in R8.

        SILICON (module docstring point 2): an absolute ring address,
        ``tr_rsrc_base + index * tr_size``, not an element index.
        """
        return (self.tr_rsrc_base + self.wrt_index * self.tr_size) & ADDR_MASK

    @property
    def fifo_occ(self) -> int:
        """``tr_rscr_fifo_occ`` -- "The number of elements in the submit FIFO".

        A property of the FIFO only.  It is deliberately *not* derived from
        the ring: ring occupancy is not an observable this interface exposes
        at all (Table 6-112 gives the ring only ``tr_rsrc_wrt_ptr``).

        SILICON (module docstring point 4): ``(wr - rd) mod 16``, where ``rd``
        advances when a TR lands, so the TR being copied still counts.  After
        an overflow the value aliases, as it does on the device.
        """
        return (self._fifo_wr - self._fifo_rd) % FIFO_PTR_MODULUS

    @property
    def busy(self) -> bool:
        """``tr_rsrc_busy`` -- "0h: Not Busy / 1x: Busy".

        Table 6-112: "It will be busy until the FIFO is empty AND the last
        write completed /data has landed."  An ID leaves the FIFO count only
        once its TR has landed (module docstring point 4), so both terms
        reduce to a non-zero occupancy.
        """
        return self.fifo_occ != 0

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
                # FIFO and any in-flight copy are left alone (inference 2).
                self.wrt_index = 0

    # -- 0x72 XOUT submit --------------------------------------------------

    def submit(self, ids: list[int]) -> None:
        """Push 1..8 TR IDs into the submit FIFO (Table 6-112, BS ID 0x72)."""
        if not 1 <= len(ids) <= MAX_IDS_PER_XOUT:
            raise ValueError(
                f"XFR2TR 0x72 XOUT carries 1 to {MAX_IDS_PER_XOUT} TR IDs "
                f"(SPRUIM2J Table 6-112); got {len(ids)}"
            )
        was_idle = not self.busy
        for tr_id in ids:
            # SILICON (module docstring point 5): no stall and no error when
            # full -- the write pointer runs on and overwrites queued slots.
            # The slot about to be written still holds an ID that no copy has
            # latched yet: that ID is lost.  (The ID being copied still counts
            # in fifo_occ but is already latched, so overwriting it is safe.)
            if self.fifo_occ - (self._in_flight is not None) >= SUBMIT_FIFO_DEPTH:
                self.overflow_events += 1
            self._slots[self._fifo_wr % SUBMIT_FIFO_DEPTH] = tr_id & TR_ID_MASK
            self._fifo_wr = (self._fifo_wr + 1) % FIFO_PTR_MODULUS
        if was_idle and self.busy:
            self._in_flight = None
            self._cycles_left = TR_START_CYCLES

    # -- the autonomous copy engine ----------------------------------------

    def tick(self, cycles: int = 1) -> None:
        """Advance the TR copy engine by *cycles* core cycles.

        Called from ``PRUCore.step()`` with the cycles the instruction took,
        stalls included.  This is what makes the accelerator autonomous:
        firmware submits IDs and polls ``tr_rsrc_busy``, and the ring drains
        because time passes -- not because anything else pokes it.
        """
        for _ in range(cycles):
            if not self.busy:
                return
            self._cycles_left -= 1
            if self._cycles_left > 0:
                continue
            if self._in_flight is not None:
                self._land(self._in_flight)
                self._fifo_rd = (self._fifo_rd + 1) % FIFO_PTR_MODULUS
                self._in_flight = None
            if self.busy:
                # Start the next copy back to back, latching its ID now
                # (SILICON, module docstring point 6).
                self._in_flight = self._slots[self._fifo_rd % SUBMIT_FIFO_DEPTH]
                self._cycles_left = tr_copy_cycles(self.tr_size)

    def _land(self, tr_id: int) -> None:
        """Copy one TR from the source list into the ring and bump wrt_ptr.

        6.4.6.3.3: "a local memory copy of fixed (preconfigured) TR receive
        list to the Send list".  6.4.6.3.3.1 shows the source list indexed
        linearly by TR ID and the destination indexed by the write pointer.
        """
        size = self.tr_size
        src = self.tr_msrc_base + tr_id * size
        dst = self.tr_rsrc_base + self.wrt_index * size
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
        self.wrt_index = (self.wrt_index + 1) % self.ring_entries


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
