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
"""

import pytest

from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from pru_io.io_port import IOPort
from xfr.task_ring_accelerator import (
    ALLOWED_CORE_NAMES,
    MAX_IDS_PER_XOUT,
    SUBMIT_FIFO_DEPTH,
    TR_SIZE_BYTES,
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
        with pytest.raises(RuntimeError, match="0x70"):
            refused.run(max_steps=50)

    def test_tx_pru_is_also_refused_through_the_same_path(self):
        refused = make_core(SETUP_ASM + "    halt\n", name="TX_PRU0")
        with pytest.raises(RuntimeError, match="0x70"):
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
        """Table 6-112 tr_rsrc_nums is R7[19-8], "0 = 1 TR / 4095 = 4096 TRs"."""
        ring, _ = make_ring()
        ring.configure_ring({6: RING_BASE, 7: 0xABC << 8})
        assert ring.tr_rsrc_nums == 0xABC
        assert ring.ring_entries == 0xABC + 1

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
        assert ring.wrt_ptr == 2

        ring.configure_ring({7: 1 << 1})
        assert ring.wrt_ptr == 0

    def test_0x71_xout_bit0_is_reserved_and_is_not_the_reset(self):
        """Table 6-112 marks R7[0] of the 0x71 XOUT "Reserved"."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        ring.submit([1, 2])
        drain(ring)
        assert ring.wrt_ptr == 2

        ring.configure_ring({7: 1 << 0})
        assert ring.wrt_ptr == 2

    def test_reset_bit_leaves_the_submit_fifo_alone(self):
        """INFERENCE (module note 4): Table 6-112 scopes reset to the pointer.

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
        """Table 6-112: "R8[17-0] 0x71 XIN tr_rsrc_wrt_ptr"."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        view = TaskRingAccelerator(ring, XFR2TR_RING_CFG, "RTU0")
        ring.submit([1, 2, 3])
        drain(ring)

        payload = view.xin(7, 8)
        assert len(payload) == 8
        assert int.from_bytes(payload[4:8], "little") == 3

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
        """DERIVED (module note 2), and required by the 6.4.6.3.3.1 example,
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

    def test_overflowing_the_submit_fifo_fails_loud(self):
        """SIMULATOR POLICY (module note 5): the TRM does not document this."""
        ring, _ = make_ring()
        ring.submit([1, 2, 3, 4, 5, 6, 7, 8])
        with pytest.raises(RuntimeError, match="overflow"):
            ring.submit([9])

    def test_fifo_occupancy_and_ring_write_pointer_are_independent(self):
        """The FIFO empties while the ring fills -- they are not one counter.

        A model that derived tr_rscr_fifo_occ from ring occupancy would report
        8 here instead of 0, and would never show an entry that has left the
        FIFO but not yet landed.
        """
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)          # a 16-entry ring, far bigger than the FIFO
        ring.submit([1, 2, 3, 4, 5, 6, 7, 8])
        assert (ring.fifo_occ, ring.wrt_ptr) == (8, 0)

        trace = []
        for _ in range(64):
            ring.tick()
            trace.append((ring.fifo_occ, ring.wrt_ptr))
            if not ring.busy:
                break

        # Final state: the FIFO is empty and all eight TRs are in the ring.
        assert (ring.fifo_occ, ring.wrt_ptr) == (0, 8)
        # And at some point an ID was in neither structure -- out of the FIFO,
        # not yet landed.  That is the state Table 6-112's "AND the last write
        # completed" clause exists to describe.
        assert any(occ + ptr < 8 for occ, ptr in trace)

    def test_a_full_fifo_can_coexist_with_a_partly_filled_ring(self):
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        ring.submit([1, 2, 3, 4, 5, 6, 7, 8])
        drain(ring)
        ring.submit([1, 2, 3, 4, 5, 6, 7, 8])
        assert ring.fifo_occ == 8
        assert ring.wrt_ptr == 8


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
        assert core.task_ring.wrt_ptr == 5

    def test_the_engine_is_driven_by_core_steps(self):
        """Submitted IDs sit still until the core executes instructions."""
        bus = make_memory()
        fill_source_table(bus)
        core = make_core("    ldi r0, 0\n" * 40, bus=bus)
        core.task_ring.configure_source({6: SRC_BASE, 7: 0})
        core.task_ring.configure_ring({6: RING_BASE, 7: 15 << 8})
        core.task_ring.submit([4, 5, 6])

        assert core.task_ring.wrt_ptr == 0     # no cycles yet, nothing moved
        core.run(max_steps=40)
        assert core.task_ring.wrt_ptr == 3
        assert core.task_ring.busy is False

    def test_nothing_drains_without_ticks(self):
        """The only drain path is time; the ring holds still without it."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        ring.submit([1, 2, 3])
        for _ in range(100):
            assert ring.fifo_occ == 3
            assert ring.wrt_ptr == 0
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

    def test_busy_stays_set_after_the_fifo_empties_until_the_write_lands(self):
        """Table 6-112: busy "until the FIFO is empty AND the last write
        completed /data has landed" -- both terms, not just the first."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=15)
        ring.submit([9])

        saw_empty_fifo_still_busy = False
        for _ in range(32):
            ring.tick()
            if ring.fifo_occ == 0 and ring.wrt_ptr == 0:
                assert ring.busy is True, (
                    "the last TR has left the FIFO but has not landed in the "
                    "ring, so tr_rsrc_busy must still read 1"
                )
                saw_empty_fifo_still_busy = True
            if not ring.busy:
                break

        assert saw_empty_fifo_still_busy
        assert ring.wrt_ptr == 1
        assert ring.busy is False

    def test_a_wait_for_idle_loop_falls_through_when_nothing_was_submitted(self):
        core = make_core(SETUP_ASM + WAIT_FOR_IDLE_ASM)
        core.run(max_steps=200)
        assert core.halted


class TestCopyEngine:
    """6.4.6.3.3.1 "XFR2TR Programming Model" -- the worked example."""

    def test_write_order_reproduces_the_trm_worked_example(self):
        """6.4.6.3.3.1: XOUT 0,1,2,6,3 lands as 3,6,2,1,0 in WR slots 0..4.

        The reverse order is INFERRED (module note 3) from the worked example;
        the TRM never states the FIFO load order normatively.
        """
        bus = make_memory()
        fill_source_table(bus)
        core = make_core(
            SETUP_ASM + SUBMIT_WORKED_EXAMPLE_ASM + WAIT_FOR_IDLE_ASM,
            bus=bus,
        )
        core.run(max_steps=1000)
        assert core.halted
        assert [ring_entry(bus, slot) for slot in range(5)] == [3, 6, 2, 1, 0]

    def test_second_worked_example_continues_the_write_pointer(self):
        """6.4.6.3.3.1: XOUT 20,4,7,0,1,2 lands as 2,1,0,7,4,20 in slots 5..10."""
        ring, bus = make_ring()
        fill_source_table(bus, count=32)
        configure(ring, nums=15)
        ring.submit([0, 1, 2, 6, 3])
        drain(ring)
        ring.submit([20, 4, 7, 0, 1, 2])
        drain(ring)

        assert ring.wrt_ptr == 11
        assert [ring_entry(bus, slot) for slot in range(5, 11)] == [2, 1, 0, 7, 4, 20]

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
        """6.4.6.3.3: "1 ring, with wrap around support"; Table 6-112 sizes it
        with tr_rsrc_nums, 0 = 1 TR."""
        ring, bus = make_ring()
        fill_source_table(bus)
        configure(ring, nums=3)          # tr_rsrc_nums = 3 -> a 4-entry ring
        assert ring.ring_entries == 4
        ring.submit([1, 2, 3, 4, 5, 6])
        drain(ring)

        assert ring.wrt_ptr == 6 % 4
        # Submitted 6,5,4,3,2,1 in drain order; the last two wrapped over the
        # first two slots.
        assert [ring_entry(bus, slot) for slot in range(4)] == [2, 1, 4, 3]

    def test_a_copy_outside_mapped_memory_fails_loud(self):
        ring, _ = make_ring()
        ring.configure_source({6: 0x3FF00, 7: 0})
        ring.configure_ring({6: RING_BASE, 7: 15 << 8})
        ring.submit([1])
        with pytest.raises(RuntimeError, match="XFR2TR copy of TR ID 1"):
            drain(ring)


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
