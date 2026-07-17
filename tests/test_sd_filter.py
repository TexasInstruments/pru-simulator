# tests/test_sd_filter.py
"""Tests for the top-level SigmaDeltaFilter peripheral."""
import pytest
from pru_io.sd_filter import SigmaDeltaFilter


class TestR30Decode:
    """R30 write decodes control fields."""

    def test_ch_sel_from_r30(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0b0001 << 26 | 1 << 25)  # ch_sel=1, sd_en=1
        assert filt.ch_sel == 1

    def test_sd_en_from_r30(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(1 << 25)
        assert filt.sd_en is True
        filt.process_r30(0)
        assert filt.sd_en is False

    def test_snoop_from_r30(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(1 << 25 | 1 << 24)
        assert filt.snoop is True

    def test_data_sel_from_r30(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(1 << 25 | 1 << 23)
        assert filt.data_sel is True


class TestR31Status:
    """R31 read returns packed status for selected channel."""

    def test_r31_format_with_valid(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)  # select ch0, enable
        filt.channels[0].shadow_acc3 = 0x0ABC123
        filt.channels[0].valid = True
        filt.channels[0].ovf = False
        r31 = filt.get_r31_status()
        assert (r31 >> 28) & 1 == 1  # valid
        assert (r31 >> 29) & 1 == 0  # no ovf
        assert r31 & 0x0FFFFFFF == 0x0ABC123

    def test_r31_read_is_nondestructive(self):
        """Reading R31 must NOT clear valid (combinatorial output per Verilog)."""
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)
        filt.channels[0].valid = True
        filt.get_r31_status()
        assert filt.channels[0].valid is True  # valid stays set after read

    def test_r31_repeated_read_returns_same_value(self):
        """Reading R31 twice returns the same status (non-destructive)."""
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)
        filt.channels[0].valid = True
        filt.channels[0].shadow_acc3 = 0x1234
        r31_a = filt.get_r31_status()
        r31_b = filt.get_r31_status()
        assert r31_a == r31_b

    def test_r31_overflow_bit(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)
        filt.channels[0].ovf = True
        filt.channels[0].shadow_acc3 = 0
        r31 = filt.get_r31_status()
        assert (r31 >> 29) & 1 == 1

    def test_r31_bits_31_30_are_zero(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)
        filt.channels[0].valid = True
        filt.channels[0].ovf = True
        filt.channels[0].shadow_acc3 = 0x0FFFFFFF
        r31 = filt.get_r31_status()
        assert (r31 >> 30) == 0  # bits [31:30] must be 0


class TestR31Commands:
    """R31 write issues commands per icss_g_scu_sd.v:
    bit[23]=reinit, bit[24]=clr_ovf+val (clears BOTH ovf and valid)."""

    def test_clr_ovf_command_uses_bit24(self):
        """R31 write bit[24] clears overflow flag (per Verilog mx_pru_r3031_5[2])."""
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)
        filt.channels[0].ovf = True
        filt.process_r31_command(1 << 24)  # clr_ovf+val
        assert filt.channels[0].ovf is False

    def test_clr_ovf_also_clears_valid(self):
        """R31 write bit[24] clears valid in addition to ovf (per Verilog comment
        '//C clr_ovf and val')."""
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)
        filt.channels[0].ovf = True
        filt.channels[0].valid = True
        filt.process_r31_command(1 << 24)
        assert filt.channels[0].ovf is False
        assert filt.channels[0].valid is False

    def test_reinit_command_uses_bit23(self):
        """R31 write bit[23] reinitialises accumulators (per Verilog mx_pru_r3031_5[4])."""
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)
        filt.channels[0].acc1 = 100
        filt.channels[0].acc2 = 200
        filt.channels[0].acc3 = 300
        filt.process_r31_command(1 << 23)  # reinit
        assert filt.channels[0].acc1 == 0
        assert filt.channels[0].acc2 == 0
        assert filt.channels[0].acc3 == 0

    def test_old_bit28_29_no_longer_trigger_commands(self):
        """Bits 28/29 in R31 write are STATUS bits (read-only), not commands."""
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)
        filt.channels[0].ovf = True
        filt.channels[0].acc1 = 42
        filt.process_r31_command((1 << 29) | (1 << 28))  # old (wrong) bit positions
        assert filt.channels[0].ovf is True   # NOT cleared
        assert filt.channels[0].acc1 == 42    # NOT reinitialised


class TestTickAdvancement:
    """tick() advances SD channels via async clock model."""

    def test_tick_ratio_one_to_one(self):
        """sd_clock=200MHz, pru_clock=200MHz: 1 SD tick per PRU step."""
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.modulators[0].sd_clock_mhz = 200.0
        filt.process_r30(1 << 25)  # enable SD
        filt.channels[0].osr = 4
        for _ in range(4):
            filt.tick()
        assert filt.channels[0].valid is True

    def test_tick_ratio_div10(self):
        """sd_clock=20MHz, pru_clock=200MHz: ~1 SD tick per 10 PRU steps."""
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.modulators[0].sd_clock_mhz = 20.0
        filt.process_r30(1 << 25)
        filt.channels[0].osr = 4
        # At 10:1 ratio, 4 SD ticks require ~40 PRU steps (±2 due to float precision)
        # Count how many PRU steps until valid is set
        for step in range(1, 50):
            filt.tick()
            if filt.channels[0].valid:
                assert 38 <= step <= 42, f"Expected valid near step 40, got {step}"
                break
        else:
            pytest.fail("valid never set within 50 PRU steps")


class TestGetState:
    """get_state() returns correct state dict for UI."""

    def test_get_state_structure(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(1 << 25)
        state = filt.get_state()
        assert "sd_en" in state
        assert "ch_sel" in state
        assert "channels" in state
        assert len(state["channels"]) == 3
        assert "modulators" in state
        assert len(state["modulators"]) == 3

    def test_get_state_selected_channel(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(1 << 26 | 1 << 25)  # ch_sel=1
        state = filt.get_state()
        assert state["channels"][1]["selected"] is True
        assert state["channels"][0]["selected"] is False
