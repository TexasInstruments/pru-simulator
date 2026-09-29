# pru_io/ring_recorder.py
"""Record a firmware's output ring from simulator memory.

Firmware that publishes samples into a ring of int32 words plus a running
count word (sample k at ring_addr + 4 * (k mod ring_len), count = samples
written) can be recorded for longer than the ring holds: poll() after every
chunk of simulation copies the entries written since the previous poll.
"""

import struct
from typing import Callable

MemoryRead = Callable[[int, int], bytes]


class RingOverrun(RuntimeError):
    """Samples were lost: the firmware wrote more than a ring between polls."""


class RingRecorder:
    """Copies new ring entries; stamps each poll with a caller-supplied time."""

    def __init__(self, count_addr: int, ring_addr: int, ring_len: int):
        if ring_len < 1:
            raise ValueError(f"ring_len must be >= 1, got {ring_len}")
        self.count_addr = count_addr
        self.ring_addr = ring_addr
        self.ring_len = ring_len
        self.samples: list[int] = []
        self.stamps: list[tuple[int, int]] = []   # (samples recorded, time stamp)
        self._last: int | None = None

    def _count(self, read: MemoryRead) -> int:
        return struct.unpack("<I", read(self.count_addr, 4))[0]

    def start(self, read: MemoryRead) -> None:
        """Begin recording at the firmware's current count (older samples are ignored)."""
        self.samples, self.stamps = [], []
        self._last = self._count(read)

    def poll(self, read: MemoryRead, time_stamp: int) -> int:
        """Copy samples written since the last poll; return how many."""
        if self._last is None:
            raise RuntimeError("call start() before poll()")
        count = self._count(read)
        new = count - self._last
        if new < 0:
            raise RingOverrun(f"count went backwards ({self._last} -> {count}): firmware restarted?")
        if new > self.ring_len:
            raise RingOverrun(f"{new} new samples since the last poll but the ring holds "
                              f"{self.ring_len}: poll more often (smaller chunks)")
        for k in range(self._last, count):
            addr = self.ring_addr + 4 * (k % self.ring_len)
            self.samples.append(struct.unpack("<i", read(addr, 4))[0])
        self._last = count
        if new:
            self.stamps.append((len(self.samples), time_stamp))
        return new

    def sample_times(self, bit_rate: float) -> list[float]:
        """Time of each recorded sample in seconds.

        A stamp (n, b) means sample n - 1 existed at time stamp b. A least-squares
        line through (n - 1, b) gives b = a + s * i for sample i; the constant lag
        between a sample and the next poll is left in and absorbed by the
        analysis delay.
        """
        if len(self.stamps) < 2:
            raise ValueError("need at least two stamps (polls with new samples) to time the samples")
        xs = [n - 1 for n, _ in self.stamps]
        ys = [b for _, b in self.stamps]
        m = len(xs)
        mx, my = sum(xs) / m, sum(ys) / m
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx == 0:
            raise ValueError("stamps do not span more than one sample")
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        icpt = my - slope * mx
        return [(icpt + slope * i) / bit_rate for i in range(len(self.samples))]
