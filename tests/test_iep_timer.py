"""IEP timer: counter, compare, and the TRM bitfield semantics.

Every expectation here is taken from the AM243x TRM (SPRUIM2J §6.4.13), not
from another simulator's behaviour.
"""

import pytest

from perif.iep import (
    IepTimer,
    GLOBAL_CFG,
    COUNT_REG0,
    COUNT_REG1,
    CMP_CFG,
    CMP_STATUS,
    CMP0_REG0,
)


def w32(iep, off, val):
    iep.write(off, val.to_bytes(4, "little"))


def r32(iep, off):
    return int.from_bytes(iep.read(off, 4), "little")


@pytest.fixture
def iep():
    return IepTimer()


# --- counter ---------------------------------------------------------------

def test_counter_does_not_run_until_cnt_enable(iep):
    """IEP_GLOBAL_CFG_REG[0] CNT_ENABLE gates the counter."""
    w32(iep, GLOBAL_CFG, 0x10)          # DEFAULT_INC=1, CNT_ENABLE=0
    for _ in range(10):
        iep.tick()
    assert r32(iep, COUNT_REG0) == 0

    w32(iep, GLOBAL_CFG, 0x11)          # DEFAULT_INC=1, CNT_ENABLE=1
    for _ in range(10):
        iep.tick()
    assert r32(iep, COUNT_REG0) == 10


def test_default_inc_is_the_step_size(iep):
    """IEP_GLOBAL_CFG_REG[7:4] DEFAULT_INC, not a hardcoded +1."""
    w32(iep, GLOBAL_CFG, (5 << 4) | 1)
    for _ in range(4):
        iep.tick()
    assert r32(iep, COUNT_REG0) == 20


def test_default_inc_zero_does_not_advance(iep):
    w32(iep, GLOBAL_CFG, 0x01)           # CNT_ENABLE=1, DEFAULT_INC=0
    for _ in range(8):
        iep.tick()
    assert r32(iep, COUNT_REG0) == 0


def test_counter_is_64_bit_across_the_register_pair(iep):
    w32(iep, GLOBAL_CFG, 0x11)
    w32(iep, COUNT_REG0, 0xFFFFFFFE)
    w32(iep, COUNT_REG1, 0x00000007)
    iep.tick()
    iep.tick()
    assert r32(iep, COUNT_REG0) == 0
    assert r32(iep, COUNT_REG1) == 8


# --- compare ---------------------------------------------------------------

def test_cmp_en_starts_at_bit_1(iep):
    """IEP_CMP_CFG_REG[16:1] CMP_EN - bit 1 maps to CMP0, bit 0 is RST_CNT_EN."""
    w32(iep, GLOBAL_CFG, 0x11)
    w32(iep, CMP0_REG0, 5)

    w32(iep, CMP_CFG, 0x1)               # RST_CNT_EN only, CMP0 NOT enabled
    for _ in range(8):
        iep.tick()
    assert r32(iep, CMP_STATUS) == 0, "CMP0 fired with CMP_EN clear"

    iep.reset()
    w32(iep, GLOBAL_CFG, 0x11)
    w32(iep, CMP0_REG0, 5)
    w32(iep, CMP_CFG, 0x2)               # CMP_EN[0] set, RST_CNT_EN clear
    for _ in range(8):
        iep.tick()
    assert r32(iep, CMP_STATUS) & 0x1, "CMP0 did not fire with CMP_EN set"


def test_cmp0_rst_cnt_en_controls_auto_reset(iep):
    """Auto-reset is configuration, not implicit on a CMP0 hit."""
    # enabled, no reset -> counter runs past the compare
    w32(iep, GLOBAL_CFG, 0x11)
    w32(iep, CMP0_REG0, 5)
    w32(iep, CMP_CFG, 0x2)
    for _ in range(8):
        iep.tick()
    assert r32(iep, COUNT_REG0) == 8

    # enabled, with reset -> counter wraps at the compare
    iep.reset()
    w32(iep, GLOBAL_CFG, 0x11)
    w32(iep, CMP0_REG0, 5)
    w32(iep, CMP_CFG, 0x3)               # RST_CNT_EN | CMP_EN[0]
    for _ in range(8):
        iep.tick()
    assert r32(iep, COUNT_REG0) == 3      # 1..5 -> 0, then 1,2,3


def test_cmp_status_is_write_one_to_clear(iep):
    """IEP_CMP_STATUS_REG: 16 status bits, write 1h to clear."""
    w32(iep, GLOBAL_CFG, 0x11)
    w32(iep, CMP0_REG0, 2)
    w32(iep, CMP_CFG, 0x2)
    iep.tick()
    iep.tick()
    assert r32(iep, CMP_STATUS) & 0x1

    w32(iep, CMP_STATUS, 0x0)             # writing 0 must NOT clear
    assert r32(iep, CMP_STATUS) & 0x1
    w32(iep, CMP_STATUS, 0x1)             # writing 1 clears
    assert r32(iep, CMP_STATUS) & 0x1 == 0


def test_compare_registers_are_64_bit_pairs(iep):
    """"16x 64-bit compare registers: IEP_CMPj_REG0/IEP_CMPj_REG1".

    So 0x4C is the UPPER half of CMP0, and CMP1_REG0 is at 0x50. A model that
    treats 0x4C as CMP1 would let this test's CMP1 write land in CMP0's high
    word - and would then never match.
    """
    w32(iep, CMP0_REG0 + 4, 0xDEADBEEF)   # CMP0_REG1
    assert iep.compare[0] == 0xDEADBEEF << 32
    assert iep.compare[1] == 0

    w32(iep, CMP0_REG0 + 8, 0x1234)       # CMP1_REG0
    assert iep.compare[1] == 0x1234
    assert iep.compare[0] == 0xDEADBEEF << 32


def test_higher_compares_set_their_own_status_bit(iep):
    w32(iep, GLOBAL_CFG, 0x11)
    w32(iep, CMP0_REG0 + 8 * 3, 4)        # CMP3
    w32(iep, CMP_CFG, 1 << 4)             # CMP_EN[3] -> bit 4
    for _ in range(6):
        iep.tick()
    assert r32(iep, CMP_STATUS) == 1 << 3


def test_unimplemented_offsets_do_not_fault(iep):
    # 0x20 is IEP_CAPR0_REG0. Capture is deliberately out of scope, so it must
    # read back zero rather than fault - firmware touching it does not crash, it
    # simply sees the feature do nothing.
    w32(iep, 0x20, 0x1234)
    assert r32(iep, 0x20) == 0


# --- integration: firmware polling the IEP through the constant table -------

class TestIepThroughFirmware:
    """The acceptance case: firmware that polls IEP_COUNT_REG0 must terminate.

    This is the pattern every ICSSG timing block uses - set CMP0 to the period,
    enable the counter, then spin on the count. Before the IEP existed in this
    simulator the LBCO returned a constant and the spin never ended.
    """

    def _sim(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from simulator import Simulator
        sim = Simulator(config_path="nonexistent.cfg")
        sim.constant_table.set(26, 0x0002E000)
        return sim

    def test_poll_on_count_terminates(self):
        sim = self._sim()
        # CNT_ENABLE | DEFAULT_INC=1, then spin until COUNT reaches 20.
        errors = sim.load("pru0", "\n".join([
            "ldi32 r1, 0x11",
            "sbco &r1, c26, 0x00, 4",
            "wait:",
            "lbco &r2, c26, 0x10, 4",
            "qbgt wait, r2, 20",
            "halt",
        ]))
        assert errors == []
        sim.cores["pru0"].run(max_steps=500)
        assert sim.cores["pru0"].halted
        assert sim.registers("pru0")[2] >= 20

    def test_cmp0_period_boundary_is_visible_to_firmware(self):
        sim = self._sim()
        errors = sim.load("pru0", "\n".join([
            "ldi32 r1, 40",
            "sbco &r1, c26, 0x78, 4",     # CMP0_REG0 = period
            "ldi32 r1, 0x3",
            "sbco &r1, c26, 0x70, 4",     # CMP_CFG: RST_CNT_EN | CMP_EN[0]
            "ldi32 r1, 0x11",
            "sbco &r1, c26, 0x00, 4",     # CNT_ENABLE, DEFAULT_INC=1
            "wait:",
            "lbco &r2, c26, 0x74, 4",     # poll CMP_STATUS
            "qbbc wait, r2, 0",
            "halt",
        ]))
        assert errors == []
        sim.cores["pru0"].run(max_steps=500)
        assert sim.cores["pru0"].halted, "firmware never saw the CMP0 period boundary"
        # auto-reset means the counter wrapped rather than running away
        assert sim.iep.count < 40

    def test_counter_does_not_run_without_firmware_enabling_it(self):
        sim = self._sim()
        sim.load("pru0", "ldi r0, 1\nldi r0, 2\nldi r0, 3\nhalt")
        sim.cores["pru0"].run(max_steps=50)
        assert sim.iep.count == 0


# --- compare semantics: equality, not threshold ----------------------------
# Raised in adversarial review 2026-08-28. Pinned here as a deliberate choice
# rather than left implicit, because either reading has a failure mode and the
# TRM text quoted in perif/iep.py does not settle it in one sentence.

def test_compare_is_equality_so_a_stepped_over_value_never_fires(iep):
    """DEFAULT_INC > 1 steps over a compare that is not on an increment boundary.

    The IEP compare is a match on the counter value, so firmware must program
    compares consistent with its increment. Modelling this as `>=` instead would
    re-assert the event on every cycle past the threshold, which is a different
    and worse wrong answer. This test exists so the choice is visible and cannot
    be changed silently.
    """
    w32(iep, GLOBAL_CFG, (2 << 4) | 1)      # DEFAULT_INC=2
    w32(iep, CMP0_REG0 + 8, 5)              # CMP1 = 5, an odd value
    w32(iep, CMP_CFG, 1 << 2)               # CMP_EN[1]
    for _ in range(10):
        iep.tick()
    assert r32(iep, COUNT_REG0) == 20
    assert r32(iep, CMP_STATUS) == 0, "counter stepped 4 -> 6; an equality compare must not fire"

    # On an increment boundary it fires exactly once.
    iep.reset()
    w32(iep, GLOBAL_CFG, (2 << 4) | 1)
    w32(iep, CMP0_REG0 + 8, 6)
    w32(iep, CMP_CFG, 1 << 2)
    for _ in range(10):
        iep.tick()
    assert r32(iep, CMP_STATUS) == (1 << 1)


def test_compare_matches_on_the_full_64_bit_value(iep):
    """A compare whose high word differs must not fire on a low-word match."""
    w32(iep, GLOBAL_CFG, 0x11)
    w32(iep, CMP0_REG0 + 8, 4)              # CMP1_REG0 = 4
    w32(iep, CMP0_REG0 + 12, 1)             # CMP1_REG1 = 1  -> compare is 2^32 + 4
    w32(iep, CMP_CFG, 1 << 2)
    for _ in range(8):
        iep.tick()
    assert r32(iep, COUNT_REG0) == 8
    assert r32(iep, CMP_STATUS) == 0, "low-word match fired despite a differing high word"


def test_counter_carries_into_the_high_word(iep):
    w32(iep, GLOBAL_CFG, 0x11)
    w32(iep, COUNT_REG0, 0xFFFFFFFF)
    iep.tick()
    assert r32(iep, COUNT_REG0) == 0
    assert r32(iep, COUNT_REG1) == 1


# ---------------------------------------------------------------------------
# Capture unit [plane:RND-19]
# ---------------------------------------------------------------------------
# Added because TDLY - and every protocol that timestamps an asynchronous
# external event against a line phase - needs it, and the module previously
# said in its own docstring that capture registers were not modelled.

from perif.iep import CAP_CFG, CAP_STATUS, CAPR0_REG0, NUM_CAPTURE  # noqa: E402


def _running_iep():
    iep = IepTimer()
    iep.global_cfg = (1 << 0) | (1 << 4)         # CNT_ENABLE, DEFAULT_INC = 1
    return iep


def test_a_disabled_slot_captures_nothing():
    """Enable gating is the difference between a timestamp and a coincidence."""
    iep = _running_iep()
    for _ in range(10):
        iep.tick()
    assert iep.capture_event(0) is False
    assert iep.read32(CAPR0_REG0) == 0
    assert iep.read32(CAP_STATUS) == 0


def test_capture_latches_the_counter_at_the_event():
    iep = _running_iep()
    iep.write32(CAP_CFG, 0x1)
    for _ in range(37):
        iep.tick()
    assert iep.capture_event(0) is True
    assert iep.read32(CAPR0_REG0) == 37
    assert iep.read32(CAP_STATUS) & 0x1


def test_a_different_trigger_time_gives_a_different_value():
    """The gate that stops this being a no-op.

    A capture path that always returns the same number - or zero - would
    satisfy every other test here. This one cannot be passed by anything that
    is not actually reading the counter at the moment of the event.
    """
    seen = []
    for ticks in (1, 9, 40, 255):
        iep = _running_iep()
        iep.write32(CAP_CFG, 0x1)
        for _ in range(ticks):
            iep.tick()
        iep.capture_event(0)
        seen.append(iep.read32(CAPR0_REG0))
    assert seen == [1, 9, 40, 255]


def test_first_mode_keeps_the_first_event():
    """A later edge must not overwrite the trigger being timestamped."""
    iep = _running_iep()
    iep.write32(CAP_CFG, 0x1)                     # slot 0, first mode
    for _ in range(5):
        iep.tick()
    iep.capture_event(0)
    for _ in range(50):
        iep.tick()
    assert iep.capture_event(0) is False
    assert iep.read32(CAPR0_REG0) == 5


def test_last_mode_overwrites():
    iep = _running_iep()
    iep.write32(CAP_CFG, 0x1 | (1 << 7))          # slot 0, last mode
    for _ in range(5):
        iep.tick()
    iep.capture_event(0)
    for _ in range(50):
        iep.tick()
    assert iep.capture_event(0) is True
    assert iep.read32(CAPR0_REG0) == 55


def test_clearing_the_valid_bit_re_arms_first_mode():
    iep = _running_iep()
    iep.write32(CAP_CFG, 0x1)
    iep.tick()
    iep.capture_event(0)
    iep.write32(CAP_STATUS, 0x1)                  # write 1 to clear
    assert iep.cap_valid(0) is False
    for _ in range(9):
        iep.tick()
    assert iep.capture_event(0) is True
    assert iep.read32(CAPR0_REG0) == 10


def test_capture_registers_are_read_only():
    """Hardware does not let software forge a timestamp; nor does this."""
    iep = _running_iep()
    iep.write32(CAP_CFG, 0x1)
    for _ in range(7):
        iep.tick()
    iep.capture_event(0)
    iep.write32(CAPR0_REG0, 0xDEADBEEF)
    assert iep.read32(CAPR0_REG0) == 7


def test_slots_are_independent():
    iep = _running_iep()
    iep.write32(CAP_CFG, 0x1 | 0x2)               # slots 0 and 1
    for _ in range(3):
        iep.tick()
    iep.capture_event(0)
    for _ in range(4):
        iep.tick()
    iep.capture_event(1)
    assert iep.read32(CAPR0_REG0) == 3
    assert iep.read32(CAPR0_REG0 + 8) == 7
    assert iep.read32(CAP_STATUS) == 0x3


def test_an_out_of_range_slot_is_rejected():
    iep = _running_iep()
    with pytest.raises(ValueError):
        iep.capture_event(NUM_CAPTURE)
