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
READER_DATA_PIN = 16
EMULATOR_CLK_IN_PIN = 8
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


def test_switched_role_pin_contract_matches_launchpad_wiring():
    """PRU1 clocks on BP.11 and receives data on BP.57; PRU0 receives
    that clock on BP.51 and drives data on BP.33.

    The virtual pin numbers mirror the direct R30/R31 bit numbers used by the
    hardware project, so this test catches a simulator-only role reversal that
    would otherwise pass with the old cross-wiring.
    """
    assert "CLK_PIN       .set 8" in EMULATOR_SRC
    assert "DATA_PIN      .set 0" in EMULATOR_SRC
    assert "CLK_PIN       .set 0" in READER_SRC
    assert "DATA_PIN      .set 16" in READER_SRC


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
    runtime._staged_profile = None
    runtime._active_config = None
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
    runtime = SSIRuntime(sim)  # implicit default stage()+apply()
    runtime.set_raw_frames([0xABC])
    runtime.apply()  # publish the slot update at an idle frame boundary

    mb = run_paired_and_settle(sim, 3)
    assert mb["position_value"] == 0xABC
    assert mb["status_bits"] == 0
    assert mb["frame_counter"] == 3


def test_default_loopback_does_not_drop_every_other_frame():
    """The default pair must return one valid value for every SSI request."""
    sim = make_paired_sim()
    runtime = SSIRuntime(sim)
    frames = (0xABC, 0xAAA, 0xBCA, 0x12A, 0xCC2)
    runtime.set_raw_frames(list(frames))
    runtime.apply()

    previous_counter = read_mailbox(sim)["frame_counter"]
    observed = []
    for _ in range(40_000):
        sim.step_paced("pru1", "pru0")
        mailbox = read_mailbox(sim)
        if mailbox["frame_counter"] == previous_counter:
            continue
        previous_counter = mailbox["frame_counter"]
        observed.append(mailbox["raw_frame"])
        if len(observed) == len(frames):
            break

    assert observed
    assert all(value in frames for value in observed)
    for previous, current in zip(observed, observed[1:]):
        next_index = (frames.index(previous) + 1) % len(frames)
        assert current == frames[next_index]


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

    position_value = 12_345_678  # fits 25 bits (max 33_554_431)
    error_value = 1               # fits 1 bit
    frame_bits = (position_value << 1) | error_value
    set_slot(sim, 0, frame_bits)
    clear_slots(sim, 1)
    runtime.apply()

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


@pytest.mark.parametrize("profile_name", sorted(PROFILES))
def test_every_named_profile_round_trips_one_semantic_position(profile_name):
    """Every catalog entry must drive one complete emulator/reader frame."""
    sim = make_paired_sim()
    runtime = SSIRuntime(sim)
    profile = PROFILES[profile_name]
    runtime.stage(profile_name)

    position_mask = (1 << profile.position_width_bits) - 1
    position = min(position_mask, 0x12345)
    status = (
        (1 << profile.error_width_bits) - 1
        if profile.error_width_bits
        else 0
    )
    runtime.set_positions([position], [status])
    runtime.apply()

    mailbox = run_paired_and_settle(sim, 2)
    assert mailbox["position_value"] == position
    assert mailbox["status_bits"] == status
    assert runtime.decode_position(
        mailbox["position_value"],
        profile.encoding_type,
        profile.position_width_bits,
    ) == position


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


def test_encode_position_binary_and_gray_round_trip():
    runtime = bare_runtime()

    assert runtime.encode_position(0b1011, encoding_type=0, width=4) == 0b1011
    assert runtime.encode_position(0b1011, encoding_type=1, width=4) == 0b1110
    assert runtime.decode_position(
        runtime.encode_position(0b1011, encoding_type=1, width=4),
        encoding_type=1,
        width=4,
    ) == 0b1011


def test_encode_position_rejects_values_outside_resolution():
    runtime = bare_runtime()

    with pytest.raises(ValueError, match="does not fit in 4 bits"):
        runtime.encode_position(16, encoding_type=0, width=4)


def test_gray_excess_uses_a_centered_non_power_of_two_code_window():
    runtime = bare_runtime()

    wire_zero = runtime.encode_position(
        0,
        encoding_type=2,
        width=4,
        position_count=10,
    )
    wire_last = runtime.encode_position(
        9,
        encoding_type=2,
        width=4,
        position_count=10,
    )

    assert runtime.decode_position(
        wire_zero, encoding_type=2, width=4, position_count=10
    ) == 0
    assert runtime.decode_position(
        wire_last, encoding_type=2, width=4, position_count=10
    ) == 9


def test_stage_rejects_invalid_alignment_and_field_layout():
    runtime = bare_runtime()

    with pytest.raises(ValueError, match="alignment"):
        runtime.stage("CUSTOM_LEGACY_12BIT_4MHZ", alignment=2)

    with pytest.raises(ValueError, match="position field"):
        runtime.stage(
            "CUSTOM_LEGACY_12BIT_4MHZ",
            frame_width_bits=12,
            position_offset_bits=1,
            position_width_bits=12,
        )


def test_stage_requires_inter_frame_gap_to_cover_sync_formation_pause():
    runtime = bare_runtime()

    with pytest.raises(ValueError, match="formation_pause_outer_iters"):
        runtime.stage(
            "CUSTOM_LEGACY_12BIT_4MHZ",
            formation_mode=1,
            formation_pause_outer_iters=20,
            tp_pause_outer_iters=16,
        )


def test_pack_position_frame_right_aligns_position_without_truncation():
    runtime = bare_runtime()

    frame = runtime.pack_position_frame(
        position=0xAB,
        status=0,
        frame_width=16,
        position_offset=0,
        position_width=8,
        error_offset=0xFFFF,
        error_width=0,
        padding_width=0,
        encoding_type=0,
        alignment=1,
    )

    assert frame == 0x00AB


def test_set_positions_packs_the_active_profile_into_frame_slots():
    sim = make_paired_sim()
    runtime = SSIRuntime(sim)
    runtime.stage("CUSTOM_LEGACY_12BIT_4MHZ")
    runtime.set_positions([0xABC, 0x12A])

    assert runtime.read_raw_frames()[:2] == [0xABC, 0x12A]


def test_decode_position_gray_excess_requires_a_declared_code_window():
    runtime = bare_runtime()
    with pytest.raises(ValueError, match="position_count"):
        runtime.decode_position(0, encoding_type=2, width=8)


def test_decode_position_binary_and_tannenbaum_are_passthrough():
    runtime = bare_runtime()
    assert runtime.decode_position(0x1234, encoding_type=0, width=16) == 0x1234
    assert runtime.decode_position(0x1234, encoding_type=3, width=16) == 0x1234


def test_mailbox_decode_uses_last_applied_configuration_until_apply():
    """Staging a new wire encoding must not reinterpret the active mailbox."""
    sim = make_reader_only_sim()
    runtime = bare_runtime()
    runtime.sim = sim
    runtime.stage("CUSTOM_LEGACY_12BIT_4MHZ", topology=1)
    runtime.set_raw_frames([0xABC])
    runtime.apply()
    sim.ssi_inject(
        core="pru1", clk_pin=READER_CLK_PIN, data_pin=READER_DATA_PIN,
        value=0xABC, bits=12,
    )

    active_read = advance_reader_only_to_frame(sim, 2)
    assert active_read["position_value"] == 0xABC

    runtime.stage(encoding_type=1)
    staged_read = runtime.read_mailbox()
    assert staged_read["position_value"] == 0xABC


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


def test_stage_rejects_tv_at_or_after_the_reader_sample_point():
    runtime = bare_runtime()
    with pytest.raises(ValueError, match="tv_cycles"):
        runtime.stage(
            "CUSTOM_LEGACY_12BIT_4MHZ",
            sample_delay_cycles=15,
            tv_cycles=15,
        )


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
