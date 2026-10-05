"""The emulator firmware is checked against an independent Python SSI master."""
from pathlib import Path
import struct

import pytest

from pru_io import ssi_config_abi as abi
from pru_io.ssi_runtime import SSIEmulatorRuntime
from simulator import Simulator

ROOT = Path(__file__).parents[1]
PHASE_STEPS = 40  # PRU cycles per clock phase; far above the firmware's needs


def read_frame(sim: Simulator, bits: int) -> int:
    """Clock one frame into PRU0 as an SSI master and return the sampled word."""
    sim.set_input("pru0", 0, True)
    sim.step("pru0", PHASE_STEPS)
    sim.set_input("pru0", 0, False)  # first falling edge latches the frame
    sim.step("pru0", PHASE_STEPS)
    word = 0
    for _ in range(bits):
        sim.set_input("pru0", 0, True)  # each rising edge presents a bit
        sim.step("pru0", PHASE_STEPS)
        word = (word << 1) | sim.io("pru0")["gpo_pins"][16]
        sim.set_input("pru0", 0, False)
        sim.step("pru0", PHASE_STEPS)
    sim.set_input("pru0", 0, True)
    sim.step("pru0", PHASE_STEPS)
    return word


def independent_frame(position, error, position_bits, error_bits, gray=False):
    """Frame word with the error bits last, written without the model."""
    if gray:
        position ^= position >> 1
    assert position < 1 << position_bits and error < 1 << max(error_bits, 1)
    return (position << error_bits) | error


@pytest.fixture
def sim():
    return Simulator(str(ROOT / "memory.cfg"))


# preset, frame bits, position bits, error bits, position, error
CASES = [
    ("CUSTOM_LEGACY_12BIT_4MHZ", 12, 12, 0, 0xABC, 0),
    ("AFS_AFM60_MULTITURN_30BIT", 33, 30, 3, 0x2ABCDEF1, 0b101),
    ("AHS_AHM36_MULTITURN", 27, 26, 1, 0x3FFFFFF, 1),
    ("TTK70", 26, 24, 2, 0x800001, 0b10),
]


@pytest.mark.parametrize(
    ("preset", "frame_bits", "position_bits", "error_bits", "position", "error"),
    CASES)
def test_emulator_shifts_the_host_packed_frame_out_msb_first(
        sim, preset, frame_bits, position_bits, error_bits, position, error):
    emulator = SSIEmulatorRuntime(sim, preset=preset, position=position,
                                  error_value=error)
    emulator.load()

    word = read_frame(sim, frame_bits)

    assert word == independent_frame(position, error, position_bits, error_bits)
    assert emulator.status() == 0


def test_emulator_sends_64_bit_frames_and_gray_positions(sim):
    emulator = SSIEmulatorRuntime(
        sim, resolution=64, position=0xDEADBEEF12345678, encoding="gray")
    emulator.load()

    word = read_frame(sim, 64)

    assert word == 0xDEADBEEF12345678 ^ (0xDEADBEEF12345678 >> 1)


def test_host_changes_between_frames_reach_the_next_frame(sim):
    emulator = SSIEmulatorRuntime(sim, preset="AFS_AFM60_SINGLETURN",
                                  position=0x12345, error_value=0)
    emulator.load()

    first = read_frame(sim, 21)
    emulator.set_position(0x3FFFF)
    emulator.set_error(0b110)
    second = read_frame(sim, 21)

    assert first == independent_frame(0x12345, 0, 18, 3)
    assert second == independent_frame(0x3FFFF, 0b110, 18, 3)


@pytest.mark.parametrize(("version", "frame_bits"), [
    (abi.ABI_VERSION + 1, 12), (abi.ABI_VERSION, 0),
    (abi.ABI_VERSION, 65), (0, 0),
])
def test_emulator_halts_with_status_one_on_invalid_config(sim, version, frame_bits):
    emulator = SSIEmulatorRuntime(sim)
    emulator.load()
    sim.memory.write(abi.EMULATOR_ADDRESS,
                     struct.pack("<IIIII", version, frame_bits, 0, 0, 0))

    read_frame(sim, 1)

    assert emulator.status() == 1
