"""Task C tests for the simulator-side ARM position producer."""

from __future__ import annotations

import pytest

from pru_io import ssi_config_abi as abi
from pru_io.ssi_position_estimator import (
    I64_MAX,
    PositionOverflowError,
    account_ring_progress,
)
from pru_io.ssi_position_producer import SSIPositionProducer
from simulator import Simulator


IEPCLK = 0x00026030
IEP_GLOBAL_CFG = 0x0002E000
Q = 32


def _write32(sim: Simulator, address: int, value: int) -> None:
    sim.memory.write(address, (value & 0xFFFF_FFFF).to_bytes(4, "little"))


def _enable_iep(sim: Simulator) -> None:
    # OCP clock + enabled + DEFAULT_INC=1: one 300 MHz IEP tick per PRU cycle.
    _write32(sim, IEPCLK, abi.IEPCLK_OCP_EN)
    _write32(sim, IEP_GLOBAL_CFG, 0x11)


def _read_sample(sim: Simulator, slot: int):
    address = abi.PRODUCER_SAMPLES_BASE + slot * abi.PRODUCER_SAMPLE_SIZE
    return abi.unpack_producer_sample(sim.memory_read(address, abi.PRODUCER_SAMPLE_SIZE))


def _read_head(sim: Simulator):
    return abi.unpack_producer_head(
        sim.memory_read(
            abi.PRODUCER_SAMPLE_DIAGNOSTICS_BASE
            + abi.PRODUCER_SAMPLE_DIAGNOSTICS_HEAD_SEQ_OFF,
            16,
        )
    )


def test_publish_writes_sample_payload_before_stable_head():
    sim = Simulator()
    writes = []
    producer = SSIPositionProducer(sim, write_observer=writes.append)

    producer.publish_at(123, position_q31_32=5 << Q, generation=7)

    sample_base = abi.PRODUCER_SAMPLES_BASE
    head_base = (
        abi.PRODUCER_SAMPLE_DIAGNOSTICS_BASE
        + abi.PRODUCER_SAMPLE_DIAGNOSTICS_HEAD_SEQ_OFF
    )
    assert [address for address, _ in writes] == [
        sample_base,
        sample_base + abi.PRODUCER_SAMPLE_WRITE_SEQ_OFF + 8,
        sample_base,
        head_base,
        head_base + 4,
        head_base,
    ]
    assert int.from_bytes(writes[0][1], "little") == 3
    assert int.from_bytes(writes[2][1], "little") == 2
    assert int.from_bytes(writes[3][1], "little") == 3
    assert int.from_bytes(writes[5][1], "little") == 2

    sample = _read_sample(sim, 0)
    assert sample["write_seq"] == 2
    assert sample["timestamp_iep"] == 123
    assert sample["position_q31_32"] == 5 << Q
    assert sample["generation"] == 7
    assert sample["flags"] & abi.SSI_PRODUCER_SAMPLE_FLAG_VALID
    assert _read_head(sim) == {
        "head_seq": 2,
        "latest_slot_index": 0,
        "latest_stable_sample_seq": 2,
    }


def test_manual_publish_rejects_q31_32_overflow_without_partial_publication():
    sim = Simulator()
    producer = SSIPositionProducer(sim)

    with pytest.raises(PositionOverflowError):
        producer.publish_at(1, position_q31_32=I64_MAX + 1)

    assert producer.read_head().empty
    assert sim.memory_read(abi.PRODUCER_SAMPLES_BASE, 32) == bytes(32)


@pytest.mark.parametrize(
    ("velocity", "expected"),
    [
        (2 << Q, [10, 12, 14]),
        (-2 << Q, [10, 8, 6]),
    ],
)
def test_linear_trajectory_is_integer_and_timestamp_based(velocity, expected):
    sim = Simulator()
    producer = SSIPositionProducer(sim)
    producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        trajectory="linear",
        initial_position_q31_32=10 << Q,
        velocity_q31_32_per_iep_tick=velocity,
    )

    for timestamp in (100, 101, 102):
        producer.publish_at(timestamp)

    assert [sample.position_q31_32 >> Q for sample in producer.read_latest_samples(3)[::-1]] == expected
    assert [sample.timestamp_iep for sample in producer.read_latest_samples(3)[::-1]] == [100, 101, 102]


def test_triangle_trajectory_reflects_at_both_integer_bounds():
    sim = Simulator()
    producer = SSIPositionProducer(sim)
    producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        trajectory="triangle",
        initial_position_q31_32=0,
        velocity_q31_32_per_iep_tick=1 << Q,
        triangle_low_q31_32=0,
        triangle_high_q31_32=4 << Q,
    )

    for timestamp in (0, 1, 4, 5, 8):
        producer.publish_at(timestamp)

    samples = producer.read_latest_samples(5)[::-1]
    assert [sample.position_q31_32 >> Q for sample in samples] == [0, 1, 4, 3, 0]


@pytest.mark.parametrize(
    ("initial", "velocity", "expected"),
    [
        (1, 1, [1, 4, 3, 0, 1]),
        (3, -1, [3, 0, 1, 4, 3]),
    ],
)
def test_triangle_preserves_non_boundary_origin_for_either_direction(
    initial, velocity, expected
):
    sim = Simulator()
    producer = SSIPositionProducer(
        sim,
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        trajectory="triangle",
        initial_position_q31_32=initial << Q,
        velocity_q31_32_per_iep_tick=velocity << Q,
        triangle_low_q31_32=0,
        triangle_high_q31_32=4 << Q,
    )

    for timestamp in (0, 3, 4, 7, 8):
        producer.publish_at(timestamp)

    samples = producer.read_latest_samples(5)[::-1]
    assert [sample.position_q31_32 >> Q for sample in samples] == expected


def test_auto_publish_uses_iep_cadence_and_catches_up_exact_timestamps():
    sim = Simulator()
    _enable_iep(sim)
    producer = SSIPositionProducer(
        sim,
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        period_iep_ticks=288,
        trajectory="linear",
        initial_position_q31_32=0,
        velocity_q31_32_per_iep_tick=1 << Q,
    )

    producer.start()
    assert producer.published_count == 1
    sim.load("pru0", "nop\njmp 0\n")
    sim.step("pru0", 287)
    assert producer.published_count == 1
    sim.step("pru0", 1)
    assert producer.published_count == 2

    # A direct timestamp advance exercises the same callback path as a large
    # simulator time chunk and must publish every due cadence point.
    producer.advance_to(1152)
    assert producer.published_count == 5
    assert [sample.timestamp_iep for sample in producer.read_latest_samples(5)[::-1]] == [
        0, 288, 576, 864, 1152
    ]
    assert [sample.position_q31_32 >> Q for sample in producer.read_latest_samples(5)[::-1]] == [
        0, 288, 576, 864, 1152
    ]


def test_integer_output_quantizes_continuous_motion_at_sample_boundaries():
    sim = Simulator()
    velocity = round(250_000 * (1 << Q) / abi.IEP_TICK_HZ)
    producer = SSIPositionProducer(
        sim,
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        period_iep_ticks=288,
        trajectory="linear",
        initial_position_q31_32=0,
        velocity_q31_32_per_iep_tick=velocity,
        quantize_generated_positions=True,
    )

    for timestamp in (0, 288, 576, 864, 1152):
        producer.publish_at(timestamp)

    samples = producer.read_latest_samples(5)[::-1]
    assert [sample.position_q31_32 & 0xFFFF_FFFF for sample in samples] == [0] * 5
    assert [sample.position_q31_32 >> Q for sample in samples] == [0, 0, 0, 1, 1]


def test_static_mode_does_not_register_iep_observer_and_stop_removes_one():
    sim = Simulator()
    _enable_iep(sim)
    producer = SSIPositionProducer(sim)

    assert sim.iep.observer_count == 0
    producer.start()
    assert sim.iep.observer_count == 0
    assert producer.published_count == 0

    producer.configure(mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED)
    producer.start()
    assert sim.iep.observer_count == 1
    producer.stop()
    assert sim.iep.observer_count == 0


def test_hard_reset_stops_live_producer_unregisters_and_clears_ring_head():
    sim = Simulator()
    _enable_iep(sim)
    assert sim.load("pru0", "nop\njmp 0\n") == []
    producer = SSIPositionProducer(
        sim,
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        period_iep_ticks=1,
        trajectory="linear",
        velocity_q31_32_per_iep_tick=1,
    )
    producer.start()
    sim.step("pru0", 3)
    assert producer.published_count == 4
    assert sim.iep.observer_count == 1

    sim.hard_reset()

    assert producer.running is False
    assert sim.iep.observer_count == 0
    assert producer.published_count == 0
    assert producer.read_head().empty
    assert sim.memory_read(abi.PRODUCER_SAMPLES_BASE, 32) == bytes(32)


def test_automatic_overflow_stops_callback_and_exposes_stable_error():
    sim = Simulator()
    _enable_iep(sim)
    assert sim.load("pru0", "nop\njmp 0\n") == []
    producer = SSIPositionProducer(
        sim,
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        period_iep_ticks=1,
        trajectory="linear",
        initial_position_q31_32=I64_MAX,
        velocity_q31_32_per_iep_tick=1,
    )
    producer.start()

    sim.step("pru0", 1)

    assert producer.running is False
    assert sim.iep.observer_count == 0
    assert producer.published_count == 1
    assert producer.error_state is not None
    assert producer.error_state.error_type == "PositionOverflowError"
    assert producer.error_state.timestamp_iep == 1
    state = producer.read_state()
    assert state["error"]["error_type"] == "PositionOverflowError"
    sim.step("pru0", 10)
    assert producer.published_count == 1
    assert producer.error_state.timestamp_iep == 1


def test_large_catch_up_skips_early_points_and_writes_at_most_one_ring():
    sim = Simulator()
    _enable_iep(sim)
    writes = []
    producer = SSIPositionProducer(
        sim,
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        period_iep_ticks=1,
        trajectory="linear",
        velocity_q31_32_per_iep_tick=1,
        write_observer=writes.append,
    )
    producer.start()
    writes.clear()

    assert producer.advance_to(1000) == 1000

    assert producer.published_count == 1001
    assert producer.last_catch_up_skipped == 744
    assert producer.skipped_overwritten_count == 744
    head = producer.read_head()
    assert head.latest_slot_index == 1000 % abi.PRODUCER_SAMPLE_COUNT
    assert head.latest_stable_sample_seq == 2 * 1001
    progress = account_ring_progress(2, head)
    assert (progress.unseen_publications, progress.overwritten_entries) == (1000, 744)
    latest = producer.read_latest_samples(abi.PRODUCER_SAMPLE_COUNT)
    assert latest[0].timestamp_iep == 1000
    assert latest[-1].timestamp_iep == 745

    sample_end = (
        abi.PRODUCER_SAMPLES_BASE
        + abi.PRODUCER_SAMPLE_COUNT * abi.PRODUCER_SAMPLE_SIZE
    )
    head_base = (
        abi.PRODUCER_SAMPLE_DIAGNOSTICS_BASE
        + abi.PRODUCER_SAMPLE_DIAGNOSTICS_HEAD_SEQ_OFF
    )
    ring_or_head_writes = [
        address
        for address, _ in writes
        if abi.PRODUCER_SAMPLES_BASE <= address < sample_end
        or head_base <= address < head_base + 16
    ]
    assert len(ring_or_head_writes) == 6 * abi.PRODUCER_SAMPLE_COUNT
    consumer_overrun = int.from_bytes(
        sim.memory_read(
            abi.PRODUCER_SAMPLE_DIAGNOSTICS_BASE
            + abi.PRODUCER_SAMPLE_DIAGNOSTICS_RING_OVERRUN_COUNT_OFF,
            4,
        ),
        "little",
    )
    assert consumer_overrun == 0


def test_invalid_reconfiguration_leaves_running_producer_unchanged():
    sim = Simulator()
    _enable_iep(sim)
    assert sim.load("pru0", "nop\njmp 0\n") == []
    producer = SSIPositionProducer(
        sim,
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        period_iep_ticks=2,
        generation=9,
    )
    producer.start()
    before = producer.configuration()

    with pytest.raises(ValueError, match="period_iep_ticks"):
        producer.configure(period_iep_ticks=0)

    assert producer.configuration() == before
    assert producer.running is True
    assert producer.generation == 9
    assert sim.iep.observer_count == 1
    before_count = producer.published_count
    sim.step("pru0", 2)
    assert producer.published_count == before_count + 1


def test_shape_or_cadence_reconfiguration_advances_generation_with_wrap():
    sim = Simulator()
    producer = SSIPositionProducer(
        sim,
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        generation=7,
    )
    producer.publish_at(0, position_q31_32=0)

    producer.configure(period_iep_ticks=144)
    assert producer.generation == 8
    producer.publish_at(1, position_q31_32=1)
    assert [sample.generation for sample in producer.read_latest_samples(2)] == [8, 7]

    producer.configure(
        trajectory="linear",
        velocity_q31_32_per_iep_tick=1,
        generation=0xFFFF_FFFF,
    )
    assert producer.generation == 0xFFFF_FFFF
    producer.configure(period_iep_ticks=72)
    assert producer.generation == 0

    # Limits do not change the generated shape or cadence.
    producer.configure(sample_age_limit_iep_ticks=1234)
    assert producer.generation == 0


def test_generation_change_applies_to_future_samples_and_reset_restarts_ring():
    sim = Simulator()
    producer = SSIPositionProducer(sim, mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED)
    producer.publish_at(10, position_q31_32=1 << Q, generation=1)
    producer.publish_at(20, position_q31_32=2 << Q, generation=1)
    producer.set_generation(2)
    producer.publish_at(30, position_q31_32=3 << Q)

    assert producer.read_head().latest_stable_sample_seq == 6
    assert producer.read_latest_samples(1)[0].generation == 2
    assert producer.read_latest_samples(2)[1].generation == 1

    producer.reset()
    assert producer.published_count == 0
    assert producer.read_head().empty
    assert sim.memory_read(abi.PRODUCER_SAMPLES_BASE, 32) == bytes(32)

    producer.publish_at(40, position_q31_32=4 << Q)
    assert producer.read_latest_samples(1)[0].write_seq == 2


def test_ring_wrap_keeps_newest_pair_and_monotonic_publication_count():
    sim = Simulator()
    producer = SSIPositionProducer(sim, mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED)
    for timestamp in range(abi.PRODUCER_SAMPLE_COUNT + 2):
        producer.publish_at(timestamp, position_q31_32=timestamp << Q)

    assert producer.published_count == abi.PRODUCER_SAMPLE_COUNT + 2
    head = producer.read_head()
    assert head.latest_slot_index == 1
    assert head.latest_stable_sample_seq == 2 * (abi.PRODUCER_SAMPLE_COUNT + 2)
    latest = producer.read_latest_samples(2)
    assert [sample.timestamp_iep for sample in latest] == [257, 256]
    assert [sample.write_seq for sample in latest] == [516, 514]


def test_producer_does_not_modify_config_mailbox_trace_or_unowned_diagnostics():
    sim = Simulator()
    sentinels = {
        abi.CONFIG_BASE: bytes(range(32)),
        abi.MAILBOX_BASE: bytes(range(64)),
        abi.TRACE_BASE: bytes(range(24)),
        abi.PRODUCER_SAMPLE_DIAGNOSTICS_BASE: bytes(range(48)),
    }
    for address, data in sentinels.items():
        sim.memory.write(address, data)

    producer = SSIPositionProducer(sim, mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED)
    producer.publish_at(10, position_q31_32=1 << Q)

    assert sim.memory_read(abi.CONFIG_BASE, 32) == sentinels[abi.CONFIG_BASE]
    assert sim.memory_read(abi.MAILBOX_BASE, 64) == sentinels[abi.MAILBOX_BASE]
    assert sim.memory_read(abi.TRACE_BASE, 24) == sentinels[abi.TRACE_BASE]
    # The producer owns only diagnostics +0x30..+0x3F, so earlier fields stay.
    assert sim.memory_read(abi.PRODUCER_SAMPLE_DIAGNOSTICS_BASE, 48)[:48] == (
        sentinels[abi.PRODUCER_SAMPLE_DIAGNOSTICS_BASE][:48]
    )
