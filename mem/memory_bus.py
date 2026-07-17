"""Memory bus that routes read/write requests to the correct MemoryRegion."""

from mem.regions import MemoryRegion


class MemoryBus:
    """Routes memory accesses to the appropriate MemoryRegion and returns stall counts."""

    def __init__(self):
        self.regions: list[MemoryRegion] = []

    def add_region(self, region: MemoryRegion) -> None:
        """Add a region and keep the list sorted by base_addr."""
        self.regions.append(region)
        self.regions.sort(key=lambda r: r.base_addr)

    def _find_region(self, addr: int) -> MemoryRegion:
        """Return the region that contains *addr*, or raise ValueError."""
        for region in self.regions:
            if region.contains(addr):
                return region
        raise ValueError(f"No memory region mapped at address 0x{addr:08X}")

    def read(self, addr: int, length: int) -> tuple[bytes, int]:
        """Read *length* bytes from *addr*.

        Returns:
            (data, stall_cycles)
        """
        region = self._find_region(addr)
        data = region.read(addr, length)
        stalls = region.calc_read_stalls(addr, length)
        return data, stalls

    def write(self, addr: int, data: bytes) -> int:
        """Write *data* to *addr*.

        Returns:
            stall_cycles
        """
        region = self._find_region(addr)
        region.write(addr, data)
        return region.calc_write_stalls(addr, len(data))
