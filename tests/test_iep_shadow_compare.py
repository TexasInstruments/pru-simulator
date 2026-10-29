"""IEP 32-bit shadow compares and PWM-efficiency CMP_STATUS auto-clear.

Spec sources (AM64x/AM243x TRM SPRUIM2J, April 2026):
  * 6.4.13.2.3 "32-Bit Shadow Mode", PDF pp731-732: with IEP_CMP_CFG_REG[17]
    SHADOW_EN the counter is 32-bit, CMPj_REG0 is the read-only active value,
    CMPj_REG1 is the read/write shadow copy, the shadow is loaded when the
    counter is reset, and the next CMP0 reset period is defined by the last
    CMP0 update.
  * IEP_CMP_STATUS_REG description, PDF p6937: with PWM_EFC_EN set "hardware
    will auto clear the bit on next clock cycle".
  * ICSSG_SA_MX_REG, Table 14-10186, PDF p6547: offset 0x40 of ICSS CFG,
    bit 16 PWM_EFC_EN.
Upstream issue #73 silicon observations: compare=0 did not fire, compare=CMP0
fired, and a CMP0 half-period is CMP0+1 IEP clocks (not CMP0+2).
Register-level probes on LP-AM243 (ICSSG0) and LP-AM261 (ICSSM0) fixed the
shadow-mode details: REG1 is not seeded on entry, the shadow->active copy also
runs while the counter is stopped with COUNT == 0, and a REG0 write copies REG1
into REG0 and clears CMP_STATUS[j]. Tests that quote a raw word name the board
it was measured on.

Expected clock numbers below are worked out by hand from those statements for
DEFAULT_INC=1 (counter value N is reached on clock N after a reset, the reset
itself takes one further clock). Nothing here asserts a measured hardware delay
for the status auto-clear: the model clears it on the next IEP clock, which is
the documented behaviour at the model's clock granularity.
"""

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from perif.iep import IepTimer                                      # noqa: E402
from simulator import Simulator                                     # noqa: E402

# Register offsets are written out from the TRM tables, not imported.
GLOBAL_CFG = 0x00
COUNT0 = 0x10
COUNT1 = 0x14
CMP_CFG = 0x70
CMP_STATUS = 0x74
CMP0_REG0 = 0x78
CMP0_REG1 = 0x7C
CMP1_REG0 = 0x80
CMP1_REG1 = 0x84
# SPRUIM2J compare map: RXIPG0/1 occupy 0xB8/0xBC between CMP7 and CMP8.
COMPARE_REG0 = (0x78, 0x80, 0x88, 0x90, 0x98, 0xA0, 0xA8, 0xB0,
                0xC0, 0xC8, 0xD0, 0xD8, 0xE0, 0xE8, 0xF0, 0xF8)

RST = 0x1                      # CMP_CFG[0] CMP0_RST_CNT_EN
EN0 = 0x2                      # CMP_CFG[1] CMP_EN for CMP0
EN1 = 0x4
SHADOW = 1 << 17
PWM_EFC = 1 << 16              # ICSSG_SA_MX_REG[16]


def w32(iep, off, val):
    iep.write(off, (val & 0xFFFFFFFF).to_bytes(4, "little"))


def r32(iep, off):
    return int.from_bytes(iep.read(off, 4), "little")


def run(iep, n):
    for _ in range(n):
        iep.tick()


def make(active0=0, shadow_cmp0=None, extra=0, cmp1=None, shadow_cmp1=None):
    """Counter enabled, DEFAULT_INC=1, CMP0 reset enabled, in shadow mode.

    The active compares are programmed in 64-bit mode, the counter is started,
    then SHADOW_EN is turned on while it runs (so no stopped-counter copy
    happens). REG1 keeps the old upper word on entry, so the shadow values are
    written explicitly; they default to the active values.
    """
    iep = IepTimer()
    w32(iep, CMP0_REG0, active0)
    if cmp1 is not None:
        w32(iep, CMP1_REG0, cmp1)
    w32(iep, GLOBAL_CFG, 0x11)
    w32(iep, CMP_CFG, RST | EN0 | (EN1 if cmp1 is not None else 0) | SHADOW | extra)
    w32(iep, CMP0_REG1, active0 if shadow_cmp0 is None else shadow_cmp0)
    if cmp1 is not None:
        w32(iep, CMP1_REG1, cmp1 if shadow_cmp1 is None else shadow_cmp1)
    return iep


def listen(iep):
    events = []
    iep.add_compare_listener(events.append)
    return events


# --- register view in shadow mode ---------------------------------------------

def test_shadow_mode_reg0_reads_active_and_reg1_is_the_shadow():
    iep = IepTimer()
    w32(iep, CMP0_REG0, 10)
    w32(iep, COUNT0, 5)                        # stopped, COUNT != 0: no copy
    w32(iep, CMP_CFG, SHADOW | EN0 | RST)
    # measured on LP-AM243 ICSSG0 (register-level probe): active value kept
    # on entry, REG1 not seeded
    assert r32(iep, CMP0_REG0) == 10
    assert r32(iep, CMP0_REG1) == 0

    w32(iep, CMP0_REG1, 20)                    # shadow write
    assert r32(iep, CMP0_REG1) == 20
    # measured on LP-AM243 ICSSG0 (register-level probe): active untouched
    assert r32(iep, CMP0_REG0) == 10


def test_reg0_write_in_shadow_mode_discards_the_data_and_copies_reg1():
    """Measured on LP-AM243 ICSSG0 (register-level probe): with
    REG0 = 0x1e, REG1 = 0x07 a write of 35 to REG0 leaves REG0 = 0x07 (the data
    never reads back); a later write of 37 with REG1 = 0x24 leaves REG0 = 0x24."""
    iep = IepTimer()
    w32(iep, CMP0_REG0, 0x1E)
    w32(iep, COUNT0, 50)
    w32(iep, CMP_CFG, SHADOW | EN0)
    w32(iep, CMP0_REG1, 0x07)
    assert (r32(iep, CMP0_REG0), r32(iep, CMP0_REG1)) == (0x1E, 0x07)
    w32(iep, CMP0_REG0, 35)
    assert (r32(iep, CMP0_REG0), r32(iep, CMP0_REG1)) == (0x07, 0x07)
    w32(iep, CMP0_REG1, 0x24)
    assert r32(iep, CMP0_REG0) == 0x07          # COUNT != 0: no transparent copy
    w32(iep, CMP0_REG0, 37)
    assert (r32(iep, CMP0_REG0), r32(iep, CMP0_REG1)) == (0x24, 0x24)


def test_reg0_write_in_shadow_mode_clears_only_that_compares_status_bit():
    """Measured on LP-AM243 ICSSG0 (register-level probe: CMP_STATUS
    5 -> 1 after a CMP2 REG0 write; 3 -> 1 after a CMP1 write; 3 -> 2 after a CMP0 write)."""
    iep = IepTimer()
    w32(iep, COUNT0, 1)
    w32(iep, CMP_CFG, SHADOW)
    iep.cmp_status = 0b111
    w32(iep, CMP0_REG0 + 16, 1)                # CMP2 REG0
    assert r32(iep, CMP_STATUS) == 0b011
    w32(iep, CMP1_REG0, 1)
    assert r32(iep, CMP_STATUS) == 0b001
    w32(iep, CMP0_REG0, 1)
    assert r32(iep, CMP_STATUS) == 0


def test_reg0_write_in_shadow_mode_leaves_the_running_period_alone():
    """Measured on LP-AM243 ICSSG0 (register-level probe): after a REG0
    write of 77 the active value and the period stay 13."""
    iep = make(active0=13)
    events = listen(iep)
    run(iep, 5)
    w32(iep, CMP0_REG0, 77)                    # REG1 == 13: copy changes nothing
    assert r32(iep, CMP0_REG0) == 13
    run(iep, 40)
    assert [e.count for e in events] == [13, 13, 13]
    assert [b.tick - a.tick for a, b in zip(events, events[1:])] == [14, 14]


# --- entry and the stopped-counter copy (register-level probes) -------------------

def test_entering_shadow_mode_does_not_seed_reg1_from_the_active_value():
    """Measured on LP-AM243 ICSSG0 (register-level probe, same on LP-AM261):
    REG0 = 13, REG1 = 0, counter running with reset; SHADOW_EN set with no
    shadow write: REG1 reads 0 at once, and REG0 is 0 after the first reset."""
    iep = IepTimer()
    events = listen(iep)
    w32(iep, CMP0_REG0, 13)
    w32(iep, GLOBAL_CFG, 0x11)
    w32(iep, CMP_CFG, RST | EN0)
    run(iep, 3)
    w32(iep, CMP_CFG, RST | EN0 | SHADOW)
    assert r32(iep, CMP0_REG1) == 0
    assert r32(iep, CMP0_REG0) == 13           # no copy while the counter runs
    run(iep, 20)                               # hit at 13, reset loads REG1 = 0
    assert r32(iep, CMP0_REG0) == 0
    assert [e.count for e in events] == [13]   # CMP0 never fires at 13 again
    run(iep, 5000)
    assert [e.count for e in events] == [13]


def test_entering_shadow_mode_keeps_the_previous_upper_word_in_reg1():
    """Measured on LP-AM243 ICSSG0 (register-level probe): REG0 = 13 and
    REG1 = 5 in 64-bit mode, counter stopped at 0; after SHADOW_EN REG1 = 5 and,
    through the stopped-counter copy, REG0 = 5."""
    iep = IepTimer()
    w32(iep, CMP0_REG0, 13)
    w32(iep, CMP0_REG1, 5)
    w32(iep, CMP_CFG, SHADOW)
    assert r32(iep, CMP0_REG1) == 5
    assert r32(iep, CMP0_REG0) == 5
    # a running counter takes no copy at entry: REG0 stays, REG1 = old upper word
    running = IepTimer()
    w32(running, CMP0_REG0, 13)
    w32(running, CMP0_REG1, 5)
    w32(running, GLOBAL_CFG, 0x11)
    w32(running, CMP_CFG, SHADOW)
    assert (r32(running, CMP0_REG0), r32(running, CMP0_REG1)) == (13, 5)


def test_shadow_copy_is_transparent_while_stopped_at_count_zero():
    """Measured on LP-AM243 ICSSG0 (register-level probe): counter
    disabled, COUNT = 0: REG0 was 0, a REG1 write of 100 shows in REG0 at once,
    before any enable, and is still 100 at enable. Setting SHADOW_EN with REG1
    = 0 and REG0 = 100 gives REG0 = 0 (= REG1); a further REG1 write is followed
    by REG0."""
    iep = IepTimer()
    w32(iep, CMP_CFG, RST | EN0 | SHADOW)
    assert r32(iep, CMP0_REG0) == 0
    w32(iep, CMP0_REG1, 100)
    assert r32(iep, CMP0_REG0) == 100          # before any enable
    w32(iep, GLOBAL_CFG, 0x11)
    assert r32(iep, CMP0_REG0) == 100
    run(iep, 100)                              # hit at 100: period 101 follows
    assert r32(iep, CMP_STATUS) == 1 and r32(iep, COUNT0) == 100
    run(iep, 1)
    assert r32(iep, COUNT0) == 0

    entry = IepTimer()
    w32(entry, CMP0_REG0, 100)                 # programmed in 64-bit mode
    w32(entry, CMP_CFG, SHADOW)                # SHADOW_EN with COUNT = 0 stopped
    assert (r32(entry, CMP0_REG0), r32(entry, CMP0_REG1)) == (0, 0)
    w32(entry, CMP0_REG1, 0x32)
    assert r32(entry, CMP0_REG0) == 0x32


def test_writing_count_zero_while_stopped_copies_the_shadow():
    iep = IepTimer()
    w32(iep, CMP0_REG0, 7)
    w32(iep, COUNT0, 5)
    w32(iep, CMP_CFG, RST | EN0 | SHADOW)
    w32(iep, CMP0_REG1, 100)
    assert r32(iep, CMP0_REG0) == 7            # stopped at COUNT != 0: no copy
    w32(iep, COUNT0, 0)
    assert r32(iep, CMP0_REG0) == r32(iep, CMP0_REG1) == 100


def test_stopping_the_counter_at_count_zero_copies_the_shadow():
    iep = IepTimer()
    w32(iep, CMP_CFG, RST | EN0 | SHADOW)
    w32(iep, CMP0_REG1, 100)
    w32(iep, COUNT0, 0)
    w32(iep, GLOBAL_CFG, 0x11)                 # running at COUNT == 0: no copy
    w32(iep, CMP0_REG1, 50)
    assert r32(iep, CMP0_REG0) == 100
    w32(iep, GLOBAL_CFG, 0x10)                 # CNT_ENABLE cleared at COUNT == 0
    assert r32(iep, CMP0_REG0) == r32(iep, CMP0_REG1) == 50


def test_stopping_the_counter_at_a_non_zero_count_does_not_copy():
    iep = IepTimer()
    w32(iep, CMP_CFG, RST | EN0 | SHADOW)
    w32(iep, GLOBAL_CFG, 0x11)
    run(iep, 3)
    w32(iep, CMP0_REG1, 50)
    w32(iep, GLOBAL_CFG, 0x10)
    assert r32(iep, COUNT0) == 3 and r32(iep, CMP0_REG0) == 0


def test_no_shadow_copy_while_stopped_with_a_non_zero_count():
    """Measured on LP-AM243 ICSSG0 (register-level probe):
    COUNT = 5 stopped, active 100, REG1 := 50 or active 7, REG1 := 100: REG0
    keeps its value after the write and at enable, and changes only at the first
    reset."""
    iep = IepTimer()
    w32(iep, CMP0_REG0, 7)
    w32(iep, COUNT0, 5)
    w32(iep, CMP_CFG, RST | EN0 | SHADOW)
    w32(iep, CMP0_REG1, 100)
    assert r32(iep, CMP0_REG0) == 7
    w32(iep, GLOBAL_CFG, 0x11)
    assert r32(iep, CMP0_REG0) == 7
    run(iep, 2)                                # count 6, 7: CMP0 hit
    assert r32(iep, CMP_STATUS) == 1 and r32(iep, CMP0_REG0) == 7
    run(iep, 1)                                # the reset clock loads REG1
    assert r32(iep, COUNT0) == 0 and r32(iep, CMP0_REG0) == 100


def test_shadow_write_is_not_a_buffer_on_reg0():
    """A write to REG1 must never become visible in REG0 before a reset."""
    iep = make(active0=10, shadow_cmp0=25)
    run(iep, 9)
    assert r32(iep, CMP0_REG0) == 10
    assert r32(iep, CMP0_REG1) == 25
    run(iep, 2)                                 # hit at 10, reset clock 11
    assert r32(iep, CMP0_REG0) == 25            # only the reset copies it


def test_partial_word_writes_follow_the_shadow_programming_model():
    iep = IepTimer()
    w32(iep, CMP0_REG0, 0x11223344)
    w32(iep, COUNT0, 1)                        # stopped, COUNT != 0: no copy
    w32(iep, CMP_CFG, SHADOW | EN0)
    w32(iep, CMP0_REG1, 0xAABBCCDD)
    iep.write(CMP0_REG1 + 1, b"\x12")          # byte 1 of the shadow
    assert r32(iep, CMP0_REG1) == 0xAABB12DD
    assert r32(iep, CMP0_REG0) == 0x11223344
    # a half-word write to the active register is a REG0 write: data discarded,
    # the shadow is copied
    iep.write(CMP0_REG0 + 2, b"\xEE\xFF")
    assert r32(iep, CMP0_REG0) == 0xAABB12DD


def test_64_bit_mode_partial_writes_are_unchanged():
    iep = IepTimer()
    w32(iep, CMP0_REG0, 0x11223344)
    iep.write(CMP0_REG0 + 1, b"\xAB")
    assert r32(iep, CMP0_REG0) == 0x1122AB44
    iep.write(CMP0_REG1 + 3, b"\x7F")
    assert r32(iep, CMP0_REG1) == 0x7F000000
    assert iep.compare[0] == (0x7F000000 << 32) | 0x1122AB44


def test_a_shadow_write_never_touches_pending_cmp_status():
    iep = make(active0=3, shadow_cmp0=3)
    run(iep, 3)
    assert r32(iep, CMP_STATUS) == 1
    w32(iep, CMP0_REG1, 5)
    assert r32(iep, CMP_STATUS) == 1


# --- reset period, hand-computed ------------------------------------------------

def test_cmp0_half_period_is_cmp0_plus_one_clocks_in_shadow_mode():
    iep = make(active0=10)
    run(iep, 9)
    assert r32(iep, COUNT0) == 9 and r32(iep, CMP_STATUS) == 0
    iep.tick()                                  # clock 10: counter == CMP0
    assert r32(iep, COUNT0) == 10 and r32(iep, CMP_STATUS) == 1
    iep.tick()                                  # clock 11: the reset clock
    assert r32(iep, COUNT0) == 0
    iep.tick()
    assert r32(iep, COUNT0) == 1


def test_compare_of_zero_does_not_fire():
    iep = make(active0=0, cmp1=0)
    run(iep, 5000)
    assert r32(iep, CMP_STATUS) == 0
    assert r32(iep, COUNT0) == 5000            # CMP0 == 0 never resets either


def test_early_shadow_write_is_loaded_at_the_reset():
    iep = make(active0=10)
    events = listen(iep)
    run(iep, 3)
    w32(iep, CMP0_REG1, 20)                     # early: well before the reset
    run(iep, 7)                                 # clock 10: first hit at 10
    assert [(e.index, e.count, e.tick) for e in events] == [(0, 10, 10)]
    assert r32(iep, CMP0_REG0) == 10            # still active until the reset
    iep.tick()                                  # clock 11 reset loads shadow
    assert r32(iep, CMP0_REG0) == 20
    run(iep, 20)                                # clocks 12..31, count 1..20
    assert [(e.index, e.count, e.tick) for e in events] == [(0, 10, 10), (0, 20, 31)]


def test_late_shadow_write_waits_for_the_following_reset():
    iep = make(active0=10)                      # REG1 written to 10 as well
    events = listen(iep)
    run(iep, 12)                                # past the first reset (clock 11)
    w32(iep, CMP0_REG1, 20)                     # late for reset 1
    run(iep, 30)                                # to clock 42
    # reset 1 loaded 10 -> hit at 21; reset 2 (clock 22) loads 20 -> hit at 42.
    assert [(e.count, e.tick) for e in events] == [(10, 10), (10, 21), (20, 42)]


def test_repeated_resets_without_new_writes_reapply_the_last_shadow():
    iep = make(active0=10, shadow_cmp0=6)
    events = listen(iep)
    run(iep, 40)
    # 10 -> shadow 6 from the first reset on: clocks 10, 17, 24, 31, 38.
    assert [(e.count, e.tick) for e in events] == [
        (10, 10), (6, 17), (6, 24), (6, 31), (6, 38)]


def test_changing_cmp0_does_not_change_the_period_in_flight():
    iep = make(active0=30)
    events = listen(iep)
    run(iep, 10)
    w32(iep, CMP0_REG1, 5)                      # mid-period CMP0 change
    run(iep, 100)
    # the running period still ends at 30; the new value applies afterwards:
    # 30 at clock 30, reset 31, then 5 at 36, 42, 48 ...
    assert [e.tick for e in events][:4] == [30, 36, 42, 48]
    assert [e.count for e in events][:4] == [30, 5, 5, 5]


def test_simultaneous_cmp0_and_other_compare_hits_share_a_clock():
    iep = make(active0=8, cmp1=8)
    events = listen(iep)
    run(iep, 8)
    assert r32(iep, CMP_STATUS) == 0b11
    assert sorted((e.index, e.tick) for e in events) == [(0, 8), (1, 8)]


def test_other_compare_beyond_cmp0_never_fires_but_fires_after_cmp0_grows():
    iep = make(active0=10, cmp1=15)
    run(iep, 100)
    assert r32(iep, CMP_STATUS) & 0b10 == 0     # reset at 10 hides 15
    assert r32(iep, CMP0_REG0) == 10            # shadow write: active unchanged
    w32(iep, CMP0_REG1, 20)
    assert r32(iep, CMP0_REG0) == 10            # (at base this sets the upper word)
    run(iep, 40)
    assert r32(iep, CMP_STATUS) & 0b10 == 0b10


def test_cmp1_is_loaded_from_its_own_shadow():
    iep = make(active0=4, cmp1=3)
    w32(iep, CMP1_REG1, 2)
    events = listen(iep)
    run(iep, 3)                                 # CMP1 active 3 hits at clock 3
    run(iep, 2)                                 # clock 4 hit CMP0, clock 5 reset
    assert r32(iep, CMP1_REG0) == 2
    run(iep, 2)                                 # clocks 6, 7 -> count 1, 2
    assert [(e.index, e.tick) for e in events] == [(1, 3), (0, 4), (1, 7)]


# --- rollover, 32/64-bit switching ------------------------------------------------

def test_32_bit_rollover_loads_the_shadow_and_does_not_carry():
    iep = IepTimer()
    w32(iep, CMP_CFG, SHADOW | EN1)             # no CMP0 reset
    w32(iep, COUNT0, 0xFFFFFFFD)
    w32(iep, CMP1_REG1, 4)
    w32(iep, GLOBAL_CFG, 0x11)
    assert r32(iep, CMP1_REG0) == 0
    run(iep, 2)                                 # 0xFFFFFFFE, 0xFFFFFFFF
    assert r32(iep, CMP1_REG0) == 0
    iep.tick()                                  # rollover to 0
    assert r32(iep, COUNT0) == 0 and r32(iep, COUNT1) == 0
    assert r32(iep, CMP1_REG0) == 4             # loaded at the rollover
    run(iep, 4)
    assert r32(iep, CMP_STATUS) == 0b10


def test_64_bit_counter_still_carries_into_the_high_word():
    iep = IepTimer()
    w32(iep, COUNT0, 0xFFFFFFFE)
    w32(iep, GLOBAL_CFG, 0x11)
    run(iep, 2)
    assert (r32(iep, COUNT1), r32(iep, COUNT0)) == (1, 0)


def test_entering_shadow_mode_truncates_the_counter_to_32_bits():
    iep = IepTimer()
    w32(iep, COUNT0, 7)
    w32(iep, COUNT1, 3)
    w32(iep, CMP_CFG, SHADOW)
    assert (r32(iep, COUNT1), r32(iep, COUNT0)) == (0, 7)


def test_switching_back_to_64_bit_restores_the_64_bit_compare_view():
    iep = IepTimer()
    w32(iep, CMP0_REG0, 9)
    w32(iep, COUNT0, 1)                          # stopped, COUNT != 0: no copy
    w32(iep, CMP_CFG, SHADOW | EN0)
    w32(iep, CMP0_REG1, 40)                      # shadow only
    w32(iep, CMP_CFG, EN0)                       # SHADOW_EN cleared
    assert r32(iep, CMP0_REG0) == 9              # active compare preserved
    assert r32(iep, CMP0_REG1) == 0              # REG1 is the upper half again
    w32(iep, CMP0_REG1, 1)
    assert iep.compare[0] == (1 << 32) | 9
    w32(iep, GLOBAL_CFG, 0x11)
    w32(iep, COUNT0, 0xFFFFFFFF)
    iep.tick()
    assert (r32(iep, COUNT1), r32(iep, COUNT0)) == (1, 0)


# --- efficiency mode --------------------------------------------------------------

def test_status_stays_set_when_efficiency_is_disabled():
    iep = IepTimer()
    w32(iep, CMP1_REG0, 3)
    w32(iep, CMP_CFG, EN1)
    w32(iep, GLOBAL_CFG, 0x11)
    run(iep, 12)
    assert r32(iep, CMP_STATUS) == 0b10


def test_status_auto_clears_on_the_next_clock_when_efficiency_is_enabled():
    iep = IepTimer()
    events = listen(iep)
    iep.write_sa_mx(PWM_EFC)
    w32(iep, CMP1_REG0, 3)
    w32(iep, CMP_CFG, EN1)
    w32(iep, GLOBAL_CFG, 0x11)
    run(iep, 3)
    assert r32(iep, CMP_STATUS) == 0b10          # visible on the hit clock
    iep.tick()
    assert r32(iep, CMP_STATUS) == 0             # gone one clock later
    run(iep, 20)
    assert r32(iep, CMP_STATUS) == 0
    assert [(e.index, e.count, e.tick) for e in events] == [(1, 3, 3)]


def test_compare_events_survive_status_auto_clear_in_one_bulk_advance():
    iep = IepTimer()
    events = listen(iep)
    iep.write_sa_mx(PWM_EFC)
    w32(iep, CMP0_REG0, 5)
    w32(iep, CMP_CFG, RST | EN0)
    w32(iep, GLOBAL_CFG, 0x11)
    iep.observe_core_cycles("pru0", 61)          # 61 clocks in a single call
    assert r32(iep, CMP_STATUS) == 0
    # 64-bit reset on the clock after the hit: period 6 clocks, hits at 5, 11, ..., 59
    assert [e.tick for e in events] == list(range(5, 61, 6))


def test_efficiency_does_not_clear_a_hit_on_the_same_clock_as_the_clear():
    iep = IepTimer()
    iep.write_sa_mx(PWM_EFC)
    w32(iep, CMP0_REG0, 1)
    w32(iep, CMP_CFG, RST | EN0)                 # hits every 2nd clock (reset clock between)
    w32(iep, GLOBAL_CFG, 0x11)
    run(iep, 5)
    assert r32(iep, CMP_STATUS) == 1             # cleared, then set again


def test_pwm_efc_en_lives_in_the_sa_mx_word_and_follows_hardware_reset():
    iep = IepTimer()
    iep.write_sa_mx(0xFFFFFFFF)
    assert iep.sa_mx == 0x10FFF                  # only R/W fields are stored
    assert iep.pwm_efc_en
    iep.reset()                                  # IEP block reset: CFG word stays
    assert iep.pwm_efc_en
    iep.hardware_reset()
    assert iep.sa_mx == 0 and not iep.pwm_efc_en


# --- review fixes: COUNT_REG1, bulk rollover, mode toggle, TRM sequence ----------

def test_count_reg1_is_inert_in_32_bit_shadow_mode():
    """The 32-bit counter has no upper word: a COUNT1 write must not make the
    next tick look like a rollover and load the shadow."""
    iep = make(active0=10, shadow_cmp0=25)
    run(iep, 3)
    w32(iep, COUNT1, 1)
    assert r32(iep, COUNT1) == 0
    iep.tick()
    assert r32(iep, COUNT0) == 4
    assert r32(iep, CMP0_REG0) == 10            # no spurious shadow load
    # bulk advance agrees with per-tick
    bulk = make(active0=10, shadow_cmp0=25)
    bulk.observe_core_cycles("pru0", 3)
    w32(bulk, COUNT1, 1)
    bulk.observe_core_cycles("pru0", 4)
    assert r32(bulk, COUNT0) == 4 and r32(bulk, CMP0_REG0) == 10


@pytest.mark.parametrize("inc", [1, 3, 5, 15])
@pytest.mark.parametrize("lead", [0, 1, 7, 40])
@pytest.mark.parametrize("chunk", [1, 5, 64, 400])
def test_bulk_advance_across_32_bit_rollover_matches_the_oracle(inc, lead, chunk):
    """Bulk advance must stop at the 2**32 wrap, load the shadow there, and
    agree with a per-clock statement of the semantics."""
    start = (1 << 32) - lead * inc - 1
    active = [3 * inc, 5 * inc] + [0] * 14
    shadow_vals = [7 * inc, 9 * inc] + [0] * 14
    iep = IepTimer()
    w32(iep, CMP0_REG0, active[0])
    w32(iep, CMP1_REG0, active[1])
    w32(iep, COUNT0, start)                     # COUNT != 0: no stopped copy
    w32(iep, CMP_CFG, EN0 | EN1 | SHADOW)       # no CMP0 reset: wrap is the load
    w32(iep, CMP0_REG1, shadow_vals[0])
    w32(iep, CMP1_REG1, shadow_vals[1])
    w32(iep, GLOBAL_CFG, (inc << 4) | 1)
    events = listen(iep)
    ref = RefIep(inc, True, False, 0b11, active, shadow_vals, False, start)
    cycles = 0
    for _ in range(max(2, (lead + 80) // chunk + 2)):
        cycles += chunk
        iep.observe_core_cycles("pru0", cycles)
        for _ in range(chunk):
            ref.step()
        assert r32(iep, COUNT0) == ref.count
        assert r32(iep, CMP_STATUS) == ref.status
        assert r32(iep, CMP0_REG0) == ref.active[0]
        assert r32(iep, CMP1_REG0) == ref.active[1]
    assert [(e.index, e.count, e.tick) for e in events] == ref.events


def test_toggling_shadow_mode_between_hit_and_reset_clock_drops_the_pending_reset():
    """Pins the (modelling) behaviour: leaving shadow mode cancels a reset that
    was scheduled for the next clock."""
    iep = make(active0=5, shadow_cmp0=5)
    run(iep, 5)                                  # hit at clock 5, reset pending
    w32(iep, CMP_CFG, RST | EN0)                 # SHADOW_EN off
    iep.tick()
    assert r32(iep, COUNT0) == 6                 # no reset happened


def test_trm_sequence_with_only_reg1_programmed_loads_at_count_zero():
    """Counter off at COUNT = 0, SHADOW_EN=1, program only REG1, counter on:
    measured on LP-AM243 ICSSG0 (register-level probe) the shadow reaches the
    active register at once, so CMP0 resets the counter with period 101."""
    iep = IepTimer()
    w32(iep, CMP_CFG, RST | EN0 | SHADOW)
    w32(iep, CMP0_REG1, 100)
    w32(iep, GLOBAL_CFG, 0x11)
    iep.observe_core_cycles("pru0", 1_010)
    assert r32(iep, CMP0_REG0) == 100
    assert r32(iep, COUNT0) == 1_010 % 101


def test_reg0_zero_with_a_non_zero_count_stays_idle_until_rollover():
    """Counter stopped at COUNT != 0, REG0 == 0, REG1 programmed: no copy (see
    test_no_shadow_copy_while_stopped_with_a_non_zero_count), so the compare
    stays 0 until the 32-bit rollover loads the shadow (about 21 s of IEP time
    at 200 MHz)."""
    iep = IepTimer()
    w32(iep, COUNT0, 1)
    w32(iep, CMP_CFG, RST | EN0 | SHADOW)
    w32(iep, CMP0_REG1, 100)
    w32(iep, GLOBAL_CFG, 0x11)
    iep.observe_core_cycles("pru0", 1_000_000)
    assert r32(iep, COUNT0) == 1_000_001
    assert r32(iep, CMP0_REG0) == 0
    assert r32(iep, CMP_STATUS) == 0


# --- independent per-clock oracle ----------------------------------------------

class RefIep:
    """Naive per-clock statement of the semantics, written separately from the
    model and never calling it."""

    def __init__(self, inc, shadow, rst, en, active, shadow_vals, efc, count):
        self.inc, self.sh, self.rst, self.en = inc, shadow, rst, en
        self.active = list(active)
        self.shadow = list(shadow_vals)
        self.efc = efc
        self.count = count
        # Compare is >= at CMP_EN (64-bit mode): enabled compares at or below COUNT.
        self.status = 0 if shadow else sum(
            1 << j for j in range(16)
            if (en >> j) & 1 and count >= self.active[j])
        self.pending = False
        self.t = 0
        self.events = []

    def step(self):
        self.t += 1
        if self.efc:
            self.status = 0
        if self.pending:
            self.pending = False
            self.count = 0
            if self.sh:
                self.active = list(self.shadow)
            return
        limit = (1 << 32) if self.sh else (1 << 64)
        total = self.count + self.inc
        self.count = total % limit
        if self.sh and total >= limit:
            self.active = list(self.shadow)
        hit0 = False
        for j in range(16):
            if (self.en >> j) & 1 and self.count == self.active[j]:
                self.status |= 1 << j
                self.events.append((j, self.count, self.t))
                hit0 |= j == 0
        if hit0 and self.rst:
            self.pending = True


def _random_case(rng):
    sh = rng.random() < 0.7
    efc = rng.random() < 0.5
    rst = rng.random() < 0.8
    inc = rng.choice([1, 1, 2, 3, 5, 15])
    en = rng.getrandbits(16) | (1 if rng.random() < 0.8 else 0)
    active = [rng.randrange(0, 60) * inc for _ in range(16)]
    shadow_vals = [rng.randrange(0, 60) * inc for _ in range(16)]
    count = rng.randrange(0, 5)
    if sh and rng.random() < 0.6:
        count = max(count, 1)                  # COUNT != 0: no stopped copy
    return sh, efc, rst, inc, en, active, shadow_vals, count


def _build(case, listener):
    sh, efc, rst, inc, en, active, shadow_vals, count = case
    iep = IepTimer()
    if listener:
        events = listen(iep)
    else:
        events = None
    for j in range(16):
        w32(iep, COMPARE_REG0[j], active[j])
    w32(iep, COUNT0, count)
    w32(iep, CMP_CFG, (RST if rst else 0) | (en << 1) | (SHADOW if sh else 0))
    if sh:
        for j in range(16):
            w32(iep, COMPARE_REG0[j] + 4, shadow_vals[j])
    if efc:
        iep.write_sa_mx(PWM_EFC)
    w32(iep, GLOBAL_CFG, (inc << 4) | 1)
    return iep, events


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("listener", [True, False])
def test_bulk_advance_matches_the_per_clock_oracle(seed, listener):
    rng = random.Random(seed)
    case = _random_case(rng)
    sh, efc, rst, inc, en, active, shadow_vals, count = case
    iep, events = _build(case, listener)
    # Counter stopped at COUNT == 0 when SHADOW_EN and the shadow values are
    # written: the active registers follow the shadow at once. With COUNT != 0
    # the active values programmed in 64-bit mode are kept.
    start_active = shadow_vals if sh and count == 0 else active
    ref = RefIep(inc, sh, rst, en, start_active, shadow_vals if sh else active,
                 efc, count)
    cycles = 0
    for _ in range(rng.randrange(1, 8)):
        chunk = rng.choice([1, 2, 3, 7, 50, 400, 2500])
        cycles += chunk
        iep.observe_core_cycles("pru0", cycles)
        for _ in range(chunk):
            ref.step()
        assert r32(iep, COUNT0) == ref.count % (1 << 32)
        assert r32(iep, CMP_STATUS) == ref.status
        for j in range(16):
            assert r32(iep, COMPARE_REG0[j]) == ref.active[j] % (1 << 32)
    if listener:
        assert [(e.index, e.count, e.tick) for e in events] == ref.events


@pytest.mark.parametrize("seed", range(25))
def test_event_engine_matches_the_legacy_engine_in_64_bit_mode(seed):
    """A listener only observes: it must not change 64-bit counter behaviour."""
    rng = random.Random(1000 + seed)
    inc = rng.choice([1, 2, 5, 15])
    en = rng.getrandbits(16)
    rst = rng.choice([0, 1])
    values = [rng.randrange(0, 90) * inc for _ in range(16)]

    def build(with_listener):
        iep = IepTimer()
        if with_listener:
            listen(iep)
        for j in range(16):
            w32(iep, COMPARE_REG0[j], values[j])
        w32(iep, CMP_CFG, rst | (en << 1))
        w32(iep, GLOBAL_CFG, (inc << 4) | 1)
        return iep

    plain, observed = build(False), build(True)
    cycles = 0
    for chunk in (1, 4, 90, 1000, 30000):
        cycles += chunk
        plain.observe_core_cycles("pru0", cycles)
        observed.observe_core_cycles("pru0", cycles)
        assert plain.count == observed.count
        assert plain.cmp_status == observed.cmp_status


def test_huge_bulk_advance_with_a_tiny_shadow_period_is_bounded():
    iep = make(active0=7)
    iep.observe_core_cycles("pru0", 10_000_003)
    # period 8 clocks (clocks 1..7 count up, clock 8 is the reset clock)
    assert iep.count == 10_000_003 % 8
    assert r32(iep, CMP0_REG0) == 7


def test_snapshot_restore_round_trips_shadow_state_and_pending_reset():
    iep = make(active0=10, shadow_cmp0=4)
    iep.write_sa_mx(PWM_EFC)
    run(iep, 10)                                 # hit clock: reset pending
    snap = iep.snapshot()
    run(iep, 25)
    expected = (iep.count, iep.cmp_status, list(iep.compare), list(iep.shadow))

    clone = IepTimer()
    clone.restore(snap)
    run(clone, 25)
    assert (clone.count, clone.cmp_status, list(clone.compare),
            list(clone.shadow)) == expected
    assert clone.pwm_efc_en


# --- firmware access through the Simulator ----------------------------------------
# These run SBCO/LBCO against the real memory map; the IEP advances only because
# the firmware's own instructions consume core cycles.

def _sim(config="nonexistent.cfg"):
    sim = Simulator(config_path=config)
    core = sim.cores["pru0"]
    core.constant_table.set(26, 0x0002E000)      # IEP0
    core.constant_table.set(4, 0x00026000)       # ICSS CFG
    return sim


def _hits(sim):
    events = []
    sim.iep.add_compare_listener(events.append)
    return events


_PROLOGUE = """
    ldi32 r1, 200
    sbco &r1, c26, 0x78, 4       ; active CMP0 (64-bit mode)
    ldi32 r1, 0x11
    sbco &r1, c26, 0x00, 4       ; counter runs: no stopped-counter copy
    ldi32 r1, 0x20003
    sbco &r1, c26, 0x70, 4       ; SHADOW_EN, CMP0 enable, reset on CMP0
    ldi32 r1, 200
    sbco &r1, c26, 0x7C, 4       ; REG1 is not seeded on entry: write it
"""
_START = """
    ldi32 r1, 0x11
    sbco &r1, c26, 0x00, 4
"""
_WAIT_HITS = """
wait:
    lbco &r2, c26, 0x74, 4
    qbbc wait, r2, 0
    ldi r3, 1
    sbco &r3, c26, 0x74, 4
    add r7, r7, 1
    qbgt wait, r7, {n}
    halt
"""


def test_firmware_shadow_write_is_loaded_at_each_cmp0_reset():
    sim = _sim()
    events = _hits(sim)
    src = (_PROLOGUE + """
    ldi32 r1, 20
    sbco &r1, c26, 0x7C, 4       ; shadow CMP0
    lbco &r5, c26, 0x78, 4
    lbco &r6, c26, 0x7C, 4
    ldi r7, 0
""" + _WAIT_HITS.format(n=3))
    assert sim.load("pru0", src) == []
    sim.cores["pru0"].run(max_steps=20000)
    assert sim.cores["pru0"].halted
    regs = sim.registers("pru0")
    assert regs[5] == 200 and regs[6] == 20
    assert [e.count for e in events] == [200, 20, 20]
    # reset clock + 20 counts between the second and third hit, and 21 again
    # between the first and second (the shadow was written before the first hit)
    assert [b.tick - a.tick for a, b in zip(events, events[1:])] == [21, 21]


def test_firmware_late_shadow_write_applies_one_reset_later():
    sim = _sim()
    events = _hits(sim)
    src = (_PROLOGUE + """
    ldi32 r1, 50
    sbco &r1, c26, 0x7C, 4       ; shadow CMP0 = 50
    ldi r7, 0
wait:
    lbco &r2, c26, 0x74, 4
    qbbc wait, r2, 0
    ldi r3, 1
    sbco &r3, c26, 0x74, 4
    add r7, r7, 1
    qbne late_done, r7, 1
    ldi32 r1, 70
    sbco &r1, c26, 0x7C, 4       ; after hit 1 has been seen: late for reset 1
late_done:
    qbgt wait, r7, 3
    halt
""")
    assert sim.load("pru0", src) == []
    sim.cores["pru0"].run(max_steps=20000)
    assert sim.cores["pru0"].halted
    # reset 1 loaded 50, reset 2 loaded the late 70
    assert [e.count for e in events] == [200, 50, 70]


def test_firmware_cmp0_change_mid_period_keeps_the_running_period():
    sim = _sim()
    events = _hits(sim)
    src = ("""
    ldi32 r1, 60
    sbco &r1, c26, 0x78, 4
    ldi32 r1, 0x11
    sbco &r1, c26, 0x00, 4
    ldi32 r1, 0x20003
    sbco &r1, c26, 0x70, 4
    ldi r7, 0
poll:
    lbco &r4, c26, 0x10, 4
    qbgt poll, r4, 5
    ldi32 r1, 20
    sbco &r1, c26, 0x7C, 4       ; mid-period CMP0 change
""" + _WAIT_HITS.format(n=2))
    assert sim.load("pru0", src) == []
    sim.cores["pru0"].run(max_steps=20000)
    assert sim.cores["pru0"].halted
    assert [e.count for e in events] == [60, 20]
    assert events[1].tick - events[0].tick == 21


def test_firmware_reg0_write_in_shadow_mode_copies_reg1_and_clears_status():
    sim = _sim()
    src = """
    ldi32 r1, 5
    sbco &r1, c26, 0x10, 4       ; COUNT = 5, counter stopped
    ldi32 r1, 10
    sbco &r1, c26, 0x78, 4
    ldi32 r1, 0x20002
    sbco &r1, c26, 0x70, 4       ; SHADOW_EN, CMP0 enable
    ldi32 r1, 20
    sbco &r1, c26, 0x7C, 4       ; shadow CMP0
    lbco &r5, c26, 0x78, 4
    ldi32 r1, 99
    sbco &r1, c26, 0x78, 4       ; REG0 write: data discarded, REG1 copied
    lbco &r6, c26, 0x78, 4
    halt
"""
    assert sim.load("pru0", src) == []
    sim.cores["pru0"].run(max_steps=100)
    assert sim.cores["pru0"].halted
    regs = sim.registers("pru0")
    assert regs[5] == 10 and regs[6] == 20


def test_firmware_efficiency_clears_status_but_the_event_still_fires():
    def program(efficiency):
        sim = _sim()
        events = _hits(sim)
        enable = ("ldi32 r1, 0x10000\n    sbco &r1, c4, 0x40, 4\n"
                  if efficiency else "")
        src = (enable + """
    ldi32 r1, 5
    sbco &r1, c26, 0x80, 4       ; CMP1 = 5
    ldi32 r1, 0x4
    sbco &r1, c26, 0x70, 4       ; CMP_EN[1]
""" + _START + """
    ldi r8, 40
spin:
    sub r8, r8, 1
    qbne spin, r8, 0
    lbco &r2, c26, 0x74, 4
    lbco &r9, c4, 0x40, 4
    halt
""")
        assert sim.load("pru0", src) == []
        sim.cores["pru0"].run(max_steps=20000)
        assert sim.cores["pru0"].halted
        return sim.registers("pru0"), events

    regs, events = program(True)
    assert regs[2] == 0                          # auto-cleared
    assert regs[9] == 0x10000                    # SA_MX readback through the bus
    assert [(e.index, e.count) for e in events] == [(1, 5)]

    regs, events = program(False)
    assert regs[2] == 0b10                       # legacy: still pending
    assert regs[9] == 0
    assert [(e.index, e.count) for e in events] == [(1, 5)]


def test_firmware_sa_mx_register_stores_only_its_writable_fields():
    sim = _sim()
    src = """
    ldi32 r1, 0xFFFFFFFF
    sbco &r1, c4, 0x40, 4
    lbco &r2, c4, 0x40, 4
    halt
"""
    assert sim.load("pru0", src) == []
    sim.cores["pru0"].run(max_steps=100)
    assert sim.registers("pru0")[2] == 0x10FFF
    assert sim.iep.pwm_efc_en


def test_sa_mx_register_is_not_mapped_on_the_icssm_targets():
    root = os.path.join(os.path.dirname(__file__), "..", "config")
    for name in ("memory_am261x.cfg", "memory_am263x.cfg"):
        sim = Simulator(config_path=os.path.join(root, name))
        with pytest.raises(ValueError):
            sim.memory.write(0x26040, (0x10000).to_bytes(4, "little"))
        assert not sim.iep.pwm_efc_en
    for name in ("memory_am243x.cfg",):
        sim = Simulator(config_path=os.path.join(root, name))
        sim.memory.write(0x26040, (0x10000).to_bytes(4, "little"))
        assert sim.iep.pwm_efc_en


def test_sa_mx_is_cleared_by_a_simulator_hardware_reset():
    sim = _sim()
    sim.memory.write(0x26040, (0x10000).to_bytes(4, "little"))
    assert sim.iep.pwm_efc_en
    sim.iep.hardware_reset()
    assert not sim.iep.pwm_efc_en
    assert sim.memory.read(0x26040, 4)[0] == (0).to_bytes(4, "little")


def test_shadow_mode_is_available_on_the_icssm_targets_without_any_pwm_block():
    root = os.path.join(os.path.dirname(__file__), "..", "config")
    sim = Simulator(config_path=os.path.join(root, "memory_am261x.cfg"))
    sim.cores["pru0"].constant_table.set(26, 0x0002E000)
    assert not hasattr(sim, "pwm")
    sim.memory.write(0x2E000 + CMP0_REG0, (10).to_bytes(4, "little"))
    sim.memory.write(0x2E000 + COUNT0, (5).to_bytes(4, "little"))
    sim.memory.write(0x2E000 + CMP_CFG, (SHADOW | EN0 | RST).to_bytes(4, "little"))
    sim.memory.write(0x2E000 + CMP0_REG1, (20).to_bytes(4, "little"))
    assert sim.iep.read(CMP0_REG1, 4) == (20).to_bytes(4, "little")
    assert sim.iep.read(CMP0_REG0, 4) == (10).to_bytes(4, "little")
    # a REG0 write copies REG1 (measured on LP-AM261 ICSSM0, same as AM243)
    sim.memory.write(0x2E000 + CMP0_REG0, (99).to_bytes(4, "little"))
    assert sim.iep.read(CMP0_REG0, 4) == (20).to_bytes(4, "little")
