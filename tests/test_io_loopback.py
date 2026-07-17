"""Tests for IOPort GPIO loopback: set_loopback_group() and write_r30() with mask."""
import pytest
from pru_io.io_port import IOPort


class TestSetLoopbackGroup:
    def test_group0_sets_low_nibble(self):
        io = IOPort()
        io.set_loopback_group(0, True)
        assert io.loopback_mask == 0xF

    def test_group1_sets_second_nibble(self):
        io = IOPort()
        io.set_loopback_group(1, True)
        assert io.loopback_mask == 0xF0

    def test_group4_sets_bits_19_to_16(self):
        io = IOPort()
        io.set_loopback_group(4, True)
        assert io.loopback_mask == 0xF0000

    def test_multiple_groups_accumulate(self):
        io = IOPort()
        io.set_loopback_group(0, True)
        io.set_loopback_group(2, True)
        assert io.loopback_mask == 0xF0F

    def test_disable_clears_bits(self):
        io = IOPort()
        io.set_loopback_group(0, True)
        io.set_loopback_group(0, False)
        assert io.loopback_mask == 0x0

    def test_disable_does_not_affect_other_groups(self):
        io = IOPort()
        io.set_loopback_group(0, True)
        io.set_loopback_group(1, True)
        io.set_loopback_group(0, False)
        assert io.loopback_mask == 0xF0

    def test_invalid_group_raises(self):
        io = IOPort()
        with pytest.raises(ValueError):
            io.set_loopback_group(5, True)


class TestLoopbackApplied:
    def test_write_r30_propagates_gpo_to_gpi_for_looped_group(self):
        io = IOPort()
        io.set_loopback_group(0, True)   # bits 3:0
        io.write_r30(0x5)
        assert io.gpi & 0xF == 0x5

    def test_write_r30_does_not_clobber_nonlooped_gpi_bits(self):
        io = IOPort()
        io.set_gpi_pin(7, True)          # set bit 7 in GPI manually
        io.set_loopback_group(0, True)   # only loopback bits 3:0
        io.write_r30(0x3)
        assert io.gpi & 0x80 == 0x80    # bit 7 unchanged
        assert io.gpi & 0x0F == 0x3     # bits 3:0 looped

    def test_no_loopback_mask_gpi_unchanged(self):
        io = IOPort()
        initial_gpi = io.gpi
        io.write_r30(0xFF)
        assert io.gpi == initial_gpi    # no groups enabled → GPI untouched

    def test_loopback_not_applied_when_sd_en_set(self):
        """Loopback must not fire when R30 bit 25 (sd_en) is set."""
        io = IOPort()
        io.set_loopback_group(0, True)
        io.write_r30((1 << 25) | 0x7)   # sd_en=1, GPO low bits=7
        assert io.gpi == 0              # sd_en → no loopback

    def test_loopback_partial_mask(self):
        """Only the nibble with loopback enabled echoes GPO."""
        io = IOPort()
        io.set_loopback_group(1, True)  # bits 7:4
        io.write_r30(0xAB)
        assert io.gpi & 0xF0 == 0xA0   # bits 7:4 = 0xA (from 0xAB)
        assert io.gpi & 0x0F == 0x00   # bits 3:0 not looped


# ---------------------------------------------------------------------------
# Simulator.set_loopback integration
# ---------------------------------------------------------------------------

from simulator import Simulator


class TestSimulatorSetLoopback:
    def _make_sim(self):
        sim = Simulator(config_path="nonexistent.cfg")
        return sim

    def test_set_loopback_updates_pru0_mask(self):
        sim = self._make_sim()
        sim.set_loopback("pru0", 0, True)
        assert sim.cores["pru0"].io_port.loopback_mask == 0xF

    def test_set_loopback_updates_rtu0_mask(self):
        sim = self._make_sim()
        sim.set_loopback("rtu0", 1, True)
        assert sim.cores["rtu0"].io_port.loopback_mask == 0xF0

    def test_set_loopback_disable_clears_mask(self):
        sim = self._make_sim()
        sim.set_loopback("pru0", 0, True)
        sim.set_loopback("pru0", 0, False)
        assert sim.cores["pru0"].io_port.loopback_mask == 0x0

    def test_set_loopback_cores_independent(self):
        sim = self._make_sim()
        sim.set_loopback("pru0", 2, True)
        assert sim.cores["rtu0"].io_port.loopback_mask == 0x0


# ---------------------------------------------------------------------------
# Firmware: source/mvi_gpio_loopback.asm assembly + loopback e2e
# ---------------------------------------------------------------------------

import pathlib


class TestFirmwareAssembly:
    def test_mvi_gpio_loopback_assembles(self):
        """source/mvi_gpio_loopback.asm assembles without errors."""
        src_path = pathlib.Path(__file__).parent.parent / "source" / "mvi_gpio_loopback.asm"
        source = src_path.read_text(encoding="utf-8")
        sim = Simulator(config_path="nonexistent.cfg")
        errors = sim.load("pru0", source)
        assert errors == [], f"Assembly errors: {errors}"

    def test_mvi_gpio_loopback_runs_16_iterations(self):
        """After 16+setup steps, RX buffer mirrors TX pattern when loopback enabled."""
        src_path = pathlib.Path(__file__).parent.parent / "source" / "mvi_gpio_loopback.asm"
        source = src_path.read_text(encoding="utf-8")
        sim = Simulator(config_path="nonexistent.cfg")
        errors = sim.load("pru0", source)
        assert errors == [], f"Assembly errors: {errors}"

        # Enable loopback for all bits 7:0 (groups 0 and 1)
        sim.set_loopback("pru0", 0, True)
        sim.set_loopback("pru0", 1, True)

        # Run enough steps for init (9) + 16 loop iterations (3 steps each = 48) + margin
        sim.step("pru0", 80)

        regs = sim.registers("pru0")
        # TX pattern: R2=0x08040201, R3=0x80402010, R4=0x08040201, R5=0x80402010
        # RX buffer:  R10–R13 should mirror R2–R5 (bits 7:0 looped)
        # Only bits 7:0 are looped, so only b0 and b1 echo; b2/b3 of each reg = 0
        assert regs[10] & 0xFFFF == 0x0201, f"R10={regs[10]:#010x}"
        assert regs[11] & 0xFFFF == 0x2010, f"R11={regs[11]:#010x}"
        assert regs[12] & 0xFFFF == 0x0201, f"R12={regs[12]:#010x}"
        assert regs[13] & 0xFFFF == 0x2010, f"R13={regs[13]:#010x}"
