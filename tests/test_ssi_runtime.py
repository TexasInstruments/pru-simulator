"""Tests for ssi_runtime.py -- the Python stand-in for real R5 firmware:
named encoder profiles, staged-then-applied configuration validation, and
position decoding.

Harness conventions are reused verbatim from Tasks 3/4's own test files
(test_ssi_generic_emulator.py / test_ssi_generic_reader.py): step_paced for
paired PRU0+PRU1 stepping, sim.memory.write/sim.memory_read for direct
memory access, and ssi_config_abi.pack_frame_slot for injecting frame
values -- ssi_runtime.py deliberately does not add its own frame-injection
helper (that's out of scope for this task; see the design doc's "Prepacked
emulator frame slots" section, which frame-slot writes always bypass R5/the
config-writer's "apply" path anyway).
"""
import pytest

from pathlib import Path

from simulator import Simulator
from pru_io import ssi_config_abi as abi
from pru_io.ssi_runtime import SSIRuntime, PROFILES


ROOT = Path(__file__).parent.parent
SOURCE_DIR = ROOT / "source"
EMULATOR_SRC = (SOURCE_DIR / "ssi_generic_emulator" / "ssi_generic_emulator.asm").read_text()
READER_SRC = (SOURCE_DIR / "ssi_generic_reader" / "ssi_generic_reader.asm").read_text()

READER_CLK_PIN = 0
READER_DATA_PIN = 8
EMULATOR_CLK_IN_PIN = 16
EMULATOR_DATA_OUT_PIN = 0


# ---------------------------------------------------------------------------
# Harness helpers (copied verbatim in spirit from test_ssi_generic_reader.py)
# ---------------------------------------------------------------------------

def make_paired_sim():
    """A fresh Simulator with the generic reader on pru1 and the generic
    emulator on pru0, wired exactly like Tasks 3/4's own paired tests. Loaded
    and hard_reset() *before* SSIRuntime is ever constructed on it -- see
    ssi_runtime.py's module docstring precondition."""
    sim = Simulator(config_path="nonexistent.cfg")
    assert sim.load("pru1", READER_SRC, include_paths=[str(SOURCE_DIR)]) == []
    assert sim.load("pru0", EMULATOR_SRC, include_paths=[str(SOURCE_DIR)]) == []
    sim.add_gpio_wire("pru1", READER_CLK_PIN, "pru0", EMULATOR_CLK_IN_PIN)
    sim.add_gpio_wire("pru0", EMULATOR_DATA_OUT_PIN, "pru1", READER_DATA_PIN)
    sim.hard_reset()
    return sim


def make_reader_only_sim():
    """A fresh Simulator with only the generic reader loaded, on pru1."""
    sim = Simulator(config_path="nonexistent.cfg")
    assert sim.load("pru1", READER_SRC, include_paths=[str(SOURCE_DIR)]) == []
    sim.hard_reset()
    return sim


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


def run_paired_and_settle(sim, frame_count, settle_steps=200, max_steps=4_000_000):
    """Step the paired sim until the mailbox's frame_counter reaches
    *frame_count*, then settle a little further so the read never lands
    mid-publish, and return the settled mailbox contents."""
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
    *frame_count*, then settle a little further, matching
    test_ssi_generic_reader.py's own advance_reader_only_to_frame
    convention."""
    for _ in range(max_steps):
        sim.step("pru1", 1)
        mb = read_mailbox(sim)
        if mb["frame_counter"] >= frame_count:
            sim.step("pru1", settle_steps)
            return read_mailbox(sim)
    raise AssertionError(f"never reached frame_counter={frame_count}, last mailbox={mb}")


def advance_past_switch_to_frame(sim, stale_frame_counter, frame_count,
                                  settle_steps=200, max_steps=6_000_000):
    """Like advance_reader_only_to_frame, but for use right after a live
    profile switch: ssi_generic_reader.asm's l_apply_config resets its
    FRAME_COUNTER *register* to 0 on every applied generation, but that
    reset is never itself published to the shared-memory mailbox -- the
    mailbox's frame_counter field only changes when a frame under the NEW
    generation actually completes and republishes it. So the mailbox still
    reads the stale pre-switch value (*stale_frame_counter*) until then;
    this waits for the value to move away from that stale reading first
    (proof a fresh publish under the new generation happened) before
    counting up to frame_count."""
    for _ in range(max_steps):
        sim.step("pru1", 1)
        mb = read_mailbox(sim)
        if mb["frame_counter"] != stale_frame_counter:
            break
    else:
        raise AssertionError("frame_counter never moved past its stale pre-switch value")

    for _ in range(max_steps):
        sim.step("pru1", 1)
        mb = read_mailbox(sim)
        if mb["frame_counter"] >= frame_count:
            sim.step("pru1", settle_steps)
            return read_mailbox(sim)
    raise AssertionError(f"never reached frame_counter={frame_count}, last mailbox={mb}")


def bare_runtime():
    """A SSIRuntime with no simulator attached, for exercising stage()'s
    pure validation logic in isolation -- stage() never touches self.sim,
    only apply()/wait_for_apply() do."""
    runtime = SSIRuntime.__new__(SSIRuntime)
    runtime.sim = None
    runtime._staged = None
    runtime._active_profile = None
    return runtime


# ---------------------------------------------------------------------------
# Default behavior: constructor applies CUSTOM_LEGACY_12BIT_4MHZ with no
# explicit stage()/apply() call from the test itself.
# ---------------------------------------------------------------------------

def test_default_behavior_matches_fixed_12bit_4mhz():
    """SSIRuntime's constructor stages+applies CUSTOM_LEGACY_12BIT_4MHZ as
    its first action; from here on this test never calls stage()/apply()
    again. The generic programs under that default must behave like the
    fixed 12-bit/4 MHz pair already proven in
    test_ssi_encoder_sequence_emulator.py: same 12-bit width, same
    order-of-magnitude timing (reaches several frames well within a normal
    step budget)."""
    sim = make_paired_sim()
    SSIRuntime(sim)  # implicit stage()+apply(); nothing else called on it

    set_slot(sim, 0, 0xABC)
    clear_slots(sim, 1)

    mb = run_paired_and_settle(sim, 3)
    assert mb["position_value"] == 0xABC
    assert mb["status_bits"] == 0
    assert mb["frame_counter"] == 3


# ---------------------------------------------------------------------------
# Named profiles end-to-end: stage -> apply -> inject -> read -> decode.
# ---------------------------------------------------------------------------

def test_profile_ahs_ahm36_singleturn_end_to_end():
    """14-bit position + 1-bit error field, binary encoding."""
    sim = make_paired_sim()
    runtime = SSIRuntime(sim)
    runtime.stage("AHS_AHM36_SINGLETURN")
    runtime.apply()

    position_value = 0x1234  # fits 14 bits (max 0x3FFF)
    error_value = 1          # fits 1 bit
    frame_bits = (position_value << 1) | error_value
    set_slot(sim, 0, frame_bits)
    clear_slots(sim, 1)

    mb = run_paired_and_settle(sim, 3)
    assert mb["position_value"] == position_value
    assert mb["status_bits"] == error_value
    decoded = runtime.decode_position(mb["position_value"], encoding_type=0, width=14)
    assert decoded == position_value


def test_profile_afs_afm60_multiturn_30bit_end_to_end():
    """30-bit multiturn position (18 singleturn + 12 multiturn) + 3-bit
    error field, binary encoding."""
    sim = make_paired_sim()
    runtime = SSIRuntime(sim)
    runtime.stage("AFS_AFM60_MULTITURN_30BIT")
    runtime.apply()

    position_value = 300_000_000  # fits 30 bits (max 1_073_741_823)
    error_value = 5               # fits 3 bits (max 7)
    frame_bits = (position_value << 3) | error_value
    set_slot(sim, 0, frame_bits)
    clear_slots(sim, 1)

    mb = run_paired_and_settle(sim, 3)
    assert mb["position_value"] == position_value
    assert mb["status_bits"] == error_value
    decoded = runtime.decode_position(mb["position_value"], encoding_type=0, width=30)
    assert decoded == position_value


def test_profile_atm60_90_tannenbaum_sync_end_to_end():
    """25-bit tannenbaum multiturn position (12 singleturn + 13 multiturn) +
    1-bit error field, sync formation mode. Tannenbaum needs no decode-time
    bit-recoding (it's a frame-layout convention handled structurally), so
    decode_position(..., encoding_type=3, ...) must return the raw value
    unchanged, same as binary."""
    sim = make_paired_sim()
    runtime = SSIRuntime(sim)
    runtime.stage("ATM60_90")
    runtime.apply()

    position_value = 12_345_678  # fits 25 bits (max 33_554_431)
    error_value = 1               # fits 1 bit
    frame_bits = (position_value << 1) | error_value
    set_slot(sim, 0, frame_bits)
    clear_slots(sim, 1)

    mb = run_paired_and_settle(sim, 3)
    assert mb["position_value"] == position_value
    assert mb["status_bits"] == error_value
    decoded = runtime.decode_position(mb["position_value"], encoding_type=3, width=25)
    assert decoded == position_value


def test_profile_custom_legacy_12bit_4mhz_explicit_apply_round_trips():
    """CUSTOM_LEGACY_12BIT_4MHZ explicitly staged/applied (not just relied on
    as the constructor's implicit default) must round-trip through apply()
    itself too."""
    sim = make_paired_sim()
    runtime = SSIRuntime(sim)
    runtime.stage("CUSTOM_LEGACY_12BIT_4MHZ")
    runtime.apply()

    set_slot(sim, 0, 0x0DE)
    clear_slots(sim, 1)

    mb = run_paired_and_settle(sim, 3)
    assert mb["position_value"] == 0x0DE
    decoded = runtime.decode_position(mb["position_value"], encoding_type=0, width=12)
    assert decoded == 0x0DE


# ---------------------------------------------------------------------------
# Gray decode: standalone unit test, no simulator needed.
# ---------------------------------------------------------------------------

def test_decode_position_gray_4bit_hand_computed():
    """Hand-traced 4-bit Gray-to-binary example.

    binary = 0b1011 (11). Standard Gray encode: gray = binary ^ (binary >> 1)
      0b1011 ^ 0b0101 = 0b1110 (14).
    Decoding 14 back to binary, MSB-first per decode_position's algorithm:
      bit3 (MSB): gray_bit=1, stays 1  -> running_binary_bit=1, result=0b1000
      bit2: gray_bit=(14>>2)&1=1, xor prev_binary_bit(1) -> 0 -> result=0b1000
      bit1: gray_bit=(14>>1)&1=1, xor prev_binary_bit(0) -> 1 -> result=0b1010
      bit0: gray_bit=(14>>0)&1=0, xor prev_binary_bit(1) -> 1 -> result=0b1011
    -> 0b1011 == 11, matching the original binary value.
    """
    runtime = bare_runtime()
    assert runtime.decode_position(0b1110, encoding_type=1, width=4) == 0b1011


def test_decode_position_gray_excess_not_implemented():
    runtime = bare_runtime()
    with pytest.raises(NotImplementedError):
        runtime.decode_position(0, encoding_type=2, width=8)


def test_decode_position_binary_and_tannenbaum_are_passthrough():
    runtime = bare_runtime()
    assert runtime.decode_position(0x1234, encoding_type=0, width=16) == 0x1234
    assert runtime.decode_position(0x1234, encoding_type=3, width=16) == 0x1234


# ---------------------------------------------------------------------------
# Runtime switch: no captured frame straddles the profile change.
# ---------------------------------------------------------------------------

def test_runtime_switch_no_frame_straddles_profile_change():
    """Profile A then profile B, both explicitly staged with topology=1
    (reader-only), captures frames before and after a live switch; every
    capture must decode correctly under whichever profile was active when it
    was captured.

    topology=1 is a deliberate choice here, not the CUSTOM_LEGACY/ARS60_SHORT
    profiles' own default (topology=0, loopback): reconfiguring a topology=0
    loopback pair *during the settled idle gap* between frames deadlocks the
    fixed generic programs by construction, because PRU0's debounce
    threshold (derived from tm_pause_outer_iters) is always shorter than
    PRU1's inter-frame pause (derived from tp_pause_outer_iters -- and
    stage() itself requires tp_pause_outer_iters > tm_pause_outer_iters).
    So PRU0 always finishes debouncing and parks waiting for the next
    falling edge *before* PRU1 reaches its own idle-boundary generation
    check and decides to detour to l_apply_config (waiting for PRU0's ack)
    instead of sending one -- neither side can make progress. Tasks 3/4's
    own generation-change tests avoid this by bumping the generation
    mid-frame (while real clock edges are still flowing), which this test's
    black-box stage()/apply() calls have no equivalent hook for. Using
    topology=1 sidesteps the two-core handshake entirely -- exactly what
    this test needs to isolate (no frame straddles a switch), without
    touching ssi_runtime.py itself (this is a property of the fixed,
    unmodified .asm files, not a bug in stage()/apply()/wait_for_apply()).

    Frame values are driven by sim.ssi_inject's pre-existing, edge-driven
    SSIEncoderGenerator (decoupled from PRU0 entirely) rather than PRU0's
    frame slots, since there is no PRU0 in this scenario."""
    sim = make_reader_only_sim()
    runtime = bare_runtime()
    runtime.sim = sim

    value_a = 0xABC  # fits 12 bits
    runtime.stage("CUSTOM_LEGACY_12BIT_4MHZ", topology=1)
    runtime.apply()
    sim.ssi_inject(core="pru1", clk_pin=READER_CLK_PIN, data_pin=READER_DATA_PIN,
                    value=value_a, bits=12)

    mb_a = advance_reader_only_to_frame(sim, 3)
    assert mb_a["frame_counter"] == 3
    assert mb_a["position_value"] == value_a
    decoded_a = runtime.decode_position(mb_a["position_value"], encoding_type=0, width=12)
    assert decoded_a == value_a

    value_b = 0x1A2B & ((1 << 13) - 1)  # fits 13 bits
    runtime.stage("ARS60_SHORT", topology=1)
    runtime.apply()
    sim.ssi_inject(core="pru1", clk_pin=READER_CLK_PIN, data_pin=READER_DATA_PIN,
                    value=value_b, bits=13)

    mb_b = advance_past_switch_to_frame(sim, stale_frame_counter=3, frame_count=3)
    assert mb_b["frame_counter"] == 3  # reset+recounted under the new generation
    assert mb_b["position_value"] == value_b
    decoded_b = runtime.decode_position(mb_b["position_value"], encoding_type=0, width=13)
    assert decoded_b == value_b


# ---------------------------------------------------------------------------
# stage() staging a single-field tweak on top of the last profile (profile=None).
# ---------------------------------------------------------------------------

def test_stage_none_tweaks_on_top_of_last_profile():
    """stage(None, **overrides) keeps every other field from the last
    staged/applied profile, and the next stage() call's max_clock_hz check
    still uses that profile's ceiling (not a reset-to-custom ceiling)."""
    runtime = bare_runtime()
    runtime.stage("KH53")
    runtime.stage(fault_mode=3)  # profile=None: tweak on top of KH53

    assert runtime._staged["fault_mode"] == 3
    assert runtime._staged["frame_width_bits"] == KH53_FRAME_WIDTH

    with pytest.raises(ValueError):
        # Still bound by KH53's max_clock_hz even though profile=None here.
        runtime.stage(clock_high_cycles=1, clock_low_cycles=1)


KH53_FRAME_WIDTH = PROFILES["KH53"].frame_width_bits


# ---------------------------------------------------------------------------
# Rejection tests: stage()'s pure validation, no simulator needed.
# ---------------------------------------------------------------------------

def test_stage_rejects_unknown_field_name():
    runtime = bare_runtime()
    with pytest.raises(ValueError):
        runtime.stage("CUSTOM_LEGACY_12BIT_4MHZ", not_a_real_field=1)


def test_stage_rejects_clock_high_cycles_over_256():
    runtime = bare_runtime()
    with pytest.raises(ValueError):
        runtime.stage("CUSTOM_LEGACY_12BIT_4MHZ", clock_high_cycles=300)


def test_stage_rejects_frame_width_smaller_than_position_plus_error():
    runtime = bare_runtime()
    with pytest.raises(ValueError):
        # AHS_AHM36_SINGLETURN needs frame_width_bits >= 14+1=15.
        runtime.stage("AHS_AHM36_SINGLETURN", frame_width_bits=10)


def test_stage_rejects_named_profile_clock_override_exceeding_max_clock_hz():
    runtime = bare_runtime()
    with pytest.raises(ValueError):
        # KH53's max_clock_hz is 1_500_000; 300MHz/(1+1) = 150 MHz.
        runtime.stage("KH53", clock_high_cycles=1, clock_low_cycles=1)


# ---------------------------------------------------------------------------
# wait_for_apply timeout: an ack that can never arrive.
# ---------------------------------------------------------------------------

def test_wait_for_apply_raises_timeout_when_pru0_never_loaded():
    """topology=0 (loopback) needs BOTH acks, but only PRU1 is loaded here
    (no PRU0 at all) -- pru0_ack_generation can never move. apply() must
    raise TimeoutError within a small timeout_steps budget, not hang or
    silently return.

    Bypasses SSIRuntime.__init__'s own implicit default-profile apply (via
    bare_runtime()) because that default profile is ALSO topology=0 and
    would hit this exact same never-acks scenario on this reader-only sim --
    this test isolates apply()/wait_for_apply()'s own timeout contract
    instead of fighting the constructor's "must succeed" guarantee."""
    sim = make_reader_only_sim()
    runtime = bare_runtime()
    runtime.sim = sim
    runtime.stage("CUSTOM_LEGACY_12BIT_4MHZ")

    with pytest.raises(TimeoutError):
        runtime.apply(timeout_steps=2000)
