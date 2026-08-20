"""Bit-level tests for the runtime-configurable SSI encoder emulator (PRU0).

These pokes config/frame-slot bytes directly into shared memory via
pru_io.ssi_config_abi (there is no ssi_runtime.py yet -- that is Task 5),
exactly like test_ssi_encoder_sequence_emulator.py pokes registers/memory
directly today.

Two harness styles are used:
  - A manual GPIO-driven harness (`clock_frame`) that drives the emulator's
    CLK input pin directly and reads its DATA output pin bit-by-bit. This is
    used for anything that needs to inspect exact bit patterns/timing that
    the fixed 12-bit reader can't express (>32-bit frames, fault-mode pin
    behavior, sub-bit timing).
  - The existing fixed ssi_reader_4mhz_12bit.asm as an end-to-end probe for
    one basic integration sanity check, matching the style already used by
    test_ssi_encoder_sequence_emulator.py.
"""
from pathlib import Path

from simulator import Simulator
from pru_io import ssi_config_abi as abi


ROOT = Path(__file__).parent.parent
SOURCE_DIR = ROOT / "source"
EMULATOR_SRC = (SOURCE_DIR / "ssi_generic_emulator" / "ssi_generic_emulator.asm").read_text()
READER_SRC = (SOURCE_DIR / "ssi_reader_4mhz_12bit" / "ssi_reader_4mhz_12bit.asm").read_text()
GENERIC_READER_SRC = (SOURCE_DIR / "ssi_generic_reader" / "ssi_generic_reader.asm").read_text()

CLK_PIN = 16   # this program's GPI clock input (matches ssi_encoder_sequence_emulator_12bit.asm)
DATA_PIN = 0   # this program's GPO data output


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------

def make_sim():
    """A fresh Simulator with only the generic emulator loaded on pru0."""
    sim = Simulator(config_path="nonexistent.cfg")
    errors = sim.load("pru0", EMULATOR_SRC, include_paths=[str(SOURCE_DIR)])
    assert errors == []
    sim.hard_reset()
    return sim


def idle_settle(sim, steps=3000):
    """Hold CLK idle-high for *steps* instructions.

    Used both for the initial boot (config load + debounce) and between
    frames (debounce must see DEBOUNCE_THRESH consecutive high polls before
    the next falling edge is recognized as a new frame).
    """
    sim.set_input("pru0", CLK_PIN, True)
    sim.step("pru0", steps)


def clock_frame(sim, n_bits, settle=400, skip_final_falling=False):
    """Manually clock one n_bits frame into/out of pru0, sampling DATA each bit.

    Mirrors the protocol pru0's bit loop expects: a falling edge starts the
    frame; each bit is sampled once tv has elapsed, then a rising edge (the
    reader's sample point) and a falling edge (next bit, or the frame's
    closing edge) follow. `skip_final_falling` omits that last falling edge,
    for the shortened-frame fault mode which abandons before it.
    """
    sim.set_input("pru0", CLK_PIN, False)
    bits = []
    for i in range(n_bits):
        sim.step("pru0", settle)
        bits.append(sim.io("pru0")["gpo_pins"][DATA_PIN])
        sim.set_input("pru0", CLK_PIN, True)
        sim.step("pru0", settle)
        last = i == n_bits - 1
        if not (last and skip_final_falling):
            sim.set_input("pru0", CLK_PIN, False)
            sim.step("pru0", settle)
    return bits


def bits_to_val(bits):
    val = 0
    for b in bits:
        val = (val << 1) | b
    return val


def configure(sim, **fields):
    """Write a full config block, defaulting to a plain 12-bit/no-fault setup."""
    base = dict(
        abi_version=1,
        struct_size=256,
        requested_generation=1,
        frame_width_bits=12,
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
    """Mark every slot from *start* through 15 as unused (sentinel)."""
    for i in range(start, 16):
        set_slot(sim, i, abi.FRAME_SLOT_UNUSED_SENTINEL)


def read_ack(sim):
    return int.from_bytes(
        sim.memory_read(abi.CONFIG_BASE + abi.CONFIG_PRU0_ACK_GENERATION_OFF, 4),
        "little",
    )


def run_single_slot(sim, frame_bits, width=12, **fault_fields):
    """One-slot setup + a single settled frame; returns the captured value."""
    configure(sim, frame_width_bits=width, **fault_fields)
    set_slot(sim, 0, frame_bits)
    clear_slots(sim, 1)
    idle_settle(sim)
    return bits_to_val(clock_frame(sim, width))


# ---------------------------------------------------------------------------
# Basic integration sanity (real reader, mirrors test_ssi_encoder_sequence_emulator.py)
# ---------------------------------------------------------------------------

def test_normal_transmission_matches_reader_capture():
    """A plain (no-fault) frame decodes correctly through the fixed 4 MHz reader."""
    sim = Simulator(config_path="nonexistent.cfg")
    assert sim.load("pru1", READER_SRC) == []
    assert sim.load("pru0", EMULATOR_SRC, include_paths=[str(SOURCE_DIR)]) == []
    sim.add_gpio_wire("pru1", 0, "pru0", CLK_PIN)
    sim.add_gpio_wire("pru0", DATA_PIN, "pru1", 8)
    sim.hard_reset()

    configure(sim, frame_width_bits=12, tv_cycles=5, tm_pause_outer_iters=15)
    set_slot(sim, 0, 0xABC)
    clear_slots(sim, 1)

    captured = []
    previous_frames = 0
    for _ in range(2_000_000):
        sim.step_paced("pru1", "pru0")
        frames = sim.registers("pru1")[20]
        if frames != previous_frames:
            captured.append(int.from_bytes(sim.memory_read(0x2000 + 16, 4), "little"))
            previous_frames = frames
            if len(captured) == 4:
                break

    # The very first frame can race pru0's own startup (config load + debounce)
    # before it starts driving real data; every frame after that must be stable.
    assert captured[1:] == [0xABC, 0xABC, 0xABC]
    assert read_ack(sim) == 1


# ---------------------------------------------------------------------------
# Fault modes (1..8)
# ---------------------------------------------------------------------------

def test_fault_mode_1_status_bits_is_pure_passthrough():
    """Mode 1 has no PRU0-side override: bits come through exactly as packed."""
    sim = make_sim()
    val = run_single_slot(sim, 0x321, fault_mode=1, fault_argument=999, fault_repeat_count=3)
    assert val == 0x321


def test_fault_mode_2_sentinel_value():
    """Mode 2 shifts out fault_argument zero-extended, ignoring the slot's bits."""
    sim = make_sim()
    val = run_single_slot(sim, 0xABC, fault_mode=2, fault_argument=0x5)
    assert val == 0x5


def test_fault_mode_3_all_ones():
    """Mode 3 drives every bit HIGH regardless of the slot's actual content."""
    sim = make_sim()
    val = run_single_slot(sim, 0x000, fault_mode=3)
    assert val == 0xFFF


def test_fault_mode_4_missing_response_no_bit_transitions():
    """Mode 4 never touches DATA: a slot with real low bits reads back as
    all-high, unlike the same slot transmitted normally."""
    normal_sim = make_sim()
    normal_val = run_single_slot(normal_sim, 0xA5A, fault_mode=0)
    assert normal_val == 0xA5A  # sanity: this slot really does have low bits

    missing_sim = make_sim()
    missing_val = run_single_slot(missing_sim, 0xA5A, fault_mode=4)
    assert missing_val == 0xFFF  # no participation -> DATA stays idle-high throughout


def test_fault_mode_5_shortened_frame():
    """Mode 5 sends only fault_argument bits (the slot's true MSBs), then
    abandons and returns to idle before the reader's clock train would end."""
    sim = make_sim()
    configure(sim, frame_width_bits=12, fault_mode=5, fault_argument=5)
    set_slot(sim, 0, 0xABC)
    clear_slots(sim, 1)
    idle_settle(sim)

    bits = clock_frame(sim, 5, skip_final_falling=True)
    assert bits_to_val(bits) == (0xABC >> 7)  # top 5 of 12 bits

    sim.step("pru0", 50)
    assert sim.io("pru0")["gpo_pins"][DATA_PIN] == 1  # already back to idle-high


def test_fault_mode_6_excessive_tv_delays_data_validity():
    """Mode 6 adds fault_argument to the tv delay (clamped at 256); data
    becomes valid measurably later than the equivalent normal frame."""

    def falling_edge_to_data_flip_delay(sim):
        sim.set_input("pru0", CLK_PIN, False)
        before = sim.io("pru0")["gpo_pins"][DATA_PIN]
        for i in range(1, 600):
            sim.step("pru0", 1)
            if sim.io("pru0")["gpo_pins"][DATA_PIN] != before:
                return i
        raise AssertionError("DATA never flipped")

    baseline_sim = make_sim()
    configure(baseline_sim, frame_width_bits=12, tv_cycles=5, fault_mode=0)
    set_slot(baseline_sim, 0, 0x7FF)  # MSB=0 -> guaranteed flip from idle-high
    clear_slots(baseline_sim, 1)
    idle_settle(baseline_sim)
    baseline_delay = falling_edge_to_data_flip_delay(baseline_sim)

    excessive_sim = make_sim()
    configure(excessive_sim, frame_width_bits=12, tv_cycles=5, fault_mode=6, fault_argument=50)
    set_slot(excessive_sim, 0, 0x7FF)
    clear_slots(excessive_sim, 1)
    idle_settle(excessive_sim)
    excessive_delay = falling_edge_to_data_flip_delay(excessive_sim)

    delta = excessive_delay - baseline_delay
    assert 50 <= delta <= 80  # ~fault_argument extra loop iterations, plus fixed overhead


def test_fault_mode_7_data_stuck_low_including_between_frames():
    """Mode 7 forces DATA low for the whole frame and does not return to
    idle-high afterward -- the one mode whose idle behavior differs."""
    sim = make_sim()
    configure(sim, frame_width_bits=12, fault_mode=7)
    set_slot(sim, 0, 0xFFF)  # a value that would read all-high under any other mode
    clear_slots(sim, 1)
    idle_settle(sim)

    bits = clock_frame(sim, 12)
    assert bits == [0] * 12

    sim.step("pru0", 50)
    assert sim.io("pru0")["gpo_pins"][DATA_PIN] == 0  # still low between frames

    idle_settle(sim, 500)
    assert sim.io("pru0")["gpo_pins"][DATA_PIN] == 0  # still low even after a long idle wait


def test_fault_mode_8_data_stuck_high():
    """Mode 8 looks identical to all-ones (mode 3) during a frame, and is
    indistinguishable from normal idle between frames, per design."""
    sim = make_sim()
    val = run_single_slot(sim, 0x000, fault_mode=8)
    assert val == 0xFFF

    sim.step("pru0", 50)
    assert sim.io("pru0")["gpo_pins"][DATA_PIN] == 1


# ---------------------------------------------------------------------------
# Generation handshake
# ---------------------------------------------------------------------------

def test_generation_change_applied_only_at_idle_boundary():
    """A mid-frame config+generation bump must not affect the frame already
    in flight, and pru0_ack_generation must not move until the next idle
    boundary, after which the new config takes effect."""
    sim = make_sim()
    configure(sim, requested_generation=1, frame_width_bits=12)
    set_slot(sim, 0, 0xAAA)
    clear_slots(sim, 1)
    idle_settle(sim)
    assert read_ack(sim) == 1

    ack_during_frame = {}

    def bump_mid_frame():
        # Real hosts never touch the ack fields themselves; preserve pru0's
        # current ack so this test isn't fooled by its own config rewrite.
        configure(
            sim,
            requested_generation=2,
            frame_width_bits=8,
            fault_mode=0,
            pru0_ack_generation=read_ack(sim),
        )
        set_slot(sim, 0, 0x3C)
        clear_slots(sim, 1)
        ack_during_frame["value"] = read_ack(sim)

    # Drive the in-flight (old, 12-bit) frame manually so we can bump config
    # partway through it.
    sim.set_input("pru0", CLK_PIN, False)
    bits = []
    for i in range(12):
        sim.step("pru0", 400)
        bits.append(sim.io("pru0")["gpo_pins"][DATA_PIN])
        sim.set_input("pru0", CLK_PIN, True)
        sim.step("pru0", 400)
        if i == 5:
            bump_mid_frame()
        sim.set_input("pru0", CLK_PIN, False)
        sim.step("pru0", 400)

    assert bits_to_val(bits) == 0xAAA  # old frame completed with the OLD config
    assert ack_during_frame["value"] == 1  # not yet applied mid-frame

    idle_settle(sim)
    assert read_ack(sim) == 2  # applied at the idle boundary right after

    next_bits = clock_frame(sim, 8)
    assert bits_to_val(next_bits) == 0x3C  # next frame uses the NEW config


def test_generation_change_during_idle_gap_after_debounce_does_not_deadlock():
    """Regression test for the topology=0 idle-gap reconfiguration deadlock
    fixed by replacing l_restart_sync's blocking `wbc r31, CLK_PIN` with a
    bounded, generation-checking poll loop (see
    .superpowers/sdd/generic_runtime_ssi_implementation_plan/
    task-3-fix-1-brief.md).

    Paired with the real ssi_generic_reader.asm (Task 4, unmodified),
    topology=0: settle a few frames, then time a config+generation bump to
    land right when this program has just finished debounce and is about to
    enter the post-debounce wait for the next falling edge -- exactly the
    PC this program used to park on forever with the old blocking `wbc`,
    since the reader (PRU1) would detour to wait for this program's ack
    instead of sending that edge. This must show real forward progress, not
    just "didn't hang for N steps" (which a bug that merely delays, rather
    than fixes, the deadlock could still pass): both pru0_ack_generation and
    pru1_ack_generation must reach the new generation, AND a real frame must
    complete under the new configuration afterward.
    """
    sim = Simulator(config_path="nonexistent.cfg")
    assert sim.load("pru1", GENERIC_READER_SRC, include_paths=[str(SOURCE_DIR)]) == []
    assert sim.load("pru0", EMULATOR_SRC, include_paths=[str(SOURCE_DIR)]) == []
    sim.add_gpio_wire("pru1", 0, "pru0", CLK_PIN)     # reader CLK out -> this program's CLK in
    sim.add_gpio_wire("pru0", DATA_PIN, "pru1", 8)    # this program's DATA out -> reader DATA in
    sim.hard_reset()

    def configure_paired(**fields):
        # A topology=0 loopback shape covering both programs' fields.
        # tm_pause_outer_iters=1 keeps this program's debounce threshold
        # tiny (16 polls) so it reliably finishes long before the reader's
        # own, much longer inter-frame pause (tp_pause_outer_iters=20,
        # 5000 loop iterations) -- the exact timing relationship
        # (tp_pause_outer_iters > tm_pause_outer_iters) the brief calls an
        # enforced invariant and the deadlock's actual trigger condition.
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
            tp_pause_outer_iters=20,
            capture_mode=0,
            tv_cycles=5,
            tm_pause_outer_iters=1,
            sequence_hold_mode=0,
            fault_mode=0,
            sequence_hold_count=1_000_000,
            fault_argument=0,
            fault_repeat_count=0,
        )
        base.update(fields)
        sim.memory.write(abi.CONFIG_BASE, abi.pack_config(**base))

    def frame_counter():
        return int.from_bytes(
            sim.memory_read(abi.MAILBOX_BASE + abi.MAILBOX_FRAME_COUNTER_OFF, 4), "little"
        )

    def pru1_ack():
        return int.from_bytes(
            sim.memory_read(abi.CONFIG_BASE + abi.CONFIG_PRU1_ACK_GENERATION_OFF, 4), "little"
        )

    configure_paired()
    set_slot(sim, 0, 0xAAA)
    clear_slots(sim, 1)

    # -- settle a few frames so both cores are past their own startup race --
    for _ in range(500_000):
        sim.step_paced("pru1", "pru0")
        if frame_counter() >= 3:
            break
    else:
        raise AssertionError("never settled to frame_counter=3")

    # -- find the exact PC this program reaches right after debounce passes
    #    and is about to enter the post-debounce wait for the falling edge
    #    (this is the PC the old code's single blocking `wbc` occupied) --
    wait_pc = sim.cores["pru0"]._parser.labels["l_wait_falling_edge"]
    for _ in range(50_000):
        sim.step_paced("pru1", "pru0")
        if sim.cores["pru0"].pc == wait_pc:
            break
    else:
        raise AssertionError("pru0 never reached the post-debounce wait loop")

    # -- inject the config+generation bump right here: debounce has already
    #    passed, and the reader is still deep in its own (much longer)
    #    inter-frame pause, nowhere near its own generation check. This is
    #    the exact deadlock trigger window from the brief's root-cause
    #    analysis. Preserve the current acks so this rewrite doesn't fake a
    #    false "already acked" read.
    old_ack0 = read_ack(sim)
    old_ack1 = pru1_ack()
    configure_paired(
        requested_generation=2,
        frame_width_bits=8,
        position_width_bits=8,
        pru0_ack_generation=old_ack0,
        pru1_ack_generation=old_ack1,
    )
    set_slot(sim, 0, 0x3C)
    clear_slots(sim, 1)

    # -- both acks must reach the new generation within a large-but-bounded
    #    step count; the old code (a single blocking wbc here) would spin
    #    forever at the same two frozen PCs instead --
    for _ in range(50_000):
        sim.step_paced("pru1", "pru0")
        if read_ack(sim) == 2 and pru1_ack() == 2:
            break
    else:
        raise AssertionError("acks never reached the new generation -- deadlocked")
    assert read_ack(sim) == 2
    assert pru1_ack() == 2

    # -- real forward progress, not just "didn't hang": at least one frame
    #    must actually complete under the NEW configuration afterward --
    fc_at_apply = frame_counter()
    for _ in range(50_000):
        sim.step_paced("pru1", "pru0")
        if frame_counter() > fc_at_apply:
            break
    else:
        raise AssertionError("no frame completed under the new configuration")

    mb = abi.unpack_mailbox(sim.memory_read(abi.MAILBOX_BASE, 64))
    assert mb["position_value"] == 0x3C   # the NEW config's data actually flowed through
    assert mb["frame_counter"] > fc_at_apply


# ---------------------------------------------------------------------------
# 64-bit frame data straddling the two 32-bit halves
# ---------------------------------------------------------------------------

def test_frame_width_40_bits_straddles_both_halves():
    sim = make_sim()
    value = (0xAA << 32) | 0x00000055
    val = run_single_slot(sim, value, width=40)
    assert val == value


def test_frame_width_64_bits_straddles_both_halves():
    sim = make_sim()
    value = (0xDEADBEEF << 32) | 0xCAFEF00D
    val = run_single_slot(sim, value, width=64)
    assert val == value


# ---------------------------------------------------------------------------
# Sequence hold modes
# ---------------------------------------------------------------------------

def _run_frames(sim, count, width=12):
    values = []
    for _ in range(count):
        values.append(bits_to_val(clock_frame(sim, width)))
        idle_settle(sim, 1000)
    return values


def test_sequence_hold_mode_0_frame_count_with_sentinel_wrap():
    """hold_mode=0: each slot holds for exactly sequence_hold_count frames,
    then advances; a sentinel-terminated sequence wraps back to slot 0."""
    sim = make_sim()
    configure(sim, sequence_hold_mode=0, sequence_hold_count=2, frame_width_bits=12)
    set_slot(sim, 0, 0xAAA)
    set_slot(sim, 1, 0xBBB)
    clear_slots(sim, 2)
    idle_settle(sim)

    values = _run_frames(sim, 8)
    assert values == [0xAAA, 0xAAA, 0xBBB, 0xBBB, 0xAAA, 0xAAA, 0xBBB, 0xBBB]


def test_sequence_hold_mode_0_wraps_at_16th_slot():
    """With all 16 slots populated (no sentinel), the sequence wraps from
    slot 15 back to slot 0."""
    sim = make_sim()
    configure(sim, sequence_hold_mode=0, sequence_hold_count=1, frame_width_bits=12)
    for i in range(16):
        set_slot(sim, i, 0x100 + i)
    idle_settle(sim)

    values = _run_frames(sim, 18)
    assert values == [0x100 + i for i in range(16)] + [0x100, 0x101]


def test_sequence_hold_mode_1_time_based():
    """hold_mode=1: advance once the estimated-elapsed-cycles accumulator
    (frame_width_bits*8 + tv_cycles per completed frame) reaches
    sequence_hold_count."""
    sim = make_sim()
    # estimate/frame = 8*8 + 8 = 72; hold_count=150 -> advances on the 3rd
    # completed frame (216 >= 150), not the 2nd (144 < 150).
    configure(
        sim,
        sequence_hold_mode=1,
        sequence_hold_count=150,
        frame_width_bits=8,
        tv_cycles=8,
    )
    set_slot(sim, 0, 0x11)
    set_slot(sim, 1, 0x22)
    clear_slots(sim, 2)
    idle_settle(sim)

    values = _run_frames(sim, 6, width=8)
    assert values == [0x11, 0x11, 0x11, 0x22, 0x22, 0x22]
