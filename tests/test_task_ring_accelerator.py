"""TRM-backed vectors for the RTU_PRU XFR2TR ring accelerator (0x70/0x71/0x72).

Every assertion below names the SPRUIM2J table or section it comes from.
Where the model rests on an inference rather than a quotation, the test says
so and names the inference, so that a reader can tell a spec gate from a
design-decision gate.

The two gates this suite exists to hold, both of which sank earlier attempts:

* ``TestAutonomousDrain`` -- the ring drains because *core cycles pass*, with
  no test-side call into the model.  ``test_wait_for_idle_firmware_terminates
  _in_a_single_run`` is the acceptance case: plain TRM-idiomatic firmware, one
  ``run()``, no poke.
* ``TestFifoIsNotTheRing`` -- ``tr_rscr_fifo_occ`` and the ring are distinct
  structures with independently observable state.

``TestSiliconBehaviour`` replays programs that were run on an LP-AM243
(AM2434, ICSSG0 RTU_PRU0) and asserts the values the device returned.  Where
silicon and the TRM's worked example disagree, silicon wins and the test says
so.
"""

import pytest

from core.pru_core import PRUCore, UnsupportedXFRError
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from pru_io.io_port import IOPort
from xfr.task_ring_accelerator import (
    ALLOWED_CORE_NAMES,
    MAX_IDS_PER_XOUT,
    SUBMIT_FIFO_DEPTH,
    TR_SIZE_BYTES,
    BUSY_TAIL_CYCLES,
    TR_START_CYCLES,
    tr_copy_cycles,
    XFR2TR_DEVICE_IDS,
    XFR2TR_RING_CFG,
    XFR2TR_SRC_CFG,
    XFR2TR_SUBMIT,
    TaskRingAccelerator,
    XFR2TRRing,
)
from xfr.xfr_bus import XFRBus

#: 6.4.6.3.3 puts both TR lists in "PRU_ICSSG shared RAM (Data RAM2)"; this
#: simulator maps its shared RAM at 0x10000, which is inside the 18 bits
#: Table 6-112 gives tr_msrc_base / tr_rsrc_base.
SRC_BASE = 0x10000
RING_BASE = 0x11000


def make_memory() -> MemoryBus:
    bus = MemoryBus()
    bus.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
    bus.add_region(MemoryRegion("ICSS_SHARED", 0x10000, 0x10000, 2, 1, 0))
    return bus


def fill_source_table(bus: MemoryBus, count: int = 32, tr_size: int = 8) -> None:
    """Lay out the "Source IDs, linear table" of 6.4.6.3.3.1.

    TR *k* is ``tr_size`` bytes of the value ``k``, so a landed ring entry
    identifies which source TR was copied.
    """
    for tr_id in range(count):
        bus.write(SRC_BASE + tr_id * tr_size, bytes([tr_id]) * tr_size)


def make_core(asm: str = "", name: str = "RTU0", bus: MemoryBus | None = None) -> PRUCore:
    bus = bus if bus is not None else make_memory()
    core = PRUCore(name, bus, XFRBus(), IOPort())
    if asm:
        assert core.load_asm(asm) == []
    return core


def make_ring(bus: MemoryBus | None = None) -> tuple[XFR2TRRing, MemoryBus]:
    bus = bus if bus is not None else make_memory()
    ring = XFR2TRRing(bus)
    return ring, bus


def configure(ring: XFR2TRRing, *, nums: int, tr_size_sel: int = 0) -> None:
    """Apply the 6.4.6.3.3.1 "Initial" step through the documented registers."""
    ring.configure_source({6: SRC_BASE, 7: tr_size_sel})
    ring.configure_ring({6: RING_BASE, 7: nums << 8})


def ptr(index: int, tr_size: int = 8) -> int:
    """``tr_rsrc_wrt_ptr`` for ring slot *index*: silicon reports the
    absolute address ``tr_rsrc_base + index * tr_size`` (module point 2)."""
    return RING_BASE + index * tr_size


def ring_entry(bus: MemoryBus, index: int, tr_size: int = 8) -> int:
    """First byte of ring slot *index*, i.e. which source TR landed there."""
    payload, _ = bus.read(RING_BASE + index * tr_size, tr_size)
    return payload[0]


# --- assembly fragments ---------------------------------------------------

#: 6.4.6.3.3.1 "Initial": set TR size, source base, ring base and ring size.
SETUP_ASM = """
    ldi32 r6, 0x10000
    ldi32 r7, 0
    xout 0x70, &r6, 8
    ldi32 r6, 0x11000
    ldi32 r7, 0x00000F00
    xout 0x71, &r6, 8
"""

#: The worked example's first submit: XOUT 0,1,2,6,3 -- five IDs, left packed
#: into R2/R3/R4 (Table 6-112) and therefore a 10-byte transfer.
SUBMIT_WORKED_EXAMPLE_ASM = """
    ldi32 r2, 0x00010000
    ldi32 r3, 0x00060002
    ldi32 r4, 0x00000003
    xout 0x72, &r2, 10
"""

#: Table 6-112's own wait-for-idle idiom: read the status word into R7 and
#: spin while tr_rsrc_busy (R7[0]) is set.
WAIT_FOR_IDLE_ASM = """
wait_idle:
    xin 0x71, &r7, 4
    qbbs wait_idle, r7, 0
    halt
"""


class TestCorePlacement:
    """TRM Table 6-60: "XFR2SHORT_DMA 0x70/71/72 -- 2 copies: RTU_PRU1/0"."""

    def test_allowed_core_names_is_the_rtu_pru_pair(self):
        assert ALLOWED_CORE_NAMES == frozenset({"RTU0", "RTU1"})

    def test_rtu_core_registers_all_three_xfr2tr_ids(self):
        core = make_core(name="RTU0")
        for device_id in XFR2TR_DEVICE_IDS:
            assert device_id in core.accelerators
        assert core.task_ring is not None

    def test_the_three_ids_share_one_block(self):
        """Table 6-60 counts one XFR2SHORT_DMA per RTU_PRU, not three."""
        core = make_core(name="RTU0")
        rings = {id(core.accelerators[d].ring) for d in XFR2TR_DEVICE_IDS}
        assert len(rings) == 1

    @pytest.mark.parametrize("name", ["PRU0", "PRU1", "TX_PRU0", "TX_PRU1"])
    def test_non_rtu_cores_have_no_xfr2tr_block(self, name):
        core = make_core(name=name)
        assert core.task_ring is None
        for device_id in XFR2TR_DEVICE_IDS:
            assert device_id not in core.accelerators

    @pytest.mark.parametrize("name", ["PRU0", "PRU1", "TX_PRU0", "TX_PRU1"])
    def test_direct_construction_on_a_non_rtu_core_is_refused(self, name):
        ring, _ = make_ring()
        with pytest.raises(PermissionError, match="Table 6-60"):
            TaskRingAccelerator(ring, XFR2TR_SRC_CFG, name)

    def test_matched_pair_rtu_admitted_pru_refused_same_asm_same_op(self):
        """Identical firmware, identical instruction, only the core differs."""
        asm = SETUP_ASM + "    halt\n"

        admitted = make_core(asm, name="RTU0")
        admitted.run(max_steps=50)
        assert admitted.halted
        assert admitted.task_ring.tr_msrc_base == SRC_BASE

        refused = make_core(asm, name="PRU0")
        refused.strict_unsupported_xfr = True
        with pytest.raises(UnsupportedXFRError, match="0x70"):
            refused.run(max_steps=50)

    def test_tx_pru_is_also_refused_through_the_same_path(self):
        refused = make_core(SETUP_ASM + "    halt\n", name="TX_PRU0")
        refused.strict_unsupported_xfr = True
        with pytest.raises(UnsupportedXFRError, match="0x70"):
            refused.run(max_steps=50)


class TestRegisterMap:
    """TRM Table 6-112 "RTU_PRU to XFR2TR Ring Interface"."""

    def test_0x70_xout_sets_msrc_base_and_tr_size(self):
        ring, _ = make_ring()
        ring.configure_source({6: SRC_BASE, 7: 1})
        assert ring.tr_msrc_base == SRC_BASE      # R6[17-0]
        assert ring.tr_size_sel == 1              # R7[0]

    def test_base_fields_are_18_bits(self):
        """Table 6-112 writes tr_msrc_base[17-0] and tr_rsrc_base[17-0]."""
        ring, _ = make_ring()
        ring.configure_source({6: 0x12345678})
        ring.configure_ring({6: 0x12345678})
        assert ring.tr_msrc_base == 0x12345678 & 0x3FFFF
        assert ring.tr_rsrc_base == 0x12345678 & 0x3FFFF

    def test_tr_size_selects_8_or_64_bytes(self):
        """Table 6-112 tr_size: "0h: 8 Bytes / 1h: 64 Bytes"."""
        assert TR_SIZE_BYTES == {0: 8, 1: 64}
        ring, _ = make_ring()
        ring.configure_source({6: SRC_BASE, 7: 0})
        assert ring.tr_size == 8
        ring.configure_source({6: SRC_BASE, 7: 1})
        assert ring.tr_size == 64

    def test_0x71_xout_takes_ring_nums_from_r7_bits_19_8(self):
        """Table 6-112 tr_rsrc_nums is R7[19-8].

        The entry count is SILICON (module point 3): ``nums`` entries, not
        the "0 = 1 TR" reading of Table 6-112.  ``nums = 0`` is unmeasured
        and modelled as one entry.
        """
        ring, _ = make_ring()
        ring.configure_ring({6: RING_BASE, 7: 0xABC << 8})
        assert ring.tr_rsrc_nums == 0xABC
        assert ring.ring_entries == 0xABC

        ring.configure_ring({6: RING_BASE, 7: 0})
        assert ring.tr_rsrc_nums == 0
        assert ring.ring_entries == 1

    def test_0x71_xout_reset_bit_is_r7_bit1_and_clears_the_write_pointer(self):
        """Table 6-112 tr_rscr_reset: "This reset the ring pointer"."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        ring.submit([1, 2])
        drain(ring)
        assert ring.wrt_ptr == ptr(2)

        ring.configure_ring({7: 1 << 1})
        assert ring.wrt_ptr == ptr(0)

    def test_0x71_xout_bit0_is_reserved_and_is_not_the_reset(self):
        """Table 6-112 marks R7[0] of the 0x71 XOUT "Reserved"."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        ring.submit([1, 2])
        drain(ring)
        assert ring.wrt_ptr == ptr(2)

        ring.configure_ring({7: 1 << 0})
        assert ring.wrt_ptr == ptr(2)

    def test_reset_bit_leaves_the_submit_fifo_alone(self):
        """INFERENCE (module inference 2): Table 6-112 scopes reset to the pointer.

        The TRM is silent on the FIFO, so this pins the model's choice, not a
        quotation.
        """
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        ring.submit([1, 2, 3])
        ring.configure_ring({7: 1 << 1})
        assert ring.fifo_occ == 3

    def test_0x71_xin_status_word_layout(self):
        """Table 6-112: tr_rsrc_busy R7[0], tr_rscr_fifo_occ R7[11-8]."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        view = TaskRingAccelerator(ring, XFR2TR_RING_CFG, "RTU0")

        assert int.from_bytes(view.xin(7, 4), "little") == 0  # idle, empty

        ring.submit([1, 2, 3, 4, 5])
        status = int.from_bytes(view.xin(7, 4), "little")
        assert status & 0x1 == 1                # busy
        assert (status >> 8) & 0xF == 5         # five IDs in the submit FIFO

    def test_0x71_xin_reports_the_write_pointer_in_r8(self):
        """Table 6-112: "R8[17-0] 0x71 XIN tr_rsrc_wrt_ptr" -- an absolute
        ring address on silicon (module point 2)."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        view = TaskRingAccelerator(ring, XFR2TR_RING_CFG, "RTU0")
        ring.submit([1, 2, 3])
        drain(ring)

        payload = view.xin(7, 8)
        assert len(payload) == 8
        assert int.from_bytes(payload[4:8], "little") == ptr(3)

    @pytest.mark.parametrize("device_id", [XFR2TR_SRC_CFG, XFR2TR_SUBMIT])
    def test_0x70_and_0x72_have_no_xin_row(self, device_id):
        ring, _ = make_ring()
        view = TaskRingAccelerator(ring, device_id, "RTU0")
        with pytest.raises(ValueError, match="XOUT-only"):
            view.xin(7, 4)

    @pytest.mark.parametrize("device_id", XFR2TR_DEVICE_IDS)
    def test_no_id_supports_xchg(self, device_id):
        """Table 6-112's Access Type column holds only XIN and XOUT rows."""
        ring, _ = make_ring()
        view = TaskRingAccelerator(ring, device_id, "RTU0")
        with pytest.raises(ValueError, match="XCHG"):
            view.xchg(2, b"\x00\x00\x00\x00")

    @pytest.mark.parametrize("device_id", [XFR2TR_SRC_CFG, XFR2TR_RING_CFG])
    def test_config_xout_must_start_at_r6(self, device_id):
        """Table 6-112 places 0x70 and 0x71 configuration in R6 and R7."""
        ring, _ = make_ring()
        view = TaskRingAccelerator(ring, device_id, "RTU0")
        with pytest.raises(ValueError, match="&r6"):
            view.xout(2, bytes(8))

    def test_0x71_xin_must_start_at_r7(self):
        ring, _ = make_ring()
        view = TaskRingAccelerator(ring, XFR2TR_RING_CFG, "RTU0")
        with pytest.raises(ValueError, match="&r7"):
            view.xin(6, 4)


class TestSubmitPacking:
    """Table 6-112, BS ID 0x72: 1 to 8 twelve-bit IDs left packed in R5:R2."""

    def test_unpacks_eight_ids_from_r5_r2(self):
        payload = b"".join(
            word.to_bytes(4, "little")
            for word in (0x0DEF0ABC, 0x02220111, 0x04440333, 0x06660555)
        )
        assert TaskRingAccelerator._decode_submit(2, payload, 0) == [
            0xABC, 0xDEF, 0x111, 0x222, 0x333, 0x444, 0x555, 0x666
        ]

    def test_ids_are_masked_to_twelve_bits(self):
        """Table 6-112 gives ID0 R2[11-0]; R2[15-12] is not part of the ID."""
        assert TaskRingAccelerator._decode_submit(2, (0xF005).to_bytes(2, "little"), 0) == [5]

    def test_byte_length_selects_the_id_count_including_odd_counts(self):
        """DERIVED (module inference 1), and required by the 6.4.6.3.3.1 example,
        which submits five IDs."""
        payload = b"".join(
            word.to_bytes(4, "little") for word in (0x00010000, 0x00060002, 0x00000003)
        )
        assert TaskRingAccelerator._decode_submit(2, payload[:10], 0) == [0, 1, 2, 6, 3]
        assert TaskRingAccelerator._decode_submit(2, payload[:4], 0) == [0, 1]
        assert TaskRingAccelerator._decode_submit(2, payload[:2], 0) == [0]

    def test_submit_must_start_at_r2_b0(self):
        """Table 6-112: "Left packed ... No holes or offsets"."""
        with pytest.raises(ValueError, match="&r2"):
            TaskRingAccelerator._decode_submit(3, bytes(4), 0)
        with pytest.raises(ValueError, match="&r2"):
            TaskRingAccelerator._decode_submit(2, bytes(4), 1)

    @pytest.mark.parametrize("length", [0, 1, 3, 18])
    def test_submit_rejects_lengths_that_cannot_be_whole_ids(self, length):
        with pytest.raises(ValueError, match="16-bit lane"):
            TaskRingAccelerator._decode_submit(2, bytes(length), 0)

    def test_at_most_eight_ids_per_xout(self):
        """6.4.6.3.3: "upto 8 IDs per XOUT"."""
        assert MAX_IDS_PER_XOUT == 8
        ring, _ = make_ring()
        with pytest.raises(ValueError, match="1 to 8"):
            ring.submit(list(range(9)))


class TestFifoIsNotTheRing:
    """6.4.6.3.3 lists the 8-deep submit FIFO and the ring as separate things.

    Table 6-112 scopes tr_rscr_fifo_occ to "the submit FIFO" and gives the
    ring only tr_rsrc_wrt_ptr, so the two must be independently observable.
    """

    def test_submit_fifo_is_eight_deep(self):
        """6.4.6.3.3: "8-level deep FIFO for TR IDs"."""
        assert SUBMIT_FIFO_DEPTH == 8
        ring, _ = make_ring()
        ring.submit([1, 2, 3, 4])
        ring.submit([5, 6, 7, 8])
        assert ring.fifo_occ == 8

    def test_overflowing_the_submit_fifo_is_silent_and_overwrites(self):
        """SILICON (module point 5): no stall, no error -- the write pointer
        runs on over still-queued slots, and the count aliases mod 16."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=31)
        ring.submit([1, 2, 3, 4, 5, 6, 7, 8])
        ring.submit([9])                      # overwrites queued ID 1
        assert ring.overflow_events == 1
        assert ring.fifo_occ == 9
        drain(ring)
        # Slot 0 now holds 9; the engine drains slots 0..8 in pointer order.
        assert [ring_entry(bus, k) for k in range(9)] == [9, 2, 3, 4, 5, 6, 7, 8, 9]

    def test_fifo_occupancy_and_ring_write_pointer_are_independent(self):
        """The FIFO empties while the ring fills -- they are not one counter.

        A model that derived tr_rscr_fifo_occ from ring occupancy would report
        8 here instead of 0.  SILICON (module point 4): an ID leaves the count
        when its TR lands, so count + landed is always the number submitted.
        """
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)          # a 15-entry ring, bigger than the FIFO
        ring.submit([1, 2, 3, 4, 5, 6, 7, 8])
        assert (ring.fifo_occ, ring.wrt_ptr) == (8, ptr(0))

        trace = []
        for _ in range(128):
            ring.tick()
            trace.append((ring.fifo_occ, ring.wrt_index))
            if not ring.busy:
                break

        # Final state: the FIFO is empty and all eight TRs are in the ring.
        assert (ring.fifo_occ, ring.wrt_ptr) == (0, ptr(8))
        assert all(occ + landed == 8 for occ, landed in trace)
        assert sorted({occ for occ, _ in trace}) == list(range(9))

    def test_a_full_fifo_can_coexist_with_a_partly_filled_ring(self):
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        ring.submit([1, 2, 3, 4, 5, 6, 7, 8])
        drain(ring)
        ring.submit([1, 2, 3, 4, 5, 6, 7, 8])
        assert ring.fifo_occ == 8
        assert ring.wrt_ptr == ptr(8)


class TestAutonomousDrain:
    """The engine runs on simulated time, not on anything a test calls.

    6.4.6.3.3: "XFR2TR creates a Send list by doing a local memory copy of
    fixed (preconfigured) TR receive list to the Send list."
    """

    def test_wait_for_idle_firmware_terminates_in_a_single_run(self):
        """ACCEPTANCE CASE.

        Plain TRM-idiomatic firmware -- configure, submit, spin on
        tr_rsrc_busy -- executed by exactly one ``run()`` call with nothing
        reaching into the model.  It must halt.
        """
        bus = make_memory()
        fill_source_table(bus)
        core = make_core(
            SETUP_ASM + SUBMIT_WORKED_EXAMPLE_ASM + WAIT_FOR_IDLE_ASM,
            bus=bus,
        )
        steps = core.run(max_steps=1000)

        assert core.halted, (
            f"wait-for-idle loop did not terminate in {steps} steps: "
            "tr_rsrc_busy never cleared, so nothing drained the ring"
        )
        assert steps < 1000
        assert core.task_ring.busy is False
        assert core.task_ring.fifo_occ == 0
        assert core.task_ring.wrt_ptr == ptr(5)

    def test_the_engine_is_driven_by_core_steps(self):
        """Submitted IDs sit still until the core executes instructions."""
        bus = make_memory()
        fill_source_table(bus)
        core = make_core("    ldi r0, 0\n" * 40, bus=bus)
        core.task_ring.configure_source({6: SRC_BASE, 7: 0})
        core.task_ring.configure_ring({6: RING_BASE, 7: 15 << 8})
        core.task_ring.submit([4, 5, 6])

        assert core.task_ring.wrt_ptr == ptr(0)   # no cycles yet, nothing moved
        core.run(max_steps=40)
        assert core.task_ring.wrt_ptr == ptr(3)
        assert core.task_ring.busy is False

    def test_nothing_drains_without_ticks(self):
        """The only drain path is time; the ring holds still without it."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        ring.submit([1, 2, 3])
        for _ in range(100):
            assert ring.fifo_occ == 3
            assert ring.wrt_ptr == ptr(0)
            assert ring.busy is True

    def test_busy_is_one_while_busy_and_zero_when_idle(self):
        """Table 6-112: "0h: Not Busy / 1x: Busy" -- polarity, not its inverse."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        assert ring.busy is False              # nothing submitted -> not busy
        ring.submit([7])
        assert ring.busy is True               # work outstanding -> busy
        drain(ring)
        assert ring.busy is False              # drained -> not busy

    def test_busy_stays_set_until_the_last_write_lands(self):
        """Table 6-112: busy "until the FIFO is empty AND the last write
        completed /data has landed".  On silicon the ID being copied still
        counts in tr_rscr_fifo_occ (module point 4), and busy outlasts the
        landing by BUSY_TAIL_CYCLES (module point 6)."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        ring.submit([9])

        landed_at = TR_START_CYCLES + tr_copy_cycles(8)
        for _ in range(landed_at):
            assert ring.busy and ring.fifo_occ == 1
            ring.tick()
        assert (ring.wrt_ptr, ring.fifo_occ, ring.busy) == (ptr(1), 0, True)
        assert drain(ring) == BUSY_TAIL_CYCLES
        assert ring.busy is False

    def test_copy_cost_is_a_shared_ram_read_plus_write(self):
        """SILICON (module point 6): (2 + words) + (1 + words) per TR."""
        assert tr_copy_cycles(8) == 7
        assert tr_copy_cycles(64) == 35

    def test_memory_stalls_advance_the_engine(self):
        """The engine counts core cycles, not instructions: a stalled LBBO
        gives the copy engine its stall cycles too."""
        bus = make_memory()
        fill_source_table(bus)
        core = make_core(
            SETUP_ASM
            + "    ldi r2, 1\n    xout 0x72, &r2, 2\n"
            + "    ldi32 r10, 0x10000\n"
            + "    lbbo &r0, r10, 0, 32\n"      # 8 words: 1 + 9 stall cycles
            + "    halt\n",
            bus=bus,
        )
        core.run(max_steps=100)
        assert core.task_ring.wrt_ptr == ptr(1)

    def test_a_wait_for_idle_loop_falls_through_when_nothing_was_submitted(self):
        core = make_core(SETUP_ASM + WAIT_FOR_IDLE_ASM)
        core.run(max_steps=200)
        assert core.halted


class TestCopyEngine:
    """6.4.6.3.3.1 "XFR2TR Programming Model" -- the worked example."""

    def test_write_order_is_submission_order(self):
        """6.4.6.3.3.1 shows XOUT 0,1,2,6,3 landing as 3,6,2,1,0.  SILICON
        (module point 1) lands it as 0,1,2,6,3, and so does the model."""
        bus = make_memory()
        fill_source_table(bus)
        core = make_core(
            SETUP_ASM + SUBMIT_WORKED_EXAMPLE_ASM + WAIT_FOR_IDLE_ASM,
            bus=bus,
        )
        core.run(max_steps=1000)
        assert core.halted
        assert [ring_entry(bus, slot) for slot in range(5)] == [0, 1, 2, 6, 3]

    def test_second_worked_example_continues_the_write_pointer(self):
        """6.4.6.3.3.1's second submit, XOUT 20,4,7,0,1,2, continues at slot 5.
        SILICON lands it in submission order, not the example's 2,1,0,7,4,20."""
        ring, bus = make_ring()
        fill_source_table(bus, count=32)
        configure(ring, nums=15)
        ring.submit([0, 1, 2, 6, 3])
        drain(ring)
        ring.submit([20, 4, 7, 0, 1, 2])
        drain(ring)

        assert ring.wrt_ptr == ptr(11)
        assert [ring_entry(bus, slot) for slot in range(5, 11)] == [20, 4, 7, 0, 1, 2]

    def test_the_engine_copies_the_whole_tr_payload(self):
        """The copy is of TR contents, not of an index."""
        ring, bus = make_ring()
        bus.write(SRC_BASE + 3 * 8, bytes.fromhex("DEADBEEFCAFEF00D"))
        configure(ring, nums=15)
        ring.submit([3])
        drain(ring)
        payload, _ = bus.read(RING_BASE, 8)
        assert payload == bytes.fromhex("DEADBEEFCAFEF00D")

    def test_source_and_ring_are_indexed_by_the_configured_tr_size(self):
        """Table 6-112 tr_size 1h selects 64-byte TR elements."""
        ring, bus = make_ring()
        fill_source_table(bus, count=8, tr_size=64)
        configure(ring, nums=15, tr_size_sel=1)
        assert ring.tr_size == 64
        ring.submit([5])
        drain(ring)
        assert ring_entry(bus, 0, tr_size=64) == 5
        payload, _ = bus.read(RING_BASE, 64)
        assert payload == bytes([5]) * 64

    def test_the_write_pointer_wraps_at_the_configured_ring_size(self):
        """6.4.6.3.3: "1 ring, with wrap around support".  SILICON (module
        point 3): tr_rsrc_nums = 3 wraps after 3 entries."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=3)
        assert ring.ring_entries == 3
        ring.submit([1, 2, 3, 4, 5])
        drain(ring)

        assert ring.wrt_ptr == ptr(5 % 3)
        # 4 and 5 wrapped over slots 0 and 1; slot 3 was never written.
        assert [ring_entry(bus, slot) for slot in range(4)] == [4, 5, 3, 0]

    def test_a_copy_outside_mapped_memory_fails_loud(self):
        ring, _ = make_ring()
        ring.configure_source({6: 0x3FF00, 7: 0})
        ring.configure_ring({6: RING_BASE, 7: 15 << 8})
        ring.submit([1])
        with pytest.raises(RuntimeError, match="XFR2TR copy of TR ID 1"):
            drain(ring)


#: Silicon replay programs use the sim's shared RAM for the result area, as
#: the device runs did, so every SBBO costs what it cost on the board.
RESULTS = 0x13100


def _results(bus: MemoryBus, count: int) -> list[int]:
    raw, _ = bus.read(RESULTS, 4 * count)
    return [int.from_bytes(raw[4 * k:4 * k + 4], "little") for k in range(count)]


def _submit_asm(ids: list[int]) -> str:
    """Left-pack *ids* into R2.. and XOUT them, as the device programs did."""
    padded = ids + [0] * (len(ids) % 2)
    lines = []
    for k in range(0, len(padded), 2):
        lines.append(f"    ldi32 r{2 + k // 2}, 0x{(padded[k + 1] << 16) | padded[k]:08X}")
    lines.append(f"    xout 0x72, &r2, {2 * len(ids)}")
    return "\n".join(lines) + "\n"


_STORE_STATUS = """
    xin 0x71, &r7, 8
    sbbo &r7, r24, 0, 4
    add r24, r24, 4
    sbbo &r8, r24, 0, 4
    add r24, r24, 4
"""

_SILICON_SETUP = """
    ldi32 r24, 0x13100
    ldi32 r6, 0x10000
    ldi r7, 0
    xout 0x70, &r6, 8
    ldi32 r6, 0x11000
    ldi32 r7, 0xF02
    xout 0x71, &r6, 8
"""


class TestSiliconBehaviour:
    """Programs run on an LP-AM243 (AM2434, ICSSG0 RTU_PRU0), replayed here.

    The device runs used source and ring bases 0x14000 / 0x16000; these use
    the suite's 0x10000 / 0x11000, so pointer values are rebased.  Every
    other expected value is what the device returned.
    """

    def _run(self, body: str) -> tuple[PRUCore, MemoryBus]:
        bus = make_memory()
        fill_source_table(bus)
        core = make_core(_SILICON_SETUP + body + WAIT_FOR_IDLE_ASM, bus=bus)
        core.run(max_steps=5000)
        assert core.halted
        return core, bus

    def test_trm_example_lands_in_submission_order(self):
        _, bus = self._run(
            _submit_asm([0, 1, 2, 6, 3]) + WAIT_FOR_IDLE_ASM.replace("halt", "")
            .replace("wait_idle", "w1") + _submit_asm([20, 4, 7, 0, 1, 2])
        )
        assert [ring_entry(bus, k) for k in range(11)] == [0, 1, 2, 6, 3, 20, 4, 7, 0, 1, 2]

    def test_eight_ids_land_in_order(self):
        _, bus = self._run(_submit_asm([1, 2, 3, 4, 5, 6, 7, 8]))
        assert [ring_entry(bus, k) for k in range(8)] == [1, 2, 3, 4, 5, 6, 7, 8]

    def test_occupancy_trace_after_an_eight_id_submit(self):
        """Device: occupancy 8,7,6,5,4,3 and pointer +0..+5 entries on six
        status reads taken 7 cycles apart, starting straight after the XOUT."""
        _, bus = self._run(_submit_asm([1, 2, 3, 4, 5, 6, 7, 8]) + _STORE_STATUS * 6)
        words = _results(bus, 12)
        assert words[0::2] == [0x801, 0x701, 0x601, 0x501, 0x401, 0x301]
        assert words[1::2] == [ptr(k) for k in range(6)]

    def test_overflow_overwrites_queued_ids(self):
        """Device (tr_rsrc_nums = 31): 1..8, then 9..16 one status read later.  Occupancy reads 8
        then 14; 16 TRs land as 1,2,3,12..16,9..16 -- IDs 4..8 lost."""
        core, bus = self._run(
            "    ldi32 r6, 0x11000\n    ldi32 r7, 0x1F02\n    xout 0x71, &r6, 8\n"
            + _submit_asm([1, 2, 3, 4, 5, 6, 7, 8]) + _STORE_STATUS
            + _submit_asm([9, 10, 11, 12, 13, 14, 15, 16]) + _STORE_STATUS
        )
        assert _results(bus, 4) == [0x801, ptr(0), 0xE01, ptr(2)]
        assert [ring_entry(bus, k) for k in range(16)] == [
            1, 2, 3, 12, 13, 14, 15, 16, 9, 10, 11, 12, 13, 14, 15, 16
        ]
        assert core.task_ring.wrt_ptr == ptr(16)
        assert core.task_ring.overflow_events == 5

    @pytest.mark.parametrize("ids,size64,iters", [
        ([5], False, [2, 2, 2, 2]),
        ([10, 11, 12], False, [6, 6, 5, 5]),
        (list(range(1, 9)), False, [15, 14, 14, 14]),
        ([2, 0], True, [18, 18, 18, 17]),
    ])
    def test_drain_time_to_one_cycle(self, ids, size64, iters):
        """Device: a 4-cycle busy poll started 0..3 NOPs after the submit
        counts these iterations (identical on two runs), which pins the drain
        time -- start, per-TR copy cost and the busy tail -- to one cycle."""
        got = []
        for ph in range(4):
            bus = make_memory()
            fill_source_table(bus, count=32, tr_size=64 if size64 else 8)
            core = make_core(
                _SILICON_SETUP.replace("ldi r7, 0\n", f"ldi r7, {int(size64)}\n")
                + "    ldi r22, 0\n    ldi32 r28, 0x4000\n" + _submit_asm(ids) + "    nop\n" * ph
                + "tp:\n    xin 0x71, &r7, 4\n    qbbc td, r7, 0\n    add r22, r22, 1\n"
                "    qbgt tp, r22, r28\ntd:\n    halt\n",
                bus=bus,
            )
            core.run(max_steps=5000)
            assert core.halted
            got.append(core.registers.read_full(22))
        assert got == iters

    @pytest.mark.parametrize("nums,ids,ptr_index,slots", [
        (0, [1, 2, 3], 0, [3]),
        (1, [1, 2, 3], 0, [3]),
        (2, [1, 2, 3, 4, 5], 1, [5, 4]),
    ])
    def test_small_rings(self, nums, ids, ptr_index, slots):
        """Device: nums 0 and 1 both behave as a one-entry ring; nums 2 as
        two entries."""
        core, bus = self._run(
            f"    ldi32 r6, 0x11000\n    ldi32 r7, 0x{(nums << 8) | 2:X}\n    xout 0x71, &r6, 8\n"
            + _submit_asm(ids)
        )
        assert core.task_ring.wrt_ptr == ptr(ptr_index)
        assert [ring_entry(bus, k) for k in range(len(slots))] == slots

    def test_ring_of_fifteen_holds_fifteen(self):
        """Device: nums = 15 and 16 TRs -> the 16th lands in slot 0 and the
        pointer ends one entry in."""
        core, bus = self._run(
            _submit_asm([1, 2, 3, 4, 5, 6, 7, 8]) + WAIT_FOR_IDLE_ASM.replace("halt", "")
            .replace("wait_idle", "w1") + _submit_asm([9, 10, 11, 12, 13, 14, 15, 16])
        )
        assert core.task_ring.wrt_ptr == ptr(1)
        assert [ring_entry(bus, k) for k in range(15)] == [16] + list(range(2, 16))

    def test_ring_of_three_wraps_after_three(self):
        """Device: tr_rsrc_nums = 3; 10,11,12 then 13,14,15 -> pointer back at
        the base both times and the ring reads 13,14,15."""
        core, bus = self._run(
            "    ldi32 r6, 0x11000\n    ldi32 r7, 0x302\n    xout 0x71, &r6, 8\n"
            + _submit_asm([10, 11, 12]) + WAIT_FOR_IDLE_ASM.replace("halt", "")
            .replace("wait_idle", "w1") + _submit_asm([13, 14, 15])
        )
        assert core.task_ring.wrt_ptr == ptr(0)
        assert [ring_entry(bus, k) for k in range(3)] == [13, 14, 15]


class TestReset:
    def test_core_reset_clears_xfr2tr_state(self):
        bus = make_memory()
        fill_source_table(bus)
        core = make_core(bus=bus)
        core.task_ring.configure_source({6: SRC_BASE, 7: 1})
        core.task_ring.configure_ring({6: RING_BASE, 7: 15 << 8})
        core.task_ring.submit([1, 2, 3])

        core.reset()

        ring = core.task_ring
        assert ring.tr_msrc_base == 0
        assert ring.tr_rsrc_base == 0
        assert ring.tr_size_sel == 0
        assert ring.tr_rsrc_nums == 0
        assert ring.wrt_ptr == 0
        assert ring.fifo_occ == 0
        assert ring.busy is False


class TestPointerDifferenceEngine:
    """Module point 7.  Replays LP-AM243 programs from the 2026-10-19 run
    (research/pru-simulator-pr-hw-checks-2026-10-19, case sets s37 and s37b):
    RTU_PRU0, 32-entry ring, IDs 1..8 in one XOUT, then 9..16 built with four
    ADDs and sent in a second XOUT before any TR lands.  Expected rings and
    write pointers are the silicon read-back, not model output.
    """

    def _run(self, tail: str):
        bus = make_memory()
        fill_source_table(bus)
        asm = (SETUP_ASM.replace("0x00000F00", "0x00001F00")   # 32-entry ring, as on the board
               + "    ldi32 r2, 0x00020001\n    ldi32 r3, 0x00040003\n"
               "    ldi32 r4, 0x00060005\n    ldi32 r5, 0x00080007\n"
               "    ldi32 r6, 0x00080008\n"
               "    xout 0x72, &r2, 16\n"
               "    add r2, r2, r6\n    add r3, r3, r6\n    add r4, r4, r6\n    add r5, r5, r6\n"
               "    xout 0x72, &r2, 16\n"
               + tail + WAIT_FOR_IDLE_ASM)
        core = make_core(asm, bus=bus)
        core.run(max_steps=5000)
        assert core.halted
        ring = core.task_ring
        n = ring.wrt_index
        return [ring_entry(bus, k) for k in range(n)], ring

    def test_sixteen_back_to_back_all_land(self):
        # silicon s37/b2b_16 and its four phases, s37b/b2b_16_ctl
        got, ring = self._run("")
        assert got == [1, 10, 11, 12, 13, 14, 15, 16, 9, 10, 11, 12, 13, 14, 15, 16]
        assert ring.wrt_ptr == RING_BASE + 16 * 8
        assert ring.overflow_events == 7         # IDs 2..8 overwritten, ID 1 already latched

    def test_seventeenth_before_the_first_landing_stops_the_engine(self):
        # silicon s37/b2b_17 and s37b/b2b_17_repeat: ring [1], wrt_ptr base + 8
        got, ring = self._run("    ldi r2, 17\n    xout 0x72, &r2, 2\n")
        assert got == [1]
        assert ring.busy is False

    def test_seventeenth_after_the_first_landing_keeps_it_running(self):
        # silicon s37b/b2b_17_late
        got, _ = self._run("    ldi r2, 17\n    nop\n    nop\n    nop\n    nop\n"
                           "    xout 0x72, &r2, 2\n")
        assert got == [1, 10, 11, 12, 13, 14, 15, 16, 17, 10, 11, 12, 13, 14, 15, 16, 17]

    def test_eighteen_lands_two(self):
        # silicon s37b/b2b_18
        got, _ = self._run("    ldi32 r2, 0x00120011\n    xout 0x72, &r2, 4\n")
        assert got == [1, 10]

    def test_twenty_four_lands_eight(self):
        # silicon s37b/b2b_24
        got, _ = self._run("    add r2, r2, r6\n    add r3, r3, r6\n    add r4, r4, r6\n"
                           "    add r5, r5, r6\n    xout 0x72, &r2, 16\n")
        assert got == [1, 10, 19, 20, 21, 22, 23, 24]

    def test_one_more_id_after_the_stop_lands_from_its_own_slot(self):
        # silicon s37b/b2b_17_then_1: ring [1, 20]
        got, _ = self._run("    ldi r2, 17\n    xout 0x72, &r2, 2\n" + WAIT_FOR_IDLE_ASM.replace("wait_idle", "wait_idle0").replace("halt", "")
                           + "    ldi r2, 20\n    xout 0x72, &r2, 2\n")
        assert got == [1, 20]


def drain(ring: XFR2TRRing, limit: int = 4096) -> int:
    """Tick until the accelerator reports idle; return the ticks taken.

    This is a *clock*, not a drain hook: it only calls ``tick()``, the same
    thing ``PRUCore.step()`` calls once per cycle.  It exists so unit-level
    tests do not have to wrap every case in firmware; the acceptance case
    ``test_wait_for_idle_firmware_terminates_in_a_single_run`` uses no helper
    at all.
    """
    ticks = 0
    while ring.busy and ticks < limit:
        ring.tick()
        ticks += 1
    assert ticks < limit, "ring never went idle"
    return ticks
