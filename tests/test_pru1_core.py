# tests/test_pru1_core.py
"""PRU1 core instance: owns the second perif block (TRM: GPCFG1/0x26100)."""
import pytest

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


def test_every_core_uses_the_one_pru_clock(sim_config):
    s = Simulator(sim_config(pru_clock_mhz=225.0))
    assert s._pru_clock_mhz == 225.0
    assert {core.clock_mhz for core in s.cores.values()} == {225.0}
    for perif in s._perif.values():
        assert abs(perif._period_ns - 1000.0 / 225.0) < 1e-9


def test_pru1_clock_mhz_key_is_rejected(sim_config):
    path = sim_config(pru_clock_mhz=200.0, pru1_clock_mhz=200.1)
    with pytest.raises(ValueError, match="pru1_clock_mhz.*removed.*pru_clock_mhz"):
        Simulator(path)
