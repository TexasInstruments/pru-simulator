"""Bit-level and integration tests for the runtime-configurable SSI encoder
reader (PRU1).

Like test_ssi_generic_emulator.py, config/frame-slot bytes are poked
directly into shared memory via pru_io.ssi_config_abi (there is no
ssi_runtime.py yet -- that is Task 5).

Two harness styles are used:
  - Paired with Task 3's ssi_generic_emulator.asm on pru0 (via GPIO wiring
    and step_paced), for anything that needs a real encoder on the other
    end of the wire -- position/status extraction, the seqlock mailbox, and
    the generation handshake. Both programs are generic and config-driven,
    so this is the natural pairing: poke the SAME config block once, both
    programs read their relevant fields from it.
  - pru1 running alone (topology=1, reader-only) for anything that doesn't
    need a real encoder -- the trace-buffer wraparound test (which needs
    many fast frames) and the topology==1 skip-wait test.
"""
from pathlib import Path

from simulator import Simulator
from pru_io import ssi_config_abi as abi


ROOT = Path(__file__).parent.parent
SOURCE_DIR = ROOT / "source"
READER_SRC = (SOURCE_DIR / "ssi_generic_reader" / "ssi_generic_reader.asm").read_text()
EMULATOR_SRC = (SOURCE_DIR / "ssi_generic_emulator" / "ssi_generic_emulator.asm").read_text()

READER_CLK_PIN = 0     # this program's GPO clock output (r30.0)
READER_DATA_PIN = 8    # this program's GPI data input (r31.8)
EMULATOR_CLK_IN_PIN = 16    # ssi_generic_emulator.asm's GPI clock input (r31.16)
EMULATOR_DATA_OUT_PIN = 0   # ssi_generic_emulator.asm's GPO data output (r30.0)


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------

def make_paired_sim():
    """A fresh Simulator with the reader on pru1 and Task 3's generic
    emulator on pru0, wired exactly like the fixed reader/emulator pairing
    in test_ssi_generic_emulator.py: pru1's CLK out -> pru0's CLK in, pru0's
    DATA out -> pru1's DATA in."""
    sim = Simulator(config_path="nonexistent.cfg")
    assert sim.load("pru1", READER_SRC, include_paths=[str(SOURCE_DIR)]) == []
    assert sim.load("pru0", EMULATOR_SRC, include_paths=[str(SOURCE_DIR)]) == []
    sim.add_gpio_wire("pru1", READER_CLK_PIN, "pru0", EMULATOR_CLK_IN_PIN)
    sim.add_gpio_wire("pru0", EMULATOR_DATA_OUT_PIN, "pru1", READER_DATA_PIN)
    sim.hard_reset()
    return sim


def make_reader_only_sim():
    """A fresh Simulator with only the reader loaded, on pru1."""
    sim = Simulator(config_path="nonexistent.cfg")
    assert sim.load("pru1", READER_SRC, include_paths=[str(SOURCE_DIR)]) == []
    sim.hard_reset()
    return sim


def configure(sim, **fields):
    """Write a full config block. Defaults: plain 12-bit/no-error loopback
    shape at the same clock timing test_ssi_generic_emulator.py already
    proved works paired with a reader (clock_high=33/clock_low=35 match
    ssi_reader_4mhz_12bit.asm's own HIGH_DLY/LOW_DLY; tv_cycles=5/
    tm_pause_outer_iters=15 match that file's known-good pairing values)."""
    base = dict(
        abi_version=1,
        struct_size=256,
        requested_generation=1,
        topology=0,
        frame_width_bits=12,
        position_offset_bits=0,
        position_width_bits=12,
        error_offset_bits=0xFFFF,
        error_width_bits=0,
        clock_high_cycles=33,
        clock_low_cycles=35,
        sample_delay_cycles=5,
        tp_pause_outer_iters=15,
        capture_mode=0,
        # ssi_generic_emulator.asm's own fields, needed whenever it's paired in
        tv_cycles=5,
        tm_pause_outer_iters=15,
        sequence_hold_mode=0,
        fault_mode=0,
        sequence_hold_count=1_000_000,
        fault_argument=0,
        fault_repeat_count=0,
    )
    base.update(fields)
    sim.memory.write(abi.CONFIG_BASE, abi.pack_config(**base))


def set_slot(sim, index, frame_bits, hold_override=0):
    sim.memory.write(
        abi.FRAMES_BASE + index * abi.FRAME_SLOT_SIZE,
        abi.pack_frame_slot(frame_bits, hold_override),
    )


def clear_slots(sim, start=0):
    for i in range(start, 16):
        set_slot(sim, i, abi.FRAME_SLOT_UNUSED_SENTINEL)


def read_mailbox(sim):
    return abi.unpack_mailbox(sim.memory_read(abi.MAILBOX_BASE, 64))


def read_u32(sim, addr):
    return int.from_bytes(sim.memory_read(addr, 4), "little")


def run_paired_and_settle(sim, frame_count, settle_steps=200, max_steps=4_000_000):
    """Step the paired sim until the mailbox's frame_counter reaches
    *frame_count*, then take a further small number of settle steps so the
    read lands deep in that frame's idle pause -- never mid-publish (the
    seqlock write is only ~10 instructions; 200 settle steps is a small
    fraction of even the shortest configured inter-frame pause) -- and
    return the settled mailbox contents."""
    for _ in range(max_steps):
        sim.step_paced("pru1", "pru0")
        mb = read_mailbox(sim)
        if mb["frame_counter"] >= frame_count:
            for _ in range(settle_steps):
                sim.step_paced("pru1", "pru0")
            return read_mailbox(sim)
    raise AssertionError(f"never reached frame_counter={frame_count}, last mailbox={mb}")


def advance_reader_only_to_frame(sim, frame_count, settle_steps=200, max_steps=6_000_000):
    """Step pru1 alone (no pru0 loaded) until frame_counter reaches
    *frame_count*, then take a further small number of settle steps so any
    same-frame work that happens after the frame_counter write (mailbox
    publish, trace write) has also completed. Used for topology=1 tests
    where there is nothing to pace against."""
    for _ in range(max_steps):
        sim.step("pru1", 1)
        fc = read_u32(sim, abi.MAILBOX_BASE + abi.MAILBOX_FRAME_COUNTER_OFF)
        if fc >= frame_count:
            sim.step("pru1", settle_steps)
            return read_u32(sim, abi.MAILBOX_BASE + abi.MAILBOX_FRAME_COUNTER_OFF)
    raise AssertionError(f"never reached frame_counter={frame_count}")


# ---------------------------------------------------------------------------
# Structural field extraction
# ---------------------------------------------------------------------------

def test_normal_transmission_position_value_matches_injected():
    """Default-ish 12-bit shape (position_offset_bits=0/width=12,
    error_width_bits=0): position_value == the injected raw frame, and
    status_bits is forced to 0 without running the extraction formula."""
    sim = make_paired_sim()
    configure(
        sim,
        frame_width_bits=12,
        position_offset_bits=0,
        position_width_bits=12,
        error_offset_bits=0xFFFF,
        error_width_bits=0,
    )
    set_slot(sim, 0, 0xABC)
    clear_slots(sim, 1)

    mb = run_paired_and_settle(sim, 2)
    assert mb["position_value"] == 0xABC
    assert mb["status_bits"] == 0


def test_position_and_error_field_afs_afm60_shape():
    """Mirrors the AFS/AFM60 singleturn shape: frame_width_bits=21, 18-bit
    position at offset 0, 3-bit error field at offset 18. Both fields land
    entirely in the low word (bit_start=3 and bit_start=0 respectively,
    since frame_width_bits=21 < 32), exercising case 2 for both, with
    position and error at DIFFERENT non-zero offsets in the same frame."""
    sim = make_paired_sim()
    position_value = 100_000       # fits 18 bits (max 262_143)
    error_value = 5                # fits 3 bits (max 7)
    frame_bits = (position_value << 3) | error_value

    configure(
        sim,
        frame_width_bits=21,
        position_offset_bits=0,
        position_width_bits=18,
        error_offset_bits=18,
        error_width_bits=3,
    )
    set_slot(sim, 0, frame_bits)
    clear_slots(sim, 1)

    mb = run_paired_and_settle(sim, 2)
    assert mb["position_value"] == position_value
    assert mb["status_bits"] == error_value


def test_position_field_straddles_32_bit_boundary():
    """frame_width_bits=40, position field width_bits=20 at offset_bits=0
    -> bit_start = 40-0-20 = 20, which is < 32 but 20+20=40 > 32, so this
    is genuinely case 3 (straddles both 32-bit halves) -- the exact
    untested-gap case the task brief flags (analogous to
    ssi_generic_emulator.asm's own mode-5 bug, but here in the reader's
    *extraction* logic, not the emulator's shift-out logic).

    Hand-verified with concrete numbers:
      raw_frame = 0xABCDE00000 (40 bits: hex digits A B C D E 0 0 0 0 0)
      RAW_LO = 0xCDE00000 (low 32 bits)
      RAW_HI = 0x000000AB (high 8 bits of the 40-bit frame)
      bit_start = 20
      RAW_LO >> 20      = 0xCDE00000 >> 20 = 0x000CDE  (top 12 bits of RAW_LO)
      RAW_HI << (32-20) = 0x000000AB << 12 = 0x0AB000
      OR                = 0x000CDE | 0x0AB000 = 0x0ABCDE
      mask with (1<<20)-1 = 0xFFFFF -> unchanged (0xABCDE already fits 20 bits)
      => position_value == 0xABCDE, exactly the 20 bits injected at offset 0.
    """
    sim = make_paired_sim()
    raw_frame = 0xABCDE00000
    assert raw_frame >> 20 == 0xABCDE  # sanity: the injected value really is here

    configure(
        sim,
        frame_width_bits=40,
        position_offset_bits=0,
        position_width_bits=20,
        error_offset_bits=0xFFFF,
        error_width_bits=0,
    )
    set_slot(sim, 0, raw_frame)
    clear_slots(sim, 1)

    mb = run_paired_and_settle(sim, 2)
    assert mb["position_value"] == 0xABCDE
    assert mb["raw_frame"] == raw_frame


# ---------------------------------------------------------------------------
# Seqlock mailbox + monotonic counters
# ---------------------------------------------------------------------------

def test_mailbox_seq_even_between_frames_and_counters_monotonic():
    """seq is always even when read between frames (settled well past the
    publish sequence, never mid-write), and frame_counter/timestamp_cycles
    both advance strictly across several frames."""
    sim = make_paired_sim()
    configure(
        sim,
        frame_width_bits=12,
        position_offset_bits=0,
        position_width_bits=12,
        error_offset_bits=0xFFFF,
        error_width_bits=0,
    )
    set_slot(sim, 0, 0x123)
    clear_slots(sim, 1)

    seqs, counters, timestamps = [], [], []
    for n in range(1, 6):
        mb = run_paired_and_settle(sim, n)
        seqs.append(mb["seq"])
        counters.append(mb["frame_counter"])
        timestamps.append(mb["timestamp_cycles"])

    assert all(s % 2 == 0 for s in seqs), seqs
    assert counters == list(range(1, 6))
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == len(timestamps)  # strictly increasing


# ---------------------------------------------------------------------------
# Trace buffer (capture_mode == 2)
# ---------------------------------------------------------------------------

def test_capture_mode_2_trace_overrun_and_wrap():
    """Run past 1024 frames at the fastest valid timing to keep the test
    quick, then assert trace_overrun_count matches the exact expected
    overwrite count and the most-recently-written slot holds the LATEST
    frame's data, not a stale pre-wrap record."""
    sim = make_reader_only_sim()
    n_frames = 1050
    configure(
        sim,
        topology=1,
        frame_width_bits=4,
        position_offset_bits=0,
        position_width_bits=4,
        error_offset_bits=0xFFFF,
        error_width_bits=0,
        clock_high_cycles=2,
        clock_low_cycles=1,
        sample_delay_cycles=1,
        tp_pause_outer_iters=1,
        capture_mode=2,
    )

    fc = advance_reader_only_to_frame(sim, n_frames)
    assert fc == n_frames

    write_idx = read_u32(sim, abi.CAPTURE_BASE + abi.CAPTURE_TRACE_WRITE_INDEX_OFF)
    overrun = read_u32(sim, abi.CAPTURE_BASE + abi.CAPTURE_TRACE_OVERRUN_COUNT_OFF)
    assert write_idx == n_frames
    assert overrun == n_frames - 1024

    # The most recently written slot must match the CURRENT mailbox state,
    # not a value left over from before the buffer wrapped around.
    last_slot = (write_idx - 1) % 1024
    rec = sim.memory_read(
        abi.TRACE_BASE + last_slot * abi.TRACE_RECORD_SIZE, abi.TRACE_RECORD_SIZE
    )
    rec_timestamp = int.from_bytes(rec[0:8], "little")
    mb = read_mailbox(sim)
    assert rec_timestamp == mb["timestamp_cycles"]


# ---------------------------------------------------------------------------
# Generation handshake
# ---------------------------------------------------------------------------

def test_topology_reader_only_skips_pru0_ack_wait():
    """topology=1 (reader-only): with no PRU0 core loaded at all (so
    pru0_ack_generation can never move), the reader must still apply its
    configuration and clock frames, not hang waiting for an ack that will
    never come."""
    sim = make_reader_only_sim()
    configure(
        sim,
        topology=1,
        frame_width_bits=12,
        position_offset_bits=0,
        position_width_bits=12,
        error_offset_bits=0xFFFF,
        error_width_bits=0,
    )

    fc = advance_reader_only_to_frame(sim, 2)
    assert fc >= 2
    pru1_ack = read_u32(sim, abi.CONFIG_BASE + abi.CONFIG_PRU1_ACK_GENERATION_OFF)
    assert pru1_ack == 1


def test_generation_change_applied_only_at_idle_boundary():
    """A mid-frame config+generation bump must not affect the frame already
    in flight (started under the OLD config), and pru1_ack_generation must
    not move until the next idle boundary, after which the new config takes
    effect on the very next frame. Mirrors
    test_ssi_generic_emulator.py's own generation-change test, but for the
    active-master reader: instead of waiting for an externally driven clock
    edge, we watch the reader's OWN clock output pin for falling edges to
    find a mid-frame point."""
    sim = make_paired_sim()
    configure(
        sim,
        requested_generation=1,
        frame_width_bits=12,
        position_offset_bits=0,
        position_width_bits=12,
        error_offset_bits=0xFFFF,
        error_width_bits=0,
    )
    set_slot(sim, 0, 0xAAA)
    clear_slots(sim, 1)

    # Frame 1 can race pru0's own startup (config load) before it starts
    # driving real data, exactly like test_ssi_generic_emulator.py's own
    # basic sanity test notes; frame 2 onward is stable.
    mb = run_paired_and_settle(sim, 2)
    assert mb["position_value"] == 0xAAA
    old_pru1_ack = read_u32(sim, abi.CONFIG_BASE + abi.CONFIG_PRU1_ACK_GENERATION_OFF)
    assert old_pru1_ack == 1

    # Drive partway into the NEXT (still 12-bit) frame: count falling edges
    # on the reader's own CLK output until 6 have passed (1 frame-start +
    # 5 completed bits), landing mid-frame.
    falling_edges = 0
    prev = sim.io("pru1")["gpo_pins"][READER_CLK_PIN]
    for _ in range(500_000):
        sim.step_paced("pru1", "pru0")
        cur = sim.io("pru1")["gpo_pins"][READER_CLK_PIN]
        if prev == 1 and cur == 0:
            falling_edges += 1
            if falling_edges >= 6:
                break
        prev = cur
    else:
        raise AssertionError("never saw 6 falling edges")

    # Preserve the ack fields' true current values across the rewrite --
    # pack_config zero-fills any field not passed, and we want to read them
    # back below to prove they truly haven't moved yet, not that our own
    # rewrite reset them.
    pru0_ack_now = read_u32(sim, abi.CONFIG_BASE + abi.CONFIG_PRU0_ACK_GENERATION_OFF)
    configure(
        sim,
        requested_generation=2,
        frame_width_bits=8,
        position_offset_bits=0,
        position_width_bits=8,
        error_offset_bits=0xFFFF,
        error_width_bits=0,
        pru0_ack_generation=pru0_ack_now,
        pru1_ack_generation=old_pru1_ack,
    )
    set_slot(sim, 0, 0x3C)
    clear_slots(sim, 1)

    mid_frame_ack = read_u32(sim, abi.CONFIG_BASE + abi.CONFIG_PRU1_ACK_GENERATION_OFF)
    assert mid_frame_ack == 1  # not yet applied mid-frame

    mb2 = run_paired_and_settle(sim, 3)  # the OLD in-flight (3rd) 12-bit frame completes
    assert mb2["position_value"] == 0xAAA
    assert mb2["frame_counter"] == 3

    # Applying the new generation happens only after frame 3's full
    # inter-frame pause elapses and the idle-boundary check runs -- by the
    # time frame 4 (the new 8-bit shape) is observed, the apply (and both
    # acks) must already be done, since l_apply_config always completes
    # before the next l_new_frame starts driving bits.
    mb3 = run_paired_and_settle(sim, 4)
    assert mb3["position_value"] == 0x3C  # next frame uses the NEW config

    new_ack = read_u32(sim, abi.CONFIG_BASE + abi.CONFIG_PRU1_ACK_GENERATION_OFF)
    assert new_ack == 2  # applied at (or before) the idle boundary that started frame 4
