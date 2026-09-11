"""TRM-backed vectors for BSWAP XIN IDs 0xA0, 0xA1 and 0xA2."""

import pytest

from core.pru_core import PRUCore
from core.registers import RegisterFile
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from pru_io.io_port import IOPort
from xfr.bswap_accelerator import BSWAP_4_8, BSWAP_4_16, BSWAP_BYTE_ORDER, BSwapAccelerator
from xfr.xfr_bus import XFRBus


def make_core(asm: str) -> PRUCore:
    mem = MemoryBus()
    mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
    core = PRUCore("PRU0", mem, XFRBus(), IOPort())
    assert core.load_asm(asm) == []
    return core


class TestByteOrderSwap:
    def test_full_register_is_reversed(self):
        regs = RegisterFile()
        regs.write_full(2, 0x11223344)
        assert BSwapAccelerator(regs, BSWAP_BYTE_ORDER).xin(2, 4) == bytes.fromhex("11223344")

    def test_partial_xin_reads_reverse_lane_without_overwriting_unselected_bytes(self):
        regs = RegisterFile()
        regs.write_full(2, 0x11223344)
        assert BSwapAccelerator(regs, BSWAP_BYTE_ORDER).xin(2, 1) == bytes.fromhex("11")

    def test_assembly_executes_selected_byte_lane(self):
        core = make_core("ldi32 r2, 0x11223344\nxin 160, &r2.b0, 1\nhalt\n")
        core.run(10)
        assert core.registers.read_full(2) == 0x11223311

    def test_rejects_register_30(self):
        regs = RegisterFile()
        with pytest.raises(ValueError, match="R0..R29"):
            BSwapAccelerator(regs, BSWAP_BYTE_ORDER).xin(30, 4)


class TestRangeSwaps:
    def test_4_8_swaps_snapshot_ranges(self):
        regs = RegisterFile()
        for reg in range(2, 10):
            regs.write_full(reg, 0xA0000000 + reg)
        data = BSwapAccelerator(regs, BSWAP_4_8).xin(2, 32)
        assert [int.from_bytes(data[i:i + 4], "little") for i in range(0, 32, 4)] == [
            0xA0000006, 0xA0000007, 0xA0000008, 0xA0000009,
            0xA0000002, 0xA0000003, 0xA0000004, 0xA0000005,
        ]

    def test_4_16_swaps_all_four_lanes(self):
        regs = RegisterFile()
        for reg in range(2, 18):
            regs.write_full(reg, reg)
        data = BSwapAccelerator(regs, BSWAP_4_16).xin(2, 64)
        assert [int.from_bytes(data[i:i + 4], "little") for i in range(0, 64, 4)] == [
            *range(14, 18), *range(10, 14), *range(6, 10), *range(2, 6),
        ]

    @pytest.mark.parametrize("device_id,length", [(BSWAP_4_8, 16), (BSWAP_4_16, 32)])
    def test_range_swap_rejects_truncated_transfer(self, device_id, length):
        with pytest.raises(ValueError, match="requires XIN"):
            BSwapAccelerator(RegisterFile(), device_id).xin(2, length)

    def test_assembly_4_8_updates_both_ranges(self):
        lines = [f"ldi32 r{reg}, 0x100000{reg:02X}" for reg in range(2, 10)]
        lines.extend(["xin 161, &r2, 32", "halt"])
        core = make_core("\n".join(lines))
        core.run(20)
        assert [core.registers.read_full(reg) for reg in range(2, 10)] == [
            0x10000006, 0x10000007, 0x10000008, 0x10000009,
            0x10000002, 0x10000003, 0x10000004, 0x10000005,
        ]
