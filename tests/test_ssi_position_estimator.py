"""Contract tests for the timestamped producer-sample ABI and oracle."""

import importlib

import pytest

from pru_io import ssi_config_abi as abi


U64_MAX = (1 << 64) - 1
I64_MAX = (1 << 63) - 1
I64_MIN = -(1 << 63)
Q = 32


def estimator_module():
    return importlib.import_module("pru_io.ssi_position_estimator")


def sample(
    module,
    write_seq,
    timestamp,
    position,
    generation=7,
    flags=abi.SSI_PRODUCER_SAMPLE_FLAG_VALID,
):
    return module.ProducerSample(
        write_seq=write_seq,
        timestamp_iep=timestamp,
        position_q31_32=position << Q,
        generation=generation,
        flags=flags,
    )


def head(module, seq, slot, sample_seq):
    return module.ProducerHead(seq, slot, sample_seq)


def estimate(module, older, newer, request, observation=None, age=20, horizon=40):
    if observation is None:
        observation = newer.timestamp_iep
    return module.estimate_position(
        older,
        newer,
        request_timestamp_iep=request,
        observation_timestamp_iep=observation,
        sample_age_limit_iep_ticks=age,
        prediction_horizon_limit_iep_ticks=horizon,
    )


def test_sample_and_head_abi_have_fixed_layout_and_round_trip():
    module = estimator_module()
    value = module.ProducerSample(
        0x102, 0x0123456789ABCDEF, -(3 << Q) + 17, 0xAABBCCDD, 0x11223344
    )
    publication_head = module.ProducerHead(0x22, 7, value.write_seq)

    assert module.unpack_producer_sample(abi.pack_producer_sample(
        value.write_seq, value.timestamp_iep, value.position_q31_32,
        value.generation, value.flags
    )) == value
    assert module.unpack_producer_head(abi.pack_producer_head(
        publication_head.head_seq, publication_head.latest_slot_index,
        publication_head.latest_stable_sample_seq
    )) == publication_head
    assert abi.PRODUCER_SAMPLES_BASE == 0x10000 + 0x6400
    assert abi.PRODUCER_SAMPLE_COUNT == 256
    assert abi.PRODUCER_SAMPLE_SIZE == 32
    assert abi.PRODUCER_SAMPLE_DIAGNOSTICS_HEAD_SEQ_OFF == 0x30
    assert abi.PRODUCER_SAMPLE_DIAGNOSTICS_LATEST_SLOT_INDEX_OFF == 0x34
    assert abi.PRODUCER_SAMPLE_DIAGNOSTICS_LATEST_STABLE_SAMPLE_SEQ_OFF == 0x38


def test_sample_abi_enforces_unsigned_and_signed_64_bit_bounds():
    module = estimator_module()
    value = module.ProducerSample(U64_MAX, U64_MAX, I64_MAX, 0xFFFFFFFF, 0xFFFFFFFF)
    assert module.unpack_producer_sample(abi.pack_producer_sample(
        value.write_seq, value.timestamp_iep, value.position_q31_32,
        value.generation, value.flags
    )) == value
    with pytest.raises(ValueError):
        module.ProducerSample(U64_MAX + 1, 0, 0, 0, 0)
    with pytest.raises(ValueError):
        module.ProducerSample(2, 0, I64_MIN - 1, 0, 0)


def test_coherent_sample_reader_retries_odd_and_changed_publications():
    module = estimator_module()
    value = sample(module, 4, 960, 12)
    payload = abi.pack_producer_sample_payload(
        value.timestamp_iep, value.position_q31_32, value.generation, value.flags
    )
    sequences = iter((3, 4, 4, 4))
    reads = []

    def read_seq():
        reads.append("seq")
        return next(sequences)

    def read_payload():
        reads.append("payload")
        return payload

    assert module.read_coherent_sample(read_seq, read_payload, max_retries=3) == value
    assert reads == ["seq", "seq", "payload", "seq"]


def test_coherent_sample_reader_rejects_continuously_torn_publication():
    module = estimator_module()
    sequences = iter((2, 4, 4, 6))
    with pytest.raises(module.CoherenceError):
        module.read_coherent_sample(lambda: next(sequences), lambda: bytes(24), max_retries=2)


def test_coherent_head_reader_retries_and_accepts_stable_payload():
    module = estimator_module()
    sequences = iter((3, 4, 4, 4))
    payload = abi.pack_producer_head_payload(9, 0x106)
    accepted = module.read_coherent_head(
        lambda: next(sequences), lambda: payload, max_retries=3
    )
    assert accepted == module.ProducerHead(4, 9, 0x106)


def test_zero_initialized_ring_is_empty_until_a_coherent_head_names_it():
    module = estimator_module()
    zero_slots = [module.ProducerSample(0, 0, 0, 0, 0) for _ in range(256)]
    assert module.ProducerHead(0, 0, 0).empty is True
    with pytest.raises(module.InsufficientSamplesError):
        module.select_latest_two(module.ProducerHead(0, 0, 0), zero_slots)


def test_head_cannot_make_a_zero_initialized_slot_a_valid_publication():
    module = estimator_module()
    zero_slots = [module.ProducerSample(0, 0, 0, 0, 0) for _ in range(256)]
    with pytest.raises(module.InsufficientSamplesError):
        module.select_latest_two(head(module, 4, 0, 0), zero_slots)


def test_one_real_sample_plus_zero_slots_is_insufficient():
    module = estimator_module()
    slots = [module.ProducerSample(0, 0, 0, 0, 0) for _ in range(256)]
    slots[0] = sample(module, 2, 100, 1)
    with pytest.raises(module.InsufficientSamplesError):
        module.select_latest_two(head(module, 2, 0, 2), slots)


def test_head_selects_latest_consecutive_pair_across_ring_and_sequence_wrap():
    module = estimator_module()
    slots = [module.ProducerSample(0, 0, 0, 0, 0) for _ in range(256)]
    slots[255] = sample(module, U64_MAX - 1, 100, 10)
    slots[0] = sample(module, 0, 110, 11)
    older, newer = module.select_latest_two(head(module, 6, 0, 0), slots)
    assert (older.write_seq, newer.write_seq) == (U64_MAX - 1, 0)


def test_static_mode_bypasses_an_empty_timestamped_ring():
    module = estimator_module()
    assert module.select_latest_two_for_mode(
        abi.SSI_PRODUCER_MODE_STATIC_SEQUENCE,
        module.ProducerHead(0, 0, 0),
        [],
    ) is None
    with pytest.raises(module.InsufficientSamplesError):
        module.select_latest_two_for_mode(
            abi.SSI_PRODUCER_MODE_TIMESTAMPED,
            module.ProducerHead(0, 0, 0),
            [],
        )


def test_estimator_extrapolates_positive_and_negative_motion():
    module = estimator_module()
    positive = estimate(module, sample(module, 2, 100, 10), sample(module, 4, 110, 20), 115)
    negative = estimate(module, sample(module, 2, 100, 20), sample(module, 4, 110, 10), 115)
    assert positive.position_q31_32 == 25 << Q
    assert negative.position_q31_32 == 5 << Q
    assert module.round_div_signed(1, 2) == 1
    assert module.round_div_signed(-1, 2) == -1


def test_fixed_point_rounding_is_nearest_with_ties_away_from_zero():
    module = estimator_module()
    older = sample(module, 2, 0, 0)
    newer = module.ProducerSample(4, 2, 1, 7, 0)
    assert estimate(module, older, newer, 3).position_q31_32 == 2


def test_zero_dt_holds_newest_position():
    module = estimator_module()
    result = estimate(module, sample(module, 2, 100, 3), sample(module, 4, 100, 9), 105)
    assert result.position_q31_32 == 9 << Q
    assert result.zero_dt is True


def test_sample_age_and_prediction_horizon_are_independent_limits():
    module = estimator_module()
    older = sample(module, 2, 100, 10)
    newer = sample(module, 4, 110, 20)
    stale = estimate(module, older, newer, request=132, observation=131, age=20, horizon=40)
    too_far = estimate(module, older, newer, request=151, observation=110, age=20, horizon=40)
    assert stale.stale is True
    assert stale.horizon_exceeded is False
    assert stale.position_q31_32 == 20 << Q
    assert too_far.stale is False
    assert too_far.horizon_exceeded is True
    assert too_far.position_q31_32 == 20 << Q


def test_future_observation_and_request_before_sample_window_are_rejected():
    module = estimator_module()
    older = sample(module, 2, 100, 10)
    newer = sample(module, 4, 110, 20)
    with pytest.raises(module.TimestampOrderError):
        estimate(module, older, newer, request=120, observation=109)
    with pytest.raises(module.TimestampOrderError):
        estimate(module, older, newer, request=99, observation=110)


def test_estimator_accepts_timestamp_rollover_and_unwrapped_position():
    module = estimator_module()
    rollover = estimate(
        module, sample(module, 2, U64_MAX - 4, 0), sample(module, 4, 2, 7),
        request=5, observation=2
    )
    unwrapped = estimate(
        module, sample(module, 2, 100, 4095), sample(module, 4, 110, 4096),
        request=120
    )
    assert rollover.position_q31_32 == 10 << Q
    assert unwrapped.position_q31_32 == 4097 << Q


def test_estimator_rejects_mixed_generations_and_position_overflow():
    module = estimator_module()
    with pytest.raises(module.GenerationMismatchError):
        estimate(module, sample(module, 2, 100, 1, 8), sample(module, 4, 110, 2, 9), 115)
    with pytest.raises(module.PositionOverflowError):
        estimate(
            module,
            module.ProducerSample(2, 100, I64_MAX - 1, 7, 0),
            module.ProducerSample(4, 110, I64_MAX, 7, 0),
            120,
        )


def test_dynamic_wire_conversion_never_truncates_but_static_frames_remain_u64():
    module = estimator_module()
    assert module.q31_32_to_wire_position((12 << Q) + (1 << (Q - 1)), 12) == 13
    with pytest.raises(module.PositionOverflowError):
        module.q31_32_to_wire_position(-(1 << Q), 12)
    with pytest.raises(module.PositionOverflowError):
        module.q31_32_to_wire_position(4096 << Q, 12)
    assert abi.pack_frame_slot(U64_MAX)[:8] == U64_MAX.to_bytes(8, "little")


def test_overrun_accounting_is_exact_and_skips_within_capacity_are_not_corruption():
    module = estimator_module()
    within = module.account_ring_progress(2, head(module, 8, 4, 2 + 2 * 256))
    one_over = module.account_ring_progress(2, head(module, 10, 5, 2 + 2 * 257))
    skipped = module.account_ring_progress(2, head(module, 12, 6, 10))
    assert (within.unseen_publications, within.overwritten_entries) == (256, 0)
    assert (one_over.unseen_publications, one_over.overwritten_entries) == (257, 1)
    assert (skipped.unseen_publications, skipped.overwritten_entries) == (4, 0)


def test_overrun_accounting_handles_64_bit_stable_sequence_wrap():
    module = estimator_module()
    progress = module.account_ring_progress(U64_MAX - 1, head(module, 14, 7, 2))
    assert (progress.unseen_publications, progress.overwritten_entries) == (2, 0)


def test_overrun_accounting_rejects_empty_or_in_progress_head():
    module = estimator_module()
    with pytest.raises(module.CoherenceError):
        module.account_ring_progress(2, module.ProducerHead(0, 0, 0))
    with pytest.raises(module.CoherenceError):
        module.account_ring_progress(2, module.ProducerHead(3, 1, 4))


def test_generated_defaults_define_timebase_modes_and_separate_limits():
    module = estimator_module()
    assert abi.IEPCLK_OCP_EN == 1
    assert abi.IEP_TICK_HZ == 300_000_000
    assert module.DEFAULT_PRODUCER_PERIOD_IEP_TICKS == 288
    assert module.DEFAULT_SAMPLE_AGE_LIMIT_IEP_TICKS == 576
    assert module.DEFAULT_PREDICTION_HORIZON_LIMIT_IEP_TICKS == 6000
    assert module.DEFAULT_PREDICTION_HORIZON_LIMIT_IEP_TICKS > 4890
    assert abi.SSI_PRODUCER_MODE_STATIC_SEQUENCE == 0
    assert abi.SSI_PRODUCER_MODE_TIMESTAMPED == 1
    assert abi.unpack_config(abi.pack_config())["producer_mode"] == 0
    assert abi.SSI_PRODUCER_SAMPLE_FLAG_VALID == 1
