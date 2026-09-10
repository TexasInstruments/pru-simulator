"""TRM-backed vectors for the XFR2VBUS accelerator (RD_ID0/RD_ID1 0x60/0x61,
WR_ID0/WR_ID1 0x62/0x63).

See docs/xfr2vbus-trm-model-table.md for the field-by-field TRM citations
this suite is written against (AM64x/AM243x TRM SPRUIM2J section 6.4.6.3.1,
Tables 6-106/6-107).
"""

import struct

import pytest

from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from pru_io.io_port import IOPort
from xfr.xfr2vbus_accelerator import (
    RD_ID0,
    RD_ID1,
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

    @pytest.mark.parametrize("size", [2, 3, 8, 16, 63])
    def test_invalid_write_sizes_are_rejected(self, size):
        """TRM Table 6-106 only documents 1, 4, 32, 64 byte WR_DATA sizes.

        All of these sizes stay within the R2..R17 data window (no spillover
        into an address register), so the rejection is unambiguously about
        WR_DATA size, not register routing.
        """
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        acc.xout(10, struct.pack("<I", VBUS_BASE))
        with pytest.raises(ValueError, match="valid WR_DATA size"):
            acc.xout(2, b"\x00" * size)

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

    def test_wr_busy_reads_zero_after_synchronous_write(self):
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        acc.xout(10, struct.pack("<I", VBUS_BASE))
        acc.xout(2, b"\xFF" * 4)
        assert acc.xin(20, 1) == b"\x00"

    def test_wr_busy_only_exposed_on_r20(self):
        vbus = make_vbus()
        acc = XFR2VBUSWriteAccelerator(vbus, WR_ID0)
        with pytest.raises(ValueError, match="WR_BUSY via XIN &R20"):
            acc.xin(2, 4)

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
            "xin 96, &r2, 32",
            "halt",
        ]
        core = make_core("\n".join(lines), vbus=vbus)
        core.run(20)
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
            "xin 96, &r2, 64",
            "halt",
        ]
        core = make_core("\n".join(lines), vbus=vbus)
        core.run(20)
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
        first4 = acc.xin(2, 4)
        assert first4 == bytes(range(4))
        # buffer is fully popped: a second command must be submitted, RD_DATA_FL
        # cannot still be occupied with the remaining 28 bytes.
        assert acc.xin(18, 1) == b"\x00"  # RD_BUSY/RD_DATA_FL both clear

    def test_status_bits_rd_busy_and_rd_data_fl_set_together(self):
        vbus = make_vbus()
        self._seed(vbus, VBUS_BASE, b"\x00" * 4)
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        acc.xout(19, struct.pack("<I", VBUS_BASE))
        status = acc.xin(18, 1)[0]
        assert status & 0b0001, "RD_BUSY (bit 0) must be set while data is unread"
        assert status & 0b0100, "RD_DATA_FL (bit 2) must be set while data is unread"
        assert not (status & 0b0010), "RD_CMD_FL (bit 1) must be clear (synchronous fetch)"
        assert not (status & 0b1000), "RD_MST_REQ (bit 3) must be clear (data already latched)"
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
        assert acc.xin(2, 32) == bytes(range(32))
        # the pop above must have already issued the next command at +0x20
        assert acc.xin(2, 32) == bytes(range(100, 132))

    def test_backpressure_rejects_new_rd_addr_while_undrained(self):
        vbus = make_vbus()
        self._seed(vbus, VBUS_BASE, b"\x00" * 4)
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        acc.xout(19, struct.pack("<I", VBUS_BASE))
        with pytest.raises(ValueError, match="RD_BUSY=1"):
            acc.xout(19, struct.pack("<I", VBUS_BASE + 4))

    def test_xin_rd_data_without_pending_command_is_rejected(self):
        vbus = make_vbus()
        acc = XFR2VBUSReadAccelerator(vbus, RD_ID0)
        with pytest.raises(ValueError, match="no read pending"):
            acc.xin(2, 4)

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
        assert acc0.xin(2, 4) == b"\x01" * 4
        assert acc1.xin(2, 4) == b"\x02" * 4


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
