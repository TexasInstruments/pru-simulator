"""Tests for core/counters.py — CycleCounters."""

import pytest
from core.counters import CycleCounters


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_initial_state():
    cc = CycleCounters()
    assert cc.cycles == 0
    assert cc.stall_cycles == 0
    assert cc.instruction_count == 0


# ---------------------------------------------------------------------------
# tick
# ---------------------------------------------------------------------------

def test_tick_increments_cycles_and_instruction_count():
    cc = CycleCounters()
    cc.tick()
    assert cc.cycles == 1
    assert cc.instruction_count == 1
    assert cc.stall_cycles == 0


def test_tick_does_not_increment_stall_cycles():
    cc = CycleCounters()
    cc.tick()
    assert cc.stall_cycles == 0


def test_tick_n_increments_by_n():
    cc = CycleCounters()
    cc.tick(5)
    assert cc.cycles == 5
    assert cc.instruction_count == 5


def test_multiple_ticks_accumulate():
    cc = CycleCounters()
    cc.tick(3)
    cc.tick(2)
    assert cc.cycles == 5
    assert cc.instruction_count == 5


# ---------------------------------------------------------------------------
# stall
# ---------------------------------------------------------------------------

def test_stall_increments_cycles_and_stall_cycles():
    cc = CycleCounters()
    cc.stall(3)
    assert cc.cycles == 3
    assert cc.stall_cycles == 3


def test_stall_does_not_increment_instruction_count():
    cc = CycleCounters()
    cc.stall(3)
    assert cc.instruction_count == 0


def test_tick_and_stall_combined():
    cc = CycleCounters()
    cc.tick(4)
    cc.stall(2)
    assert cc.cycles == 6
    assert cc.instruction_count == 4
    assert cc.stall_cycles == 2


# ---------------------------------------------------------------------------
# ipc property
# ---------------------------------------------------------------------------

def test_ipc_zero_when_no_cycles():
    cc = CycleCounters()
    assert cc.ipc == 0.0


def test_ipc_perfect_throughput():
    cc = CycleCounters()
    cc.tick(10)
    assert cc.ipc == pytest.approx(1.0)


def test_ipc_with_stalls():
    cc = CycleCounters()
    cc.tick(4)    # 4 instructions, 4 cycles
    cc.stall(2)   # 2 stall cycles
    # total cycles = 6, instructions = 4  → ipc ≈ 0.667
    assert cc.ipc == pytest.approx(4 / 6)


def test_ipc_fractional():
    cc = CycleCounters()
    cc.tick(1)
    cc.stall(3)   # total cycles=4, instructions=1 → ipc=0.25
    assert cc.ipc == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

def test_reset_clears_all_counters():
    cc = CycleCounters()
    cc.tick(10)
    cc.stall(5)
    cc.reset()
    assert cc.cycles == 0
    assert cc.stall_cycles == 0
    assert cc.instruction_count == 0
    assert cc.ipc == 0.0
