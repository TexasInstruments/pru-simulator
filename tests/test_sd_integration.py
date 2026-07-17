# tests/test_sd_integration.py
"""Integration tests: PRU core <-> SD filter via IOPort."""
import pytest
from simulator import Simulator


class TestSDModeSwitch:
    """IOPort delegates to SD filter when sd_en is set via R30."""

    def test_io_port_has_sd_filter(self):
        """Each core's IOPort gets an SD filter attached."""
        sim = Simulator()
        assert sim.cores["pru0"].io_port.sd_filter is not None
        assert sim.cores["rtu0"].io_port.sd_filter is not None

    def test_r30_write_enables_sd(self):
        """Writing R30 with sd_en=1 (bit 25) enables SD mode."""
        sim = Simulator()
        # Write R30 with sd_en=1 via assembly
        source = "MOV r30, 0x02000000"  # bit 25 = sd_en
        sim.load("pru0", source)
        sim.step("pru0")
        assert sim.cores["pru0"].io_port.sd_filter.sd_en is True

    def test_r31_read_returns_sd_status_when_enabled(self):
        """When SD enabled, R31 read returns SD filter status not GPIO."""
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter
        sd.process_r30(1 << 25)  # enable SD
        sd.channels[0].shadow_acc3 = 0x12345
        sd.channels[0].valid = True
        r31 = sim.cores["pru0"].io_port.read_r31()
        assert r31 & 0x0FFFFFFF == 0x12345
        assert (r31 >> 28) & 1 == 1  # valid

    def test_r31_returns_gpi_when_sd_disabled(self):
        """When SD disabled, R31 returns GPIO state."""
        sim = Simulator()
        # Explicitly verify SD is disabled (default state)
        assert sim.cores["pru0"].io_port.sd_filter.sd_en is False
        sim.cores["pru0"].io_port.set_gpi_pin(5, True)
        r31 = sim.cores["pru0"].io_port.read_r31()
        assert (r31 >> 5) & 1 == 1

    def test_sd_tick_called_on_step(self):
        """SD filter advances when PRU steps (1:1 clock ratio)."""
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter
        sd.modulators[0].sd_clock_mhz = 333.0  # 1:1 with PRU (AM243x = 333 MHz)
        sd.process_r30(1 << 25)  # enable SD
        sd.channels[0].osr = 4
        source = "NOP\nNOP\nNOP\nNOP"
        sim.load("pru0", source)
        for _ in range(4):
            sim.step("pru0")
        assert sd.channels[0].valid is True
