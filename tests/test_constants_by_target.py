"""The C0-C31 constant table follows the [device] target.

AM261x/AM263x have a different PRU-ICSSM constant table (AM261x TRM SPRUJB6B
Table 7-40) from the AM243x ICSSG one, so a preset must not run with the
AM243x values.  A target with no table of its own keeps the AM243x one.
"""

import configparser
import os
import shutil

from simulator import Simulator

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sim(tmp_path, target):
    """Simulator constant table on a throw-away root config with [device] target = `target`."""
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(_REPO_ROOT, "memory.cfg"))
    cfg["device"]["target"] = target
    path = tmp_path / "memory.cfg"
    with open(path, "w") as f:
        cfg.write(f)
    shutil.copytree(os.path.join(_REPO_ROOT, "config"), tmp_path / "config")
    # Simulator() rejects targets it cannot run (e.g. AM261x), so exercise the
    # constant-table lookup on its own.
    sim = Simulator.__new__(Simulator)
    sim.constant_table = sim._load_constants(str(path))
    return sim


def _table(sim):
    return dict(enumerate(sim.constant_table.entries))


def test_am261x_target_loads_trm_table(tmp_path):
    t = _table(_sim(tmp_path, "AM261x"))
    # SPRUJB6B Table 7-40, p.370-371
    assert t[0] == 0x00020000   # PRU-ICSS INTC
    assert t[1] == 0x0002F000   # PRU-ICSS IEP
    assert t[3] == 0x00030000   # ECAP0
    assert t[4] == 0x00026000   # CFG
    assert t[7] == 0x00028000   # UART0
    assert t[8] == 0x0002E100   # IEP0_0x100
    assert t[21] == 0x00032400  # MDIO


def test_am261x_differs_from_am243x(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    am243x = _table(_sim(tmp_path / "a", "AM243x"))
    am261x = _table(_sim(tmp_path / "b", "AM261x"))
    # c12-c14 hold the same pointers on both (reserved on ICSS-M, whose window
    # reads zero); c10 is per-core and not in the AM261x sidecar; c26 (IEP) is
    # 0x0002E000 on both at reset.
    assert (am243x[12], am261x[12]) == (0x00027000, 0x00027000)
    assert (am243x[14], am261x[14]) == (0x00024800, 0x00024800)
    assert (am243x[10], am261x[10]) == (0x48318000, 0)
    assert (am243x[26], am261x[26]) == (0x0002E000, 0x0002E000)


def test_am243x_target_keeps_current_table(tmp_path):
    t = _table(_sim(tmp_path, "AM243x"))
    assert t[0] == 0x00020000
    assert t[1] == 0x0002F000
    assert t[24] == 0x00000000
    assert t[28] == 0   # CTPPR0 = 0 at reset


def test_root_memory_cfg_keeps_current_table():
    t = _table(Simulator(os.path.join(_REPO_ROOT, "memory.cfg")))
    assert t[1] == 0x0002F000
    assert t[28] == 0


def test_unknown_target_falls_back_to_am243x(tmp_path):
    t = _table(_sim(tmp_path, "AM999x"))
    assert t[1] == 0x0002F000
    assert t[28] == 0
