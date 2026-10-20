from fractions import Fraction
from pathlib import Path
import shutil
import struct

import pytest

from pru_io import ssi_config_abi as abi
from pru_io.ssi_runtime import SSIEmulatorRuntime, SSIRuntime
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

    assert sim.io("pru1")["gpo_drive_mask"] == (initial_mask & ~bit) | 1

    runtimes[close_order[0]].close()
    assert sim.io("pru1")["gpo_drive_mask"] == (initial_mask & ~bit) | 1
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


# preset: (frame bits, position bits, error bits); values written from the
# SICK document, not read back from the model under test.
PRESET_CASES = [
    ("RM08_12BIT_4MHZ", 12, 12, 0),
    ("AHS_AHM36_SINGLETURN", 15, 14, 1),
    ("AFS_AFM60_SINGLETURN", 21, 18, 3),
    ("AFS_AFM60_MULTITURN_30BIT", 33, 30, 3),
    ("TTK70", 26, 24, 2),
    ("KH53", 24, 24, 0),
]


@pytest.mark.parametrize(("preset", "frame_bits", "position_bits", "error_bits"),
                         PRESET_CASES)
def test_reader_publishes_both_mailbox_words_for_each_preset(
        preset, frame_bits, position_bits, error_bits):
    sim = Simulator("memory.cfg")
    position = 0xA5A5A5A5A5A5A5A5 & ((1 << position_bits) - 1)
    error = 0b101 & ((1 << error_bits) - 1)
    # Vendor Gray position literal for 0xA5A5A5 is 0xF77777; error bits last.
    expected = {"TTK70": 0x3DDDDDD, "KH53": 0xF77777}.get(
        preset, (position << error_bits) | error)

    with SSIRuntime(sim, preset=preset, position=position,
                    error_value=error) as runtime:
        runtime.load(clock_delay_loops=40)
        result = runtime.run_until_frames(1, max_steps=40_000)
        runtime.step(10_000)  # let the encoder's Tm guard expire
        mailbox = runtime.mailbox()
        events = runtime.encoder.events()
        faults = runtime.encoder.faults()

    assert result["reached"] is True
    assert (mailbox["raw_frame_lo"], mailbox["raw_frame_hi"]) == (
        expected & 0xFFFFFFFF, expected >> 32)
    assert (mailbox["raw_frame"], mailbox["position"], mailbox["error"]) == (
        expected, position, error)
    assert mailbox["status"] == 0
    assert faults == []
    # A short tm lets the reader finish another frame while the test waits.
    assert events and all(event["raw_value"] == expected for event in events)


def test_reader_runtime_decodes_gray_position_next_to_an_error_field():
    sim = Simulator("memory.cfg")

    with SSIRuntime(sim, preset="AFS_AFM60_SINGLETURN", position=0x2AAAA,
                    error_value=0b010, encoding="gray") as runtime:
        runtime.load(clock_delay_loops=40)
        runtime.run_until_frames(1, max_steps=40_000)
        runtime.step(10_000)
        mailbox = runtime.mailbox()

    assert mailbox["raw_frame"] == ((0x2AAAA ^ 0x15555) << 3) | 0b010
    assert (mailbox["position"], mailbox["error"]) == (0x2AAAA, 0b010)


def test_reader_runtime_rejects_unknown_preset():
    with pytest.raises(ValueError, match="unknown SSI preset"):
        SSIRuntime(Simulator("memory.cfg"), preset="NOT_A_PRESET")


@pytest.mark.parametrize("config", [
    struct.pack("<IIII", abi.ABI_VERSION, 0, 20, 8),
    struct.pack("<IIII", abi.ABI_VERSION, 65, 20, 8),
    struct.pack("<IIII", abi.ABI_VERSION - 1, 12, 20, 8),
])
def test_reader_halts_with_status_one_on_invalid_config(config):
    sim = Simulator("memory.cfg")
    runtime = SSIRuntime(sim)
    sim.memory.write(abi.CONFIG_ADDRESS, config)
    firmware = Path("source/ssi_generic_reader/ssi_generic_reader.asm")
    assert sim.load("pru1", firmware.read_text(encoding="utf-8"),
                    include_paths=["source"]) == []
    runtime._loaded = True

    runtime.step(100)

    assert runtime.mailbox()["status"] == 1
    assert runtime.mailbox()["frame_count"] == 0
    runtime.close()


@pytest.mark.parametrize(("raw", "encoding", "position_bits", "error_bits", "position", "error"), [
    (0xA5, "binary", 8, 0, 0xA5, 0),
    (0x17, "gray", 4, 2, 6, 3),
    (0x123456789ABCDEF0, "binary", 60, 4, 0x123456789ABCDEF, 0),
])
def test_mailbox_decodes_literal_word_without_model_decoder(
        raw, encoding, position_bits, error_bits, position, error, monkeypatch):
    sim = Simulator("memory.cfg")
    with SSIRuntime(sim, resolution=position_bits + error_bits,
                    position_bits=position_bits, error_bits=error_bits,
                    encoding=encoding) as runtime:
        def forbidden(*args):
            raise AssertionError("host must decode independently of the model")
        monkeypatch.setattr(runtime.encoder, "decode_frame", forbidden)
        sim.memory.write(abi.MAILBOX_ADDRESS,
                         struct.pack("<IIIII", 2, raw & 0xFFFFFFFF,
                                     raw >> 32, 1, 0))
        mailbox = runtime.mailbox()
        assert mailbox["coherent"] is True
        assert (mailbox["raw_frame"], mailbox["position"], mailbox["error"]) == (
            raw, position, error)


def test_odd_mailbox_is_diagnostic_only_and_does_not_advance():
    sim = Simulator("memory.cfg")
    with SSIRuntime(sim) as runtime:
        runtime.load()
        sim.memory.write(abi.MAILBOX_ADDRESS,
                         struct.pack("<IIIII", 3, 0xA5, 0, 99, 0))
        before = sim.status()
        mailbox = runtime.mailbox()
        assert mailbox["coherent"] is False
        assert mailbox["sequence"] == 3
        assert mailbox["frame_count"] == 99
        assert (mailbox["raw_frame"], mailbox["position"], mailbox["error"]) == (
            None, None, None)
        assert sim.status() == before
        result = runtime.run_until_frames(1, max_steps=0)
        assert result["reached"] is False
        assert result["steps"] == 0
        sim.memory.write(abi.MAILBOX_ADDRESS, struct.pack("<IIIII", 4, 0xA5, 0, 1, 0))
        assert runtime.mailbox()["raw_frame"] == 0xA5


def test_mailbox_rejects_sequence_change_during_body_read(monkeypatch):
    sim = Simulator("memory.cfg")
    with SSIRuntime(sim) as runtime:
        sim.memory.write(abi.MAILBOX_ADDRESS,
                         struct.pack("<IIIII", 2, 0xA5, 0, 1, 0))
        original = sim.memory_read
        def read(address, size):
            data = original(address, size)
            if size == abi.MAILBOX_SIZE:
                sim.memory.write(abi.MAILBOX_ADDRESS, struct.pack("<I", 4))
            return data
        monkeypatch.setattr(sim, "memory_read", read)
        mailbox = runtime.mailbox()
        assert mailbox["coherent"] is False
        assert mailbox["raw_frame"] is None


def test_reader_leases_clock_output_and_data_input_then_restores():
    sim = Simulator("memory.cfg")
    initial = 1 << 16
    sim.set_gpio_drive_mask("pru1", initial)
    runtime = SSIRuntime(sim)
    assert sim.io("pru1")["gpo_drive_mask"] == 1
    runtime.close()
    assert sim.io("pru1")["gpo_drive_mask"] == initial


def test_emulator_leases_data_output_and_clock_input_then_restores():
    from pru_io.ssi_runtime import SSIEmulatorRuntime
    sim = Simulator("memory.cfg")
    initial = 1
    sim.set_gpio_drive_mask("pru0", initial)
    runtime = SSIEmulatorRuntime(sim)
    runtime.load()
    assert sim.io("pru0")["gpo_drive_mask"] == 1 << 16
    runtime.close()
    assert sim.io("pru0")["gpo_drive_mask"] == initial


@pytest.mark.parametrize("runtime_class, core, peer", [
    (SSIRuntime, "pru1", "pru0"),
    (SSIEmulatorRuntime, "pru0", "pru1"),
])
def test_successful_helper_load_resets_selected_core_and_rebases_its_time(
        runtime_class, core, peer):
    sim = Simulator("memory.cfg")
    sim.iep.global_cfg = 0x11
    assert sim.load(peer, "nop\njmp 0") == []
    sim.step(peer, 30)
    assert sim.load(core, "nop\nhalt") == []
    sim.step(core, 2)
    assert sim.cores[core].halted
    peer_before = sim.status()[peer]
    count_before = sim.iep.count

    runtime = runtime_class(sim, core=core)
    try:
        runtime.load()
        assert sim.cores[core].pc == 0
        assert sim.cores[core].halted is False
        assert sim.cores[core].counters.cycles == 0
        assert sim.status()[peer] == peer_before
        assert sim.iep.count == count_before
        sim.step(core, 2)
        assert sim.iep.count > count_before
    finally:
        runtime.close()


@pytest.mark.parametrize("failure", ["assembly errors", "assembly exception", "config"])
def test_failed_initial_reader_load_closes_only_its_resources(monkeypatch, failure):
    sim = Simulator("memory.cfg")
    existing = SSIRuntime(sim, name="existing")
    initial_mask = sim.io("pru1")["gpo_drive_mask"]
    runtime = SSIRuntime(sim, name="failed")
    if failure == "assembly errors":
        monkeypatch.setattr(sim, "load", lambda *args, **kwargs: ["original error"])
        expected = ValueError
    elif failure == "assembly exception":
        def fail(*args, **kwargs):
            raise OSError("original error")
        monkeypatch.setattr(sim, "load", fail)
        expected = OSError
    else:
        expected = ValueError

    with pytest.raises(expected, match="unsigned 32-bit|original error"):
        runtime.load(clock_delay_loops=-1 if failure == "config" else 20)

    assert sim.device_bus.devices == [existing.encoder]
    assert sim.io("pru1")["gpo_drive_mask"] == initial_mask
    assert id(runtime.encoder) not in sim.device_bus._lease_owners
    with pytest.raises(RuntimeError, match="closed"):
        runtime.load()
    existing.close()
    assert sim.io("pru1")["gpo_drive_mask"] == 0


@pytest.mark.parametrize("failure", ["errors", "exception", "invalid config"])
def test_failed_reader_reload_restores_last_successful_config(monkeypatch, failure):
    sim = Simulator("memory.cfg")
    with SSIRuntime(sim, position=0xABC) as runtime:
        runtime.load(clock_delay_loops=20)
        sim.step(runtime.core, 8)
        config_before = sim.memory_read(abi.CONFIG_ADDRESS, abi.CONFIG_SIZE)
        instructions = sim.cores[runtime.core].instructions
        state_before = sim.status()[runtime.core]
        drive_mask = sim.io(runtime.core)["gpo_drive_mask"]
        load = sim.load
        if failure == "errors":
            monkeypatch.setattr(sim, "load", lambda core, *args, **kwargs: load(core, "ldi r0"))
            expected = ValueError
        elif failure == "exception":
            def fail(*args, **kwargs):
                raise OSError("reload failed")
            monkeypatch.setattr(sim, "load", fail)
            expected = OSError
        else:
            expected = ValueError
        with pytest.raises(expected):
            runtime.load(clock_delay_loops=-1 if failure == "invalid config" else 99)
        assert sim.memory_read(abi.CONFIG_ADDRESS, abi.CONFIG_SIZE) == config_before
        assert sim.cores[runtime.core].instructions is instructions
        assert sim.status()[runtime.core] == state_before
        assert sim.io(runtime.core)["gpo_drive_mask"] == drive_mask
        assert sim.device_bus.devices == [runtime.encoder]
        assert not runtime._closed and runtime._loaded
        sim.reset(runtime.core)
        assert runtime.run_until_frames(1, max_steps=16000)["mailbox"]["raw_frame"] == 0xABC
