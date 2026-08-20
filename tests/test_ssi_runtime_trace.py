"""Tests for SSIRuntime.read_trace / read_mailbox (Task 6).

Harness conventions are reused verbatim from test_ssi_generic_reader.py's own
capture-mode-2 trace-wraparound test (test_capture_mode_2_trace_overrun_and_wrap):
reader-only (topology=1, no PRU0 emulator, no encoder needed -- the reader
just clocks its own frames against whatever's on the floating data pin) at
the fastest timing that test proved legal (clock_high_cycles=2/
clock_low_cycles=1/sample_delay_cycles=1/tp_pause_outer_iters=1,
frame_width_bits=4), so 1,050 frames runs in well under a second.

Going through SSIRuntime.stage()/apply() (this task's actual target, not a
raw memory poke) rather than test_ssi_generic_reader.py's configure()
requires a real Profile: stage()'s own validation enforces
"derived clock Hz <= profile.max_clock_hz", and every profile in
ssi_runtime.PROFILES (including the fastest, CUSTOM_LEGACY_12BIT_4MHZ, at
max_clock_hz=4_500_000) is far too slow a ceiling for this raw timing
(300_000_000/(2+1) = 100 MHz). So this file defines its own throwaway
Profile instance -- stage() accepts any Profile, not just named ones -- with
the same fastest-legal wire timing as test_ssi_generic_reader.py's own test,
just given a permissive max_clock_hz since this is a simulation-speed
concern, not a physical-encoder one.
"""
from pathlib import Path

from simulator import Simulator
from pru_io import ssi_config_abi as abi
from pru_io.ssi_runtime import SSIRuntime, Profile


ROOT = Path(__file__).parent.parent
SOURCE_DIR = ROOT / "source"
READER_SRC = (SOURCE_DIR / "ssi_generic_reader" / "ssi_generic_reader.asm").read_text()


# Same fastest-legal-timing numbers as
# test_ssi_generic_reader.py::test_capture_mode_2_trace_overrun_and_wrap,
# wrapped in a Profile so SSIRuntime.stage()'s validation (in particular the
# max_clock_hz ceiling) can be satisfied without slowing the test down to a
# real encoder's pace.
FAST_TEST_PROFILE = Profile(
    name="_FAST_TEST_ONLY",
    frame_width_bits=4,
    position_offset_bits=0,
    position_width_bits=4,
    singleturn_width_bits=4,
    multiturn_width_bits=0,
    error_offset_bits=0xFFFF,
    error_width_bits=0,
    padding_width_bits=0,
    topology=1,
    clock_high_cycles=2,
    clock_low_cycles=1,
    sample_delay_cycles=1,
    tv_cycles=1,
    tm_pause_outer_iters=0,
    tp_pause_outer_iters=1,
    capture_mode=2,
    max_clock_hz=300_000_000,
)


def make_reader_only_sim():
    """A fresh Simulator with only the generic reader loaded, on pru1."""
    sim = Simulator(config_path="nonexistent.cfg")
    assert sim.load("pru1", READER_SRC, include_paths=[str(SOURCE_DIR)]) == []
    sim.hard_reset()
    return sim


def bare_runtime():
    """An SSIRuntime with no implicit default-profile apply -- see
    test_ssi_runtime.py's own bare_runtime() for why: the constructor's
    implicit CUSTOM_LEGACY_12BIT_4MHZ default is topology=0 (loopback),
    which would deadlock waiting for a PRU0 ack that never arrives on a
    reader-only sim."""
    runtime = SSIRuntime.__new__(SSIRuntime)
    runtime.sim = None
    runtime._staged = None
    runtime._active_profile = None
    return runtime


def read_mailbox_raw(sim):
    return abi.unpack_mailbox(sim.memory_read(abi.MAILBOX_BASE, 64))


def advance_reader_only_to_frame(sim, frame_count, settle_steps=200, max_steps=3_000_000):
    """Step pru1 alone until frame_counter reaches frame_count, then settle
    a little further (matches test_ssi_generic_reader.py's own
    advance_reader_only_to_frame convention)."""
    for _ in range(max_steps):
        sim.step("pru1", 1)
        mb = read_mailbox_raw(sim)
        if mb["frame_counter"] >= frame_count:
            sim.step("pru1", settle_steps)
            return
    raise AssertionError(f"never reached frame_counter={frame_count}, last mailbox={mb}")


def make_fast_runtime():
    sim = make_reader_only_sim()
    runtime = bare_runtime()
    runtime.sim = sim
    runtime.stage(FAST_TEST_PROFILE)
    runtime.apply()
    return runtime


# ---------------------------------------------------------------------------
# read_trace: overrun counting, ordering, limit.
#
# All of these assertions share a single 1,050-frame run (~6s of simulated
# stepping, matching test_ssi_generic_reader.py's own baseline for this exact
# workload) rather than re-running it per assertion, to keep this file's
# total runtime down.
# ---------------------------------------------------------------------------

def test_read_trace_overrun_ordering_and_limit_after_wraparound():
    """After exactly 1,050 frames (26 past the 1,024-slot ring):
    - overrun_count is exactly 1,050 - 1,024 = 26, not merely > 0.
    - read_trace returns exactly 1,024 records.
    - newest_first=True's first record really is the most recent capture
      (highest timestamp_cycles, matching the current mailbox -- trace
      records carry no frame_counter of their own to check directly), not a
      stale pre-wrap record.
    - newest_first=False is the exact reverse, oldest-to-newest.
    - limit takes the most-recent N (newest_first) / oldest N (not).
    """
    n_frames = 1050
    runtime = make_fast_runtime()
    sim = runtime.sim
    advance_reader_only_to_frame(sim, n_frames)

    write_index = int.from_bytes(
        sim.memory_read(abi.CAPTURE_BASE + abi.CAPTURE_TRACE_WRITE_INDEX_OFF, 4),
        "little",
    )
    overrun_count = int.from_bytes(
        sim.memory_read(abi.CAPTURE_BASE + abi.CAPTURE_TRACE_OVERRUN_COUNT_OFF, 4),
        "little",
    )
    assert write_index == n_frames
    assert overrun_count == n_frames - 1024

    newest_first = runtime.read_trace(newest_first=True)
    assert len(newest_first) == 1024

    newest_timestamps = [r["timestamp_cycles"] for r in newest_first]
    assert newest_timestamps == sorted(newest_timestamps, reverse=True)
    assert len(set(newest_timestamps)) == len(newest_timestamps)  # strictly decreasing

    mb = read_mailbox_raw(sim)
    assert newest_timestamps[0] == mb["timestamp_cycles"]  # newest == current mailbox

    oldest_first = runtime.read_trace(newest_first=False)
    assert len(oldest_first) == 1024
    assert oldest_first == list(reversed(newest_first))
    oldest_timestamps = [r["timestamp_cycles"] for r in oldest_first]
    assert oldest_timestamps == sorted(oldest_timestamps)

    assert runtime.read_trace(newest_first=True, limit=5) == newest_first[:5]
    assert runtime.read_trace(newest_first=False, limit=5) == oldest_first[:5]


def test_read_trace_before_wraparound_is_write_order():
    """Before the ring buffer ever wraps (trace_write_index <= 1024), the
    oldest record is slot 0 and there is no overrun."""
    n_frames = 5
    runtime = make_fast_runtime()
    sim = runtime.sim
    advance_reader_only_to_frame(sim, n_frames)

    oldest_first = runtime.read_trace(newest_first=False)
    assert len(oldest_first) == n_frames
    timestamps = [r["timestamp_cycles"] for r in oldest_first]
    assert timestamps == sorted(timestamps)

    overrun_count = int.from_bytes(
        sim.memory_read(abi.CAPTURE_BASE + abi.CAPTURE_TRACE_OVERRUN_COUNT_OFF, 4),
        "little",
    )
    assert overrun_count == 0


# ---------------------------------------------------------------------------
# read_mailbox: seqlock-safe read + semantic decode.
# ---------------------------------------------------------------------------

def test_read_mailbox_matches_raw_and_is_decoded():
    runtime = make_fast_runtime()
    sim = runtime.sim
    advance_reader_only_to_frame(sim, 5)

    raw = read_mailbox_raw(sim)
    decoded = runtime.read_mailbox()

    assert decoded["frame_counter"] == raw["frame_counter"]
    assert decoded["seq"] == raw["seq"]
    assert decoded["seq"] % 2 == 0
    # FAST_TEST_PROFILE's encoding_type is 0 (binary): decode is a passthrough.
    assert decoded["position_value"] == raw["position_value"]
