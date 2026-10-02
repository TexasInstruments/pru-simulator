from fractions import Fraction
from pathlib import Path
import shutil

import pytest

from pru_io.ssi_runtime import SSIRuntime
from simulator import Simulator


def test_reader_firmware_runs_through_normal_simulator_execution():
    sim = Simulator("memory.cfg")
    initial_drive_mask = sim.io("pru1")["gpo_drive_mask"]
    runtime = SSIRuntime(sim, position=0xABC, resolution=12)

    runtime.load(clock_delay_loops=20)
    run = runtime.run_until_frames(1, max_steps=16_000)
    runtime.step(6_000)  # allow the encoder's Tm guard to expire

    assert run["reached"] is True
    assert run["mailbox"]["raw_frame"] == 0xABC
    assert run["mailbox"]["frame_count"] == 1
    assert runtime.encoder.faults() == []
    assert runtime.encoder.events()[0]["position"] == 0xABC

    runtime.close()
    assert sim.io("pru1")["gpo_drive_mask"] == initial_drive_mask


def test_reader_runtime_supports_gray_encoder_and_multiple_frames():
    sim = Simulator("memory.cfg")
    runtime = SSIRuntime(sim, position=0b1101, resolution=4,
                         encoding="gray", f_max_hz=4_000_000)
    runtime.load(clock_delay_loops=40)

    first = runtime.run_until_frames(1, max_steps=12_000)
    runtime.encoder.set_position(0b0110)
    second = runtime.run_until_frames(2, max_steps=12_000)
    runtime.step(6_000)  # allow the second frame's Tm guard to expire

    assert first["reached"] is True
    assert first["mailbox"]["raw_frame"] == (0b1101 ^ (0b1101 >> 1))
    assert second["reached"] is True
    assert second["mailbox"]["raw_frame"] == (0b0110 ^ (0b0110 >> 1))
    assert second["mailbox"]["frame_count"] == 2
    assert runtime.encoder.faults() == []
    assert len([event for event in runtime.encoder.events()
                if event["kind"] == "frame"]) == 2


@pytest.mark.parametrize("close_order", [(0, 1), (1, 0)])
def test_overlapping_reader_runtimes_restore_output_after_last_close(close_order):
    sim = Simulator("memory.cfg")
    bit = 1 << 16
    initial_mask = sim.io("pru1")["gpo_drive_mask"]
    runtimes = [SSIRuntime(sim, name="axis_a"), SSIRuntime(sim, name="axis_b")]

    assert sim.io("pru1")["gpo_drive_mask"] == initial_mask & ~bit

    runtimes[close_order[0]].close()
    assert sim.io("pru1")["gpo_drive_mask"] == initial_mask & ~bit
    runtimes[close_order[1]].close()

    assert sim.io("pru1")["gpo_drive_mask"] == initial_mask


def test_reader_runtime_preserves_data_pin_that_was_initially_an_input():
    sim = Simulator("memory.cfg")
    bit = 1 << 16
    initial_mask = sim.io("pru1")["gpo_drive_mask"] & ~bit
    sim.set_gpio_drive_mask("pru1", initial_mask)

    runtime = SSIRuntime(sim)
    runtime.close()

    assert sim.io("pru1")["gpo_drive_mask"] == initial_mask


def test_reader_runtime_uses_exact_selected_core_clock(tmp_path):
    config = tmp_path / "clock.cfg"
    config.write_text(
        "[device]\n"
        "pru_clock_mhz = 200.123456789123456\n"
        "pru1_clock_mhz = 201.987654321987654\n",
        encoding="utf-8",
    )
    sim = Simulator(str(config))

    runtime = SSIRuntime(sim, core="pru1")

    assert runtime.encoder.core_clock_hz == Fraction("201.987654321987654") * 1_000_000
    runtime.close()


def test_documented_300mhz_run_uses_copied_config_and_constants_sidecar(tmp_path):
    project = Path(__file__).resolve().parents[1]
    data = (project / "memory.cfg").read_bytes()
    data = data.replace(b"pru_clock_mhz = 250", b"pru_clock_mhz = 300", 1)
    data = data.replace(b"pru1_clock_mhz = 250", b"pru1_clock_mhz = 300", 1)
    config = tmp_path / "memory.cfg"
    config.write_bytes(data)
    sidecar = tmp_path / "config"
    sidecar.mkdir()
    shutil.copy2(project / "config" / "constants_am243x.cfg",
                 sidecar / "constants_am243x.cfg")

    sim = Simulator(str(config))
    assert sim.iep.core_clock_hz("pru1") == Fraction(300_000_000)
    assert sim.constant_table.resolve(28) == 0x00010000

    with SSIRuntime(sim, position=0xABC, resolution=12,
                    f_max_hz=4_000_000, monoflop_us=20.5) as runtime:
        runtime.load(clock_delay_loops=20)
        result = runtime.run_until_frames(1, max_steps=20_000)

    assert result["reached"] is True
    assert result["mailbox"]["raw_frame"] == 0xABC
