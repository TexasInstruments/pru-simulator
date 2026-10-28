"""ICSSG PWM (SPRUIM2J 6.4.10): register, state-machine and trip tests.

Register offsets, bit positions and encodings are written out from the TRM
register tables (ICSSG_PWMn at CFG+0x130.., ICSSG_PWMm_k at CFG+0x140..) rather
than imported.  Expected edge times are hand-computed IEP clocks for
DEFAULT_INC=1 from counter start: compare value N is hit on clock N.

The firmware tests drive everything through SBCO/LBCO; Python only loads the
program and, where a test needs an external pad, drives that pad.

Register-level expectations marked "measured on LP-AM243 ICSSG0" are the raw
words read back from silicon with the software trip sources (bit 20 = source 0,
bit 19 = source 1), taken from the raw register-level probe dumps (the measured
words are in icssg_pwm_silicon_words.py).  Pad levels, edge times and
dead time were not measured: those tests check the model against the TRM text
only and are simulator-tested only.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mcp_server.vcd_export import export_pin_waveform           # noqa: E402
from simulator import Simulator                                  # noqa: E402
from tests.icssg_pwm_silicon_words import BPWM, BPWM2, BPWM3     # noqa: E402

CFG = 0x26000
PWM0_CFG = 0x130
GLOBAL_CFG, COUNT, CMP_CFG = 0x00, 0x10, 0x70
CMP0, CMP1, CMP2, CMP7 = 0x78, 0x80, 0x88, 0xB0       # IEP_CMP0_REG0 + 8 * n

OVER_ERR = 1 << 19
POS_ERR = 1 << 20
TRIP_RESET = 1 << 18
CMP0_EN = 1 << 17
TRIP_S = 1 << 30
MASK = lambda n: 1 << (8 + n)                           # noqa: E731
ALLM = 0x1FF << 8                                       # every source enabled (set = enable)
TRIP_S_CLEAR = 1 << 23          # TRIP_VEC[2] position: a write of 1 clears TRIP_S
# TRIP_VEC is one-hot (measured): source n sets bit n of the field.
VEC = lambda n: (1 << n) << 21                          # noqa: E731

# state word: INIT POS[1:0] NEG[3:2], TRIP POS[5:4] NEG[7:6], ACT POS[9:8] NEG[11:10]
# encodings: init/trip 0=Z 1=L 2=H; active 0=toggle 1=L 2=H
def state_word(init=(0, 0), trip=(0, 0), act=(0, 0)):
    return (init[0] | init[1] << 2 | trip[0] << 4 | trip[1] << 6
            | act[0] << 8 | act[1] << 10)


def make_sim(config="nonexistent.cfg"):
    sim = Simulator(config_path=config)
    core = sim.cores["pru0"]
    core.constant_table.set(26, 0x0002E000)             # IEP0
    core.constant_table.set(4, CFG)                     # ICSS CFG
    return sim


def pwm_wr(off, val):
    return f"ldi r10, {off}\n ldi32 r1, {val}\n sbco &r1, c4, r10, 4\n"


def iep_wr(off, val):
    return f"ldi32 r1, {val}\n sbco &r1, c26, {off}, 4\n"


def pwm_rd(off, reg):
    return f"ldi r10, {off}\n lbco &{reg}, c4, r10, 4\n"


START = iep_wr(GLOBAL_CFG, 0x11)          # CNT_ENABLE, DEFAULT_INC = 1


def wait_count_ge(n, label):
    return f"{label}:\n lbco &r4, c26, {COUNT}, 4\n ldi32 r5, {n}\n qbgt {label}, r4, r5\n"


def run(sim, src, steps=50000):
    assert sim.load("pru0", src) == []
    sim.cores["pru0"].run(max_steps=steps)
    assert sim.cores["pru0"].halted
    assert sim.cores["pru0"].fault is None
    return sim.registers("pru0")


def fw_trip_clear(base, off=PWM0_CFG):
    """The measured TRIP_S clear sequence, source low: bit18=1, bit18=0, bit23=1, 0."""
    return (pwm_wr(off, base | TRIP_RESET) + pwm_wr(off, base)
            + pwm_wr(off, base | TRIP_S_CLEAR) + pwm_wr(off, base))


def trip_clear(sim, base, off=PWM0_CFG):
    prime(sim, [(CFG + off, base | TRIP_RESET), (CFG + off, base),
                (CFG + off, base | TRIP_S_CLEAR), (CFG + off, base)])


def fw_cfg(sim, off=PWM0_CFG):
    """Read an ICSSG_PWMn word with a real LBCO on pru0 (no Python attribute)."""
    sim.cores["pru0"].reset()
    return run(sim, pwm_rd(off, "r2") + "halt\n")[2]


def edges_after_start(sim):
    return [e for e in sim.pwm.edges if e[0] > 0]


def reg(sim, off):
    return int.from_bytes(sim.memory_read(CFG + off, 4), "little")


def prime(sim, mem_writes):
    for off, val in mem_writes:
        sim.memory.write(off, val.to_bytes(4, "little"))


# --------------------------------------------------------------------------
# edge timing, dead time, R31
# --------------------------------------------------------------------------
def test_firmware_complementary_pair_edges_and_dead_time_in_iep_clocks():
    sim = make_sim()
    sim.set_input("pru0", 3, True)                      # a live GPI so R31 is not trivially 0
    # PWM0_0: POS init L, NEG init H, both toggle on their compare.
    src = (pwm_wr(0x140, state_word(init=(1, 2)))
           + iep_wr(CMP1, 110) + iep_wr(CMP2, 100) + iep_wr(CMP_CFG, 0xC)
           + "mov r6, r31\n" + START
           + wait_count_ge(150, "w1")
           + iep_wr(CMP1, 300) + iep_wr(CMP2, 310)      # POS falls, then NEG rises
           + wait_count_ge(320, "w2")
           + "mov r7, r31\n halt\n")
    regs = run(sim, src)
    # NEG falls at 100, POS rises at 110 (dead time 10), POS falls at 300,
    # NEG rises at 310 (dead time 10).  Pad 0 = PWM0_0_POS, pad 1 = PWM0_0_NEG.
    assert edges_after_start(sim) == [(100, 1, 0), (110, 0, 1), (300, 0, 0), (310, 1, 1)]
    dead_ticks = 110 - 100
    assert dead_ticks * 1000 / float(sim.iep.clock_mhz) == 50.0       # ns at 200 MHz
    assert sim.io("pru0")["pwm_pins"][:2] == [0, 1]
    assert sim.io("pru0")["pwm_pins"][2:] == [None] * 22
    # Pad levels never appear in R31.
    assert regs[6] == regs[7] == 1 << 3
    assert sim.io("pru0")["gpi_pins"] == [0, 0, 0, 1] + [0] * 16


def test_firmware_active_levels_simultaneous_compares_and_second_set():
    sim = make_sim()
    src = (pwm_wr(0x140, state_word(init=(1, 1), act=(2, 2)))     # PWM0_0
           + pwm_wr(0x14C, state_word(init=(1, 1), act=(2, 1)))   # PWM1_0
           + iep_wr(CMP1, 100) + iep_wr(CMP2, 100) + iep_wr(CMP7, 100)
           + iep_wr(CMP_CFG, 0x4 | 0x8 | (1 << 8))                # CMP1, CMP2, CMP7
           + START + wait_count_ge(120, "w") + "halt\n")
    run(sim, src)
    assert edges_after_start(sim) == [(100, 0, 1), (100, 1, 1), (100, 6, 1)]
    assert sim.io("pru0")["pwm_pins"][:8] == [1, 1, None, None, None, None, 1, 0]


def test_compare_event_does_not_touch_pads_of_a_tripped_set_and_set_state_is_shared():
    sim = make_sim()
    sim.memory.write(CFG + 0x140, state_word(init=(1, 1), act=(2, 2)).to_bytes(4, "little"))
    sim.memory.write(CFG + 0x144, state_word(init=(1, 1), act=(2, 2)).to_bytes(4, "little"))
    sim.iep.write32(CMP1, 10)
    sim.iep.write32(CMP2 + 8, 20)               # CMP3 -> PWM0_1_POS
    sim.iep.write32(CMP_CFG, 0x4 | 0x10)
    sim.iep.write32(GLOBAL_CFG, 0x11)
    for _ in range(10):
        sim.iep.tick()
    # First compare moves the whole set to active, but only its own pad acts.
    assert sim.pwm.state[0] == "active"
    assert sim.io("pru0")["pwm_pins"][:4] == [1, 0, 0, 0]
    for _ in range(10):
        sim.iep.tick()
    assert sim.io("pru0")["pwm_pins"][:4] == [1, 0, 1, 0]


# --------------------------------------------------------------------------
# trip: sources, mask, priority, latching, recovery
# --------------------------------------------------------------------------
def test_firmware_software_trip_blocks_compares_and_trip_reset_recovers():
    sim = make_sim()
    word = state_word(init=(1, 2), trip=(1, 1), act=(0, 0))
    src = (pwm_wr(0x140, word)
           + iep_wr(CMP1, 100) + iep_wr(CMP2, 150) + iep_wr(CMP_CFG, 0xC)
           + START + wait_count_ge(105, "w1")
           + pwm_wr(PWM0_CFG, MASK(1) | OVER_ERR) + pwm_rd(PWM0_CFG, "r2")
           + wait_count_ge(170, "w2")                    # CMP2 at 150 must be ignored
           + pwm_wr(PWM0_CFG, MASK(1) | TRIP_RESET) + pwm_rd(PWM0_CFG, "r3")
           + pwm_wr(PWM0_CFG, MASK(1)) + pwm_rd(PWM0_CFG, "r6")
           + iep_wr(CMP1, 300) + wait_count_ge(310, "w3") + "halt\n")
    regs = run(sim, src)
    assert regs[2] == TRIP_S | VEC(1) | MASK(1) | OVER_ERR
    # measured on LP-AM243 ICSSG0 (register-level probe): TRIP_RESET is a level
    # that reads back, clears TRIP_VEC and leaves TRIP_S set.
    assert regs[3] == TRIP_S | TRIP_RESET | MASK(1)
    assert regs[6] == TRIP_S | MASK(1)
    levels = [(t, p, v) for t, p, v in sim.pwm.edges if t > 0]
    assert (100, 0, 1) in levels                         # active toggle before the trip
    assert not any(t == 150 for t, _, _ in levels)       # no edge while tripped
    # After reset the pads are in the initial state; the next POS compare toggles L->H.
    assert (300, 0, 1) in levels
    assert sim.io("pru0")["pwm_pins"][:2] == [1, 1]


def test_firmware_both_sources_in_one_write_latch_both_vec_bits():
    # measured on LP-AM243 ICSSG0 (register-level probe): write 0x00180300 reads
    # back 0x40780300, TRIP_VEC = 0b011 (no priority, no single-bit latch)
    sim = make_sim()
    src = (pwm_wr(0x140, state_word(init=(1, 1), trip=(2, 2)))
           + pwm_wr(PWM0_CFG, 0x00180300) + pwm_rd(PWM0_CFG, "r2")
           + "halt\n")
    regs = run(sim, src)
    assert regs[2] == 0x40780300
    assert regs[2] == TRIP_S | VEC(0) | VEC(1) | MASK(0) | MASK(1) | OVER_ERR | POS_ERR


def test_firmware_trip_vec_follows_asserted_sources_and_freezes_when_all_deassert():
    # measured on LP-AM243 ICSSG0 (register-level probe): src0, src0+src1,
    # src1 only, none: VEC 0x001, 0x003, 0x002, then frozen at 0x002 until
    # TRIP_RESET (VEC 0x000, TRIP_S still 1, bit 18 reads back 1).
    sim = make_sim()
    src = (pwm_wr(0x140, state_word(init=(1, 1), trip=(2, 2)))
           + pwm_wr(PWM0_CFG, 0x00100300) + pwm_rd(PWM0_CFG, "r2")
           + pwm_wr(PWM0_CFG, 0x00180300) + pwm_rd(PWM0_CFG, "r3")
           + pwm_wr(PWM0_CFG, 0x00080300) + pwm_rd(PWM0_CFG, "r4")
           + pwm_wr(PWM0_CFG, 0x00000300) + pwm_rd(PWM0_CFG, "r5")
           + pwm_wr(PWM0_CFG, 0x00040300) + pwm_rd(PWM0_CFG, "r6")
           + "halt\n")
    regs = run(sim, src)
    assert regs[2:7] == [0x40300300, 0x40780300, 0x40480300, 0x40400300, 0x40040300]
    assert sim.pwm.state[0] == "initial"                  # TRIP_RESET returned the pads


def test_firmware_trip_mask_set_bit_enables_the_source():
    # measured on LP-AM243 ICSSG0: mask 0 + OVER_ERR does not trip (register-level probe);
    # mask bit1 + OVER_ERR trips with VEC 0x002 ; mask bit0 + OVER_ERR, or
    # mask bit1 + POS_ERR, does not (register-level probe).
    sim = make_sim()
    src = (pwm_wr(0x140, state_word(init=(1, 1), trip=(2, 2)))
           # reset value 0 enables nothing: both software levels are ignored
           + pwm_wr(PWM0_CFG, OVER_ERR | POS_ERR) + pwm_rd(PWM0_CFG, "r2")
           # crossed enables: bit0 with OVER_ERR only
           + pwm_wr(PWM0_CFG, MASK(0) | OVER_ERR) + pwm_rd(PWM0_CFG, "r3")
           # only OVER_ERR enabled: it is the cause although POS_ERR is also asserted
           + pwm_wr(PWM0_CFG, MASK(1) | OVER_ERR | POS_ERR) + pwm_rd(PWM0_CFG, "r4")
           # source 8 enabled alone with nothing asserted adds nothing
           + pwm_wr(PWM0_CFG, MASK(8)) + pwm_rd(PWM0_CFG, "r5")
           + "halt\n")
    regs = run(sim, src)
    assert regs[2] == OVER_ERR | POS_ERR
    assert regs[3] == MASK(0) | OVER_ERR
    assert regs[4] == TRIP_S | VEC(1) | MASK(1) | OVER_ERR | POS_ERR
    assert regs[5] == TRIP_S | VEC(1) | MASK(8)           # VEC frozen, TRIP_S sticky


def test_firmware_tz_in_not_enabled_then_enabled_trips_and_sets_vec_5():
    sim = make_sim()
    first = (pwm_wr(0x140, state_word(init=(1, 1), trip=(2, 0)))
             + pwm_wr(PWM0_CFG, 0))
    assert sim.load("pru0", first + "halt\n") == []
    sim.cores["pru0"].run(max_steps=100)
    sim.set_pwm_trip_input(0, 5, True)                    # PWM0_TZ_IN pad goes high
    assert reg(sim, PWM0_CFG) == 0                        # TZ_IN not enabled: no trip
    src = (pwm_rd(PWM0_CFG, "r2") + pwm_wr(PWM0_CFG, MASK(5)) + pwm_rd(PWM0_CFG, "r3") + "halt\n")
    sim.cores["pru0"].reset()
    regs = run(sim, src)
    assert regs[2] == 0
    assert regs[3] == TRIP_S | VEC(5) | MASK(5)
    assert sim.io("pru0")["pwm_pins"][:2] == [1, None]
    assert sim.registers("pru0")[31] == 0                 # R31 untouched by the pad


def test_trip_reset_with_source_still_asserted_trips_again():
    # measured on LP-AM243 ICSSG0 (register-level probe): bit18=1 with OVER_ERR
    # still high gives VEC 0 and TRIP_S unchanged; releasing bit18 gives VEC 0x002
    # again at once.
    sim = make_sim()
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1), trip=(2, 2)))])
    sim.memory.write(CFG + PWM0_CFG, MASK(3).to_bytes(4, "little"))
    sim.pwm.set_trip_input(0, 3, True)                    # SD short, phase 0 (source 3)
    assert sim.pwm.trip_vec[0] == 1 << 3 and sim.pwm.trip_s[0]
    sim.memory.write(CFG + PWM0_CFG, (TRIP_RESET | MASK(3)).to_bytes(4, "little"))
    assert sim.pwm.trip_vec[0] == 0 and sim.pwm.trip_s[0] and sim.pwm.state[0] == "initial"
    sim.memory.write(CFG + PWM0_CFG, MASK(3).to_bytes(4, "little"))
    assert sim.pwm.trip_s[0] and sim.pwm.trip_vec[0] == 1 << 3 and sim.pwm.state[0] == "trip"
    sim.pwm.set_trip_input(0, 3, False)
    trip_clear(sim, MASK(3))
    assert not sim.pwm.trip_s[0] and sim.pwm.state[0] == "initial"


def test_firmware_cmp0_returns_pads_to_initial_only_when_enabled_and_keeps_trip_status():
    def program(enabled):
        sim = make_sim()
        en = (CMP0_EN if enabled else 0) | MASK(1)
        src = (pwm_wr(0x140, state_word(init=(1, 1), trip=(2, 2)))
               + iep_wr(CMP0, 300) + iep_wr(CMP_CFG, 0x3)       # CMP0 + counter reset
               + pwm_wr(PWM0_CFG, en | OVER_ERR) + pwm_wr(PWM0_CFG, en)
               + pwm_rd(PWM0_CFG, "r2")
               + START
               + "ldi r8, 0\nspin:\n add r8, r8, 1\n ldi r9, 200\n qbgt spin, r8, r9\n"
               + pwm_rd(PWM0_CFG, "r3")
               + pwm_wr(PWM0_CFG, en | TRIP_RESET) + pwm_rd(PWM0_CFG, "r4") + "halt\n")
        return sim, run(sim, src), en

    sim, regs, en = program(True)
    assert regs[2] == TRIP_S | VEC(1) | en
    # Pads are back to initial L at the CMP0 hit (tick 300) ...
    assert (300, 0, 0) in sim.pwm.edges and (300, 1, 0) in sim.pwm.edges
    # ... but TRIP_S/TRIP_VEC are untouched by CMP0 (measured on LP-AM243 ICSSG0,
    # register-level probe, with the source dropped) and TRIP_RESET clears only
    # TRIP_VEC (TRIP_S is sticky, bit 18 reads back).
    assert regs[3] == TRIP_S | VEC(1) | en
    assert regs[4] == TRIP_S | TRIP_RESET | en

    sim, regs, en = program(False)
    assert regs[3] == TRIP_S | VEC(1) | en                  # stays latched without CMP0_EN
    assert sim.io("pru0")["pwm_pins"][:2] == [0, 0]         # firmware reset already ran
    assert not any(e[0] == 300 for e in sim.pwm.edges)      # CMP0 itself did nothing


def test_cmp0_returns_active_set_to_initial_before_same_clock_compare_reactivates_it():
    sim = make_sim()
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1), act=(2, 2))),
                (CFG + PWM0_CFG, CMP0_EN)])
    sim.iep.write32(CMP0, 100)
    sim.iep.write32(CMP1, 100)
    sim.iep.write32(CMP_CFG, 0x1 | 0x2 | 0x4)
    sim.iep.write32(GLOBAL_CFG, 0x11)
    for _ in range(200):
        sim.iep.tick()
    # period 100 clocks.  tick 100: CMP0 (initial: nothing), CMP1 -> active/H.
    # tick 200: CMP0 returns to initial (L) then CMP1 re-activates (H).
    assert [e for e in sim.pwm.edges if e[0] > 0 and e[1] == 0] == [
        (100, 0, 1), (200, 0, 0), (200, 0, 1)]


# --------------------------------------------------------------------------
# debounce / glitch filter and the external trip pad
# --------------------------------------------------------------------------
def _debounce_sim(value):
    sim = make_sim()
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1), trip=(2, 2))),
                (CFG + PWM0_CFG, value | ALLM)])
    sim.iep.write32(GLOBAL_CFG, 0x11)
    return sim


def _tick(sim, n):
    for _ in range(n):
        sim.iep.tick()


@pytest.mark.parametrize("width,trips", [(15, False), (16, True), (17, True)])
def test_glitch_filter_passes_a_pulse_of_at_least_debounce_core_clocks(width, trips):
    sim = _debounce_sim(16)                  # 16 core clocks == 16 IEP clocks (both 200 MHz)
    _tick(sim, 5)
    sim.set_pwm_trip_input(0, 5, True)
    _tick(sim, width)
    sim.set_pwm_trip_input(0, 5, False)
    _tick(sim, 40)
    word = fw_cfg(sim)                       # LBCO of the register, not a Python attribute
    if trips:
        assert word == TRIP_S | VEC(5) | ALLM | 16
        # trip takes effect when the pulse has been high 16 clocks: 5 + 16
        assert [e for e in sim.pwm.edges if e[0] > 0] == [(21, 0, 1), (21, 1, 1)]
    else:
        assert word == ALLM | 16
        assert not [e for e in sim.pwm.edges if e[0] > 0]


def test_debounce_zero_bypasses_the_filter_and_unfiltered_sources_trip_at_once():
    sim = _debounce_sim(0)
    _tick(sim, 3)
    sim.set_pwm_trip_input(0, 5, True)
    assert sim.pwm.trip_vec[0] == 1 << 5
    sim = _debounce_sim(16)
    _tick(sim, 3)
    sim.set_pwm_trip_input(0, 2, True)        # SD short is not filtered
    assert sim.pwm.trip_vec[0] == 1 << 2


def test_debounce_timer_converts_core_clocks_to_iep_clocks():
    sim = _debounce_sim(10)
    sim.iep.set_clock_mhz(100)               # IEP half the 200 MHz core clock
    assert sim.pwm.debounce_ticks(0) == 5


def test_trip_mask_polarity_is_set_bit_enables_for_external_sources_too():
    # polarity measured on LP-AM243 ICSSG0 with the software sources (see
    # test_firmware_trip_mask_set_bit_enables_the_source); sources 2..8 follow the
    # same mask by assumption.
    sim = make_sim()
    sim.pwm.set_trip_input(0, 5, True)
    assert not sim.pwm.trip_s[0]              # bit 5 clear: TZ_IN not enabled
    sim.memory.write(CFG + PWM0_CFG, MASK(5).to_bytes(4, "little"))
    assert sim.pwm.trip_s[0] and sim.pwm.trip_vec[0] == 1 << 5


# --------------------------------------------------------------------------
# PWM2/PWM3, registers, device gating
# --------------------------------------------------------------------------
@pytest.mark.parametrize("off", [0x134, 0x138, 0x13C])
def test_firmware_pwm1_to_pwm3_latch_the_trip_registers_like_pwm0(off):
    # measured on LP-AM243 ICSSG0: PWM1, PWM2 and PWM3 latch TRIP_S/VEC from the
    # software sources and take the bit 23 clear exactly like PWM0
    # (register-level probe,
    #).  PWM2/PWM3 have no pad side (IEP1 is not modelled).
    sim = make_sim()
    src = (pwm_wr(off, 0x00080200) + pwm_rd(off, "r2")
           + pwm_wr(off, 0x00000200) + pwm_rd(off, "r3")
           + pwm_wr(off, 0x00040200) + pwm_rd(off, "r4")
           + pwm_wr(off, 0x00000000) + pwm_rd(off, "r5")
           + pwm_wr(off, 0x00800000) + pwm_rd(off, "r6")
           + pwm_wr(off, 0xFFFFFFFF) + pwm_rd(off, "r7")
           + "halt\n")
    regs = run(sim, src)
    assert regs[2:8] == [0x40480200, 0x40400200, 0x40040200, 0x40000000, 0x00000000, 0x001FFFFF]
    assert sim.io("pru0")["pwm_pins"][12:] == [None] * 12


def test_pwm2_pwm3_have_no_pad_state_machine_but_their_trip_registers_work():
    sim = make_sim()
    prime(sim, [(CFG + 0x158, 0xFFFFFFFF), (CFG + 0x16C, 0x00000A5A)])
    assert [reg(sim, 0x158), reg(sim, 0x16C)] == [0x0FFF, 0x0A5A]
    sim.pwm.set_trip_input(2, 5, True)
    assert not sim.pwm.trip_s[2]                          # TZ_IN not enabled
    prime(sim, [(CFG + 0x138, MASK(5))])
    assert reg(sim, 0x138) == TRIP_S | VEC(5) | MASK(5)
    assert sim.io("pru0")["pwm_pins"][12:] == [None] * 12
    assert sim.pwm.state[2:] == ["initial", "initial"]


@pytest.mark.parametrize("cfg", ["memory_am261x.cfg", "memory_am263x.cfg"])
def test_icssm_pwm_registers_read_zero_by_default_and_fault_in_strict_mode(
        cfg, tmp_path, sim_config):
    # Main's absent-block model owns CFG 0x130..0x16C on ICSS-M: default
    # reads are zero and writes are discarded. Strict mode flags the same
    # accesses; it does not instantiate an ICSSG PWM peripheral.
    root = os.path.join(os.path.dirname(__file__), "..", "config")
    sim = Simulator(config_path=os.path.join(root, cfg))
    assert not hasattr(sim, "pwm")
    for off in (0x130, 0x13C, 0x140, 0x16C):
        assert sim.memory_read(CFG + off, 4) == bytes(4)
        sim.memory.write(CFG + off, (1).to_bytes(4, "little"))
        assert sim.memory_read(CFG + off, 4) == bytes(4)
    target = "AM261x" if "261x" in cfg else "AM263x"
    strict = Simulator(sim_config(target=target, icssg_only_blocks="strict"))
    assert not hasattr(strict, "pwm")
    for off in (0x130, 0x13C, 0x140, 0x16C):
        with pytest.raises(ValueError, match="ICSSG PWM registers.*absent on ICSS-M"):
            strict.memory.read(CFG + off, 4)
        with pytest.raises(ValueError, match="ICSSG PWM registers.*absent on ICSS-M"):
            strict.memory.write(CFG + off, (1).to_bytes(4, "little"))
    assert sim.io("pru0")["pwm_pins"] == []
    with pytest.raises(ValueError):
        sim.set_pwm_trip_input(0, 5, True)
    with pytest.raises(ValueError):
        export_pin_waveform(sim, "pru0", str(tmp_path / "x.vcd"), include_pwm=True)
    # an IEP compare event changes nothing on these devices
    sim.iep.write32(CMP1, 3)
    sim.iep.write32(CMP_CFG, 0x4)
    sim.iep.write32(GLOBAL_CFG, 0x11)
    _tick(sim, 5)


@pytest.mark.parametrize("cfg", ["memory_am243x.cfg", "nonexistent.cfg"])
def test_icssg_target_maps_the_registers(cfg):
    root = os.path.join(os.path.dirname(__file__), "..", "config")
    sim = Simulator(config_path=os.path.join(root, cfg) if cfg != "nonexistent.cfg" else cfg)
    sim.memory.write(CFG + 0x16C, (0xABC).to_bytes(4, "little"))
    assert reg(sim, 0x16C) == 0xABC
    with pytest.raises(ValueError):
        sim.memory.read(CFG + 0x170, 4)


# --------------------------------------------------------------------------
# reset, snapshot, VCD
# --------------------------------------------------------------------------
def test_hardware_reset_returns_registers_pads_and_state_to_reset_values():
    for reset in (lambda s: s.hard_reset(), lambda s: s.iep.hardware_reset()):
        sim = make_sim()
        prime(sim, [(CFG + 0x140, state_word(init=(1, 2), trip=(2, 2))),
                    (CFG + PWM0_CFG, MASK(1) | 0x5 | OVER_ERR | CMP0_EN)])
        assert sim.pwm.state[0] == "trip"
        assert sim.io("pru0")["pwm_pins"][:2] == [1, 1]
        reset(sim)
        assert [reg(sim, o) for o in (0x130, 0x140)] == [0, 0]
        assert sim.io("pru0")["pwm_pins"] == [None] * 24
        assert sim.pwm.state == ["initial"] * 4 and not sim.pwm.edges


def test_snapshot_restore_replays_identically_including_pending_debounce():
    sim = _debounce_sim(16)
    prime(sim, [(CFG + 0x144, state_word(init=(1, 1)))])
    sim.iep.write32(CMP1, 30)
    sim.iep.write32(CMP_CFG, 0x4)
    _tick(sim, 4)
    sim.set_pwm_trip_input(0, 5, True)                    # filter timer pending
    pwm_snap, iep_snap = sim.pwm.snapshot(), sim.iep.snapshot()
    _tick(sim, 40)
    expected = (list(sim.pwm.edges), list(sim.pwm.levels), sim.pwm.trip_vec[0])
    assert sim.pwm.trip_s[0]
    sim.pwm.restore(pwm_snap)
    sim.iep.restore(iep_snap)
    assert not sim.pwm.trip_s[0] and sim.pwm.pending
    _tick(sim, 40)
    assert (list(sim.pwm.edges), list(sim.pwm.levels), sim.pwm.trip_vec[0]) == expected


def test_vcd_shows_pwm_pads_with_z_and_leaves_gpio_signals_alone(tmp_path):
    sim = make_sim()
    src = (pwm_wr(0x140, state_word(init=(1, 2)))
           + iep_wr(CMP1, 110) + iep_wr(CMP2, 100) + iep_wr(CMP_CFG, 0xC)
           + START + wait_count_ge(130, "w") + "halt\n")
    assert sim.load("pru0", src) == []
    path = tmp_path / "pwm.vcd"
    info = export_pin_waveform(sim, "pru0", str(path), max_steps=5000,
                               pins="0", include_pwm=True)
    assert info["complete"]
    text = path.read_text()
    ids = {}
    for line in text.splitlines():
        if line.startswith("$var"):
            parts = line.split()
            ids[parts[4]] = parts[3]
    history = {name: [] for name in ("pwm_0", "pwm_1", "pwm_2", "gpo_0")}
    for line in text.splitlines():
        for name in history:
            if line.endswith(ids[name]) and line[0] in "01zx" and not line.startswith("$"):
                history[name].append(line[0])
    assert history["pwm_0"] == ["z", "0", "1"]            # Z -> initial L -> active H
    assert history["pwm_1"] == ["z", "1", "0"]            # Z -> initial H -> active L
    assert history["pwm_2"] == ["z"]
    assert history["gpo_0"] == ["0"]


# --------------------------------------------------------------------------
# review round: sources 5..8, masks, debounce width and rounding, end-to-end reads
# --------------------------------------------------------------------------
@pytest.mark.parametrize("source", [5, 6, 7, 8])
@pytest.mark.parametrize("width,trips", [(15, False), (16, True)])
def test_filtered_sources_5_to_8_all_pass_the_glitch_filter(source, width, trips):
    sim = _debounce_sim(16)
    _tick(sim, 5)
    sim.set_pwm_trip_input(0, source, True)
    _tick(sim, width)
    sim.set_pwm_trip_input(0, source, False)
    _tick(sim, 40)
    expect = (TRIP_S | VEC(source)) if trips else 0
    assert fw_cfg(sim) == expect | ALLM | 16


@pytest.mark.parametrize("source", range(9))
def test_each_source_trips_only_when_its_own_mask_bit_is_set(source):
    sim = make_sim()
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1), trip=(2, 2))),
                (CFG + PWM0_CFG, ALLM & ~MASK(source))])      # every other source enabled
    sim.set_pwm_trip_input(0, source, True)                   # pin path for sources 0 and 1 too
    assert fw_cfg(sim) == ALLM & ~MASK(source)
    assert sim.io("pru0")["pwm_pins"][:2] == [0, 0]
    prime(sim, [(CFG + PWM0_CFG, MASK(source))])
    assert fw_cfg(sim) == TRIP_S | VEC(source) | MASK(source)
    assert sim.io("pru0")["pwm_pins"][:2] == [1, 1]


@pytest.mark.parametrize("value", [128, 200, 255])
def test_debounce_field_is_eight_bits_wide(value):
    sim = _debounce_sim(value)
    assert fw_cfg(sim) == ALLM | value
    _tick(sim, 3)
    sim.set_pwm_trip_input(0, 5, True)
    _tick(sim, value - 1)
    sim.set_pwm_trip_input(0, 5, False)                       # one clock short: rejected
    _tick(sim, 5)
    assert fw_cfg(sim) == ALLM | value
    sim.set_pwm_trip_input(0, 5, True)
    _tick(sim, value)
    sim.set_pwm_trip_input(0, 5, False)                       # exactly DEBOUNCE clocks: passes
    assert fw_cfg(sim) == TRIP_S | VEC(5) | ALLM | value


def test_debounce_rounding_rounds_a_fractional_iep_clock_count_up():
    sim = _debounce_sim(5)
    sim.iep.set_clock_mhz(100)               # 5 core clocks = 2.5 IEP clocks -> 3
    assert sim.pwm.debounce_ticks(0) == 3
    sim = _debounce_sim(1)
    sim.iep.set_clock_mhz(100)               # 0.5 -> 1, never 0 (0 would bypass the filter)
    assert sim.pwm.debounce_ticks(0) == 1


def test_rejected_glitch_and_falling_pad_are_seen_through_the_register():
    sim = _debounce_sim(16)
    _tick(sim, 2)
    sim.set_pwm_trip_input(0, 5, True)
    _tick(sim, 4)
    sim.set_pwm_trip_input(0, 5, False)       # pad falls with the timer pending
    _tick(sim, 60)                            # a kept timer would trip at tick 18
    assert fw_cfg(sim) == ALLM | 16
    sim.set_pwm_trip_input(0, 5, True)
    _tick(sim, 20)                            # passes the filter and trips
    assert fw_cfg(sim) & TRIP_S
    sim.set_pwm_trip_input(0, 5, False)       # pad falls after the trip ...
    trip_clear(sim, ALLM | 16)
    assert fw_cfg(sim) == ALLM | 16           # ... so a trip reset does not trip again


def _stale_filter_run(late_write):
    sim = make_sim()
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1), trip=(2, 2))),
                (CFG + PWM0_CFG, 100 | MASK(5))])
    assert sim.load("pru0", START + "halt\n") == []
    sim.cores["pru0"].run(max_steps=100)
    t0 = sim.iep.clock_ticks
    sim.set_pwm_trip_input(0, 5, True)        # filter deadline t0 + 100
    sim.cores["pru0"].reset()
    src = (pwm_rd(PWM0_CFG, "r2") + wait_count_ge(t0 + 130, "w")
           + (pwm_wr(PWM0_CFG, 100) if late_write else "")      # drop the enable after the deadline
           + pwm_rd(PWM0_CFG, "r3") + "halt\n")
    return run(sim, src), t0


def test_firmware_reads_trip_status_after_a_nonzero_filter_delay():
    regs, t0 = _stale_filter_run(late_write=False)
    assert regs[2] == 100 | MASK(5)                           # deadline not reached yet
    assert regs[3] == TRIP_S | VEC(5) | 100 | MASK(5)


def test_firmware_write_after_the_filter_deadline_cannot_undo_the_trip():
    regs, _ = _stale_filter_run(late_write=True)
    assert regs[3] == TRIP_S | VEC(5) | 100                   # tripped at t0 + 100, before the write


def test_pending_debounce_is_visible_in_io_and_edge_log_without_pwm_register_access():
    sim = _debounce_sim(16)
    sim.set_pwm_trip_input(0, 5, True)
    _tick(sim, 30)                            # IEP time only; no PWM register is touched
    assert sim.io("pru0")["pwm_pins"][:2] == [1, 1]
    sim = _debounce_sim(16)
    sim.set_pwm_trip_input(0, 5, True)
    _tick(sim, 30)
    assert [e for e in sim.pwm.edges if e[0] > 0] == [(16, 0, 1), (16, 1, 1)]


def _vcd_history(text, names):
    ids = {}
    for line in text.splitlines():
        if line.startswith("$var"):
            parts = line.split()
            ids[parts[4]] = parts[3]
    history = {name: [] for name in names}
    for line in text.splitlines():
        for name in names:
            if line.endswith(ids[name]) and line[0] in "01zx" and not line.startswith("$"):
                history[name].append(line[0])
    return history


def test_vcd_shows_a_filtered_trip_without_any_pwm_register_access(tmp_path):
    sim = make_sim()
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1), trip=(2, 2))),
                (CFG + PWM0_CFG, 100 | MASK(5))])
    sim.set_pwm_trip_input(0, 5, True)        # deadline at IEP clock 100
    assert sim.load("pru0", START + wait_count_ge(150, "w") + "halt\n") == []
    path = tmp_path / "trip.vcd"
    info = export_pin_waveform(sim, "pru0", str(path), max_steps=5000, pins="0",
                               include_pwm=True)
    assert info["complete"]
    history = _vcd_history(path.read_text(), ("pwm_0", "pwm_1"))
    assert history["pwm_0"] == ["0", "1"]
    assert history["pwm_1"] == ["0", "1"]


# --------------------------------------------------------------------------
# review round: reset with a held pad, snapshot, edge log, state actions
# --------------------------------------------------------------------------
@pytest.mark.parametrize("reset", [lambda s: s.hard_reset(), lambda s: s.iep.hardware_reset()])
def test_module_reset_keeps_a_held_trip_pad_and_the_filter_restarts(reset):
    sim = make_sim()
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1), trip=(2, 2))),
                (CFG + PWM0_CFG, 16 | MASK(5))])
    sim.iep.write32(GLOBAL_CFG, 0x11)
    _tick(sim, 3)
    sim.set_pwm_trip_input(0, 5, True)
    _tick(sim, 20)
    assert reg(sim, PWM0_CFG) & TRIP_S
    reset(sim)
    assert reg(sim, PWM0_CFG) == 0 and sim.pwm.state[0] == "initial"
    assert sim.pwm.ext[0][5]                                  # the pad itself is still high
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1), trip=(2, 2))),
                (CFG + PWM0_CFG, MASK(5))])
    assert fw_cfg(sim) == TRIP_S | VEC(5) | MASK(5)           # trips again, pad never fell
    sim.set_pwm_trip_input(0, 5, False)                       # the fall is registered
    trip_clear(sim, MASK(5))
    assert fw_cfg(sim) == MASK(5)


def test_snapshot_restore_keeps_held_pad_levels():
    sim = make_sim()
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1), trip=(2, 2))),
                (CFG + PWM0_CFG, MASK(5))])
    sim.set_pwm_trip_input(0, 5, True)
    snap = sim.pwm.snapshot()
    sim.set_pwm_trip_input(0, 5, False)
    sim.pwm.restore(snap)
    assert sim.pwm.ext[0][5] and sim.pwm.trip_s[0]
    sim.set_pwm_trip_input(0, 5, False)                       # a held pad must be able to fall
    trip_clear(sim, MASK(5))
    assert fw_cfg(sim) == MASK(5)


def test_edge_log_keeps_the_last_entries_and_counts_the_dropped_ones():
    from perif.pwm import EDGE_LOG_DEPTH, IcssgPwm
    sim = make_sim()
    assert EDGE_LOG_DEPTH == 4096 and sim.pwm.edges.maxlen == 4096
    assert sim.pwm.edges_dropped == 0
    pwm = IcssgPwm(sim.iep, edge_log_depth=4)
    pwm.write32(0x140, state_word(init=(1, 1), trip=(2, 2)))             # 2 edges: Z -> L
    pwm.write32(PWM0_CFG, MASK(3))
    for _ in range(3):                                                    # 4 edges per cycle
        pwm.set_trip_input(0, 3, True)
        pwm.set_trip_input(0, 3, False)
        pwm.write32(PWM0_CFG, MASK(3) | TRIP_RESET)
        pwm.write32(PWM0_CFG, MASK(3))                                   # release the level
    assert len(pwm.edges) == 4 and pwm.edges_dropped == 10
    assert [(p, v) for _, p, v in pwm.edges] == [(0, 1), (1, 1), (0, 0), (1, 0)]


def test_active_state_uses_a_new_action_only_at_the_next_compare():
    sim = make_sim()
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1), act=(2, 2)))])
    sim.iep.write32(CMP1, 10)
    sim.iep.write32(CMP_CFG, 0x4)
    sim.iep.write32(GLOBAL_CFG, 0x11)
    _tick(sim, 12)
    assert sim.io("pru0")["pwm_pins"][:2] == [1, 0]
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1), act=(1, 1)))])    # new action: drive L
    assert sim.io("pru0")["pwm_pins"][:2] == [1, 0]                      # not applied yet
    sim.iep.write32(CMP1, 30)
    _tick(sim, 25)
    assert sim.io("pru0")["pwm_pins"][:2] == [0, 0]
    assert (30, 0, 0) in sim.pwm.edges


def test_compare_n_drives_pad_n_minus_1_and_compares_13_14_drive_no_pad():
    sim = make_sim()
    for k in range(6):
        prime(sim, [(CFG + 0x140 + 4 * k, state_word(act=(2, 2)))])      # init Z, active H
    for k in range(6, 9):
        prime(sim, [(CFG + 0x140 + 4 * k, state_word(init=(1, 1), act=(2, 2)))])   # PWM2
    for n in range(1, 15):
        sim.iep.compare[n] = 10 + n
    sim.iep.write32(CMP_CFG, sum(1 << (n + 1) for n in range(1, 15)))
    sim.iep.write32(GLOBAL_CFG, 0x11)
    _tick(sim, 40)
    assert edges_after_start(sim) == [(10 + n, n - 1, 1) for n in range(1, 13)]
    assert sim.io("pru0")["pwm_pins"] == [1] * 12 + [None] * 12
    assert sim.pwm.state[2:] == ["initial", "initial"]


# --------------------------------------------------------------------------
# review round: documented assumptions pinned so a later change is deliberate
# --------------------------------------------------------------------------
def test_assumption_reserved_encoding_3_is_z_in_initial_and_trip_and_holds_in_active():
    sim = make_sim()
    prime(sim, [(CFG + 0x140, state_word(init=(3, 3), trip=(3, 3)))])
    assert sim.io("pru0")["pwm_pins"][:2] == [None, None]
    sim = make_sim()
    prime(sim, [(CFG + 0x140, state_word(init=(2, 2), trip=(3, 3), act=(3, 3)))])
    sim.iep.write32(CMP1, 10)
    sim.iep.write32(CMP_CFG, 0x4)
    sim.iep.write32(GLOBAL_CFG, 0x11)
    _tick(sim, 15)
    assert sim.pwm.state[0] == "active"
    assert sim.io("pru0")["pwm_pins"][:2] == [1, 1]                      # held, not toggled
    assert not [e for e in sim.pwm.edges if e[0] > 0]
    prime(sim, [(CFG + PWM0_CFG, MASK(1) | OVER_ERR)])
    assert sim.io("pru0")["pwm_pins"][:2] == [None, None]                # reserved trip = Z


def test_assumption_toggle_of_a_z_pad_drives_h_then_alternates():
    sim = make_sim()
    prime(sim, [(CFG + 0x140, state_word(init=(0, 1)))])                  # POS: init Z, toggle
    sim.iep.write32(CMP0, 20)
    sim.iep.write32(CMP1, 10)
    sim.iep.write32(CMP_CFG, 0x1 | 0x2 | 0x4)
    sim.iep.write32(GLOBAL_CFG, 0x11)
    _tick(sim, 45)
    assert [e for e in sim.pwm.edges if e[0] > 0 and e[1] == 0] == [(10, 0, 1), (30, 0, 0)]


def test_pwm_efc_en_does_not_change_pad_edges_and_is_exposed_as_a_flag():
    results = []
    for efc in (0, 1):
        sim = make_sim()
        sim.iep.write_sa_mx(efc << 16)
        assert sim.pwm.efficiency_mode is bool(efc)
        prime(sim, [(CFG + 0x140, state_word(init=(1, 2)))])
        sim.iep.write32(CMP1, 110)
        sim.iep.write32(CMP2, 100)
        sim.iep.write32(CMP_CFG, 0xC)
        sim.iep.write32(GLOBAL_CFG, 0x11)
        _tick(sim, 130)
        results.append(edges_after_start(sim))
    assert results[0] == results[1] == [(100, 1, 0), (110, 0, 1)]


# --------------------------------------------------------------------------
# review round: R31 leak, lazy compare listener, UI
# --------------------------------------------------------------------------
def _r31_on_both_cores(sim):
    for name in ("pru0", "pru1"):
        assert sim.load(name, "mov r6, r31\n halt\n") == []
        sim.cores[name].run(max_steps=100)
        assert sim.cores[name].halted and sim.cores[name].fault is None
    return sim.registers("pru0")[6], sim.registers("pru1")[6]


def test_r31_on_pru0_and_pru1_ignores_every_live_pad_while_all_are_high():
    pins = (3, 10, 17)
    base = make_sim()
    for name in ("pru0", "pru1"):
        for pin in pins:
            base.set_input(name, pin, True)
    gpi_only = _r31_on_both_cores(base)
    assert gpi_only == (1 << 3 | 1 << 10 | 1 << 17,) * 2

    sim = make_sim()
    for name in ("pru0", "pru1"):
        for pin in pins:
            sim.set_input(name, pin, True)
    for k in range(12):                                       # PWM0/1 live; PWM2/3 inert storage
        prime(sim, [(CFG + 0x140 + 4 * k, state_word(init=(2, 2)))])
    assert sim.io("pru0")["pwm_pins"] == [1] * 12 + [None] * 12
    assert _r31_on_both_cores(sim) == gpi_only
    assert sim.io("pru0")["pwm_pins"][:12] == [1] * 12
    assert sim.io("pru0")["gpi_pins"] == base.io("pru0")["gpi_pins"]
    assert sim.io("pru1")["gpi_pins"] == base.io("pru1")["gpi_pins"]


def test_compare_listener_is_registered_on_first_pwm_use_only():
    sim = make_sim()
    assert sim.iep._compare_listeners == []                   # no cost for non-PWM programs
    prime(sim, [(CFG + PWM0_CFG, 0)])                         # a reset-value write is not use
    assert sim.iep._compare_listeners == []
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1)))])
    assert len(sim.iep._compare_listeners) == 1
    prime(sim, [(CFG + 0x144, state_word(init=(1, 1)))])
    assert len(sim.iep._compare_listeners) == 1
    sim = make_sim()
    sim.set_pwm_trip_input(0, 5, True)
    assert len(sim.iep._compare_listeners) == 1


def test_default_state_words_toggle_pad_when_cmp1_is_enabled():
    # Reset-default state words are (Z, toggle): firmware that leaves them alone
    # and only enables CMP1 must still see the pad move.
    sim = make_sim()
    assert sim.iep._compare_listeners == []
    sim.iep.write32(CMP1, 10)
    sim.iep.write32(CMP_CFG, 0x4)
    assert len(sim.iep._compare_listeners) == 1
    sim.iep.write32(GLOBAL_CFG, 0x11)
    _tick(sim, 15)
    assert sim.io("pru0")["pwm_pins"][0] == 1
    assert [e[1:] for e in sim.pwm.edges] == [(0, 1)] and 10 <= sim.pwm.edges[0][0] <= 11
    assert sim.pwm.state[0] == "active"


@pytest.mark.parametrize("cfg", [0x0, 0x1, 0x2, 0x3, 0x1 << 17, 0x1 << 14])
def test_cmp_cfg_without_cmp1_to_cmp12_keeps_the_fast_path(cfg):
    sim = make_sim()
    sim.iep.write32(CMP_CFG, cfg)
    assert sim.iep._compare_listeners == []


@pytest.mark.parametrize("cfg", [0x4, 0x1 << 13, 0x3FFC])
def test_any_of_cmp1_to_cmp12_arms_the_listener(cfg):
    sim = make_sim()
    sim.iep.write32(CMP_CFG, cfg)
    assert len(sim.iep._compare_listeners) == 1


def test_restoring_a_snapshot_with_cmp1_enabled_arms_the_listener():
    src = make_sim()
    src.iep.write32(CMP_CFG, 0x4)
    snap = src.iep.snapshot()
    sim = make_sim()
    assert sim.iep._compare_listeners == []
    sim.iep.restore(snap)
    assert len(sim.iep._compare_listeners) == 1


def _random_pwm_run(seed, eager):
    import random
    rng = random.Random(seed)
    sim = make_sim()
    if eager:
        sim.pwm._arm()

    def word():
        return state_word(init=(rng.randrange(3), rng.randrange(3)),
                          trip=(rng.randrange(3), rng.randrange(3)),
                          act=(rng.randrange(3), rng.randrange(3)))

    prime(sim, [(CFG + 0x140, word() | 0x15)])                # first write is never all-zero
    for k in range(1, 6):
        prime(sim, [(CFG + 0x140 + 4 * k, word())])
    prime(sim, [(CFG + PWM0_CFG, rng.randrange(0, 20) | (ALLM & rng.getrandbits(32))
                 | CMP0_EN * rng.randrange(2))])
    prime(sim, [(CFG + 0x134, rng.randrange(0, 20) | ALLM)])
    sim.iep.write32(CMP0, rng.randrange(40, 90))
    for n in range(1, 13):
        sim.iep.compare[n] = rng.randrange(5, 80)
    sim.iep.write32(CMP_CFG, 0x3 | sum(1 << (n + 1) for n in range(1, 13) if rng.random() < 0.8))
    sim.iep.write32(GLOBAL_CFG, 0x11)
    for _ in range(40):
        action = rng.randrange(4)
        if action == 0:
            sim.set_pwm_trip_input(rng.randrange(2), rng.randrange(9), bool(rng.randrange(2)))
        elif action == 1:
            prime(sim, [(CFG + PWM0_CFG + 4 * rng.randrange(2),
                         rng.getrandbits(8) | (rng.getrandbits(32) & (ALLM | TRIP_RESET | OVER_ERR | POS_ERR)))])
        elif action == 2:
            prime(sim, [(CFG + 0x140 + 4 * rng.randrange(6), word())])
        _tick(sim, rng.randrange(1, 30))
    return (list(sim.pwm.edges), sim.io("pru0")["pwm_pins"],
            [reg(sim, 0x130), reg(sim, 0x134)], sim.iep.count, sim.iep.cmp_status)


@pytest.mark.parametrize("seed", range(25))
def test_lazy_listener_matches_an_eagerly_armed_module_on_configured_programs(seed):
    assert _random_pwm_run(seed, eager=False) == _random_pwm_run(seed, eager=True)


def test_ui_state_push_carries_the_pwm_pads_and_icssm_has_none(monkeypatch):
    import asyncio
    from ui import server as ui_server

    class WS:
        sent = None

        async def send_json(self, data):
            self.sent = data

    sim = make_sim()
    prime(sim, [(CFG + 0x140, state_word(init=(1, 2)))])
    monkeypatch.setattr(ui_server, "sim", sim)
    ws = WS()
    asyncio.run(ui_server._send_state(ws, "pru0"))
    assert ws.sent["io"]["pwm_pins"][:3] == [0, 1, None]
    assert len(ws.sent["io"]["pwm_pins"]) == 24
    root = os.path.join(os.path.dirname(__file__), "..", "config")
    monkeypatch.setattr(ui_server, "sim", Simulator(config_path=os.path.join(root, "memory_am261x.cfg")))
    asyncio.run(ui_server._send_state(ws, "pru0"))
    assert "pwm_pins" not in ws.sent["io"]


# --------------------------------------------------------------------------
# review round: ordering and latching corners found by the mutant run
# --------------------------------------------------------------------------
def test_enabled_cmp0_returns_pads_and_a_still_asserted_source_trips_again():
    sim = make_sim()
    both = MASK(0) | MASK(1) | CMP0_EN
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1), trip=(2, 2))),
                (CFG + PWM0_CFG, both | OVER_ERR)])
    prime(sim, [(CFG + PWM0_CFG, both | OVER_ERR | POS_ERR)])    # second source joins later
    sim.iep.write32(CMP0, 30)
    sim.iep.write32(CMP_CFG, 0x3)
    sim.iep.write32(GLOBAL_CFG, 0x11)
    _tick(sim, 35)
    # CMP0 returned the pads to initial (30, L) and the still-asserted sources
    # tripped the set again; TRIP_VEC tracks both asserted enabled sources.
    assert (30, 0, 0) in sim.pwm.edges and sim.pwm.state[0] == "trip"
    assert reg(sim, PWM0_CFG) == TRIP_S | VEC(0) | VEC(1) | both | OVER_ERR | POS_ERR


def test_filter_deadline_on_the_same_clock_is_applied_before_the_compare():
    sim = _debounce_sim(16)
    prime(sim, [(CFG + 0x140, state_word(init=(1, 1), trip=(1, 1), act=(2, 2)))])
    sim.iep.write32(CMP1, 21)
    sim.iep.write32(CMP_CFG, 0x4)
    _tick(sim, 5)
    sim.set_pwm_trip_input(0, 5, True)        # filter deadline: clock 21
    _tick(sim, 25)
    assert sim.pwm.state[0] == "trip"         # the compare at 21 found the set tripped
    assert not [e for e in sim.pwm.edges if e[0] > 0 and e[1] == 0]      # pad 0 never left L


def test_register_write_after_the_filter_deadline_cannot_undo_the_trip_direct_write32():
    sim = _debounce_sim(16)
    sim.pwm.write32(PWM0_CFG, 16 | MASK(5))
    _tick(sim, 3)
    sim.set_pwm_trip_input(0, 5, True)
    _tick(sim, 30)                            # deadline long passed, nobody looked
    sim.pwm.write32(PWM0_CFG, 16)             # enable dropped; the trip already happened
    assert sim.pwm.trip_s[0] and sim.pwm.trip_vec[0] == 1 << 5


# --------------------------------------------------------------------------
# silicon replay: the measured register sequences, word for word
# --------------------------------------------------------------------------
def _replay(sim, steps, label):
    for k in range(0, len(steps), 12):
        chunk = steps[k:k + 12]
        src = "".join(pwm_wr(off, wr) + pwm_rd(off, f"r{16 + i}")
                      for i, (off, wr, _) in enumerate(chunk)) + "halt\n"
        sim.cores["pru0"].reset()
        got = run(sim, src)[16:16 + len(chunk)]
        want = [rd for _, _, rd in chunk]
        assert got == want, (label, k, [hex(x) for x in got], [hex(x) for x in want])


def test_firmware_replays_the_measured_trip_register_sequences_word_for_word():
    """Every PWM register write of the three silicon runs, executed by SBCO/LBCO
    in the measured order (the sequences are contiguous); the words read back
    must equal the measured ones.  Covers mask polarity, one-hot VEC, both
    sources in one write, level-type TRIP_RESET, sticky TRIP_S, the bit 23
    clear and its conditions, CMP0_EN, direct TRIP_S/VEC writes, the state
    words and PWM1..PWM3."""
    sim = make_sim()
    _replay(sim, BPWM, "register-level probe")
    assert reg(sim, PWM0_CFG) == TRIP_S                   # register-level probe end state
    _replay(sim, BPWM2, "register-level probe")
    _replay(sim, BPWM3, "register-level probe")
    assert [reg(sim, 0x130 + 4 * n) for n in range(4)] == [0, 0, 0, 0]


def test_trip_reset_is_a_level_that_reads_back_what_was_written():
    # measured on LP-AM243 ICSSG0: bit 18 stays 1 until written 0
    # (register-level probe)
    sim = make_sim()
    src = (pwm_wr(PWM0_CFG, TRIP_RESET) + pwm_rd(PWM0_CFG, "r2")
           + "ldi r8, 0\nspin:\n add r8, r8, 1\n ldi r9, 200\n qbgt spin, r8, r9\n"
           + pwm_rd(PWM0_CFG, "r3")
           + pwm_wr(PWM0_CFG, 0) + pwm_rd(PWM0_CFG, "r4") + "halt\n")
    regs = run(sim, src)
    assert regs[2:5] == [TRIP_RESET, TRIP_RESET, 0]


def test_trip_reset_clears_vec_but_not_trip_s_issue_72():
    # measured on LP-AM243 ICSSG0 (register-level probe, three runs): source low,
    # bit 18 = 1 gives VEC 0 with TRIP_S still 1; releasing bit 18 keeps TRIP_S.
    sim = make_sim()
    src = (pwm_wr(PWM0_CFG, MASK(1) | OVER_ERR) + pwm_wr(PWM0_CFG, MASK(1))
           + pwm_wr(PWM0_CFG, MASK(1) | TRIP_RESET) + pwm_rd(PWM0_CFG, "r2")
           + pwm_wr(PWM0_CFG, MASK(1)) + pwm_rd(PWM0_CFG, "r3") + "halt\n")
    regs = run(sim, src)
    assert regs[2] == TRIP_S | TRIP_RESET | MASK(1)       # 0x40040200
    assert regs[3] == TRIP_S | MASK(1)                    # 0x40000200


def test_bit23_clears_trip_s_only_when_vec_is_zero_or_reset_is_held():
    sim = make_sim()
    base = MASK(1)
    latch = [(base | OVER_ERR), base]                     # TRIP_S=1, VEC=0x002, source low
    # ignored while VEC is latched (register-level probe)
    prime(sim, [(CFG + PWM0_CFG, w) for w in latch] + [(CFG + PWM0_CFG, base | TRIP_S_CLEAR)])
    assert reg(sim, PWM0_CFG) == TRIP_S | VEC(1) | base
    # with TRIP_RESET held in the same write it clears (register-level probe)
    prime(sim, [(CFG + PWM0_CFG, base | TRIP_RESET | TRIP_S_CLEAR)])
    assert reg(sim, PWM0_CFG) == TRIP_RESET | base
    # other VEC bits (21, 22, 24..29) and a direct TRIP_S write do nothing
    # (register-level probe)
    prime(sim, [(CFG + PWM0_CFG, base | (1 << 30)), (CFG + PWM0_CFG, base)])
    assert reg(sim, PWM0_CFG) == base
    prime(sim, [(CFG + PWM0_CFG, 0), (CFG + PWM0_CFG, base | OVER_ERR), (CFG + PWM0_CFG, base),
                (CFG + PWM0_CFG, base | TRIP_RESET), (CFG + PWM0_CFG, base)])
    assert reg(sim, PWM0_CFG) == TRIP_S | base
    for bit in (21, 22, 24, 25, 26, 27, 28, 29, 30):
        prime(sim, [(CFG + PWM0_CFG, base | 1 << bit), (CFG + PWM0_CFG, base)])
        assert reg(sim, PWM0_CFG) == TRIP_S | base, bit
    # and writing TRIP_S=1 while it is 0 does not set it (register-level probe)
    prime(sim, [(CFG + PWM0_CFG, base | TRIP_S_CLEAR), (CFG + PWM0_CFG, base | TRIP_S)])
    assert reg(sim, PWM0_CFG) == base


def test_bit23_clear_while_reset_held_and_source_asserted_trips_again_on_release():
    # measured on LP-AM243 ICSSG0 (register-level probe): TRIP_S reads 0 while
    # bit 18 is held, 1 again as soon as it is released; bit 23 does nothing while
    # the source is asserted and VEC is latched.
    sim = make_sim()
    prime(sim, [(CFG + PWM0_CFG, MASK(1) | OVER_ERR)])
    prime(sim, [(CFG + PWM0_CFG, MASK(1) | OVER_ERR | TRIP_RESET)])
    prime(sim, [(CFG + PWM0_CFG, MASK(1) | OVER_ERR | TRIP_RESET | TRIP_S_CLEAR)])
    assert reg(sim, PWM0_CFG) == MASK(1) | OVER_ERR | TRIP_RESET
    prime(sim, [(CFG + PWM0_CFG, MASK(1) | OVER_ERR)])
    assert reg(sim, PWM0_CFG) == TRIP_S | VEC(1) | MASK(1) | OVER_ERR
    prime(sim, [(CFG + PWM0_CFG, MASK(1) | OVER_ERR | TRIP_S_CLEAR)])
    assert reg(sim, PWM0_CFG) == TRIP_S | VEC(1) | MASK(1) | OVER_ERR


@pytest.mark.parametrize("n", range(12))
def test_state_config_words_store_bits_11_to_0_and_encoding_3(n):
    # measured on LP-AM243 ICSSG0 (register-level probe): 0xFFFFFFFF reads
    # 0x00000FFF, 0xAAAAAAAA reads 0xAAA, bit 12 and bit 31 alone read 0, every
    # 2-bit field written with 3 reads back 3.
    sim = make_sim()
    off = 0x140 + 4 * n
    for wr, rd in ((0xFFFFFFFF, 0xFFF), (0xAAAAAAAA, 0xAAA), (0x55555555, 0x555),
                   (1 << 12, 0), (1 << 31, 0)):
        prime(sim, [(CFG + off, wr)])
        assert reg(sim, off) == rd, hex(wr)
    for shift in range(0, 12, 2):
        prime(sim, [(CFG + off, 3 << shift)])
        assert reg(sim, off) == 3 << shift


def test_pwm_word_stores_bits_20_to_0_only():
    # measured on LP-AM243 ICSSG0 (register-level probe): 0xFFFFFFFF reads
    # 0x001FFFFF on PWM1..PWM3 (bit 18 held: no trip latched)
    sim = make_sim()
    for n in range(4):
        prime(sim, [(CFG + PWM0_CFG + 4 * n, 0xFFFFFFFF)])
        assert reg(sim, PWM0_CFG + 4 * n) == 0x001FFFFF


# --------------------------------------------------------------------------
# review round: partial writes, state-word access ordering
# --------------------------------------------------------------------------
def _latched_source2_trip():
    sim = make_sim()
    prime(sim, [(CFG + PWM0_CFG, MASK(2))])
    sim.set_pwm_trip_input(0, 2, True)
    sim.set_pwm_trip_input(0, 2, False)           # TRIP_VEC freezes, TRIP_S stays
    assert reg(sim, PWM0_CFG) == TRIP_S | VEC(2) | MASK(2)
    return sim


@pytest.mark.parametrize("lane,data", [
    (0, b"\x00"), (0, b"\xff"), (1, b"\x00"), (1, b"\xff"),
    (3, b"\x00"), (3, b"\xff"), (0, b"\x00\x00"), (0, b"\xff\xff"),
])
def test_partial_writes_that_skip_byte_2_do_not_clear_a_latched_trip(lane, data):
    # Byte 2 holds both TRIP_RESET (bit 18) and the bit-23 clear command, so a
    # write that skips it replays the current values of both; with TRIP_VEC
    # latched, TRIP_RESET reads 0 and the clear is ignored.
    sim = _latched_source2_trip()
    sim.memory.write(CFG + PWM0_CFG + lane, data)
    assert reg(sim, PWM0_CFG) & TRIP_S
    assert reg(sim, PWM0_CFG) & VEC(2)


def test_filtered_trip_deadline_is_applied_before_a_state_word_write():
    sim = _debounce_sim(16)
    sim.set_pwm_trip_input(0, 5, True)            # deadline 16 clocks later
    _tick(sim, 30)                                # passed, nothing has synced yet
    prime(sim, [(CFG + 0x140, state_word(init=(0, 0), trip=(2, 2)))])
    ticks = [e[0] for e in sim.pwm.edges]
    assert ticks == sorted(ticks)                 # trip edge first, then nothing in trip state
    assert sim.pwm.state[0] == "trip"
    assert sim.io("pru0")["pwm_pins"][0] == 1


def test_state_word_read_applies_an_expired_trip_deadline():
    sim = _debounce_sim(16)
    sim.set_pwm_trip_input(0, 5, True)
    _tick(sim, 30)
    assert sim.pwm.state[0] == "initial"
    reg(sim, 0x140)
    assert sim.pwm.state[0] == "trip"
