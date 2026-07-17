"""Memory region model with latency-aware stall counting."""

import random


class MemoryRegion:
    """A named memory region with configurable read/write latency and jitter."""

    def __init__(
        self,
        name: str,
        base_addr: int,
        size: int,
        read_latency: int,
        write_latency: int,
        jitter: int = 0,
    ):
        self.name = name
        self.base_addr = base_addr
        self.size = size
        self.read_latency = read_latency
        self.write_latency = write_latency
        self.jitter = jitter
        self._data = bytearray(size)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_bounds(self, addr: int, length: int) -> None:
        """Raise ValueError if [addr, addr+length) is not fully inside this region."""
        if addr < self.base_addr or (addr + length) > (self.base_addr + self.size):
            raise ValueError(
                f"Address range 0x{addr:08X}+{length} is out of bounds for "
                f"region '{self.name}' "
                f"[0x{self.base_addr:08X}, 0x{self.base_addr + self.size:08X})"
            )

    def _word_count(self, addr: int, length: int) -> int:
        """Return the number of 32-bit words touched by [addr, addr+length)."""
        first_word = (addr >> 2) << 2          # align down to 4-byte boundary
        last_addr = addr + length - 1
        last_word = (last_addr >> 2) << 2      # align down to 4-byte boundary
        return ((last_word - first_word) >> 2) + 1

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def contains(self, addr: int) -> bool:
        """Return True if *addr* falls within this region."""
        return self.base_addr <= addr < (self.base_addr + self.size)

    def read(self, addr: int, length: int) -> bytes:
        """Read *length* bytes starting at absolute address *addr*."""
        self._check_bounds(addr, length)
        local = addr - self.base_addr
        return bytes(self._data[local : local + length])

    def write(self, addr: int, data: bytes) -> None:
        """Write *data* bytes starting at absolute address *addr*."""
        self._check_bounds(addr, len(data))
        local = addr - self.base_addr
        self._data[local : local + len(data)] = data

    def calc_read_stalls(self, addr: int, length: int) -> int:
        """Return the number of stall cycles for a read of *length* bytes at *addr*."""
        self._check_bounds(addr, length)
        words = self._word_count(addr, length)
        return self.read_latency + random.randint(0, self.jitter) + (words - 1)

    def calc_write_stalls(self, addr: int, length: int) -> int:
        """Return the number of stall cycles for a write of *length* bytes at *addr*."""
        self._check_bounds(addr, length)
        words = self._word_count(addr, length)
        return self.write_latency + (words - 1)
