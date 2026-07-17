"""Tests for SD configuration register memory-mapped interface."""
import pytest
from pru_io.sd_registers import SDRegisters


class TestSDCfgReg0:
    """Global SD_CFG_REG0 at offset 0x26044."""

    def test_initial_value_zero(self):
        regs = SDRegisters()
        assert regs.read(0x26044, 4) == b'\x00\x00\x00\x00'

    def test_write_share_en(self):
        """Write SHARE_EN bit [8]."""
        regs = SDRegisters()
        regs.write(0x26044, (1 << 8).to_bytes(4, 'little'))
        val = int.from_bytes(regs.read(0x26044, 4), 'little')
        assert (val >> 8) & 1 == 1

    def test_read_share_en_field(self):
        regs = SDRegisters()
        regs.write(0x26044, (1 << 8).to_bytes(4, 'little'))
        assert regs.get_share_en() is True


class TestSDClkSelReg:
    """Per-channel SD_CLK_SEL_REGn at offset 0x26048 + n*8."""

    def test_channel0_offset(self):
        regs = SDRegisters()
        # Write ACC_SEL=1 (bits [5:4]) for channel 0
        val = 1 << 4
        regs.write(0x26048, val.to_bytes(4, 'little'))
        assert regs.get_acc_sel(0) == 1

    def test_channel1_offset(self):
        regs = SDRegisters()
        # Channel 1 at offset 0x26050
        val = 2 << 4  # ACC_SEL=2 (sinc1)
        regs.write(0x26050, val.to_bytes(4, 'little'))
        assert regs.get_acc_sel(1) == 2

    def test_channel2_offset(self):
        regs = SDRegisters()
        # Channel 2 at offset 0x26058 — write ACC_SEL=1 (sinc2)
        val = 1 << 4
        regs.write(0x26058, val.to_bytes(4, 'little'))
        assert regs.get_acc_sel(2) == 1

    def test_clk_sel_field(self):
        regs = SDRegisters()
        val = 2  # CLK_SEL=2 (shared)
        regs.write(0x26048, val.to_bytes(4, 'little'))
        assert regs.get_clk_sel(0) == 2

    def test_clk_inv_field(self):
        regs = SDRegisters()
        val = 1 << 2  # CLK_INV=1
        regs.write(0x26048, val.to_bytes(4, 'little'))
        assert regs.get_clk_inv(0) is True


class TestSDSampleSizeReg:
    """Per-channel SD_SAMPLE_SIZE_REGn at offset 0x2604C + n*8."""

    def test_sample_size_field(self):
        regs = SDRegisters()
        # Write SAMPLE_SIZE=63 (OSR=64)
        regs.write(0x2604C, (63).to_bytes(4, 'little'))
        assert regs.get_sample_size(0) == 63
        assert regs.get_osr(0) == 64  # OSR = sample_size + 1

    def test_channel1_sample_size(self):
        regs = SDRegisters()
        # Channel 1 at offset 0x26054
        regs.write(0x26054, (127).to_bytes(4, 'little'))
        assert regs.get_osr(1) == 128

    def test_fd_en_field(self):
        regs = SDRegisters()
        val = 1 << 23  # FD_EN bit
        regs.write(0x2604C, val.to_bytes(4, 'little'))
        assert regs.get_fd_en(0) is True

    def test_fd_window_size_field(self):
        regs = SDRegisters()
        val = 3 << 8  # FD_WINDOW_SIZE=3 → 16 samples
        regs.write(0x2604C, val.to_bytes(4, 'little'))
        assert regs.get_fd_window_size(0) == 3


class TestWriteCallbacks:
    """Write callbacks notify the SD filter of config changes."""

    def test_callback_on_osr_change(self):
        received = []
        regs = SDRegisters()
        regs.on_config_change = lambda ch, field, val: received.append((ch, field, val))
        regs.write(0x2604C, (63).to_bytes(4, 'little'))
        assert any(ch == 0 and field == "osr" for ch, field, val in received)

    def test_callback_on_acc_sel_change(self):
        received = []
        regs = SDRegisters()
        regs.on_config_change = lambda ch, field, val: received.append((ch, field, val))
        regs.write(0x26048, (1 << 4).to_bytes(4, 'little'))
        assert any(ch == 0 and field == "acc_sel" for ch, field, val in received)

    def test_callback_on_share_en_change(self):
        received = []
        regs = SDRegisters()
        regs.on_config_change = lambda ch, field, val: received.append((ch, field, val))
        regs.write(0x26044, (1 << 8).to_bytes(4, 'little'))
        assert (-1, "share_en", 1) in received


class TestChannelConfig:
    def test_get_channel_config_keys(self):
        regs = SDRegisters()
        config = regs.get_channel_config(0)
        assert set(config.keys()) == {"osr", "acc_sel", "clk_sel", "clk_inv", "fd_en", "fd_window_size"}

    def test_get_channel_config_values(self):
        regs = SDRegisters()
        regs.write(0x2604C, (63).to_bytes(4, 'little'))  # OSR=64
        regs.write(0x26048, (1 << 4).to_bytes(4, 'little'))  # ACC_SEL=1
        config = regs.get_channel_config(0)
        assert config["osr"] == 64
        assert config["acc_sel"] == 1
