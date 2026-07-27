# tests/conftest.py
"""Shared fixtures.

`Simulator()` (and `PRUSimulatorMCP()`) default to the repo's `memory.cfg`,
which the dashboard's core-speed selector rewrites in place — so a test that
constructs one with no arguments inherits whatever clock the user last picked
in the UI.  That is fine for most tests, but cycle-timed ones (the bit-bang
UART receiver, the SD end-to-end runs) are written against a specific core
clock and must pin it instead of inheriting it.

`sim_config` builds a throw-away `memory.cfg` in `tmp_path` from the repo's,
with `[device]` values overridden, and returns its path.  The `config/`
directory is copied alongside it because `Simulator._load_constants()` looks
for `constants_am243x.cfg` relative to the config file — without it the
constant table is empty and firmware using `c24` (DRAM0) stores nowhere.
"""

import configparser
import os
import shutil

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Core clock the cycle-timed firmware tests are written against.
NOMINAL_CLOCK_MHZ = 200.0


@pytest.fixture
def sim_config(tmp_path):
    """Factory: `sim_config(pru_clock_mhz=200)` -> path to a temp memory.cfg."""

    def _make(**device_overrides) -> str:
        cfg = configparser.ConfigParser()
        cfg.read(os.path.join(_REPO_ROOT, "memory.cfg"))
        if "device" not in cfg:
            cfg["device"] = {}
        for key, value in device_overrides.items():
            cfg["device"][key] = str(value)
        path = tmp_path / "memory.cfg"
        with open(path, "w") as f:
            cfg.write(f)
        src_config_dir = os.path.join(_REPO_ROOT, "config")
        if os.path.isdir(src_config_dir):
            shutil.copytree(src_config_dir, tmp_path / "config", dirs_exist_ok=True)
        return str(path)

    return _make


@pytest.fixture
def nominal_config(sim_config):
    """Path to a memory.cfg pinned to the nominal 200 MHz core clock."""
    return sim_config(pru_clock_mhz=NOMINAL_CLOCK_MHZ,
                      pru1_clock_mhz=NOMINAL_CLOCK_MHZ)
