from pathlib import Path
from math import ceil

import pytest

from mem.constant_table import CTPPR0
from pru_io import ssi_config_abi as abi
from pru_io.ssi_runtime import SSIEmulatorRuntime
from simulator import Simulator


ROOT = Path(__file__).parents[1]


def run_board_loopback(preset, position, error, clock_delay_loops=20, config=None,
                       frame_positions=None):
    """Run both firmwares and collect coherent mailboxes after each frame."""
    sim = Simulator(config or str(ROOT / "memory.cfg"))
    source_dir = ROOT / "source"
    reader = (source_dir / "ssi_generic_reader" / "ssi_generic_reader.asm").read_text(
        encoding="utf-8")
    sim.add_gpio_wire("pru1", 0, "pru0", 0)
    sim.add_gpio_wire("pru0", 16, "pru1", 16)
    sim.set_gpio_drive_mask("pru0", (1 << 20) - 1 & ~(1 << 0))
    sim.set_gpio_drive_mask("pru1", (1 << 20) - 1 & ~(1 << 16))

    emulator = SSIEmulatorRuntime(sim, preset=preset, position=position,
                                  error_value=error)
    emulator.load()
    sim.memory.write(abi.CONFIG_ADDRESS, abi.pack_config(
        frame_bits=emulator.layout.resolution,
        clock_delay_loops=clock_delay_loops,
        # Each reader idle loop takes two cycles. Leave the same eight-loop
        # polling margin used by SSIRuntime after the configured Tm interval.
        idle_delay_loops=ceil(emulator.layout.monoflop_us
                              * sim.iep.core_clock_hz("pru1") / 2_000_000) + 8,
    ))
    # The reader reaches Shared RAM through C28, 0 until CTPPR0 is programmed.
    sim.ctrl_write("pru1", CTPPR0, 0x100)
    assert sim.load("pru1", reader, include_paths=[str(source_dir)]) == []

    mailboxes = []
    for frame_count, position in enumerate(frame_positions or [position], 1):
        emulator.set_position(position)
        for _ in range(60_000):
            sim.step("pru0", 1)
            sim.step("pru1", 1)
            mailbox = abi.unpack_mailbox(
                sim.memory_read(abi.MAILBOX_ADDRESS, abi.MAILBOX_SIZE))
            if mailbox["frame_count"] == frame_count and not mailbox["sequence"] & 1:
                mailboxes.append(mailbox)
                break
        else:
            pytest.fail(f"reader did not publish coherent frame {frame_count}")
    assert emulator.status() == 0
    return mailboxes


def test_reader_receives_fixed_word_from_second_core_firmware():
    mailbox, = run_board_loopback("RM08_12BIT_4MHZ", 0xABC, 0)

    assert mailbox["frame_count"] == 1
    assert mailbox["raw_frame_lo"] == 0xABC
    assert mailbox["raw_frame_hi"] == 0
    assert mailbox["status"] == 0


@pytest.mark.parametrize(("preset", "position", "error", "frame"), [
    ("AFS_AFM60_MULTITURN_30BIT", 0x2ABCDEF1, 0b101, 0x155E6F78D),
    ("AHS_AHM36_MULTITURN", 0x2000001, 1, 0x4000003),
])
def test_reader_receives_multiturn_preset_frames_from_emulator(
        preset, position, error, frame):
    mailbox, = run_board_loopback(preset, position, error)

    assert mailbox["frame_count"] == 1
    assert (mailbox["raw_frame_lo"], mailbox["raw_frame_hi"]) == (
        frame & 0xFFFFFFFF, frame >> 32)
    assert mailbox["status"] == 0


# At 300 MHz the reader's bit period needs at least 75 cycles for the 4 MHz RM08
# limit and 150 cycles for the 2 MHz SICK limit.
@pytest.mark.parametrize(("preset", "position", "error", "delay", "frame"), [
    ("RM08_12BIT_4MHZ", 0xABC, 0, 20, 0xABC),
    ("AFS_AFM60_MULTITURN_30BIT", 0x2ABCDEF1, 5, 40, 0x155E6F78D),
    ("AHS_AHM36_SINGLETURN", 0x1555, 1, 40, 0x2AAB),
])
def test_emulator_firmware_feeds_the_reader_at_300mhz(
        sim_config, preset, position, error, delay, frame):
    config = sim_config(pru_clock_mhz=300, pru1_clock_mhz=300)

    mailbox, = run_board_loopback(preset, position, error, delay, config=config)

    assert mailbox["frame_count"] == 1
    assert (mailbox["raw_frame_lo"], mailbox["raw_frame_hi"]) == (
        frame & 0xFFFFFFFF, frame >> 32)
    assert mailbox["status"] == 0


@pytest.mark.parametrize("reader_mhz", [200, 250, 300])
@pytest.mark.parametrize("preset, positions, error, frames", [
    ("RM08_12BIT_4MHZ", [0xABC, 0xABC, 0x123], 0, [0xABC, 0xABC, 0x123]),
    ("AFS_AFM60_MULTITURN_30BIT", [0x2ABCDEF1, 0x2ABCDEF1, 0x12345678], 5,
     [0x155E6F78D, 0x155E6F78D, 0x91A2B3C5]),
    ("TTK70", [0x15, 0x15, 0x6], 2, [0x7E, 0x7E, 0x16]),
])
def test_board_loopback_receives_consecutive_frames_and_position_updates(
        sim_config, reader_mhz, preset, positions, error, frames):
    config = sim_config(pru_clock_mhz=250, pru1_clock_mhz=reader_mhz,
                        iep_clock_mhz=200)
    mailboxes = run_board_loopback(preset, positions[0], error, 40, config,
                                   frame_positions=positions)
    assert [mailbox["raw_frame_lo"] | mailbox["raw_frame_hi"] << 32
            for mailbox in mailboxes] == frames
    assert [mailbox["frame_count"] for mailbox in mailboxes] == [1, 2, 3]
    assert all(mailbox["status"] == 0 for mailbox in mailboxes)
