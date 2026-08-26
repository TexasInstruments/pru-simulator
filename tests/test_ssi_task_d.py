"""Task D end-to-end contracts for timestamped SSI production."""

from pathlib import Path

import pytest

from simulator import Simulator
from pru_io import ssi_config_abi as abi
from pru_io.ssi_runtime import SSIRuntime


ROOT = Path(__file__).parent.parent
SOURCE_DIR = ROOT / "source"
EMULATOR_SRC = (
    SOURCE_DIR / "ssi_generic_emulator" / "ssi_generic_emulator.asm"
).read_text()
READER_SRC = (
    SOURCE_DIR / "ssi_generic_reader" / "ssi_generic_reader.asm"
).read_text()


def make_pair() -> tuple[Simulator, SSIRuntime]:
    # Task D depends on the AM243x c26 IEP window and c4 ICSS CFG mapping;
    # use the checked-in target configuration so the pair exercises the same
    # constant-table topology as the real PRU project.
    sim = Simulator(config_path=str(ROOT / "config" / "memory_am243x.cfg"))
    assert sim.load("pru1", READER_SRC, include_paths=[str(SOURCE_DIR)]) == []
    assert sim.load("pru0", EMULATOR_SRC, include_paths=[str(SOURCE_DIR)]) == []
    sim.add_gpio_wire("pru1", 0, "pru0", 8)
    sim.add_gpio_wire("pru0", 0, "pru1", 16)
    sim.hard_reset()
    return sim, SSIRuntime(sim)


def run_until_frames(sim: Simulator, count: int, limit: int = 1_000_000):
    previous = 0
    values = []
    for _ in range(limit):
        sim.step_paced("pru1", "pru0")
        mailbox = abi.unpack_mailbox(sim.memory_read(abi.MAILBOX_BASE, 64))
        if mailbox["frame_counter"] != previous:
            previous = mailbox["frame_counter"]
            values.append(mailbox)
            if len(values) >= count:
                return values
    raise AssertionError("SSI pair did not produce the requested frames")


def configure_timestamped(runtime: SSIRuntime, **overrides) -> None:
    fields = dict(
        producer_mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        producer_period_iep_ticks=288,
        producer_sample_age_limit_iep_ticks=100_000,
        producer_prediction_horizon_limit_iep_ticks=100_000,
    )
    fields.update(overrides)
    runtime.stage("CUSTOM_LEGACY_12BIT_4MHZ", **fields)
    runtime.apply()


def run_next_frames(sim: Simulator, previous: int, count: int, limit: int = 1_000_000):
    values = []
    target = previous + count
    for _ in range(limit):
        sim.step_paced("pru1", "pru0")
        mailbox = abi.unpack_mailbox(sim.memory_read(abi.MAILBOX_BASE, 64))
        if mailbox["frame_counter"] > previous:
            previous = mailbox["frame_counter"]
            values.append(mailbox)
            if previous >= target:
                return values
    raise AssertionError("SSI pair did not produce the requested subsequent frames")


def test_static_apply_does_not_start_timestamped_producer():
    sim, runtime = make_pair()

    assert runtime.producer.running is False
    assert runtime._active_config["producer_mode"] == (
        abi.SSI_PRODUCER_MODE_STATIC_SEQUENCE
    )


def test_timestamped_apply_is_staged_without_implicit_start():
    sim, runtime = make_pair()

    runtime.stage(
        "CUSTOM_LEGACY_12BIT_4MHZ",
        producer_mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        producer_sample_age_limit_iep_ticks=100_000,
        producer_prediction_horizon_limit_iep_ticks=100_000,
    )
    runtime.apply()

    assert runtime.producer.running is False
    assert runtime._active_config["producer_mode"] == (
        abi.SSI_PRODUCER_MODE_TIMESTAMPED
    )


def test_timestamped_constant_position_reaches_pru0_and_reader():
    sim, runtime = make_pair()
    runtime.stage(
        "CUSTOM_LEGACY_12BIT_4MHZ",
        producer_mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        producer_sample_age_limit_iep_ticks=100_000,
        producer_prediction_horizon_limit_iep_ticks=100_000,
    )
    runtime.apply()
    runtime.producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        trajectory="constant",
        initial_position_q31_32=0x345 << 32,
    )
    runtime.producer.start()

    frames = run_until_frames(sim, 4)
    # The first request can only use the already prepared static frame: the
    # producer has not published a coherent pair yet.  Once the second sample
    # exists, every subsequent request uses the timestamped estimate.
    assert [frame["position_value"] for frame in frames[:2]] == [0xABC, 0xABC]
    assert [frame["position_value"] for frame in frames[2:]] == [0x345] * 2
    diagnostics = runtime.read_producer_diagnostics()
    assert diagnostics["accepted_count"] >= 2
    assert diagnostics["status"] == 0


@pytest.mark.parametrize(
    ("overrides", "expected_raw"),
    [
        (
            {
                "frame_width_bits": 16,
                "position_width_bits": 12,
                "singleturn_width_bits": 12,
                "multiturn_width_bits": 0,
                "alignment": 1,
            },
            0x0345,
        ),
        (
            {
                "frame_width_bits": 12,
                "position_width_bits": 12,
                "singleturn_width_bits": 12,
                "multiturn_width_bits": 0,
                "encoding_type": abi.SSI_ENCODING_GRAY,
            },
            0x0345 ^ (0x0345 >> 1),
        ),
        (
            {
                "frame_width_bits": 64,
                "position_width_bits": 12,
                "singleturn_width_bits": 12,
                "multiturn_width_bits": 0,
                "alignment": 1,
            },
            0x0345,
        ),
        (
            {
                "frame_width_bits": 16,
                "position_width_bits": 12,
                "singleturn_width_bits": 12,
                "multiturn_width_bits": 0,
                "position_offset_bits": 2,
            },
            0x0D14,
        ),
        (
            {
                "frame_width_bits": 20,
                "position_width_bits": 12,
                "singleturn_width_bits": 12,
                "multiturn_width_bits": 0,
                "alignment": 1,
                "padding_width_bits": 4,
            },
            0x03450,
        ),
    ],
)
def test_timestamped_consumer_packs_supported_wire_layouts(overrides, expected_raw):
    """Timestamped samples must use the active wire layout, not raw counts."""
    sim, runtime = make_pair()
    configure_timestamped(runtime, **overrides)
    runtime.producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        trajectory="constant",
        initial_position_q31_32=0x345 << 32,
    )
    runtime.producer.start()

    frames = run_until_frames(sim, 5)

    assert [frame["raw_frame"] for frame in frames[2:]] == [expected_raw] * 3
    assert runtime.read_producer_diagnostics()["status"] == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"encoding_type": abi.SSI_ENCODING_GRAY_EXCESS},
        {
            "frame_width_bits": 16,
            "position_width_bits": 12,
            "singleturn_width_bits": 12,
            "multiturn_width_bits": 0,
            "error_offset_bits": 12,
            "error_width_bits": 2,
            "padding_width_bits": 2,
        },
    ],
)
def test_timestamped_consumer_rejects_layout_without_sample_metadata(overrides):
    """Unsupported dynamic metadata must fall back, never silently change wire data."""
    sim, runtime = make_pair()
    configure_timestamped(runtime, **overrides)
    runtime.producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        trajectory="constant",
        initial_position_q31_32=0x345 << 32,
    )
    runtime.producer.start()

    frames = run_until_frames(sim, 5)
    diagnostics = runtime.read_producer_diagnostics()

    assert [frame["raw_frame"] for frame in frames] == [0xABC] * 5
    assert diagnostics["accepted_count"] == 0
    assert diagnostics["status"] & 0x80  # DYN_STATUS_LAYOUT


def test_timestamped_linear_motion_supports_both_directions():
    for velocity, expected_sign in ((1 << 28, 1), (-(1 << 28), -1)):
        sim, runtime = make_pair()
        runtime.stage(
            "CUSTOM_LEGACY_12BIT_4MHZ",
            producer_mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
            producer_sample_age_limit_iep_ticks=100_000,
            producer_prediction_horizon_limit_iep_ticks=100_000,
        )
        runtime.apply()
        runtime.producer.configure(
                mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
                trajectory="linear",
                initial_position_q31_32=0x800 << 32,
            # 2^-4 counts/IEP tick gives a visible integer-aligned change over one
            # roughly-16-us SSI request while remaining inside 12 bits.
            velocity_q31_32_per_iep_tick=velocity,
        )
        runtime.producer.start()

        frames = run_until_frames(sim, 6)
        positions = [frame["position_value"] for frame in frames[2:]]
        deltas = [b - a for a, b in zip(positions, positions[1:])]
        assert any(delta * expected_sign > 0 for delta in deltas)
        assert all(0 <= position <= 0xFFF for position in positions)
        assert runtime.read_producer_diagnostics()["status"] == 0


def test_timestamped_producer_can_publish_at_960ns_while_ssi_requests_are_slower():
    sim, runtime = make_pair()
    configure_timestamped(runtime, producer_period_iep_ticks=288)
    runtime.producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        period_iep_ticks=288,
        trajectory="constant",
        initial_position_q31_32=0x345 << 32,
    )
    runtime.producer.start()

    frames = run_until_frames(sim, 8)
    diagnostics = runtime.read_producer_diagnostics()

    # The 300-MHz IEP makes 288 ticks = 960 ns.  The legacy SSI request is
    # roughly 16 us, so many producer samples must be available per request;
    # the PRU still emits one coherent frame per SSI request.
    assert runtime.producer.published_count >= 8 * 8
    assert diagnostics["accepted_count"] >= len(frames) - 2
    assert diagnostics["status"] == 0


@pytest.mark.parametrize("producer_period", [4_900, 9_800])
def test_timestamped_consumer_also_accepts_synchronized_or_slower_producers(
    producer_period,
):
    sim, runtime = make_pair()
    configure_timestamped(
        runtime,
        producer_period_iep_ticks=producer_period,
        producer_sample_age_limit_iep_ticks=30_000,
        producer_prediction_horizon_limit_iep_ticks=30_000,
    )
    runtime.producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        period_iep_ticks=producer_period,
        trajectory="constant",
        initial_position_q31_32=0x345 << 32,
    )
    runtime.producer.start()

    frames = run_until_frames(sim, 10)
    diagnostics = runtime.read_producer_diagnostics()

    assert frames[-1]["position_value"] == 0x345
    assert diagnostics["accepted_count"] > 0
    assert diagnostics["status"] == 0


def test_stopped_timestamped_producer_holds_last_prepared_frame_and_sets_stale():
    sim, runtime = make_pair()
    configure_timestamped(runtime, producer_sample_age_limit_iep_ticks=1_000)
    runtime.producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        trajectory="constant",
        initial_position_q31_32=0x345 << 32,
    )
    runtime.producer.start()

    initial = run_until_frames(sim, 5)
    accepted_before = runtime.read_producer_diagnostics()["accepted_count"]
    runtime.producer.stop()
    later = run_next_frames(sim, initial[-1]["frame_counter"], 10)
    diagnostics = runtime.read_producer_diagnostics()

    assert [frame["position_value"] for frame in later] == [0x345] * len(later)
    assert all(frame["position_value"] != 0xFFF for frame in later)
    assert diagnostics["accepted_count"] <= accepted_before + 1
    assert diagnostics["status"] & 0x2  # DYN_STATUS_STALE
    assert diagnostics["status"] & 0x200  # DYN_STATUS_MISSED_PREP
    assert diagnostics["stale_sample_count"] > 0


def test_generation_switch_never_emits_a_mixed_generation_frame():
    sim, runtime = make_pair()
    configure_timestamped(runtime)
    runtime.producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        trajectory="constant",
        initial_position_q31_32=0x345 << 32,
    )
    runtime.producer.start()
    before = run_until_frames(sim, 5)
    generation = runtime.producer.generation + 1

    # Reconfiguration publishes one new-generation sample immediately, so
    # the PRU must hold the old prepared frame until the second sample of the
    # new generation closes a coherent pair.
    runtime.producer.configure(
        generation=generation,
        trajectory="constant",
        initial_position_q31_32=0x456 << 32,
    )
    after = run_next_frames(sim, before[-1]["frame_counter"], 5)
    diagnostics = runtime.read_producer_diagnostics()

    assert [frame["position_value"] for frame in after] == [
        0x345,
        0x456,
        0x456,
        0x456,
        0x456,
    ]
    assert diagnostics["generation"] == generation
    assert diagnostics["status"] == 0


def test_ring_overrun_holds_last_frame_and_reports_overrun():
    sim, runtime = make_pair()
    configure_timestamped(runtime, producer_sample_age_limit_iep_ticks=100_000)
    runtime.producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        trajectory="constant",
        initial_position_q31_32=0x345 << 32,
    )
    runtime.producer.start()
    before = run_until_frames(sim, 5)
    accepted_before = runtime.read_producer_diagnostics()["accepted_count"]

    runtime.producer.stop()
    timestamp = sim.iep.count
    for index in range(300):
        timestamp += 288
        runtime.producer.publish_at(
            timestamp,
            position_q31_32=0x456 << 32,
        )
    # Keep the consumer's current IEP time beside the newest ring sample;
    # this isolates the ring-progress diagnostic from the stale-age check.
    sim.iep.counter = timestamp

    later = run_next_frames(sim, before[-1]["frame_counter"], 2)
    diagnostics = runtime.read_producer_diagnostics()

    assert [frame["position_value"] for frame in later] == [0x345, 0x345]
    # One preparation may already have been in flight when the producer was
    # stopped and the ring was filled; that prepared result is still valid.
    # Once the new head is observed, no further estimate is accepted.
    assert diagnostics["accepted_count"] <= accepted_before + 1
    assert diagnostics["status"] & 0x20  # DYN_STATUS_OVERRUN
    assert diagnostics["ring_overrun_count"] > 0


def test_fractional_q31_32_payload_falls_back_explicitly_without_truncation():
    sim, runtime = make_pair()
    configure_timestamped(runtime)
    runtime.producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        trajectory="constant",
        initial_position_q31_32=(0x345 << 32) | 0x1,
    )
    runtime.producer.start()

    frames = run_until_frames(sim, 5)
    diagnostics = runtime.read_producer_diagnostics()

    assert [frame["position_value"] for frame in frames] == [0xABC] * len(frames)
    assert diagnostics["accepted_count"] == 0
    assert diagnostics["status"] & 0x40  # DYN_STATUS_FRACTIONAL


def test_timestamped_estimator_survives_iep_low_word_rollover():
    sim, runtime = make_pair()
    configure_timestamped(runtime)
    runtime.producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        trajectory="constant",
        initial_position_q31_32=0x345 << 32,
    )
    # Start close enough to COUNT_LO rollover that both producer samples and
    # the next SSI request cross it.  The high word is latched by the IEP
    # model on COUNT_LO reads, just as on AM243x.
    sim.iep.write_count(low=0xFFFF_FF00, high=7)
    runtime.producer.start()

    frames = run_until_frames(sim, 5)
    diagnostics = runtime.read_producer_diagnostics()

    assert [frame["position_value"] for frame in frames[2:]] == [0x345] * 3
    assert diagnostics["accepted_count"] >= 2
    assert diagnostics["status"] == 0


def test_timestamped_edge_to_first_data_bit_stays_bounded_at_4mhz():
    sim, runtime = make_pair()
    configure_timestamped(runtime)
    runtime.producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        trajectory="constant",
        initial_position_q31_32=0x345 << 32,
    )
    runtime.producer.start()

    previous_clock = sim.io("pru1")["gpo_pins"][0]
    previous_data = sim.io("pru0")["gpo_pins"][0]
    edge_cycle = None
    edge_to_data_cycles = []
    for _ in range(20_000):
        sim.step_paced("pru1", "pru0")
        clock = sim.io("pru1")["gpo_pins"][0]
        data = sim.io("pru0")["gpo_pins"][0]
        if previous_clock == 1 and clock == 0:
            edge_cycle = sim.cores["pru0"].counters.cycles
        if edge_cycle is not None and previous_data == 1 and data == 0:
            edge_to_data_cycles.append(
                sim.cores["pru0"].counters.cycles - edge_cycle
            )
            edge_cycle = None
        previous_clock = clock
        previous_data = data
        if len(edge_to_data_cycles) >= 5:
            break

    assert edge_to_data_cycles[2:5] == [18, 18, 18]
    assert max(edge_to_data_cycles) <= 24


def test_timestamped_loopback_preserves_the_4mhz_clock_shape():
    sim, runtime = make_pair()
    configure_timestamped(runtime)
    runtime.producer.configure(
        mode=abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        trajectory="constant",
        initial_position_q31_32=0x345 << 32,
    )
    runtime.producer.start()

    previous = sim.io("pru1")["gpo_pins"][0]
    transitions = []
    for _ in range(20_000):
        sim.step_paced("pru1", "pru0")
        current = sim.io("pru1")["gpo_pins"][0]
        if current != previous:
            transitions.append(
                (sim.cores["pru1"].counters.cycles, previous, current)
            )
            previous = current
        if len(transitions) >= 30:
            break

    rising = [cycle for cycle, old, new in transitions if old == 0 and new == 1]
    falling = [cycle for cycle, old, new in transitions if old == 1 and new == 0]
    assert [rise - fall for rise, fall in zip(rising, falling)][2:8] == [35] * 6
    assert [fall - rise for fall, rise in zip(falling[1:], rising)][2:8] == [40] * 6


def test_emulator_source_has_bounded_timestamped_consumer_path():
    assert "PRODUCER_SAMPLES_BASE" in EMULATOR_SRC
    assert "PRODUCER_SAMPLE_DIAGNOSTICS_BASE" in EMULATOR_SRC
    assert "l_dynamic_prepare" in EMULATOR_SRC
    assert "l_read_iep" in EMULATOR_SRC
    assert "c26" in EMULATOR_SRC
    assert "LOOP" in EMULATOR_SRC
