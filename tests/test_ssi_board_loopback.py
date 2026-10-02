from pathlib import Path

from pru_io import ssi_config_abi as abi
from simulator import Simulator


ROOT = Path(__file__).parents[1]


def test_reader_receives_fixed_word_from_second_core_firmware():
    sim = Simulator(str(ROOT / "memory.cfg"))
    source_dir = ROOT / "source"
    emulator = (source_dir / "ssi_generic_emulator.asm").read_text(encoding="utf-8")
    reader = (source_dir / "ssi_generic_reader" / "ssi_generic_reader.asm").read_text(
        encoding="utf-8")
    sim.add_gpio_wire("pru1", 0, "pru0", 0)
    sim.add_gpio_wire("pru0", 16, "pru1", 16)
    sim.set_gpio_drive_mask("pru0", (1 << 20) - 1 & ~(1 << 0))
    sim.set_gpio_drive_mask("pru1", (1 << 20) - 1 & ~(1 << 16))

    assert sim.load("pru0", emulator, include_paths=[str(source_dir)]) == []
    sim.memory.write(abi.CONFIG_ADDRESS, abi.pack_config(
        frame_bits=12,
        clock_delay_loops=20,
        idle_delay_loops=8,
    ))
    assert sim.load("pru1", reader, include_paths=[str(source_dir)]) == []

    mailbox = abi.unpack_mailbox(
        sim.memory_read(abi.MAILBOX_ADDRESS, abi.MAILBOX_SIZE))
    for _ in range(20_000):
        if mailbox["frame_count"] >= 1:
            break
        sim.step("pru0", 1)
        sim.step("pru1", 1)
        mailbox = abi.unpack_mailbox(
            sim.memory_read(abi.MAILBOX_ADDRESS, abi.MAILBOX_SIZE))

    assert mailbox["frame_count"] == 1
    assert mailbox["raw_frame"] == 0xABC
    assert mailbox["status"] == 0
