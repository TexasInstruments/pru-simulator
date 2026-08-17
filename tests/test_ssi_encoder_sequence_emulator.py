"""Regression test for the repeating 12-bit SSI encoder emulator."""

from pathlib import Path

from simulator import Simulator


ROOT = Path(__file__).parent.parent


def test_ssi_encoder_sequence_emulator_repeats_distinct_values():
    """The PRU1 emulator presents one complete five-value cycle repeatedly."""
    sim = Simulator(config_path="nonexistent.cfg")
    reader = (ROOT / "source" / "ssi_reader_4mhz_12bit" / "ssi_reader_4mhz_12bit.asm").read_text()
    emulator = (ROOT / "source" / "ssi_encoder_sequence_emulator_12bit" / "ssi_encoder_sequence_emulator_12bit.asm").read_text()

    assert sim.load("pru0", reader) == []
    assert sim.load("pru1", emulator) == []
    sim.add_gpio_wire("pru0", 0, "pru1", 16)
    sim.add_gpio_wire("pru1", 0, "pru0", 8)
    sim.hard_reset()

    expected = [0xABC, 0xAAA, 0xBCA, 0x12A, 0xCC2]
    captured = []
    previous_frames = 0
    for _ in range(500_000):
        sim.step_paced("pru0", "pru1")
        frames = sim.registers("pru0")[20]
        if frames != previous_frames:
            captured.append(int.from_bytes(sim.memory_read(16, 4), "little"))
            previous_frames = frames
            if len(captured) == 10:
                break

    assert captured == expected + expected
