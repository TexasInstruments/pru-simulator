from __future__ import annotations

import pytest

from pru_io.ssi_encoder_model import SSIEncoderModel


def pin_word(pin: int, level: int) -> int:
    return level << pin


def tick(model: SSIEncoderModel, cycle: int, clock: int) -> int:
    drive_mask, drive_values = model.tick(cycle, pin_word(model.clock_pin, clock))
    assert drive_mask & (1 << model.data_pin)
    return (drive_values >> model.data_pin) & 1


def send_word(model: SSIEncoderModel, start: int, period: int) -> tuple[list[int], int]:
    """Send one complete SSI word; return sampled data and final falling cycle."""
    tick(model, start, 1)
    cycle = start + period // 2
    tick(model, cycle, 0)
    sampled = []
    for _ in range(model.resolution):
        cycle += period // 2
        sampled.append(tick(model, cycle, 1))
        cycle += period // 2
        tick(model, cycle, 0)
    return sampled, cycle


def send_word_without_final_fall(model: SSIEncoderModel, start: int,
                                 period: int) -> tuple[list[int], int, int]:
    """Send one word and leave CLK high after sampling its final bit."""
    tick(model, start, 1)
    cycle = start + period // 2
    tick(model, cycle, 0)
    sampled = []
    for index in range(model.resolution):
        cycle += period // 2
        sampled.append(tick(model, cycle, 1))
        if index + 1 < model.resolution:
            cycle += period // 2
            tick(model, cycle, 0)
    return sampled, cycle - period // 2, cycle


def complete_events(model: SSIEncoderModel) -> list[dict]:
    return [event for event in model.events() if event["kind"] == "frame"]


def test_ssi_latches_on_first_falling_and_presents_msb_on_rising_edges():
    model = SSIEncoderModel(resolution=8, position=0xA5,
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1)

    sampled, last_falling = send_word(model, start=0, period=20)
    for cycle in range(last_falling + 1, last_falling + model.monoflop_cycles):
        tick(model, cycle, 0)
    tick(model, last_falling + model.monoflop_cycles, 0)

    assert sampled == [int(bit) for bit in f"{0xA5:08b}"]
    assert complete_events(model) == [{
        "cycle": last_falling - 10,
        "kind": "frame",
        "raw_value": 0xA5,
        "position": 0xA5,
        "resolution": 8,
        "encoding": "binary",
    }]
    assert model.get_state()["state"] == "idle"


def test_nth_rising_edge_completes_word_without_a_final_falling_edge():
    model = SSIEncoderModel(resolution=8, position=0xA5,
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1)

    sampled, last_fall, final_rise = send_word_without_final_fall(
        model, start=0, period=20)
    assert sampled == [int(bit) for bit in f"{0xA5:08b}"]
    assert final_rise - last_fall == 10
    assert model.get_state()["state"] == "guard"

    tick(model, last_fall + model.monoflop_cycles, 1)

    assert model.faults() == []
    assert complete_events(model)[0]["cycle"] == final_rise
    assert model.get_state()["state"] == "idle"


def test_reader_final_fall_then_high_idle_is_a_valid_completed_word():
    model = SSIEncoderModel(resolution=8, position=0xA5,
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1)

    sampled, last_fall = send_word(model, start=0, period=20)
    tick(model, last_fall + 1, 1)  # reader returns CLK high after the final fall
    tick(model, last_fall + model.monoflop_cycles, 1)

    assert sampled == [int(bit) for bit in f"{0xA5:08b}"]
    assert model.faults() == []
    assert complete_events(model)[0]["cycle"] == last_fall - 10


def test_lsb_is_sampled_high_then_data_stays_low_until_tm_expires():
    model = SSIEncoderModel(resolution=1, position=1, idle_value=1,
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1)
    tick(model, 0, 1)
    tick(model, 1, 0)  # latch the word

    assert tick(model, 11, 1) == 1  # LSB remains available in the high phase
    assert tick(model, 12, 0) == 0  # final falling edge starts Tm and clears data
    assert tick(model, 13, 1) == 0  # return to high clock idle does not raise data
    assert tick(model, 111, 1) == 0
    assert tick(model, 112, 1) == 1  # idle level returns exactly at Tm

    assert model.faults() == []
    assert complete_events(model)[0]["raw_value"] == 1


def test_additional_full_pulse_after_reader_idle_return_faults():
    model = SSIEncoderModel(resolution=2, position=0b10,
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1)
    _, last_fall = send_word(model, start=0, period=20)

    tick(model, last_fall + 1, 1)  # accepted return to idle high
    tick(model, last_fall + 11, 0)
    tick(model, last_fall + 21, 1)  # genuine extra clock pulse

    assert any("past the end" in fault for fault in model.faults())
    assert complete_events(model) == []


def test_gray_encoding_presents_encoded_bits_but_reports_binary_position():
    model = SSIEncoderModel(resolution=4, position=0b1101, encoding="gray",
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1)

    sampled, last_falling = send_word(model, start=0, period=20)
    for cycle in range(last_falling + 1, last_falling + model.monoflop_cycles + 1):
        tick(model, cycle, 0)

    assert sampled == [int(bit) for bit in f"{0b1011:04b}"]
    assert complete_events(model)[0]["position"] == 0b1101
    assert complete_events(model)[0]["raw_value"] == 0b1011


def test_exact_monoflop_boundary_from_last_falling_allows_a_new_frame():
    model = SSIEncoderModel(resolution=4, position=0b1001,
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1)
    _, last_falling = send_word(model, start=0, period=20)

    tick(model, last_falling + model.monoflop_cycles - 1, 0)
    assert model.get_state()["state"] == "guard"
    tick(model, last_falling + model.monoflop_cycles, 0)
    assert model.get_state()["state"] == "idle"
    tick(model, last_falling + model.monoflop_cycles + 1, 1)
    tick(model, last_falling + model.monoflop_cycles + 2, 0)

    assert model.get_state()["state"] == "active"
    assert not any("premature" in fault for fault in model.faults())
    assert len(complete_events(model)) == 1


def test_idle_timeout_is_time_driven_and_restores_high_after_zero_bit():
    model = SSIEncoderModel(resolution=2, position=0b01,
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1, idle_value=1)
    tick(model, 0, 1)
    tick(model, 1, 0)
    assert tick(model, 11, 1) == 0
    tick(model, 21, 0)

    for cycle in range(22, 21 + model.monoflop_cycles):
        data = tick(model, cycle, 0)
    data = tick(model, 21 + model.monoflop_cycles, 0)

    assert data == 1
    assert model.get_state()["state"] == "idle"
    assert any("monoflop timeout" in fault for fault in model.faults())
    assert complete_events(model) == []


def test_too_fast_clocking_faults_and_does_not_publish_a_frame():
    model = SSIEncoderModel(resolution=4, position=0b1010,
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1)

    sampled, last_falling = send_word(model, start=0, period=4)
    for cycle in range(last_falling + 1, last_falling + model.monoflop_cycles + 1):
        tick(model, cycle, 0)

    assert any("f_max" in fault for fault in model.faults())
    assert complete_events(model) == []


def test_clocking_past_resolution_faults_and_does_not_publish_a_frame():
    model = SSIEncoderModel(resolution=2, position=0b10,
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1)
    tick(model, 0, 1)
    tick(model, 1, 0)
    tick(model, 11, 1)
    tick(model, 21, 0)
    tick(model, 31, 1)
    tick(model, 41, 0)
    tick(model, 51, 1)
    tick(model, 61, 0)
    tick(model, 71, 1)

    assert any("past the end" in fault for fault in model.faults())
    assert complete_events(model) == []


@pytest.mark.parametrize("kwargs", [
    {"resolution": 0},
    {"resolution": 65},
    {"position": -1},
    {"resolution": 4, "position": 16},
    {"encoding": "bcd"},
    {"f_max_hz": 0},
    {"monoflop_us": 0},
    {"clock_pin": 2, "data_pin": 2},
])
def test_invalid_model_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        SSIEncoderModel(**kwargs)


@pytest.mark.parametrize("idle_value", [0.0, 1.0, False, True])
def test_idle_value_requires_an_integer_zero_or_one(idle_value):
    with pytest.raises(ValueError, match="idle_value"):
        SSIEncoderModel(idle_value=idle_value)


def test_snapshot_restores_frame_progress_and_reports():
    model = SSIEncoderModel(resolution=8, position=0xA5,
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1)
    tick(model, 0, 1)
    tick(model, 1, 0)
    tick(model, 11, 1)
    snap = model.snapshot()
    tick(model, 21, 0)
    model.reset()
    model.restore(snap)

    assert model.get_state()["state"] == "active"
    assert model.get_state()["bits_clocked"] == 1
    assert model.faults() == []
    assert model.events() == []


def clock_frame(model: SSIEncoderModel, start: int = 0, period: int = 20) -> int:
    """Clock one word and return the bits the encoder presented."""
    sampled, last_falling = send_word(model, start=start, period=period)
    for cycle in range(last_falling + 1, last_falling + model.monoflop_cycles + 1):
        tick(model, cycle, 0)
    return int("".join(map(str, sampled)), 2)


def test_error_field_is_packed_after_the_position_and_decoded_in_events():
    model = SSIEncoderModel(resolution=8, position=0b10110, error_bits=3,
                            error_value=0b101, core_clock_hz=100_000_000,
                            f_max_hz=10_000_000, monoflop_us=1)

    assert (model.position_bits, model.position_offset,
            model.error_offset) == (5, 3, 0)
    assert clock_frame(model) == 0b10110_101
    assert complete_events(model)[0]["position"] == 0b10110
    assert complete_events(model)[0]["error"] == 0b101
    assert complete_events(model)[0]["raw_value"] == 0b10110_101


def test_wide_multiturn_and_over_32_bit_frames_keep_every_bit():
    model = SSIEncoderModel(resolution=33, position=(1 << 30) - 2, error_bits=3,
                            error_value=0b011, core_clock_hz=100_000_000,
                            f_max_hz=10_000_000, monoflop_us=1)

    word = clock_frame(model)

    assert word == (((1 << 30) - 2) << 3) | 0b011
    assert word >> 32 == 1
    event = complete_events(model)[0]
    assert (event["raw_value"], event["position"], event["error"]) == (
        word, (1 << 30) - 2, 0b011)
    assert model.faults() == []


def test_gray_encoding_applies_to_the_position_field_not_the_error_field():
    model = SSIEncoderModel(resolution=7, position=0b1101, encoding="gray",
                            position_bits=4, error_bits=3, error_value=0b101,
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1)

    assert clock_frame(model) == (0b1011 << 3) | 0b101
    event = complete_events(model)[0]
    assert (event["position"], event["error"]) == (0b1101, 0b101)


def test_explicit_offsets_place_a_leading_error_and_a_64_bit_frame():
    model = SSIEncoderModel(resolution=64, position=(1 << 59) + 5,
                            position_bits=60, error_bits=4, error_offset=60,
                            error_value=0b1001, core_clock_hz=1_000_000_000,
                            f_max_hz=10_000_000, monoflop_us=1)

    assert model.position_offset == 0
    assert clock_frame(model, period=200) == (0b1001 << 60) | ((1 << 59) + 5)


def test_position_and_error_can_change_between_frames():
    model = SSIEncoderModel(resolution=8, position=1, error_bits=2,
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1)
    first = clock_frame(model, start=0)
    model.set_position(0b111111)
    model.set_error(0b10)
    second = clock_frame(model, start=10_000)

    assert (first, second) == (0b000001_00, 0b111111_10)
    assert [(event["position"], event["error"])
            for event in complete_events(model)] == [(1, 0), (0b111111, 0b10)]


def test_snapshot_restores_the_error_field():
    model = SSIEncoderModel(resolution=8, error_bits=2, error_value=0b11)
    snap = model.snapshot()
    model.set_error(0)
    model.restore(snap)

    assert model.error == 0b11


@pytest.mark.parametrize("kwargs", [
    {"resolution": 8, "error_bits": 8},
    {"resolution": 8, "error_bits": -1},
    {"resolution": 8, "position_bits": 9},
    {"resolution": 8, "position_bits": 0},
    {"resolution": 8, "error_bits": 2, "error_value": 4},
    {"resolution": 8, "error_value": 1},
    {"resolution": 8, "position_bits": 6, "position": 64},
    {"resolution": 8, "position_bits": 4, "position_offset": 5},
    {"resolution": 8, "error_bits": 2, "error_offset": 7},
    {"resolution": 8, "position_bits": 4, "error_bits": 2,
     "position_offset": 0, "error_offset": 3},
    {"resolution": 8, "error_bits": True},
    {"resolution": 8, "error_value": True},
    {"resolution": 8, "error_bits": 1, "error_value": -1},
])
def test_invalid_frame_layouts_are_rejected(kwargs):
    with pytest.raises(ValueError):
        SSIEncoderModel(**kwargs)


@pytest.mark.parametrize("restored", [False, True])
def test_first_low_sample_is_idle_then_literal_waveform_latches_cleanly(restored):
    # SICK IM0100079 / 8027422 (2022-02-08), section 2.2,
    # p.5 Figure2: first falling edge latches; each rise presents next MSB.
    # https://www.sick.com/media/docs/9/79/079/technical_information_ssi_interface_description_en_im0100079.pdf
    model = SSIEncoderModel(resolution=8, position=0xA5,
                            core_clock_hz=100_000_000, f_max_hz=10_000_000,
                            monoflop_us=1)
    model.reset()
    if restored:
        model.restore(model.snapshot())
    assert tick(model, 0, 0) == 1
    assert tick(model, 150, 0) == 1
    assert model.faults() == []
    assert model.get_state()["state"] == "idle"
    assert tick(model, 160, 1) == 1
    # Explicit clock/data oracle; high-phase samples are included separately.
    waveform = [
        (170, 0, 1), (180, 1, 1), (185, 1, 1),
        (190, 0, 1), (200, 1, 0), (205, 1, 0),
        (210, 0, 0), (220, 1, 1), (225, 1, 1),
        (230, 0, 1), (240, 1, 0), (245, 1, 0),
        (250, 0, 0), (260, 1, 0), (265, 1, 0),
        (270, 0, 0), (280, 1, 1), (285, 1, 1),
        (290, 0, 1), (300, 1, 0), (305, 1, 0),
        (310, 0, 0), (320, 1, 1), (325, 1, 1),
        (330, 0, 0), (340, 1, 0), (429, 1, 0), (430, 1, 1),
    ]
    sampled = []
    for cycle, clock, data in waveform:
        actual = tick(model, cycle, clock)
        assert actual == data, (cycle, clock)
        if cycle in (180, 200, 220, 240, 260, 280, 300, 320):
            sampled.append(actual)
        if cycle == 185:
            model.set_position(0)
    assert sampled == [1, 0, 1, 0, 0, 1, 0, 1]
    assert complete_events(model)[0]["raw_value"] == 0xA5
    assert model.faults() == []


def test_restore_of_initialized_clock_preserves_real_next_edge():
    model = SSIEncoderModel(resolution=8, position=0xA5)
    tick(model, 0, 1)
    snapshot = model.snapshot()
    model.reset()
    model.restore(snapshot)
    tick(model, 100, 0)
    assert model.get_state()["state"] == "active"
