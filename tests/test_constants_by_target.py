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
    """Simulator on a throw-away root config with [device] target = `target`."""
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(_REPO_ROOT, "memory.cfg"))
    cfg["device"]["target"] = target
    path = tmp_path / "memory.cfg"
    with open(path, "w") as f:
        cfg.write(f)
    shutil.copytree(os.path.join(_REPO_ROOT, "config"), tmp_path / "config")
    return Simulator(str(path))


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
    # AM243x c1 is a 0x48xx_xxxx SoC address; AM261x c1 is the local IEP.
    assert am243x[1] != am261x[1]
    assert am243x[21] != am261x[21]


def test_am243x_target_keeps_current_table(tmp_path):
    t = _table(_sim(tmp_path, "AM243x"))
    assert t[0] == 0x00020000
    assert t[1] == 0x48040000
    assert t[24] == 0x00000000
    assert t[28] == 0x00010000


def test_root_memory_cfg_keeps_current_table():
    t = _table(Simulator(os.path.join(_REPO_ROOT, "memory.cfg")))
    assert t[1] == 0x48040000
    assert t[28] == 0x00010000


def test_unknown_target_falls_back_to_am243x(tmp_path):
    t = _table(_sim(tmp_path, "AM999x"))
    assert t[1] == 0x48040000
    assert t[28] == 0x00010000
