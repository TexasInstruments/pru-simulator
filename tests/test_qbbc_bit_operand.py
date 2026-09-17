"""QBBS/QBBC bit operand must be a literal 0-31, and never crash the simulator.

Regression for: an unresolved symbol in the bit slot became
Label(resolved_addr=-1), passed load() with NO reported error, and then raised a
bare `ValueError: negative shift count` out of core/branch.py, killing the run
with a Python traceback instead of a diagnosable error.

Found by sweeping TI's NoCodePRU block library through this simulator: 3 of 42
generated block macros hit it (event_block, iep_block, trigger_router_block).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.branch import BranchUnit  # noqa: E402
from simulator import Simulator  # noqa: E402


def _sim():
    return Simulator(config_path="nonexistent.cfg")


def test_literal_out_of_range_bit_is_rejected():
    """This already worked; pinned so the new Label check cannot regress it."""
    errs = _sim().load("pru0", "ldi r1, 0\nqbbc done, r1, 99\ndone:\nhalt")
    assert errs and "out of range for bit position" in errs[0]


def test_unresolved_symbol_as_bit_position_is_a_load_error():
    errs = _sim().load("pru0", "ldi r1, 0\nqbbc done, r1, someUndefined\ndone:\nhalt")
    assert errs, "an undefined symbol in the bit slot was accepted as a clean parse"
    assert "someUndefined" in errs[0]
    assert "bit position" in errs[0]


def test_defined_label_as_bit_position_is_still_an_error():
    """A resolvable label is no better - the slot is a bit index, not a target."""
    errs = _sim().load("pru0", "top:\nldi r1, 0\nqbbc done, r1, top\ndone:\nhalt")
    assert errs and "must be a literal" in errs[0]


def test_valid_bit_position_still_assembles_and_runs():
    sim = _sim()
    assert sim.load("pru0", "ldi r1, 0\nqbbc done, r1, 3\nldi r2, 1\ndone:\nhalt") == []
    sim.cores["pru0"].run(max_steps=20)
    assert sim.cores["pru0"].halted


@pytest.mark.parametrize("bad", [-1, 32, 99, None, "x"])
def test_branch_unit_never_raises_a_bare_shift_error(bad):
    """Defence in depth: the execution unit must not be reachable with a bad bit."""
    bu = BranchUnit()
    with pytest.raises(ValueError) as e:
        bu.qbbc(0, bad)
    assert "0-31" in str(e.value)
    with pytest.raises(ValueError):
        bu.qbbs(0, bad)


def test_branch_unit_accepts_the_whole_valid_range():
    bu = BranchUnit()
    for b in range(32):
        assert bu.qbbc(0, b) is True
        assert bu.qbbs(0xFFFFFFFF, b) is True
