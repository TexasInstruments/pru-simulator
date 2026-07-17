"""Tests for core/branch.py"""

import pytest
from core.branch import BranchUnit, LoopState


# ---------------------------------------------------------------------------
# BranchUnit – basic taken / not-taken for each condition
# ---------------------------------------------------------------------------

class TestQbeq:
    def setup_method(self):
        self.bu = BranchUnit()

    def test_taken_equal(self):
        assert self.bu.qbeq(5, 5) is True

    def test_not_taken_different(self):
        assert self.bu.qbeq(5, 4) is False

    def test_not_taken_gt(self):
        assert self.bu.qbeq(3, 6) is False

    def test_equal_zero(self):
        assert self.bu.qbeq(0, 0) is True


class TestQbne:
    def setup_method(self):
        self.bu = BranchUnit()

    def test_taken_different(self):
        assert self.bu.qbne(5, 4) is True

    def test_not_taken_equal(self):
        assert self.bu.qbne(5, 5) is False

    def test_taken_zero_vs_nonzero(self):
        assert self.bu.qbne(0, 1) is True


class TestQbgt:
    def setup_method(self):
        self.bu = BranchUnit()

    def test_taken_op_greater(self):
        # op_val=10 > reg_val=5 → taken
        assert self.bu.qbgt(5, 10) is True

    def test_not_taken_equal(self):
        assert self.bu.qbgt(5, 5) is False

    def test_not_taken_op_less(self):
        assert self.bu.qbgt(10, 5) is False


class TestQbge:
    def setup_method(self):
        self.bu = BranchUnit()

    def test_taken_op_greater(self):
        assert self.bu.qbge(5, 10) is True

    def test_taken_equal(self):
        assert self.bu.qbge(5, 5) is True

    def test_not_taken_op_less(self):
        assert self.bu.qbge(10, 5) is False


class TestQblt:
    def setup_method(self):
        self.bu = BranchUnit()

    def test_taken_op_less(self):
        # op_val=3 < reg_val=10 → taken
        assert self.bu.qblt(10, 3) is True

    def test_not_taken_equal(self):
        assert self.bu.qblt(5, 5) is False

    def test_not_taken_op_greater(self):
        assert self.bu.qblt(5, 10) is False


class TestQble:
    def setup_method(self):
        self.bu = BranchUnit()

    def test_taken_op_less(self):
        assert self.bu.qble(10, 3) is True

    def test_taken_equal(self):
        assert self.bu.qble(5, 5) is True

    def test_not_taken_op_greater(self):
        assert self.bu.qble(5, 10) is False


# ---------------------------------------------------------------------------
# Bit tests
# ---------------------------------------------------------------------------

class TestQbbs:
    def setup_method(self):
        self.bu = BranchUnit()

    def test_taken_bit_set(self):
        assert self.bu.qbbs(0b1010, 1) is True

    def test_taken_bit_set_high(self):
        assert self.bu.qbbs(0x8000_0000, 31) is True

    def test_not_taken_bit_clear(self):
        assert self.bu.qbbs(0b1010, 0) is False

    def test_not_taken_zero(self):
        assert self.bu.qbbs(0, 7) is False


class TestQbbc:
    def setup_method(self):
        self.bu = BranchUnit()

    def test_taken_bit_clear(self):
        assert self.bu.qbbc(0b1010, 0) is True

    def test_not_taken_bit_set(self):
        assert self.bu.qbbc(0b1010, 1) is False

    def test_taken_all_zeros(self):
        assert self.bu.qbbc(0, 15) is True


# ---------------------------------------------------------------------------
# Equal edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def setup_method(self):
        self.bu = BranchUnit()

    def test_qbeq_large_values(self):
        assert self.bu.qbeq(0xFFFF_FFFF, 0xFFFF_FFFF) is True

    def test_qbne_large_different(self):
        assert self.bu.qbne(0xFFFF_FFFF, 0xFFFF_FFFE) is True

    def test_qbgt_boundary(self):
        assert self.bu.qbgt(0xFFFF_FFFE, 0xFFFF_FFFF) is True

    def test_qblt_boundary(self):
        assert self.bu.qblt(0xFFFF_FFFF, 0xFFFF_FFFE) is True


# ---------------------------------------------------------------------------
# LoopState
# ---------------------------------------------------------------------------

class TestLoopState:
    def test_initial_not_done(self):
        ls = LoopState(count=3, start_address=0x100, end_address=0x120)
        assert ls.is_done() is False

    def test_tick_decrements_count(self):
        ls = LoopState(count=3, start_address=0, end_address=0)
        ls.tick()
        assert ls.count == 2

    def test_tick_returns_true_while_looping(self):
        ls = LoopState(count=3, start_address=0, end_address=0)
        assert ls.tick() is True   # count → 2, still looping
        assert ls.tick() is True   # count → 1, still looping

    def test_tick_returns_false_on_last_iteration(self):
        ls = LoopState(count=1, start_address=0, end_address=0)
        assert ls.tick() is False  # count → 0, done

    def test_is_done_after_all_ticks(self):
        ls = LoopState(count=2, start_address=0, end_address=0)
        ls.tick()
        ls.tick()
        assert ls.is_done() is True

    def test_count_zero_is_done(self):
        ls = LoopState(count=0, start_address=0, end_address=0)
        assert ls.is_done() is True

    def test_addresses_preserved(self):
        ls = LoopState(count=5, start_address=0x200, end_address=0x280)
        assert ls.start_address == 0x200
        assert ls.end_address == 0x280
        ls.tick()
        assert ls.start_address == 0x200
        assert ls.end_address == 0x280
