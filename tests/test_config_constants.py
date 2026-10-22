# tests/test_config_constants.py
"""A device config selected by its path must load the constant table.

`Simulator._load_constants()` looked for `<config dir>/config/constants_am243x.cfg`.
That works for the root `memory.cfg`, but for a preset under `config/` it
resolved to `config/config/...`, found nothing, and left C0-C31 at zero, so
LBCO/SBCO through a nonzero base went to the wrong address.
"""

import os

import pytest

from simulator import Simulator

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("cfg", [
    "memory.cfg",
    os.path.join("config", "memory_am243x.cfg"),
    os.path.join("config", "memory_am263x.cfg"),
])
def test_constant_table_loaded_for_config_path(cfg):
    sim = Simulator(os.path.join(_REPO_ROOT, cfg))
    # c0 (INTC) and c28 (Shared RAM) are nonzero in constants_am243x.cfg
    assert sim.constant_table.resolve(0) == 0x00020000
    assert sim.constant_table.resolve(28) == 0x00010000


def test_am261x_preset_loads_its_own_constant_table():
    # config/memory_am261x.cfg selects constants_am261x.cfg (SPRUJB6B Table 7-40),
    # not the AM243x table.
    sim = Simulator(os.path.join(_REPO_ROOT, "config", "memory_am261x.cfg"))
    assert sim.constant_table.resolve(0) == 0x00020000
    assert sim.constant_table.resolve(1) == 0x0002F000


def test_config_without_constants_file_still_loads(tmp_path):
    # A config with no constants file next to it keeps the old behaviour: an
    # empty table, no error.
    cfg = tmp_path / "memory.cfg"
    cfg.write_text("[device]\ntarget = AM243x\n\n[DRAM0]\nbase = 0x0\nsize = 0x2000\n")
    sim = Simulator(str(cfg))
    assert sim.constant_table.resolve(0) == 0
