"""Tests for the memory subsystem: MemoryRegion, MemoryBus, ConstantTable."""

import pytest

from mem.regions import MemoryRegion
from mem.memory_bus import MemoryBus
from mem.constant_table import ConstantTable


# ---------------------------------------------------------------------------
# Helpers – common region factories
# ---------------------------------------------------------------------------

def make_dram(base=0x0000_0000, size=0x1000):
    """DRAM-like: read_latency=2, write_latency=1, jitter=0."""
    return MemoryRegion("DRAM", base, size, read_latency=2, write_latency=1, jitter=0)


def make_ms_ram(base=0x0001_0000, size=0x1000):
    """MS_RAM: read_latency=40, write_latency=1, jitter=0."""
    return MemoryRegion("MS_RAM", base, size, read_latency=40, write_latency=1, jitter=0)


# ===========================================================================
# MemoryRegion – creation & basic read/write
# ===========================================================================

class TestMemoryRegionBasics:
    def test_initial_data_is_zero(self):
        r = make_dram()
        assert r.read(0, 16) == bytes(16)

    def test_write_and_read_back(self):
        r = make_dram()
        r.write(0x100, b"\xDE\xAD\xBE\xEF")
        assert r.read(0x100, 4) == b"\xDE\xAD\xBE\xEF"

    def test_write_partial_does_not_corrupt_neighbours(self):
        r = make_dram()
        r.write(0, b"\xFF" * 8)
        r.write(4, b"\x00\x00\x00\x00")
        assert r.read(0, 4) == b"\xFF\xFF\xFF\xFF"
        assert r.read(4, 4) == b"\x00\x00\x00\x00"

    def test_contains_inside(self):
        r = make_dram(base=0x1000, size=0x100)
        assert r.contains(0x1000) is True
        assert r.contains(0x10FF) is True

    def test_contains_outside(self):
        r = make_dram(base=0x1000, size=0x100)
        assert r.contains(0x0FFF) is False
        assert r.contains(0x1100) is False


# ===========================================================================
# MemoryRegion – stall calculations (DRAM: read=2, write=1, jitter=0)
# ===========================================================================

class TestReadStalls:
    def test_single_word_read(self):
        """4-byte aligned read touching 1 word → 2 + 0 + (1-1) = 2."""
        r = make_dram()
        assert r.calc_read_stalls(0x00, 4) == 2

    def test_two_word_read(self):
        """8-byte read aligned to 0 touches words at 0 and 4 → 2 + (2-1) = 3."""
        r = make_dram()
        assert r.calc_read_stalls(0x00, 8) == 3

    def test_three_word_read(self):
        """12-byte read from 0 touches words 0, 4, 8 → 2 + (3-1) = 4."""
        r = make_dram()
        assert r.calc_read_stalls(0x00, 12) == 4

    def test_boundary_crossing_read(self):
        """2-byte read at 0x03 spans byte offsets 3-4, crossing 0x04 word boundary → 2 words → 2+(2-1)=3."""
        r = make_dram()
        stalls = r.calc_read_stalls(0x03, 2)
        assert stalls == 3

    def test_single_byte_within_one_word(self):
        """1-byte read at 0x01 is entirely within word 0 → 1 word → stalls=2."""
        r = make_dram()
        assert r.calc_read_stalls(0x01, 1) == 2


class TestWriteStalls:
    def test_single_word_write(self):
        """4-byte aligned write touching 1 word → 1 + (1-1) = 1."""
        r = make_dram()
        assert r.calc_write_stalls(0x00, 4) == 1

    def test_two_word_write(self):
        """8-byte write from 0 touches 2 words → 1 + (2-1) = 2."""
        r = make_dram()
        assert r.calc_write_stalls(0x00, 8) == 2

    def test_boundary_crossing_write(self):
        """2-byte write at 0x03 crosses word boundary → 2 words → 1+(2-1)=2."""
        r = make_dram()
        stalls = r.calc_write_stalls(0x03, 2)
        assert stalls == 2


# ===========================================================================
# MemoryRegion – MS_RAM high latency (read=40, write=1)
# ===========================================================================

class TestMSRAMLatency:
    def test_single_word_read_stalls(self):
        """MS_RAM single 4-byte read → 40 + (1-1) = 40."""
        r = make_ms_ram()
        assert r.calc_read_stalls(0x0001_0000, 4) == 40

    def test_two_word_read_stalls(self):
        """MS_RAM 8-byte read → 40 + (2-1) = 41."""
        r = make_ms_ram()
        assert r.calc_read_stalls(0x0001_0000, 8) == 41

    def test_single_word_write_stalls(self):
        """MS_RAM single write → 1 + 0 = 1."""
        r = make_ms_ram()
        assert r.calc_write_stalls(0x0001_0000, 4) == 1


# ===========================================================================
# MemoryRegion – out-of-bounds raises ValueError
# ===========================================================================

class TestOutOfBounds:
    def test_read_below_base(self):
        r = make_dram(base=0x1000, size=0x100)
        with pytest.raises(ValueError):
            r.read(0x0FFF, 1)

    def test_read_above_top(self):
        r = make_dram(base=0x1000, size=0x100)
        with pytest.raises(ValueError):
            r.read(0x1100, 1)

    def test_read_spanning_top(self):
        r = make_dram(base=0x1000, size=0x100)
        with pytest.raises(ValueError):
            r.read(0x10FF, 2)   # 0x10FF + 2 > 0x1100

    def test_write_out_of_bounds(self):
        r = make_dram(base=0x1000, size=0x100)
        with pytest.raises(ValueError):
            r.write(0x1100, b"\x00")

    def test_calc_read_stalls_out_of_bounds(self):
        r = make_dram(base=0x1000, size=0x100)
        with pytest.raises(ValueError):
            r.calc_read_stalls(0x1100, 4)

    def test_calc_write_stalls_out_of_bounds(self):
        r = make_dram(base=0x1000, size=0x100)
        with pytest.raises(ValueError):
            r.calc_write_stalls(0x1100, 4)


# ===========================================================================
# MemoryBus – routing
# ===========================================================================

class TestMemoryBus:
    def _bus_with_two_regions(self):
        bus = MemoryBus()
        bus.add_region(make_dram(base=0x0000_0000, size=0x1000))
        bus.add_region(make_ms_ram(base=0x0001_0000, size=0x1000))
        return bus

    def test_regions_sorted_by_base_addr(self):
        bus = MemoryBus()
        # Add in reverse order
        bus.add_region(make_ms_ram(base=0x0001_0000, size=0x1000))
        bus.add_region(make_dram(base=0x0000_0000, size=0x1000))
        assert bus.regions[0].base_addr < bus.regions[1].base_addr

    def test_read_routes_to_dram(self):
        bus = self._bus_with_two_regions()
        bus.regions[0].write(0x0000_0010, b"\x12\x34")
        data, stalls = bus.read(0x0000_0010, 2)
        assert data == b"\x12\x34"
        assert stalls == 2   # DRAM read_latency=2, 1 word

    def test_read_routes_to_ms_ram(self):
        bus = self._bus_with_two_regions()
        bus.regions[1].write(0x0001_0000, b"\xAB\xCD")
        data, stalls = bus.read(0x0001_0000, 2)
        assert data == b"\xAB\xCD"
        assert stalls == 40  # MS_RAM read_latency=40, 1 word

    def test_write_routes_to_correct_region(self):
        bus = self._bus_with_two_regions()
        stalls = bus.write(0x0000_0000, b"\xFF\xFF\xFF\xFF")
        assert stalls == 1   # DRAM write_latency=1
        data, _ = bus.read(0x0000_0000, 4)
        assert data == b"\xFF\xFF\xFF\xFF"

    def test_write_returns_stall_count(self):
        bus = self._bus_with_two_regions()
        stalls = bus.write(0x0001_0000, b"\x00" * 8)
        assert stalls == 2   # MS_RAM write_latency=1, 2 words → 1+(2-1)=2

    def test_unmapped_read_raises(self):
        bus = self._bus_with_two_regions()
        with pytest.raises(ValueError):
            bus.read(0xDEAD_0000, 4)

    def test_unmapped_write_raises(self):
        bus = self._bus_with_two_regions()
        with pytest.raises(ValueError):
            bus.write(0xDEAD_0000, b"\x00")


# ===========================================================================
# ConstantTable
# ===========================================================================

class TestConstantTable:
    def test_initial_entries_are_zero(self):
        ct = ConstantTable()
        for i in range(32):
            assert ct.resolve(i) == 0

    def test_set_and_resolve(self):
        ct = ConstantTable()
        ct.set(0, 0x0000_8000)
        assert ct.resolve(0) == 0x0000_8000

    def test_set_and_resolve_last_entry(self):
        ct = ConstantTable()
        ct.set(31, 0xFFFF_FFFF)
        assert ct.resolve(31) == 0xFFFF_FFFF

    def test_set_invalid_index_low(self):
        ct = ConstantTable()
        with pytest.raises(ValueError):
            ct.set(-1, 0)

    def test_set_invalid_index_high(self):
        ct = ConstantTable()
        with pytest.raises(ValueError):
            ct.set(32, 0)

    def test_resolve_invalid_index(self):
        ct = ConstantTable()
        with pytest.raises(ValueError):
            ct.resolve(32)

    def test_load_from_dict(self):
        ct = ConstantTable()
        ct.load_from_dict({0: 0xAAAA, 15: 0xBBBB, 31: 0xCCCC})
        assert ct.resolve(0) == 0xAAAA
        assert ct.resolve(15) == 0xBBBB
        assert ct.resolve(31) == 0xCCCC
        # Unset entries remain zero
        assert ct.resolve(1) == 0

    def test_load_from_dict_invalid_key_raises(self):
        ct = ConstantTable()
        with pytest.raises(ValueError):
            ct.load_from_dict({32: 0x1234})
