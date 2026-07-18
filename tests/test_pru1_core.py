# tests/test_pru1_core.py
"""PRU1 core instance: own clock, owns the second perif block (TRM: GPCFG1/0x26100)."""
import configparser
import os

import simulator
from simulator import Simulator


def test_pru1_core_exists_with_perif():
    s = Simulator()
    assert "pru1" in s.cores
    assert s.cores["pru1"].io_port.perif is not None
    assert s._perif["pru1"].registers._base == 0x26100
    # rtu0 lost the perif block (it never had one on real hardware)
    assert s.cores["rtu0"].io_port.perif is None


def test_loopback_targets_pru1():
    s = Simulator()
    assert s._loopback.target is s._perif["pru1"]
    assert s._loopback.source is s._perif["pru0"]


def test_gpcfg_mapping():
    s = Simulator()
    s.gpcfg_write("pru1", 1)
    assert s._perif["pru1"].enabled is True
    assert s.gpcfg_state("pru1") == {"mux_sel": 1}
    # rtu0 has no GPCFG mux: write is a no-op, state reads 0
    s.gpcfg_write("rtu0", 1)
    assert s.gpcfg_state("rtu0") == {"mux_sel": 0}
    assert s.gpcfg_state("pru1") == {"mux_sel": 1}   # unaffected


def test_pru1_clock_defaults_to_pru_clock():
    s = Simulator()
    assert s._pru1_clock_mhz == s._pru_clock_mhz


def test_pru1_clock_from_config(tmp_path):
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(os.path.dirname(simulator.__file__), "memory.cfg"))
    cfg["device"]["pru1_clock_mhz"] = "200.1"
    path = tmp_path / "memory.cfg"
    with open(path, "w") as f:
        cfg.write(f)
    s = Simulator(str(path))
    assert s._pru1_clock_mhz == 200.1
    # PRU1's perif ns-timeline runs off its own clock
    assert abs(s._perif["pru1"]._period_ns - 1000.0 / 200.1) < 1e-9
    assert abs(s._perif["pru0"]._period_ns - 1000.0 / 200.0) < 1e-9
