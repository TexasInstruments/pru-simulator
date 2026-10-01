"""Pure reference model for timestamped SSI producer samples.

The dynamic position representation is signed Q31.32 continuous/unwrapped
encoder counts. It preserves sub-count motion but intentionally has a smaller
range than a raw 64-bit SSI frame. Dynamic estimates are range-checked before
wire conversion; static/prepacked frames retain the full unsigned 64-bit ABI.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Callable, Sequence

from pru_io import ssi_config_abi as abi


U64_MASK = (1 << 64) - 1
U64_HALF = 1 << 63
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1
U32_MAX = (1 << 32) - 1
Q_FRACTION_BITS = abi.PRODUCER_SAMPLE_Q_FRACTION_BITS
DEFAULT_PRODUCER_PERIOD_IEP_TICKS = abi.DEFAULT_PRODUCER_PERIOD_IEP_TICKS
DEFAULT_SAMPLE_AGE_LIMIT_IEP_TICKS = abi.DEFAULT_PRODUCER_SAMPLE_AGE_LIMIT_IEP_TICKS
DEFAULT_PREDICTION_HORIZON_LIMIT_IEP_TICKS = (
    abi.DEFAULT_PRODUCER_PREDICTION_HORIZON_LIMIT_IEP_TICKS
)


class CoherenceError(ValueError):
    """A sample or producer head did not remain stable while read."""


class TimestampOrderError(ValueError):
    """Timestamp order is backward or ambiguous across the half-wrap."""


class GenerationMismatchError(ValueError):
    """The two samples belong to different configuration generations."""


class PositionOverflowError(OverflowError):
    """A dynamic position cannot be represented without truncation."""


class InsufficientSamplesError(ValueError):
    """A coherent producer head does not name two consecutive samples."""


def _bounded_int(value: int, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be in [{minimum}, {maximum}]")
    return value


def _u64(value: int, name: str) -> int:
    return _bounded_int(value, 0, U64_MASK, name)


def _u32(value: int, name: str) -> int:
    return _bounded_int(value, 0, U32_MAX, name)


def _i64(value: int, name: str) -> int:
    return _bounded_int(value, I64_MIN, I64_MAX, name)


@dataclass(frozen=True)
class ProducerSample:
    write_seq: int
    timestamp_iep: int
    position_q31_32: int
    generation: int
    flags: int

    def __post_init__(self) -> None:
        _u64(self.write_seq, "write_seq")
        _u64(self.timestamp_iep, "timestamp_iep")
        _i64(self.position_q31_32, "position_q31_32")
        _u32(self.generation, "generation")
        _u32(self.flags, "flags")

    @property
    def stable(self) -> bool:
        return (self.write_seq & 1) == 0

    @property
    def valid(self) -> bool:
        return bool(self.flags & abi.SSI_PRODUCER_SAMPLE_FLAG_VALID)


@dataclass(frozen=True)
class ProducerHead:
    head_seq: int
    latest_slot_index: int
    latest_stable_sample_seq: int

    def __post_init__(self) -> None:
        _u32(self.head_seq, "head_seq")
        _u32(self.latest_slot_index, "latest_slot_index")
        _u64(self.latest_stable_sample_seq, "latest_stable_sample_seq")
        if not self.empty and self.latest_slot_index >= abi.PRODUCER_SAMPLE_COUNT:
            raise ValueError("latest_slot_index is outside the producer ring")

    @property
    def stable(self) -> bool:
        return (self.head_seq & 1) == 0

    @property
    def empty(self) -> bool:
        return (
            self.head_seq == 0
            and self.latest_slot_index == 0
            and self.latest_stable_sample_seq == 0
        )


@dataclass(frozen=True)
class EstimateResult:
    position_q31_32: int
    extrapolated_ticks: int
    sample_age_ticks: int
    prediction_horizon_ticks: int
    stale: bool
    horizon_exceeded: bool
    zero_dt: bool
    older_write_seq: int
    newer_write_seq: int


@dataclass(frozen=True)
class RingProgress:
    unseen_publications: int
    overwritten_entries: int


def unpack_producer_sample(data: bytes | bytearray | memoryview) -> ProducerSample:
    return ProducerSample(**abi.unpack_producer_sample(data))


def unpack_producer_head(data: bytes | bytearray | memoryview) -> ProducerHead:
    return ProducerHead(**abi.unpack_producer_head(data))


def _read_coherent(
    read_seq: Callable[[], int],
    read_payload: Callable[[], bytes],
    unpack_payload: Callable[[bytes, int], dict],
    sequence_name: str,
    sequence_max: int,
    result_type,
    max_retries: int,
):
    _bounded_int(max_retries, 1, U32_MAX, "max_retries")
    for _ in range(max_retries):
        before = _bounded_int(read_seq(), 0, sequence_max, sequence_name)
        if before & 1:
            continue
        payload = read_payload()
        after = _bounded_int(read_seq(), 0, sequence_max, sequence_name)
        if before != after or (after & 1):
            continue
        try:
            return result_type(**unpack_payload(payload, after))
        except (struct.error, TypeError, ValueError) as exc:
            raise CoherenceError(f"invalid {sequence_name} payload") from exc
    raise CoherenceError(f"{sequence_name} did not remain coherent")


def read_coherent_sample(
    read_seq: Callable[[], int],
    read_payload: Callable[[], bytes],
    *,
    max_retries: int = 100,
) -> ProducerSample:
    return _read_coherent(
        read_seq,
        read_payload,
        abi.unpack_producer_sample_payload,
        "write_seq",
        U64_MASK,
        ProducerSample,
        max_retries,
    )


def read_coherent_head(
    read_seq: Callable[[], int],
    read_payload: Callable[[], bytes],
    *,
    max_retries: int = 100,
) -> ProducerHead:
    return _read_coherent(
        read_seq,
        read_payload,
        abi.unpack_producer_head_payload,
        "head_seq",
        U32_MAX,
        ProducerHead,
        max_retries,
    )


def round_div_signed(numerator: int, denominator: int) -> int:
    if isinstance(numerator, bool) or not isinstance(numerator, int):
        raise ValueError("numerator must be an integer")
    _bounded_int(denominator, 1, U64_MASK, "denominator")
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return sign * quotient


def _forward_delta(newer: int, older: int, name: str) -> int:
    newer = _u64(newer, name)
    older = _u64(older, name)
    delta = (newer - older) & U64_MASK
    if delta >= U64_HALF:
        raise TimestampOrderError(f"{name} order is backward or ambiguous")
    return delta


def publication_distance(older_stable_seq: int, newer_stable_seq: int) -> int:
    older_stable_seq = _u64(older_stable_seq, "older_stable_seq")
    newer_stable_seq = _u64(newer_stable_seq, "newer_stable_seq")
    if (older_stable_seq | newer_stable_seq) & 1:
        raise ValueError("publication sequences must be stable/even")
    delta = (newer_stable_seq - older_stable_seq) & U64_MASK
    if delta >= U64_HALF:
        raise ValueError("publication sequence distance is ambiguous")
    return delta // 2


def account_ring_progress(
    last_consumed_stable_seq: int,
    producer_head: ProducerHead,
    *,
    capacity: int = abi.PRODUCER_SAMPLE_COUNT,
) -> RingProgress:
    _bounded_int(capacity, 1, U32_MAX, "capacity")
    if producer_head.empty or not producer_head.stable:
        raise CoherenceError("overrun accounting requires a coherent non-empty head")
    unseen = publication_distance(
        last_consumed_stable_seq,
        producer_head.latest_stable_sample_seq,
    )
    return RingProgress(unseen, max(0, unseen - capacity))


def select_latest_two(
    producer_head: ProducerHead,
    samples: Sequence[ProducerSample],
) -> tuple[ProducerSample, ProducerSample]:
    if producer_head.empty or not producer_head.stable:
        raise InsufficientSamplesError("producer head is empty or in progress")
    if len(samples) < abi.PRODUCER_SAMPLE_COUNT:
        raise InsufficientSamplesError("producer ring snapshot is incomplete")

    latest_slot = producer_head.latest_slot_index
    latest = samples[latest_slot]
    if (
        not latest.stable
        or not latest.valid
        or latest.write_seq != producer_head.latest_stable_sample_seq
    ):
        raise InsufficientSamplesError("head does not name a coherent latest sample")

    older = samples[(latest_slot - 1) % abi.PRODUCER_SAMPLE_COUNT]
    if (
        not older.stable
        or not older.valid
        or publication_distance(older.write_seq, latest.write_seq) != 1
    ):
        raise InsufficientSamplesError("previous slot is not the preceding publication")
    return older, latest


def select_latest_two_for_mode(
    producer_mode: int,
    producer_head: ProducerHead,
    samples: Sequence[ProducerSample],
) -> tuple[ProducerSample, ProducerSample] | None:
    if producer_mode == abi.SSI_PRODUCER_MODE_STATIC_SEQUENCE:
        return None
    if producer_mode != abi.SSI_PRODUCER_MODE_TIMESTAMPED:
        raise ValueError("unknown producer mode")
    return select_latest_two(producer_head, samples)


def estimate_position(
    older: ProducerSample,
    newer: ProducerSample,
    *,
    request_timestamp_iep: int,
    observation_timestamp_iep: int,
    sample_age_limit_iep_ticks: int,
    prediction_horizon_limit_iep_ticks: int,
) -> EstimateResult:
    if older.generation != newer.generation:
        raise GenerationMismatchError("producer sample generations differ")
    if not older.stable or not newer.stable:
        raise CoherenceError("estimation requires stable samples")

    _bounded_int(sample_age_limit_iep_ticks, 0, U32_MAX, "sample_age_limit_iep_ticks")
    _bounded_int(
        prediction_horizon_limit_iep_ticks,
        0,
        U32_MAX,
        "prediction_horizon_limit_iep_ticks",
    )
    sample_dt = _forward_delta(newer.timestamp_iep, older.timestamp_iep, "sample timestamp")
    sample_age = _forward_delta(
        observation_timestamp_iep, newer.timestamp_iep, "observation timestamp"
    )
    prediction_horizon = _forward_delta(
        request_timestamp_iep, observation_timestamp_iep, "request timestamp"
    )
    request_delta = _forward_delta(
        request_timestamp_iep, newer.timestamp_iep, "request timestamp"
    )

    stale = sample_age > sample_age_limit_iep_ticks
    horizon_exceeded = prediction_horizon > prediction_horizon_limit_iep_ticks
    zero_dt = sample_dt == 0
    position = newer.position_q31_32

    if not stale and not horizon_exceeded and not zero_dt:
        position += round_div_signed(
            (newer.position_q31_32 - older.position_q31_32) * request_delta,
            sample_dt,
        )
        if not I64_MIN <= position <= I64_MAX:
            raise PositionOverflowError("estimated Q31.32 position exceeds int64")

    return EstimateResult(
        position_q31_32=position,
        extrapolated_ticks=request_delta,
        sample_age_ticks=sample_age,
        prediction_horizon_ticks=prediction_horizon,
        stale=stale,
        horizon_exceeded=horizon_exceeded,
        zero_dt=zero_dt,
        older_write_seq=older.write_seq,
        newer_write_seq=newer.write_seq,
    )


def q31_32_to_wire_position(position_q31_32: int, width_bits: int) -> int:
    """Round dynamic Q31.32 counts and range-check for an unsigned wire field."""
    _i64(position_q31_32, "position_q31_32")
    _bounded_int(width_bits, 1, 64, "width_bits")
    integer_counts = round_div_signed(position_q31_32, 1 << Q_FRACTION_BITS)
    maximum = (1 << width_bits) - 1
    if not 0 <= integer_counts <= maximum:
        raise PositionOverflowError(
            f"dynamic position does not fit unsigned {width_bits}-bit field"
        )
    return integer_counts
