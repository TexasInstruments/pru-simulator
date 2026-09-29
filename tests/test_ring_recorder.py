# tests/test_ring_recorder.py
"""RingRecorder: copies new entries of a firmware's memory ring as they appear."""
import struct

import pytest
from pru_io.ring_recorder import RingOverrun, RingRecorder

COUNT, RING, LEN = 0x04, 0x10, 8


class FakeRing:
    """A firmware ring in a byte array: write(k, v) stores sample k and bumps the count."""

    def __init__(self):
        self.mem = bytearray(0x100)
        self.count = 0

    def write(self, value):
        struct.pack_into("<i", self.mem, RING + 4 * (self.count % LEN), value)
        self.count += 1
        struct.pack_into("<I", self.mem, COUNT, self.count)

    def read(self, addr, length):
        return bytes(self.mem[addr:addr + length])


def test_records_new_samples_across_the_wrap_with_stamps():
    ring, rec = FakeRing(), RingRecorder(COUNT, RING, LEN)
    for v in range(3):
        ring.write(-v)                        # before start: not recorded
    rec.start(ring.read)
    for v in range(5):
        ring.write(100 + v)
    assert rec.poll(ring.read, 1000) == 5
    assert rec.poll(ring.read, 1100) == 0     # nothing new: no stamp
    for v in range(7):                        # wraps the 8-entry ring
        ring.write(200 + v)
    assert rec.poll(ring.read, 2000) == 7
    assert rec.samples == [100, 101, 102, 103, 104] + list(range(200, 207))
    assert rec.stamps == [(5, 1000), (12, 2000)]


def test_more_than_a_ring_between_polls_is_an_overrun():
    ring, rec = FakeRing(), RingRecorder(COUNT, RING, LEN)
    rec.start(ring.read)
    for v in range(LEN + 1):
        ring.write(v)
    with pytest.raises(RingOverrun, match="9 new samples"):
        rec.poll(ring.read, 0)


def test_count_going_backwards_is_an_overrun():
    ring, rec = FakeRing(), RingRecorder(COUNT, RING, LEN)
    for v in range(4):
        ring.write(v)
    rec.start(ring.read)
    ring.count = 1
    ring.write(0)
    with pytest.raises(RingOverrun, match="went backwards"):
        rec.poll(ring.read, 0)


def test_sample_times_fit_a_line_through_the_stamps():
    rec = RingRecorder(COUNT, RING, LEN)
    rec.samples = list(range(40))
    # Sample i is produced at bit 192 * i + 500; polls lag by a few bits.
    rec.stamps = [(10, 192 * 9 + 500 + 30), (20, 192 * 19 + 500 + 10),
                  (30, 192 * 29 + 500 + 50), (40, 192 * 39 + 500 + 30)]
    t = rec.sample_times(bit_rate=3.072e6)
    assert len(t) == 40
    step = (t[-1] - t[0]) / 39
    assert step == pytest.approx(192 / 3.072e6, rel=1e-2)    # lag jitter / span
    assert t[0] == pytest.approx((500 + 30) / 3.072e6, abs=30 / 3.072e6)


def test_sample_times_need_two_stamps():
    rec = RingRecorder(COUNT, RING, LEN)
    rec.samples, rec.stamps = [1, 2], [(2, 100)]
    with pytest.raises(ValueError, match="at least two"):
        rec.sample_times(bit_rate=1e6)
