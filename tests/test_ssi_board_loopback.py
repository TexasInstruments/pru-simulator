from pathlib import Path

import pytest

from pru_io import ssi_config_abi as abi
from pru_io.ssi_runtime import SSIEmulatorRuntime
from simulator import Simulator


ROOT = Path(__file__).parents[1]


def run_board_loopback(preset, position, error, clock_delay_loops=20):
    """Run the emulator on PRU0 and the reader on PRU1 and return the mailbox."""
    sim = Simulator(str(ROOT / "memory.cfg"))
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
        idle_delay_loops=8,
    ))
    assert sim.load("pru1", reader, include_paths=[str(source_dir)]) == []

    mailbox = abi.unpack_mailbox(
        sim.memory_read(abi.MAILBOX_ADDRESS, abi.MAILBOX_SIZE))
    for _ in range(60_000):
        if mailbox["frame_count"] >= 1:
            break
        sim.step("pru0", 1)
        sim.step("pru1", 1)
        mailbox = abi.unpack_mailbox(
            sim.memory_read(abi.MAILBOX_ADDRESS, abi.MAILBOX_SIZE))
    assert emulator.status() == 0
    return mailbox


def test_reader_receives_fixed_word_from_second_core_firmware():
    mailbox = run_board_loopback("CUSTOM_LEGACY_12BIT_4MHZ", 0xABC, 0)

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
    mailbox = run_board_loopback(preset, position, error)

    assert mailbox["frame_count"] == 1
    assert (mailbox["raw_frame_lo"], mailbox["raw_frame_hi"]) == (
        frame & 0xFFFFFFFF, frame >> 32)
    assert mailbox["status"] == 0
