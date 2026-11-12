"""The UI scratchpad state lists only the banks the target can reach.

IPC_SP (XFR device 15) is not a broadside id on the ICSS-M, so firmware on
AM261x / AM263x cannot touch it and the UI must not offer it.
"""
import os

import pytest

from simulator import Simulator
from ui.server import _read_spad

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sim(name):
    return Simulator(os.path.join(REPO_ROOT, "config", f"memory_{name}.cfg"))


@pytest.mark.parametrize("name", ["am261x", "am263x"])
def test_icssm_spad_state_has_no_ipc_bank(name):
    spad = _read_spad(_sim(name))
    assert "ipc" not in spad
    assert set(spad) == {"bank0", "bank1", "bank2"}


def test_icssg_spad_state_keeps_ipc_bank():
    spad = _read_spad(_sim("am243x"))
    assert set(spad) == {"bank0", "bank1", "bank2", "ipc"}
    assert len(spad["ipc"]) == 8
