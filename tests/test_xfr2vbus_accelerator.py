"""TRM-backed vectors for the XFR2VBUS accelerator (RD_ID0/RD_ID1 0x60/0x61,
WR_ID0/WR_ID1 0x62/0x63).

See docs/xfr2vbus-trm-model-table.md for the field-by-field TRM citations
this suite is written against (AM64x/AM243x TRM SPRUIM2J section 6.4.6.3.1,
Tables 6-106/6-107).  ``TestSiliconBehaviour`` replays programs run on an
LP-AM243 (AM2434, ICSSG0 PRU0, reading MSRAM) and asserts what the device
returned.
"""

import struct

import pytest

from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from pru_io.io_port import IOPort
from xfr.xfr2vbus_accelerator import (
    AM243X_MSRAM_READ_LATENCY,
    RD_ID0,
    RD_ID1,
    RD_STATUS_DATA_READY,
    RD_STATUS_IN_FLIGHT,
    WR_BUSY_CYCLES,
    WR_ID0,
    WR_ID1,
    XFR2VBUSReadAccelerator,
    XFR2VBUSWriteAccelerator,
)
from xfr.xfr_bus import XFRBus

VBUS_BASE = 0x00100000
VBUS_SIZE = 0x00010000


def make_vbus() -> MemoryBus:
    vbus = MemoryBus()
    vbus.add_region(MemoryRegion("MSMC", VBUS_BASE, VBUS_SIZE, 4, 4, 0))
    return vbus


def make_core(asm: str = "halt\n", vbus: MemoryBus | None = None) -> PRUCore:
    mem = MemoryBus()
    mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
    core = PRUCore("PRU0", mem, XFRBus(), IOPort(), vbus_memory=vbus if vbus is not None else make_vbus())
    errors = core.load_asm(asm)
    assert errors == [], f"Assembly errors: {errors}"
    return core


def reg(core: PRUCore, n: int) -> int:
    return core.registers.read_full(n)


def land(acc) -> None:
    """Let enough core cycles pass for any read in flight to land."""
    acc.advance(max(AM243X_MSRAM_READ_LATENCY.values()))


#: Poll RD status until RD_DATA_FL (R18[2]) is set -- the TRM's read flow.
WAIT_RD_DATA_FL = """
wait_rd:
    xin 96, &r18, 1
    qbbc wait_rd, r18, 2
"""


# ===========================================================================
# Write channel (WR_ID0=0x62=98, WR_ID1=0x63=99) — Table 6-106
# ===========================================================================

class TestWriteChannel:
    def test_direct_class_write_reaches_backing_memory(self):
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        acc.xout(10, struct.pack("<I", VBUS_BASE))  # WR_ADDR lower32, 32B window (R10)
        acc.xout(2, b"\xAA" * 32)                    # WR_DATA
        assert vbus.read(VBUS_BASE, 32)[0] == b"\xAA" * 32

    def test_assembly_32byte_write_reaches_backing_memory(self):
        lines = [f"ldi32 r{r}, 0x{0xB0000000 + r:08X}" for r in range(2, 10)]
        lines += [
            "ldi32 r10, 0x00100000",  # WR_ADDR lower32 (32B window: R11:R10)
            "xout 98, &r10, 4",
            "xout 98, &r2, 32",       # WR_DATA
            "halt",
        ]
        core = make_core("\n".join(lines))
        core.run(30)
        data, _ = core.vbus_memory.read(VBUS_BASE, 32)
        expected = b"".join(struct.pack("<I", 0xB0000000 + r) for r in range(2, 10))
        assert data == expected

    def test_assembly_64byte_write_reaches_backing_memory(self):
        lines = [f"ldi32 r{r}, 0x{0xC0000000 + r:08X}" for r in range(2, 18)]
        lines += [
            "ldi32 r18, 0x00100000",  # WR_ADDR lower32 (64B window: R19:R18)
            "xout 99, &r18, 4",
            "xout 99, &r2, 64",       # WR_DATA
            "halt",
        ]
        core = make_core("\n".join(lines))
        core.run(40)
        data, _ = core.vbus_memory.read(VBUS_BASE, 64)
        expected = b"".join(struct.pack("<I", 0xC0000000 + r) for r in range(2, 18))
        assert data == expected

    def test_combined_atomic_xout_sets_address_and_data_together(self):
        """TRM Table 6-106: 'the one XOUT can set the address and data at the
        same time' — a single XOUT spanning R19-R2 (data then address)."""
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        payload = b"\xEE" * 64 + struct.pack("<I", VBUS_BASE + 0x40) + b"\x00\x00"
        acc.xout(2, payload)
        assert vbus.read(VBUS_BASE + 0x40, 64)[0] == b"\xEE" * 64

    def test_1_byte_write_is_unaligned_allowed(self):
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        acc.xout(10, struct.pack("<I", VBUS_BASE + 1))
        acc.xout(2, b"\x7F")
        assert vbus.read(VBUS_BASE + 1, 1)[0] == b"\x7F"

    def test_4_byte_write_requires_size_aligned_address(self):
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        acc.xout(10, struct.pack("<I", VBUS_BASE + 2))  # not 4-byte aligned
        with pytest.raises(ValueError, match="aligned"):
            acc.xout(2, b"\x01\x02\x03\x04")

    def test_32_byte_write_requires_size_aligned_address(self):
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        acc.xout(10, struct.pack("<I", VBUS_BASE + 4))  # 4-aligned, not 32-aligned
        with pytest.raises(ValueError, match="aligned"):
            acc.xout(2, b"\xAA" * 32)

    @pytest.mark.parametrize("size", [2, 3, 16, 63])
    def test_invalid_write_sizes_are_rejected(self, size):
        """TRM section 6.4.6.3.1.6's Programming Model documents exactly
        four split-flow WR_DATA sizes: 1, 4, 8, 32, 64 bytes (line 7543,
        "XOUT (addr) then XOUT 32 Byte/ 8 Byte/ 4 Byte/ 1 Byte data"). 8 is
        NOT one of these rejected sizes — see
        test_8_byte_split_write_is_accepted below; this list covers sizes
        that stay within the R2..R17 data window (no spillover into an
        address register) and are absent from that list, so the rejection
        is unambiguously about WR_DATA size, not register routing.
        """
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        acc.xout(10, struct.pack("<I", VBUS_BASE))
        with pytest.raises(ValueError, match="valid split-flow WR_DATA size"):
            acc.xout(2, b"\x00" * size)

    def test_8_byte_split_write_is_accepted(self):
        """TRM SPRUIM2J section 6.4.6.3.1.6 "XFR2VBUS Programming Model"
        (line 7543) lists the split write flow "XOUT (addr) then XOUT 32
        Byte/ 8 Byte/ 4 Byte/ 1 Byte data" — 8 bytes is one of the four
        documented split-flow data sizes and must be accepted, not
        rejected. This regression test replaces a prior version of this
        suite that pinned the opposite (wrong) behaviour under a citation
        to Table 6-106 alone; see docs/xfr2vbus-trm-model-table.md "Write
        commands" for why the two TRM passages do not actually conflict.
        """
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        acc.xout(10, struct.pack("<I", VBUS_BASE + 8))  # 8-byte aligned
        acc.xout(2, b"\x11" * 8)
        assert vbus.read(VBUS_BASE + 8, 8)[0] == b"\x11" * 8

    def test_8_byte_write_requires_size_aligned_address(self):
        """Same alignment discipline already enforced for the 4- and
        32-byte split sizes (TRM section 6.4.6.3.1.4) applies to 8."""
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        acc.xout(10, struct.pack("<I", VBUS_BASE + 4))  # 4-aligned, not 8-aligned
        with pytest.raises(ValueError, match="aligned"):
            acc.xout(2, b"\x00" * 8)

    def test_8_byte_combined_write_is_rejected(self):
        """Table 6-106's WR_ADDR notes for both the 32-byte window ("...in
        this case data needs to be the full 32 bytes", R11-R10 note, line
        7438) and the 64-byte window ("...full 64 bytes", R19-R18 note,
        line 7413) require a combined single-XOUT address+data write to
        carry the *entire* data window — never a partial size such as 8
        bytes. That combined-with-8-bytes shape cannot actually be
        constructed through `xout()`: reaching an address register
        requires having already supplied the whole preceding data window
        (32 or 64 bytes), so this exercises the accelerator's
        `_validate_size` combined-size rule directly, the same rule
        `xout()` applies internally, rather than asserting the shape is
        merely unreachable.
        """
        acc = XFR2VBUSWriteAccelerator(make_vbus(), WR_ID0)
        with pytest.raises(ValueError, match="valid combined size"):
            acc._validate_size(8, combined=True)
        # The paired positive case: 32 and 64 remain the only legal
        # combined sizes, matching the two documented WR_DATA windows.
        acc._validate_size(32, combined=True)
        acc._validate_size(64, combined=True)

    def test_65_byte_xout_spills_last_byte_into_wr_addr_not_data(self):
        """A 65-byte XOUT from &R2 reaches into R18 (byte 0 of WR_ADDR in the
        64B window): that byte is address, so only 64 bytes count as
        WR_DATA — a valid size, not a rejected one. The injected address byte
        (0x40 = 64) is picked so the resulting address is still 64-byte
        aligned, isolating this test to the size/routing behaviour."""
        vbus = MemoryBus()
        vbus.add_region(MemoryRegion("LOW", 0x0, 0x1000, 4, 4, 0))
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        acc.xout(2, b"\xCC" * 64 + b"\x40")
        assert vbus.read(0x40, 64)[0] == b"\xCC" * 64

    def test_write_data_must_start_at_r2(self):
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        with pytest.raises(ValueError, match="must start at &R2"):
            acc.xout(3, b"\x00" * 4)

    def test_xout_to_unmapped_register_is_rejected(self):
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        with pytest.raises(ValueError, match="neither WR_DATA"):
            acc.xout(20, b"\x00" * 4)

    def test_wr_busy_is_set_after_a_write_then_clears(self):
        """SILICON: WR_BUSY (R20[0]) reads 1 straight after a write and clears
        WR_BUSY_CYCLES core cycles later.  The data is already in memory."""
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        acc.xout(10, struct.pack("<I", VBUS_BASE))
        assert acc.xin(20, 1) == b"\x00"          # address-only XOUT: no write
        acc.xout(2, b"\xFF" * 4)
        assert acc.xin(20, 1) == b"\x01"
        assert vbus.read(VBUS_BASE, 4)[0] == b"\xFF" * 4
        acc.advance(WR_BUSY_CYCLES - 1)
        assert acc.xin(20, 1) == b"\x01"
        acc.advance(1)
        assert acc.xin(20, 1) == b"\x00"

    def test_wr_busy_only_exposed_on_r20(self):
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        with pytest.raises(ValueError, match="WR_BUSY via XIN &R20"):
            acc.xin(2, 4)

    def test_wr_busy_is_byte_zero_of_r20_only(self):
        """WR_BUSY is R20[0] (Table 6-106): an XIN starting at &r20.b1 sees
        bits 8+ of the status word, which are all zero, even while busy."""
        acc = XFR2VBUSWriteAccelerator(make_vbus(), WR_ID0)
        acc.xout(10, struct.pack("<I", VBUS_BASE))
        acc.xout(2, b"\xFF" * 4)
        assert acc.xin(20, 1, 0) == b"\x01"
        assert acc.xin(20, 1, 1) == b"\x00"
        assert acc.xin(20, 2, 0) == b"\x01\x00"
        assert acc.xin(20, 3, 1) == b"\x00\x00\x00"

    def test_xchg_is_unsupported(self):
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        with pytest.raises(ValueError, match="no XCHG access type"):
            acc.xchg(2, b"\x00" * 4)

    def test_wr_id0_and_wr_id1_have_independent_address_latches(self):
        vbus = make_vbus()
        acc0 = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        acc1 = XFR2VBUSWriteAccelerator(vbus, WR_ID1)
        acc0.xout(10, struct.pack("<I", VBUS_BASE))
        acc1.xout(10, struct.pack("<I", VBUS_BASE + 0x100))
        acc0.xout(2, b"\x01" * 4)
        acc1.xout(2, b"\x02" * 4)
        assert vbus.read(VBUS_BASE, 4)[0] == b"\x01" * 4
        assert vbus.read(VBUS_BASE + 0x100, 4)[0] == b"\x02" * 4


# ===========================================================================
# Read channel (RD_ID0=0x60=96, RD_ID1=0x61=97) — Table 6-107
# ===========================================================================

class TestReadChannel:
    def _seed(self, vbus, addr, pattern):
        vbus.write(addr, pattern)

    def test_direct_class_4byte_default_size_read(self):
        vbus = make_vbus()
        self._seed(vbus, VBUS_BASE, b"\xDE\xAD\xBE\xEF")
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        acc.xout(19, struct.pack("<I", VBUS_BASE))  # RD_ADDR, default RD_SIZE=4B
        land(acc)
        assert acc.xin(2, 4) == b"\xDE\xAD\xBE\xEF"

    def test_assembly_32byte_read(self):
        vbus = make_vbus()
        pattern = bytes(range(32))
        self._seed(vbus, VBUS_BASE, pattern)
        lines = [
            "ldi r18.b0, 4",        # RD_AUTO=0, RD_SIZE=2 (32 Bytes)
            "xout 96, &r18.b0, 1",
            "ldi32 r19, 0x00100000",
            "xout 96, &r19, 4",     # submits the read
            WAIT_RD_DATA_FL,
            "xin 96, &r2, 32",
            "halt",
        ]
        core = make_core("\n".join(lines), vbus=vbus)
        core.run(100)
        assert core.halted
        result = bytearray()
        for r in range(2, 10):
            result += struct.pack("<I", reg(core, r))
        assert bytes(result) == pattern

    def test_assembly_64byte_read(self):
        vbus = make_vbus()
        pattern = bytes(range(64))
        self._seed(vbus, VBUS_BASE, pattern)
        lines = [
            "ldi r18.b0, 6",        # RD_AUTO=0, RD_SIZE=3 (64 Bytes)
            "xout 96, &r18.b0, 1",
            "ldi32 r19, 0x00100000",
            "xout 96, &r19, 4",
            WAIT_RD_DATA_FL,
            "xin 96, &r2, 64",
            "halt",
        ]
        core = make_core("\n".join(lines), vbus=vbus)
        core.run(100)
        assert core.halted
        result = bytearray()
        for r in range(2, 18):
            result += struct.pack("<I", reg(core, r))
        assert bytes(result) == pattern

    def test_full_pop_discards_unrequested_bytes(self):
        """TRM lines 7360/7376: XIN fully pops the data independent of XIN size."""
        vbus = make_vbus()
        self._seed(vbus, VBUS_BASE, bytes(range(32)))
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        acc.xout(18, bytes([0b100]))  # RD_SIZE=2 (32 Bytes)
        acc.xout(19, struct.pack("<I", VBUS_BASE))
        land(acc)
        first4 = acc.xin(2, 4)
        assert first4 == bytes(range(4))
        # buffer is fully popped: a second command must be submitted, RD_DATA_FL
        # cannot still be occupied with the remaining 28 bytes.
        assert acc.xin(18, 1) == b"\x00"  # RD_BUSY/RD_DATA_FL both clear

    def test_status_in_flight_then_data_ready_then_idle(self):
        """SILICON: 0x0B (RD_BUSY, RD_CMD_FL, RD_MST_REQ) while the read is in
        flight, 0x05 (RD_BUSY, RD_DATA_FL) once it lands, 0 after the pop."""
        vbus = make_vbus()
        self._seed(vbus, VBUS_BASE, b"\x00" * 4)
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        acc.xout(19, struct.pack("<I", VBUS_BASE))
        assert acc.xin(18, 1)[0] == RD_STATUS_IN_FLIGHT == 0x0B
        acc.advance(AM243X_MSRAM_READ_LATENCY[4] - 1)
        assert acc.xin(18, 1)[0] == RD_STATUS_IN_FLIGHT
        acc.advance(1)
        assert acc.xin(18, 1)[0] == RD_STATUS_DATA_READY == 0x05
        acc.xin(2, 4)
        assert acc.xin(18, 1) == b"\x00"

    @pytest.mark.parametrize("size_code,mutated_bit", [(0, 1), (2, 2), (3, 3)])
    def test_rd_size_encoding_bit_positions(self, size_code, mutated_bit):
        """Mutation: flipping a bit of RD_SIZE must select a different transfer
        size (proves the encoding, not just that *some* size works)."""
        vbus = make_vbus()
        self._seed(vbus, VBUS_BASE, bytes(range(64)))
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        acc.xout(18, bytes([size_code << 1]))
        acc.xout(19, struct.pack("<I", VBUS_BASE))
        land(acc)
        expected = {0: 4, 2: 32, 3: 64}[size_code]
        assert acc.xin(2, expected) == bytes(range(expected))

    def test_rd_size_reserved_value_is_rejected(self):
        vbus = make_vbus()
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        with pytest.raises(ValueError, match="Reserved"):
            acc.xout(18, bytes([0b010]))  # RD_SIZE=1 (Reserved)

    def test_rd_auto_rejects_4byte_mode(self):
        vbus = make_vbus()
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        with pytest.raises(ValueError, match="4 Byte mode"):
            acc.xout(18, bytes([0b001]))  # RD_AUTO=1, RD_SIZE=0 (4B)

    def test_auto_mode_reissues_read_on_pop_address_increments_by_size(self):
        vbus = make_vbus()
        self._seed(vbus, VBUS_BASE, bytes(range(32)))
        self._seed(vbus, VBUS_BASE + 0x20, bytes(range(100, 132)))
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        acc.xout(18, bytes([0b101]))  # RD_AUTO=1, RD_SIZE=2 (32 Bytes)
        acc.xout(19, struct.pack("<I", VBUS_BASE))
        land(acc)
        assert acc.xin(2, 32) == bytes(range(32))
        # the pop above must have already issued the next command at +0x20
        assert acc.xin(18, 1)[0] == RD_STATUS_IN_FLIGHT
        land(acc)
        assert acc.xin(2, 32) == bytes(range(100, 132))

    def test_backpressure_rejects_new_rd_addr_while_undrained(self):
        vbus = make_vbus()
        self._seed(vbus, VBUS_BASE, b"\x00" * 4)
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        acc.xout(19, struct.pack("<I", VBUS_BASE))
        with pytest.raises(ValueError, match="RD_BUSY=1"):
            acc.xout(19, struct.pack("<I", VBUS_BASE + 4))

    def test_xin_rd_data_before_data_lands_returns_the_old_register(self):
        """SILICON: an XIN of RD_DATA with RD_DATA_FL = 0 returns the data
        register as it stands (the last data that landed) and pops nothing."""
        vbus = make_vbus()
        self._seed(vbus, VBUS_BASE, b"\x11" * 4)
        self._seed(vbus, VBUS_BASE + 4, b"\x22" * 4)
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        assert acc.xin(2, 4) == b"\x00" * 4          # nothing ever landed
        acc.xout(19, struct.pack("<I", VBUS_BASE))
        land(acc)
        assert acc.xin(2, 4) == b"\x11" * 4
        acc.xout(19, struct.pack("<I", VBUS_BASE + 4))
        assert acc.xin(2, 4) == b"\x11" * 4          # early: old data, no pop
        assert acc.xin(18, 1)[0] == RD_STATUS_IN_FLIGHT
        land(acc)
        assert acc.xin(2, 4) == b"\x22" * 4

    def test_read_requires_size_aligned_address(self):
        vbus = make_vbus()
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        acc.xout(18, bytes([0b100]))  # RD_SIZE=2 (32 Bytes)
        with pytest.raises(ValueError, match="aligned"):
            acc.xout(19, struct.pack("<I", VBUS_BASE + 4))

    def test_upper_address_register_alone_does_not_trigger_a_read(self):
        vbus = make_vbus()
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        acc.xout(20, struct.pack("<H", 0) + b"\x00\x00")  # touches R20 only
        # No command was submitted: status must read idle, not busy.
        assert acc.xin(18, 1) == b"\x00"

    def test_xchg_is_unsupported(self):
        vbus = make_vbus()
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        with pytest.raises(ValueError, match="no XCHG access type"):
            acc.xchg(2, b"\x00" * 4)

    def test_rd_id0_and_rd_id1_have_independent_buffers(self):
        vbus = make_vbus()
        self._seed(vbus, VBUS_BASE, b"\x01" * 4)
        self._seed(vbus, VBUS_BASE + 4, b"\x02" * 4)
        acc0 = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        acc1 = XFR2VBUSReadAccelerator(vbus, RD_ID1)
        acc0.xout(19, struct.pack("<I", VBUS_BASE))
        acc1.xout(19, struct.pack("<I", VBUS_BASE + 4))
        land(acc0)
        land(acc1)
        assert acc0.xin(2, 4) == b"\x01" * 4
        assert acc1.xin(2, 4) == b"\x02" * 4


# ===========================================================================
# Silicon replay: LP-AM243 (AM2434), ICSSG0 PRU0, reads from MSRAM
# ===========================================================================

#: The device runs read MSRAM at 0x70170000 + off; these read the suite's
#: VBUS region instead, filled with the same pattern (word i = 0xC0DE0000+i).
PATTERN = struct.pack("<128I", *[0xC0DE0000 + i for i in range(128)])

#: The status/poll sequence the device programs used after the read XOUT:
#: one status XIN, then a 6-cycle poll loop until RD_DATA_FL=1, RD_MST_REQ=0.
STATUS_THEN_POLL = """
    xin 96, &r18, 1
    and r21, r18, 0xFF
    sbbo &r21, r24, 0, 4
    add r24, r24, 4
    ldi r22, 0
    zero &r29, 4
rp:
    xin 96, &r18, 1
    and r21, r18, 0xFF
    or r29, r29, r21
    qbbc rw, r21, 2
    qbbc rd, r21, 3
rw:
    add r22, r22, 1
    qbgt rp, r22, r28
rd:
    sbbo &r22, r24, 0, 4
    add r24, r24, 4
    sbbo &r29, r24, 0, 4
    add r24, r24, 4
"""


class TestSiliconBehaviour:
    def _run(self, body: str) -> tuple[PRUCore, list[int]]:
        vbus = make_vbus()
        vbus.write(VBUS_BASE, PATTERN)
        core = make_core("    ldi r24, 0x100\n    ldi32 r28, 0x4000\n" + body + "    halt\n", vbus=vbus)
        core.run(2000)
        assert core.halted
        raw, _ = core.memory.read(0x100, 64)
        return core, list(struct.unpack("<16I", bytes(raw)))

    @pytest.mark.parametrize("size,code,polls", [(4, 0x0, 2), (32, 0x4, 2), (64, 0x6, 3)])
    def test_combined_setup_read_latency_and_data(self, size, code, polls):
        """Device: XOUT 0x60, &r18, 8 is accepted; status 0x0B straight after,
        then 2 / 2 / 3 polls of the 6-cycle loop before RD_DATA_FL for
        4 / 32 / 64 bytes; the statuses seen OR to 0x0F; data matches."""
        last = {4: 2, 32: 9, 64: 17}[size]
        _, out = self._run(
            f"    ldi r18, {code}\n    ldi32 r19, 0x{VBUS_BASE:X}\n    xout 96, &r18, 8\n"
            + STATUS_THEN_POLL
            + f"    xin 96, &r2, {size}\n    sbbo &r2, r24, 0, 4\n    add r24, r24, 4\n"
            f"    sbbo &r{last}, r24, 0, 4\n"
        )
        assert out[:5] == [0x0B, polls, 0x0F, 0xC0DE0000, 0xC0DE0000 + size // 4 - 1]

    def test_early_xin_returns_old_data_and_pops_nothing(self):
        """Device: XIN RD_DATA straight after the XOUT returns the previous
        data (here the first read's) and the new data arrives later."""
        _, out = self._run(
            f"    ldi r18, 0x4\n    ldi32 r19, 0x{VBUS_BASE:X}\n    xout 96, &r18, 8\n"
            "w1:\n    xin 96, &r18, 1\n    qbbc w1, r18, 2\n    xin 96, &r2, 32\n"
            f"    ldi32 r19, 0x{VBUS_BASE + 0x20:X}\n    xout 96, &r19, 4\n"
            "    xin 96, &r2, 32\n    sbbo &r2, r24, 0, 4\n    add r24, r24, 4\n"
            + STATUS_THEN_POLL
            + "    xin 96, &r2, 32\n    sbbo &r2, r24, 0, 4\n"
        )
        assert out[0] == 0xC0DE0000          # old data, not consumed
        assert out[1] == 0x0B                # still in flight
        assert out[4] == 0xC0DE0008          # the new block, intact

    def test_auto_mode_drain_right_after_enable_does_not_skip_a_block(self):
        """open-pru FFT pattern: enable RD_AUTO, XIN RD_DATA at once.  Device:
        the drain pops nothing, so the first polled block is block 0."""
        body = (f"    ldi r18, 0x5\n    ldi32 r19, 0x{VBUS_BASE:X}\n    xout 96, &r18, 8\n"
                "    xin 96, &r2, 32\n")
        for k in range(3):
            body += (STATUS_THEN_POLL.replace("rp", f"rp{k}").replace("rw", f"rw{k}").replace("rd", f"rd{k}")
                     .replace("rd{k}:".format(k=k), f"rd{k}:")
                     + "    xin 96, &r2, 32\n    sbbo &r2, r24, 0, 4\n    add r24, r24, 4\n")
        _, out = self._run(body)
        blocks = [out[k * 4 + 3] for k in range(3)]
        assert blocks == [0xC0DE0000, 0xC0DE0008, 0xC0DE0010]

    def test_wr_busy_seen_straight_after_a_write(self):
        """Device: WR_BUSY reads 1 on the XIN after a 32-byte combined write
        and 0 on the poll five cycles later."""
        _, out = self._run(
            f"    ldi r17, 0\n    ldi32 r10, 0x{VBUS_BASE + 0x400:X}\n    ldi r11, 0\n"
            "    xout 98, &r2, 40\n    xin 98, &r20, 1\n    and r21, r20, 0xFF\n"
            "    sbbo &r21, r24, 0, 4\n    add r24, r24, 4\n    ldi r22, 0\n"
            "wp:\n    xin 98, &r20, 1\n    qbbc wd, r20, 0\n    add r22, r22, 1\n    qbgt wp, r22, r28\n"
            "wd:\n    sbbo &r22, r24, 0, 4\n"
        )
        assert out[:2] == [1, 0]


    # 4-cycle poll loop (XIN, QBBx, ADD, QBGT) started 0..3 NOPs after the
    # command: the iteration counts across the four phases pin the latency to
    # one cycle.  Values are what the LP-AM243 returned.
    TIGHT = """
tp:
    xin {xid}, &{reg}, 1
    {qb} td, {reg}, {bit}
    add r22, r22, 1
    qbgt tp, r22, r28
td:
    sbbo &r22, r24, 0, 4
    add r24, r24, 4
"""

    @pytest.mark.parametrize("size,code,iters", [
        (4, 0x0, [5, 4, 4, 4]), (32, 0x4, [5, 5, 5, 4]), (64, 0x6, [6, 6, 6, 5])])
    def test_read_latency_to_one_cycle(self, size, code, iters):
        got = []
        for ph in range(4):
            _, out = self._run(
                f"    ldi r18, {code}\n    ldi32 r19, 0x{VBUS_BASE:X}\n    ldi r22, 0\n"
                "    xout 96, &r18, 8\n" + "    nop\n" * ph
                + self.TIGHT.format(xid=96, reg="r18", qb="qbbs", bit=2)
            )
            got.append(out[0])
        assert got == iters

    def test_auto_reissue_lands_one_cycle_later_than_xout(self):
        """Device: after a pop in RD_AUTO mode the next 32-byte block polls
        5, 4, 4, 4 times across phases 0..3 -- 21 cycles, not 20."""
        got = []
        for ph in range(4):
            _, out = self._run(
                f"    ldi r18, 0x5\n    ldi32 r19, 0x{VBUS_BASE:X}\n    ldi r22, 0\n"
                "    xout 96, &r18, 8\n"
                + self.TIGHT.format(xid=96, reg="r18", qb="qbbs", bit=2).replace("tp", "ta").replace("td", "tb")
                + "    ldi r22, 0\n    xin 96, &r2, 32\n" + "    nop\n" * ph
                + "    sbbo &r2, r24, 0, 4\n    add r24, r24, 4\n"
                + self.TIGHT.format(xid=96, reg="r18", qb="qbbs", bit=2)
            )
            got.append(out[2])
        assert got == [5, 4, 4, 4]

    @pytest.mark.parametrize("size", [32, 64])
    def test_wr_busy_is_visible_for_one_poll_only(self, size):
        """Device: WR_BUSY is seen by an XIN on the cycle after the write and
        by none later (busy iterations 1, 0, 0, 0 across phases)."""
        xout = (f"    ldi r17, 0\n    ldi32 r10, 0x{VBUS_BASE + 0x400:X}\n    ldi r11, 0\n"
                "    ldi r22, 0\n    xout 98, &r2, 40\n") if size == 32 else (
                f"    ldi32 r18, 0x{VBUS_BASE + 0x400:X}\n    ldi r19, 0\n"
                "    ldi r22, 0\n    xout 98, &r2, 72\n")
        got = []
        for ph in range(4):
            _, out = self._run(xout + "    nop\n" * ph
                               + self.TIGHT.format(xid=98, reg="r20", qb="qbbc", bit=0))
            got.append(out[0])
        assert got == [1, 0, 0, 0]


# ===========================================================================
# Registration is load-bearing (mutation proving PR #30's fail-loud path
# still applies to a genuinely unmodelled ID, and that removing the
# registration reverts these IDs to that same fail-loud path).
# ===========================================================================

class TestRegistrationIsLoadBearing:
    @pytest.mark.parametrize("device_id", [RD_ID0, RD_ID1, WR_ID0, WR_ID1])
    def test_removing_registration_reverts_to_unsupported_xfr_path(self, device_id):
        core = make_core(f"xin {device_id}, &r2, 4\nhalt\n")
        del core.accelerators[device_id]
        with pytest.raises(RuntimeError, match=rf"XIN XFR device ID {device_id}.*not modelled"):
            core.run(4)

    def test_registered_ids_do_not_hit_the_unsupported_xfr_path(self):
        core = make_core("halt\n")
        for device_id in (RD_ID0, RD_ID1, WR_ID0, WR_ID1):
            assert device_id in core.accelerators
            assert core.xfr.supports(device_id) is False  # not an XFRBus scratchpad either
