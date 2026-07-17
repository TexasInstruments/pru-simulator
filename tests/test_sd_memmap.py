# tests/test_sd_memmap.py
"""Tests for SD register access through the memory bus."""
import pytest
from simulator import Simulator


class TestSDRegisterMemoryMap:
    """SD config registers accessible via memory read/write."""

    def test_write_osr_via_memory(self):
        """Writing SAMPLE_SIZE register (0x2604C) updates channel 0 OSR."""
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter
        # Write OSR=64-1=63 to SD_SAMPLE_SIZE_REG0
        sim.memory.write(0x2604C, (63).to_bytes(4, 'little'))
        assert sd.channels[0].osr == 64

    def test_write_acc_sel_via_memory(self):
        """Writing CLK_SEL register (0x26048) stores acc_sel."""
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter
        # Write ACC_SEL=1 (sinc2) to SD_CLK_SEL_REG0
        sim.memory.write(0x26048, (1 << 4).to_bytes(4, 'little'))
        assert sd.registers.get_acc_sel(0) == 1

    def test_read_register_via_memory(self):
        """Reading SD register address returns current value."""
        sim = Simulator()
        sim.memory.write(0x2604C, (127).to_bytes(4, 'little'))
        data = sim.memory_read(0x2604C, 4)
        val = int.from_bytes(data, 'little')
        assert val & 0xFF == 127

    def test_write_channel1_osr(self):
        """Writing SAMPLE_SIZE_REG1 (0x26054) updates channel 1 OSR."""
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter
        sim.memory.write(0x26054, (127).to_bytes(4, 'little'))  # OSR=128
        assert sd.channels[1].osr == 128

    def test_write_osr_updates_both_cores(self):
        """Writing OSR register updates channels on both PRU0 and RTU0."""
        sim = Simulator()
        sim.memory.write(0x2604C, (63).to_bytes(4, 'little'))  # OSR=64
        assert sim.cores["pru0"].io_port.sd_filter.channels[0].osr == 64
        assert sim.cores["rtu0"].io_port.sd_filter.channels[0].osr == 64
