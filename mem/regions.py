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
        misaligned_write_penalty: int = 0,
        per_word_latency: int = 1,
    ):
        self.name = name
        self.base_addr = base_addr
        self.size = size
        self.read_latency = read_latency
        self.write_latency = write_latency
        self.jitter = jitter
        # Extra cycles for a write that starts off a 4-byte boundary AND spans
        # two or more words. ICSS-local RAM (DRAM0/1, Shared RAM) charges 1 on
        # silicon (LP-AM261 ICSSM and LP-AM243 ICSSG); MSRAM charges 0.
        self.misaligned_write_penalty = misaligned_write_penalty
        # Cycles charged for each 32-bit word after the first of a burst read.
        # 1 is the original model; LP-AM261 MSRAM measures 10 on silicon.
        self.per_word_latency = per_word_latency
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
        return self.read_latency + random.randint(0, self.jitter) + (words - 1) * self.per_word_latency

    def calc_write_stalls(self, addr: int, length: int) -> int:
        """Return the number of stall cycles for a write of *length* bytes at *addr*."""
        self._check_bounds(addr, length)
        words = self._word_count(addr, length)
        stalls = self.write_latency + (words - 1)
        if addr & 3 and words >= 2:
            stalls += self.misaligned_write_penalty
        return stalls
