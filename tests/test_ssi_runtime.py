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
