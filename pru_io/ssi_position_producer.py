"""Deterministic simulator-side ARM position producer.

The real ARM producer publishes timestamped, continuous encoder positions into
the SSI shared-memory sample ring.  This component models that ownership
boundary without touching PRU firmware: it writes each sample with the ABI
seqlock ordering, then publishes the constant-time producer head.  Static
prepacked SSI sequences remain the default and never register an IEP callback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pru_io import ssi_config_abi as abi
from pru_io.ssi_position_estimator import (
    I64_MAX,
    I64_MIN,
    ProducerHead,
    ProducerSample,
    PositionOverflowError,
    U64_HALF,
    U64_MASK,
    _bounded_int,
    read_coherent_head,
    read_coherent_sample,
)


Q_FRACTION_BITS = abi.PRODUCER_SAMPLE_Q_FRACTION_BITS
Q_ONE = 1 << Q_FRACTION_BITS


def _u64(value: int, name: str) -> int:
    return _bounded_int(value, 0, U64_MASK, name)


def _u32(value: int, name: str) -> int:
    return _bounded_int(value, 0, 0xFFFF_FFFF, name)


def _i64(value: int, name: str) -> int:
    return _bounded_int(value, I64_MIN, I64_MAX, name)


def _forward_delta(newer: int, older: int) -> int:
    delta = (_u64(newer, "timestamp_iep") - _u64(older, "timestamp_iep")) & U64_MASK
    if delta >= U64_HALF:
        raise ValueError("timestamp order is backward or ambiguous")
    return delta


def _due(now: int, scheduled: int) -> bool:
    delta = (now - scheduled) & U64_MASK
    return delta < U64_HALF


@dataclass(frozen=True)
class ProducerConfiguration:
    mode: int
    period_iep_ticks: int
    sample_age_limit_iep_ticks: int
    prediction_horizon_limit_iep_ticks: int
    trajectory: str
    initial_position_q31_32: int
    velocity_q31_32_per_iep_tick: int
    triangle_low_q31_32: int
    triangle_high_q31_32: int
    quantize_generated_positions: bool


@dataclass(frozen=True)
class ProducerErrorState:
    error_type: str
    message: str
    timestamp_iep: int


class SSIPositionProducer:
    """Publish deterministic ARM-side positions into the SSI sample ring.

    ``position_q31_32`` values are signed fixed-point counts.  A linear
    velocity is expressed in Q31.32 counts per IEP tick.  Triangle motion uses
    the absolute value of that velocity as its speed and reflects between the
    configured low/high bounds.  All timestamps and arithmetic are integers.
    """

    def __init__(
        self,
        sim,
        *,
        mode: int = abi.SSI_PRODUCER_MODE_STATIC_SEQUENCE,
        period_iep_ticks: int = abi.DEFAULT_PRODUCER_PERIOD_IEP_TICKS,
        sample_age_limit_iep_ticks: int = (
            abi.DEFAULT_PRODUCER_SAMPLE_AGE_LIMIT_IEP_TICKS
        ),
        prediction_horizon_limit_iep_ticks: int = (
            abi.DEFAULT_PRODUCER_PREDICTION_HORIZON_LIMIT_IEP_TICKS
        ),
        generation: int = 1,
        trajectory: str = "constant",
        initial_position_q31_32: int = 0,
        velocity_q31_32_per_iep_tick: int = 0,
        triangle_low_q31_32: int = 0,
        triangle_high_q31_32: int = Q_ONE,
        quantize_generated_positions: bool = False,
        write_observer: Callable[[tuple[int, bytes]], None] | None = None,
    ):
        self.sim = sim
        self._write_observer = write_observer
        self._running = False
        self._observer_registered = False
        self._next_due_timestamp: int | None = None
        self._trajectory_origin_timestamp: int | None = None
        self._published_count = 0
        self._last_catch_up_skipped = 0
        self._skipped_overwritten_count = 0
        self._next_sample_seq = 2
        self._next_head_seq = 2
        self._error_state: ProducerErrorState | None = None
        self._generation = 1
        self._mode = abi.SSI_PRODUCER_MODE_STATIC_SEQUENCE
        self._period_iep_ticks = abi.DEFAULT_PRODUCER_PERIOD_IEP_TICKS
        self._sample_age_limit_iep_ticks = (
            abi.DEFAULT_PRODUCER_SAMPLE_AGE_LIMIT_IEP_TICKS
        )
        self._prediction_horizon_limit_iep_ticks = (
            abi.DEFAULT_PRODUCER_PREDICTION_HORIZON_LIMIT_IEP_TICKS
        )
        self._trajectory = "constant"
        self._initial_position_q31_32 = 0
        self._velocity_q31_32_per_iep_tick = 0
        self._triangle_low_q31_32 = 0
        self._triangle_high_q31_32 = Q_ONE
        self._quantize_generated_positions = False
        self.configure(
            mode=mode,
            period_iep_ticks=period_iep_ticks,
            sample_age_limit_iep_ticks=sample_age_limit_iep_ticks,
            prediction_horizon_limit_iep_ticks=prediction_horizon_limit_iep_ticks,
            generation=generation,
            trajectory=trajectory,
            initial_position_q31_32=initial_position_q31_32,
            velocity_q31_32_per_iep_tick=velocity_q31_32_per_iep_tick,
            triangle_low_q31_32=triangle_low_q31_32,
            triangle_high_q31_32=triangle_high_q31_32,
            quantize_generated_positions=quantize_generated_positions,
        )
        add_reset_hook = getattr(self.sim, "add_hard_reset_hook", None)
        if add_reset_hook is not None:
            add_reset_hook(self.reset)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def mode(self) -> int:
        return self._mode

    @property
    def period_iep_ticks(self) -> int:
        return self._period_iep_ticks

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def published_count(self) -> int:
        return self._published_count

    @property
    def current_count(self) -> int:
        """Unbounded host-side publication count (the ring index is modulo 256)."""
        return self._published_count

    @property
    def last_catch_up_skipped(self) -> int:
        return self._last_catch_up_skipped

    @property
    def skipped_overwritten_count(self) -> int:
        return self._skipped_overwritten_count

    @property
    def error_state(self) -> ProducerErrorState | None:
        return self._error_state

    def configuration(self) -> ProducerConfiguration:
        return ProducerConfiguration(
            mode=self._mode,
            period_iep_ticks=self._period_iep_ticks,
            sample_age_limit_iep_ticks=self._sample_age_limit_iep_ticks,
            prediction_horizon_limit_iep_ticks=self._prediction_horizon_limit_iep_ticks,
            trajectory=self._trajectory,
            initial_position_q31_32=self._initial_position_q31_32,
            velocity_q31_32_per_iep_tick=self._velocity_q31_32_per_iep_tick,
            triangle_low_q31_32=self._triangle_low_q31_32,
            triangle_high_q31_32=self._triangle_high_q31_32,
            quantize_generated_positions=self._quantize_generated_positions,
        )

    def configure(
        self,
        *,
        mode: int | None = None,
        period_iep_ticks: int | None = None,
        sample_age_limit_iep_ticks: int | None = None,
        prediction_horizon_limit_iep_ticks: int | None = None,
        generation: int | None = None,
        trajectory: str | None = None,
        initial_position_q31_32: int | None = None,
        velocity_q31_32_per_iep_tick: int | None = None,
        triangle_low_q31_32: int | None = None,
        triangle_high_q31_32: int | None = None,
        quantize_generated_positions: bool | None = None,
    ) -> ProducerConfiguration:
        """Validate and install a producer configuration.

        Reconfiguring a running timestamped producer restarts its schedule at
        the current IEP timestamp, producing a clean deterministic boundary.
        Existing ring entries are retained so a consumer can reject mixed
        generations until two samples from the new generation exist.
        Shape or cadence changes increment the generation modulo ``2**32``
        unless the caller supplies an explicit generation.
        """
        candidate_mode = self._mode if mode is None else mode
        if candidate_mode not in (
            abi.SSI_PRODUCER_MODE_STATIC_SEQUENCE,
            abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        ):
            raise ValueError(f"unknown producer mode: {candidate_mode}")

        candidate_period = (
            self._period_iep_ticks if period_iep_ticks is None else period_iep_ticks
        )
        _bounded_int(candidate_period, 1, 0xFFFF_FFFF, "period_iep_ticks")

        candidate_age = (
            self._sample_age_limit_iep_ticks
            if sample_age_limit_iep_ticks is None
            else sample_age_limit_iep_ticks
        )
        candidate_horizon = (
            self._prediction_horizon_limit_iep_ticks
            if prediction_horizon_limit_iep_ticks is None
            else prediction_horizon_limit_iep_ticks
        )
        _bounded_int(candidate_age, 0, 0xFFFF_FFFF, "sample_age_limit_iep_ticks")
        _bounded_int(
            candidate_horizon,
            0,
            0xFFFF_FFFF,
            "prediction_horizon_limit_iep_ticks",
        )

        candidate_trajectory = self._trajectory if trajectory is None else trajectory
        if candidate_trajectory == "ramp":
            candidate_trajectory = "linear"
        if candidate_trajectory not in ("constant", "linear", "triangle"):
            raise ValueError("trajectory must be constant, linear/ramp, or triangle")

        candidate_initial = (
            self._initial_position_q31_32
            if initial_position_q31_32 is None
            else initial_position_q31_32
        )
        candidate_velocity = (
            self._velocity_q31_32_per_iep_tick
            if velocity_q31_32_per_iep_tick is None
            else velocity_q31_32_per_iep_tick
        )
        candidate_low = (
            self._triangle_low_q31_32
            if triangle_low_q31_32 is None
            else triangle_low_q31_32
        )
        candidate_high = (
            self._triangle_high_q31_32
            if triangle_high_q31_32 is None
            else triangle_high_q31_32
        )
        candidate_quantize = (
            self._quantize_generated_positions
            if quantize_generated_positions is None
            else quantize_generated_positions
        )
        if not isinstance(candidate_quantize, bool):
            raise ValueError("quantize_generated_positions must be boolean")
        _i64(candidate_initial, "initial_position_q31_32")
        _i64(candidate_velocity, "velocity_q31_32_per_iep_tick")
        _i64(candidate_low, "triangle_low_q31_32")
        _i64(candidate_high, "triangle_high_q31_32")
        if candidate_trajectory == "triangle":
            if candidate_low >= candidate_high:
                raise ValueError("triangle_low_q31_32 must be below triangle_high_q31_32")
            if candidate_initial < candidate_low or candidate_initial > candidate_high:
                raise ValueError("triangle initial position must lie within its bounds")
            if candidate_velocity == 0:
                raise ValueError("triangle velocity must be non-zero")

        shape_or_cadence_changed = (
            candidate_mode != self._mode
            or candidate_period != self._period_iep_ticks
            or candidate_trajectory != self._trajectory
            or candidate_initial != self._initial_position_q31_32
            or candidate_velocity != self._velocity_q31_32_per_iep_tick
            or candidate_low != self._triangle_low_q31_32
            or candidate_high != self._triangle_high_q31_32
            or candidate_quantize != self._quantize_generated_positions
        )
        if generation is None:
            candidate_generation = self._generation
            if shape_or_cadence_changed:
                candidate_generation = (candidate_generation + 1) & 0xFFFF_FFFF
        else:
            candidate_generation = generation
        _u32(candidate_generation, "generation")

        was_running = self._running
        if was_running:
            self.stop()

        self._mode = candidate_mode
        self._period_iep_ticks = candidate_period
        self._sample_age_limit_iep_ticks = candidate_age
        self._prediction_horizon_limit_iep_ticks = candidate_horizon
        self._generation = candidate_generation
        self._trajectory = candidate_trajectory
        self._initial_position_q31_32 = candidate_initial
        self._velocity_q31_32_per_iep_tick = candidate_velocity
        self._triangle_low_q31_32 = candidate_low
        self._triangle_high_q31_32 = candidate_high
        self._quantize_generated_positions = candidate_quantize
        self._trajectory_origin_timestamp = None
        self._next_due_timestamp = None

        if was_running and self._mode == abi.SSI_PRODUCER_MODE_TIMESTAMPED:
            self.start()
        return self.configuration()

    def set_generation(self, generation: int) -> None:
        """Use *generation* for future samples without rewriting old entries."""
        self._generation = _u32(generation, "generation")

    def _iep(self):
        iep = getattr(self.sim, "iep", None)
        if iep is None:
            raise RuntimeError("timestamped auto publication requires an AM243x IEP")
        return iep

    def _now(self) -> int:
        return _u64(self._iep().count, "timestamp_iep")

    def start(self) -> bool:
        """Start timestamped auto-publication, including one sample immediately."""
        if self._mode != abi.SSI_PRODUCER_MODE_TIMESTAMPED:
            self._running = False
            return False
        if self._running:
            return True
        now = self._now()
        self._running = True
        self._trajectory_origin_timestamp = now
        try:
            self.publish_at(now)
        except (ArithmeticError, ValueError) as error:
            self._stop_with_error(error, now)
            return False
        self._next_due_timestamp = (now + self._period_iep_ticks) & U64_MASK
        self._iep().add_counter_observer(self._on_iep_advanced)
        self._observer_registered = True
        self._error_state = None
        return True

    def stop(self) -> None:
        """Stop auto-publication; manual ``publish_at`` remains available."""
        if self._observer_registered:
            self._iep().remove_counter_observer(self._on_iep_advanced)
            self._observer_registered = False
        self._running = False
        self._next_due_timestamp = None

    def step(self, timestamp_iep: int | None = None) -> ProducerSample:
        """Publish one deterministic sample at an explicit/current timestamp."""
        timestamp = self._now() if timestamp_iep is None else _u64(timestamp_iep, "timestamp_iep")
        sample = self.publish_at(timestamp)
        if self._running:
            self._next_due_timestamp = (timestamp + self._period_iep_ticks) & U64_MASK
        return sample

    def advance_to(self, timestamp_iep: int) -> int:
        """Publish all due cadence points through *timestamp_iep*.

        This is the same bounded callback path used by the IEP observer and is
        also useful for deterministic chunk/catch-up tests.
        """
        if not self._running:
            raise RuntimeError("producer is not running")
        now = _u64(timestamp_iep, "timestamp_iep")
        if self._next_due_timestamp is None:
            self._next_due_timestamp = now
        if not _due(now, self._next_due_timestamp):
            self._last_catch_up_skipped = 0
            return 0
        delta = (now - self._next_due_timestamp) & U64_MASK
        due_count = delta // self._period_iep_ticks + 1
        skipped = max(0, due_count - abi.PRODUCER_SAMPLE_COUNT)
        self._last_catch_up_skipped = skipped
        if skipped:
            self._published_count += skipped
            self._next_sample_seq = (
                self._next_sample_seq + 2 * skipped
            ) & U64_MASK
            self._next_head_seq = (
                self._next_head_seq + 2 * skipped
            ) & 0xFFFF_FFFF
            self._next_due_timestamp = (
                self._next_due_timestamp + skipped * self._period_iep_ticks
            ) & U64_MASK
            self._skipped_overwritten_count += skipped
        for _ in range(min(due_count, abi.PRODUCER_SAMPLE_COUNT)):
            scheduled = self._next_due_timestamp
            try:
                self.publish_at(scheduled)
            except (ArithmeticError, ValueError) as error:
                self._stop_with_error(error, scheduled)
                return due_count
            self._next_due_timestamp = (
                scheduled + self._period_iep_ticks
            ) & U64_MASK
        return due_count

    def _on_iep_advanced(self, timestamp_iep: int) -> None:
        if self._running:
            self.advance_to(timestamp_iep)

    def _stop_with_error(self, error: Exception, timestamp_iep: int) -> None:
        self._error_state = ProducerErrorState(
            error_type=type(error).__name__,
            message=str(error),
            timestamp_iep=timestamp_iep,
        )
        self.stop()

    def _position_at(self, timestamp_iep: int) -> int:
        if self._trajectory_origin_timestamp is None:
            self._trajectory_origin_timestamp = timestamp_iep
        elapsed = _forward_delta(timestamp_iep, self._trajectory_origin_timestamp)

        if self._trajectory == "constant":
            position = self._initial_position_q31_32
            return self._quantize_position(position)
        if self._trajectory == "linear":
            position = self._initial_position_q31_32 + (
                elapsed * self._velocity_q31_32_per_iep_tick
            )
            if not I64_MIN <= position <= I64_MAX:
                raise PositionOverflowError("linear trajectory exceeds Q31.32 range")
            return self._quantize_position(position)

        span = self._triangle_high_q31_32 - self._triangle_low_q31_32
        travel_period = 2 * span
        initial_offset = self._initial_position_q31_32 - self._triangle_low_q31_32
        if self._velocity_q31_32_per_iep_tick < 0:
            phase = 2 * span - initial_offset
        else:
            phase = initial_offset
        phase = (phase + elapsed * abs(self._velocity_q31_32_per_iep_tick)) % travel_period
        if phase <= span:
            position = self._triangle_low_q31_32 + phase
        else:
            position = self._triangle_high_q31_32 - (phase - span)
        return self._quantize_position(position)

    def _quantize_position(self, position: int) -> int:
        if not self._quantize_generated_positions:
            return position
        half = Q_ONE // 2
        if position >= 0:
            count = (position + half) // Q_ONE
        else:
            count = -((-position + half) // Q_ONE)
        quantized = count * Q_ONE
        if not I64_MIN <= quantized <= I64_MAX:
            raise PositionOverflowError("quantized trajectory exceeds Q31.32 range")
        return quantized

    def _write(self, address: int, data: bytes) -> None:
        data = bytes(data)
        if self._write_observer is not None:
            self._write_observer((address, data))
        self.sim.memory.write(address, data)

    def publish_at(
        self,
        timestamp_iep: int,
        *,
        position_q31_32: int | None = None,
        generation: int | None = None,
        flags: int = abi.SSI_PRODUCER_SAMPLE_FLAG_VALID,
    ) -> ProducerSample:
        """Publish one coherent sample and then its coherent producer head."""
        timestamp = _u64(timestamp_iep, "timestamp_iep")
        position = self._position_at(timestamp) if position_q31_32 is None else position_q31_32
        if not I64_MIN <= position <= I64_MAX:
            raise PositionOverflowError("position_q31_32 exceeds signed 64-bit range")
        sample_generation = self._generation if generation is None else _u32(generation, "generation")
        _u32(flags, "flags")
        flags |= abi.SSI_PRODUCER_SAMPLE_FLAG_VALID

        stable_seq = self._next_sample_seq
        slot = self._published_count % abi.PRODUCER_SAMPLE_COUNT
        sample_base = abi.PRODUCER_SAMPLES_BASE + slot * abi.PRODUCER_SAMPLE_SIZE
        self._write(sample_base, (stable_seq | 1).to_bytes(8, "little"))
        self._write(
            sample_base + abi.PRODUCER_SAMPLE_TIMESTAMP_IEP_OFF,
            abi.pack_producer_sample_payload(
                timestamp, position, sample_generation, flags
            ),
        )
        self._write(sample_base, stable_seq.to_bytes(8, "little"))

        head_seq = self._next_head_seq
        head_base = (
            abi.PRODUCER_SAMPLE_DIAGNOSTICS_BASE
            + abi.PRODUCER_SAMPLE_DIAGNOSTICS_HEAD_SEQ_OFF
        )
        self._write(head_base, ((head_seq | 1) & 0xFFFF_FFFF).to_bytes(4, "little"))
        self._write(
            head_base + 4,
            abi.pack_producer_head_payload(slot, stable_seq),
        )
        self._write(head_base, head_seq.to_bytes(4, "little"))

        self._next_sample_seq = (stable_seq + 2) & U64_MASK
        self._next_head_seq = (head_seq + 2) & 0xFFFF_FFFF
        self._published_count += 1
        return ProducerSample(
            stable_seq, timestamp, position, sample_generation, flags
        )

    def read_head(self) -> ProducerHead:
        head_base = (
            abi.PRODUCER_SAMPLE_DIAGNOSTICS_BASE
            + abi.PRODUCER_SAMPLE_DIAGNOSTICS_HEAD_SEQ_OFF
        )
        return read_coherent_head(
            lambda: int.from_bytes(self.sim.memory_read(head_base, 4), "little"),
            lambda: self.sim.memory_read(head_base + 4, 12),
        )

    def read_sample(self, slot: int) -> ProducerSample:
        _bounded_int(slot, 0, abi.PRODUCER_SAMPLE_COUNT - 1, "slot")
        base = abi.PRODUCER_SAMPLES_BASE + slot * abi.PRODUCER_SAMPLE_SIZE
        return read_coherent_sample(
            lambda: int.from_bytes(self.sim.memory_read(base, 8), "little"),
            lambda: self.sim.memory_read(base + 8, 24),
        )

    def read_latest_samples(self, count: int = 2) -> list[ProducerSample]:
        """Return up to *count* newest valid samples, newest first."""
        _bounded_int(count, 1, abi.PRODUCER_SAMPLE_COUNT, "count")
        head = self.read_head()
        if head.empty:
            return []
        if not head.stable:
            return []
        samples: list[ProducerSample] = []
        expected = head.latest_stable_sample_seq
        for offset in range(count):
            slot = (head.latest_slot_index - offset) % abi.PRODUCER_SAMPLE_COUNT
            sample = self.read_sample(slot)
            if not sample.stable or not sample.valid or sample.write_seq != expected:
                break
            samples.append(sample)
            expected = (expected - 2) & U64_MASK
        return samples

    def read_state(self) -> dict:
        configuration = self.configuration()
        return {
            "running": self._running,
            "mode": configuration.mode,
            "period_iep_ticks": configuration.period_iep_ticks,
            "sample_age_limit_iep_ticks": configuration.sample_age_limit_iep_ticks,
            "prediction_horizon_limit_iep_ticks": configuration.prediction_horizon_limit_iep_ticks,
            "trajectory": configuration.trajectory,
            "initial_position_q31_32": configuration.initial_position_q31_32,
            "velocity_q31_32_per_iep_tick": configuration.velocity_q31_32_per_iep_tick,
            "triangle_low_q31_32": configuration.triangle_low_q31_32,
            "triangle_high_q31_32": configuration.triangle_high_q31_32,
            "generation": self._generation,
            "published_count": self._published_count,
            "last_catch_up_skipped": self._last_catch_up_skipped,
            "skipped_overwritten_count": self._skipped_overwritten_count,
            "error": None if self._error_state is None else self._error_state.__dict__,
            "current_timestamp_iep": getattr(getattr(self.sim, "iep", None), "count", 0),
            "head": self.read_head().__dict__,
            "latest_samples": [sample.__dict__ for sample in self.read_latest_samples(2)],
        }

    def reset(self) -> None:
        """Stop and clear producer-owned state while retaining configuration."""
        self.stop()
        self.sim.memory.write(
            abi.PRODUCER_SAMPLES_BASE,
            bytes(abi.PRODUCER_SAMPLE_COUNT * abi.PRODUCER_SAMPLE_SIZE),
        )
        head_base = (
            abi.PRODUCER_SAMPLE_DIAGNOSTICS_BASE
            + abi.PRODUCER_SAMPLE_DIAGNOSTICS_HEAD_SEQ_OFF
        )
        self.sim.memory.write(head_base, bytes(16))
        self._published_count = 0
        self._last_catch_up_skipped = 0
        self._skipped_overwritten_count = 0
        self._next_sample_seq = 2
        self._next_head_seq = 2
        self._next_due_timestamp = None
        self._trajectory_origin_timestamp = None
        self._error_state = None
