# pru_io/pdm_link.py
"""PdmLink: one core's GPO pins drive another core's SD clock and data inputs.

The producer core (PRU1 in the PDM demo) toggles a clock pin and data pins on
its R30. Each change is queued with a time stamp. The consumer core's SD
filter (PRU0) applies the queued edges in its own time, just before each of
its instructions executes. This is event driven, so a consumer that
fast-forwards later on misses no edge.

Timing convention:

* An edge caused by a producer R30 write in an instruction that starts at
  producer cycle ``c`` is stamped ``time_of(producer, c + 1)``: the output is
  valid when the instruction retires. ``c`` is ``PRUCore.step_start_cycle``.
* A producer reset drives its GPO low at the reset instant
  (``time_of(producer, 0)`` on the rebased timeline). That is a real edge:
  a falling-edge sampler samples it, with the reset data level 0.
* A consumer instruction that starts at consumer cycle ``d`` sees exactly the
  edges stamped ``<= time_of(consumer, d)``.
* The consumer must not get ahead of what the producer has already decided.
  ``advance_consumer`` raises :class:`PdmLinkError` when
  ``time_of(consumer, d) > horizon()``.
"""

from __future__ import annotations

import math
from collections import deque
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from core.pru_core import PRUCore


class PdmLinkError(RuntimeError):
    """The consumer tried to run past the producer's decided edges."""


class PdmLink:
    """Wire producer GPO pins to the consumer's SD clock net and data inputs."""

    def __init__(self, producer_core: PRUCore, consumer_core: PRUCore,
                 time_of: Callable[[str, int], int], clock_pin: int,
                 data_pins: dict[int, int], clock_net: int = 0,
                 keep_history: bool = False):
        self.producer = producer_core
        self.consumer = consumer_core
        self.time_of = time_of
        self.clock_pin = clock_pin
        self.data_pins = dict(data_pins)   # SD channel -> producer GPO pin
        self.clock_net = clock_net
        self.keep_history = keep_history
        self._producer_name = producer_core.name.lower()
        self._consumer_name = consumer_core.name.lower()
        self._clock_mask = 1 << clock_pin
        self._watch_mask = self._clock_mask
        for pin in self.data_pins.values():
            self._watch_mask |= 1 << pin
        self._queue: deque[tuple[int, int]] = deque()
        # Producer GPO word as the consumer has seen it so far.
        self._last_word = producer_core.io_port.gpo
        self.edge_history: list[tuple[int, int]] = []
        self.sampled_bits: dict[int, list[int]] = {ch: [] for ch in self.data_pins}

    # ------------------------------------------------------------------
    # Producer side
    # ------------------------------------------------------------------

    def on_producer_gpo(self, old: int, new: int) -> None:
        """IOPort GPO listener: queue a change on any watched pin.

        Stamped ``time_of(producer, c + 1)`` with ``c`` the producer's
        ``step_start_cycle``: the writing instruction's start cycle, whatever
        stalls it counts. A producer reset (``c = -1``) stamps its pin drop at
        the reset instant.
        """
        if not (old ^ new) & self._watch_mask:
            return
        stamp = self.time_of(self._producer_name, self.producer.step_start_cycle + 1)
        self._queue.append((stamp, new))
        if self.keep_history:
            self.edge_history.append((stamp, new))

    def horizon(self) -> int | float:
        """Latest time up to which the producer's edges are all queued."""
        p = self.producer
        if p.halted or p.pc >= len(p.instructions):
            return math.inf
        return self.time_of(self._producer_name, p.counters.cycles)

    # ------------------------------------------------------------------
    # Consumer side
    # ------------------------------------------------------------------

    def advance_consumer(self, consumer_cycles: int) -> None:
        """Apply every queued edge stamped at or before the consumer's time."""
        now = self.time_of(self._consumer_name, consumer_cycles)
        if now > self.horizon():
            raise PdmLinkError(
                f"PDM link: consumer {self._consumer_name} is ahead of "
                f"producer {self._producer_name}")
        queue = self._queue
        sd = self.consumer.io_port.sd_filter
        while queue and queue[0][0] <= now:
            _, word = queue.popleft()
            changed = word ^ self._last_word
            self._last_word = word
            if changed & self._clock_mask:
                rising = bool(word & self._clock_mask)
                sd.clock_edge(self.clock_net, rising,
                              lambda ch, _w=word: self._data_level(ch, _w))

    def _data_level(self, ch: int, word: int) -> int:
        pin = self.data_pins.get(ch)
        bit = 0 if pin is None else (word >> pin) & 1
        if self.keep_history and pin is not None:
            self.sampled_bits[ch].append(bit)
        return bit

    def next_valid_time(self, ch: int) -> int | None:
        """Stamp of the queued edge that would set ``channels[ch].valid``.

        Walks the queue without applying anything. None if the channel does
        not sample this link's clock or the queue holds too few edges.
        """
        sd = self.consumer.io_port.sd_filter
        edge = sd.sampling_edge(ch, self.clock_net)
        if edge is None:
            return None
        channel = sd.channels[ch]
        remaining = channel.osr - channel._sample_count
        last = self._last_word
        for stamp, word in self._queue:
            changed = word ^ last
            last = word
            if changed & self._clock_mask and bool(word & self._clock_mask) == edge:
                remaining -= 1
                if remaining <= 0:
                    return stamp
        return None

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a copy of all mutable state for step-back."""
        return {
            "queue": list(self._queue),
            "last_word": self._last_word,
            "edge_history": list(self.edge_history),
            "sampled_bits": {ch: list(bits) for ch, bits in self.sampled_bits.items()},
        }

    def restore(self, snap: dict) -> None:
        """Restore all mutable state from a snapshot."""
        self._queue = deque(snap["queue"])
        self._last_word = snap["last_word"]
        self.edge_history = list(snap["edge_history"])
        self.sampled_bits = {ch: list(bits) for ch, bits in snap["sampled_bits"].items()}

    def reset(self) -> None:
        """Drop queued edges and history; resync to the producer's GPO now."""
        self._queue.clear()
        self._last_word = self.producer.io_port.gpo
        self.edge_history.clear()
        self.sampled_bits = {ch: [] for ch in self.data_pins}
