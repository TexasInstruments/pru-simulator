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
        line_bytes: int = 0,
        line_cross_fit: int = 0,
        line_readers: tuple[str, ...] = (),
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
        if line_bytes < 0 or line_bytes % 4:
            raise ValueError("line_bytes must be zero or a positive multiple of 4")
        if line_cross_fit < 0:
            raise ValueError("line_cross_fit must be nonnegative")
        self.line_bytes = line_bytes
        # Constant of an empirical fit, not an additive extra: the stalls added
        # to a line-crossing read are line_cross_fit - W1//2, where W1 is the
        # number of 32-bit words read from the first line, clamped at zero. The
        # value 13 reproduces LP-AM243 MSRAM for line_bytes = 64 and
        # per_word_latency = 1 only.
        self.line_cross_fit = line_cross_fit
        # Empty means all readers; configured names (stored lower case, matched
        # case-insensitively) limit the empirical model to measured cores.
        # Requests without a core name then keep base timing.
        self.line_readers = tuple(name.lower() for name in line_readers)
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

    def deterministic_read_stalls(self, addr: int, length: int, *, requester: str | None = None) -> int:
        """Return the jitter-free stall cycles for a read of *length* bytes at *addr*."""
        self._check_bounds(addr, length)
        words = self._word_count(addr, length)
        stalls = self.read_latency + (words - 1) * self.per_word_latency
        if (self.line_bytes and self.line_cross_fit and 0 < length <= self.line_bytes
                and (not self.line_readers
                     or (requester is not None and requester.lower() in self.line_readers))):
            boundary = (addr // self.line_bytes + 1) * self.line_bytes
            if addr + length > boundary:
                first_words = self._word_count(addr, boundary - addr)
                # LP-AM243 PRU0, issue #69: with per_word_latency=1 this is
                # read_latency + 14 + (W2-1) + (W1-1)//2. Only bursts up to
                # one line in length were measured; do not extrapolate further.
                stalls += max(0, self.line_cross_fit - first_words // 2)
        return stalls

    def calc_read_stalls(self, addr: int, length: int, *, requester: str | None = None) -> int:
        """Return the number of stall cycles for a read of *length* bytes at *addr*."""
        stalls = self.deterministic_read_stalls(addr, length, requester=requester)
        return stalls + random.randint(0, self.jitter)

    def calc_write_stalls(self, addr: int, length: int) -> int:
        """Return the number of stall cycles for a write of *length* bytes at *addr*."""
        self._check_bounds(addr, length)
        words = self._word_count(addr, length)
        stalls = self.write_latency + (words - 1)
        if addr & 3 and words >= 2:
            stalls += self.misaligned_write_penalty
        return stalls
