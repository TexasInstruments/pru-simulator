# tests/test_sd_channel.py
"""Tests for a single SD filter channel accumulator."""
import pytest
from pru_io.sd_channel import SDChannel


class TestAccumulation:
    """Accumulators run continuously, accumulating every SD tick."""

    def test_acc1_counts_ones(self):
        """acc1 is a running sum of input bits."""
        ch = SDChannel(osr=8)
        for _ in range(8):
            ch.tick(1)  # feed all ones
        assert ch.acc1 == 8

    def test_acc2_sums_acc1(self):
        """acc2 is running sum of acc1 values."""
        ch = SDChannel(osr=8)
        # Feed: 1,1,1,0,0,0,0,0
        for bit in [1, 1, 1, 0, 0, 0, 0, 0]:
            ch.tick(bit)
        # acc1 after each tick: 1,2,3,3,3,3,3,3
        # acc2 = sum of acc1 series: 1+2+3+3+3+3+3+3 = 21
        assert ch.acc1 == 3
        assert ch.acc2 == 21

    def test_acc3_sums_acc2(self):
        """acc3 is running sum of acc2 values."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        # acc1: 1,2,3,4 → acc1=4
        # acc2: 1,3,6,10 → acc2=10
        # acc3: 1,4,10,20 → acc3=20
        assert ch.acc1 == 4
        assert ch.acc2 == 10
        assert ch.acc3 == 20


class TestShadowLatch:
    """Shadow registers latch every OSR ticks; valid flag set."""

    def test_valid_set_after_osr_ticks(self):
        """valid flag asserts after exactly OSR ticks."""
        ch = SDChannel(osr=4)
        for _ in range(3):
            ch.tick(1)
            assert ch.valid is False
        ch.tick(1)
        assert ch.valid is True

    def test_shadow_captures_accumulator_values(self):
        """Shadow registers capture acc1/2/3 at OSR boundary."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        assert ch.shadow_acc1 == 4
        assert ch.shadow_acc2 == 10
        assert ch.shadow_acc3 == 20

    def test_accumulators_keep_running_after_latch(self):
        """Accumulators do NOT reset after shadow latch."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        # After latch, feed 4 more ones
        for _ in range(4):
            ch.tick(1)
        # acc1 keeps accumulating: was 4, now 4+4=8
        assert ch.acc1 == 8

    def test_valid_cleared_on_read(self):
        """Reading clears the valid flag."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        assert ch.valid is True
        ch.read_and_clear_valid()
        assert ch.valid is False

    def test_second_latch_updates_shadow(self):
        """Next OSR boundary updates shadow with new accumulator state."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        ch.read_and_clear_valid()
        for _ in range(4):
            ch.tick(0)
        # acc1 was 4, now +0+0+0+0 = still 4
        assert ch.shadow_acc1 == 4
        # acc2 was 10, now +4+4+4+4 = 26
        assert ch.shadow_acc2 == 26

    def test_read_does_not_modify_accumulators(self):
        """read_and_clear_valid() must not touch live accumulators."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        acc1_before = ch.acc1
        acc2_before = ch.acc2
        acc3_before = ch.acc3
        ch.read_and_clear_valid()
        assert ch.acc1 == acc1_before
        assert ch.acc2 == acc2_before
        assert ch.acc3 == acc3_before


class TestReinit:
    """reinit command resets accumulators to zero."""

    def test_reinit_zeros_accumulators(self):
        """reinit clears acc1, acc2, acc3, and sample counter."""
        ch = SDChannel(osr=8)
        for _ in range(5):
            ch.tick(1)
        ch.reinit()
        assert ch.acc1 == 0
        assert ch.acc2 == 0
        assert ch.acc3 == 0

    def test_reinit_does_not_clear_shadow(self):
        """reinit preserves the last latched shadow values."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        shadow_before = ch.shadow_acc3
        ch.reinit()
        assert ch.shadow_acc3 == shadow_before

    def test_valid_after_reinit_requires_full_osr(self):
        """After reinit, need full OSR ticks for next valid."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        ch.read_and_clear_valid()
        ch.reinit()
        for _ in range(3):
            ch.tick(1)
            assert ch.valid is False
        ch.tick(1)
        assert ch.valid is True

    def test_reinit_preserves_valid_flag(self):
        """reinit() does not clear the valid flag."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        assert ch.valid is True
        ch.reinit()
        assert ch.valid is True  # still set


class TestOverflow:
    """Overflow when accumulator exceeds 28-bit range."""

    def test_overflow_flag_on_28bit_exceed(self):
        """ovf flag set when acc3 exceeds 0x0FFFFFFF."""
        ch = SDChannel(osr=256)
        ch.acc3 = 0x0FFFFFFF
        ch.tick(1)  # acc1+=1, acc2+=acc1, acc3+=acc2 → acc3 overflows
        assert ch.ovf is True

    def test_overflow_sticky(self):
        """ovf remains set until explicitly cleared."""
        ch = SDChannel(osr=256)
        ch.acc3 = 0x0FFFFFFF
        ch.tick(1)
        ch.tick(0)  # another tick
        assert ch.ovf is True  # still set

    def test_clear_overflow(self):
        """clr_ovf resets the overflow flag."""
        ch = SDChannel(osr=256)
        ch.acc3 = 0x0FFFFFFF
        ch.tick(1)
        assert ch.ovf is True
        ch.clear_ovf()
        assert ch.ovf is False


class TestGetData:
    """get_data(acc_sel) returns correct shadow accumulator."""

    def test_acc_sel_0_returns_shadow_acc3(self):
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        # shadow_acc3 should be 20
        assert ch.get_data(0) == 20

    def test_acc_sel_1_returns_shadow_acc2(self):
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        # shadow_acc2 should be 10
        assert ch.get_data(1) == 10

    def test_acc_sel_2_returns_shadow_acc1(self):
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        # shadow_acc1 should be 4
        assert ch.get_data(2) == 4

    def test_get_data_masks_to_28_bits(self):
        ch = SDChannel(osr=4)
        ch.shadow_acc3 = 0x1FFFFFFF  # exceeds 28-bit max
        assert ch.get_data(0) == 0x0FFFFFFF  # masked to 28 bits


class TestCICIntegrators:
    """CIC integrators grow monotonically; only the output is 28-bit masked."""

    def test_accumulators_grow_unbounded(self):
        """Live accumulators must NOT be masked — CIC requires monotonic growth."""
        ch = SDChannel(osr=4)
        for _ in range(10_000):
            ch.tick(1)
        # With all-ones input, acc3 should be much larger than 28-bit max
        assert ch.acc3 > 0x0FFFFFFF

    def test_get_data_masks_output_to_28_bits(self):
        """get_data() provides 28-bit value to firmware regardless of internal size."""
        ch = SDChannel(osr=4)
        for _ in range(10_000):
            ch.tick(1)
        assert ch.get_data(0) <= 0x0FFFFFFF

    def test_sinc3_comb_produces_correct_output(self):
        """With unbounded integrators, the 3-stage comb gives correct SINC3 output.

        The comb filter is: y = x[k] - 3x[k-1] + 3x[k-2] - x[k-3], implemented
        as three chained first-differences on the 28-bit get_data() values.
        For DC input, the steady-state output = density * OSR^3.
        """
        ch = SDChannel(osr=64)
        # DC input: all ones (density = 1.0) → expected SINC3 output = 1.0 * 64^3 = 262144
        shadows = []
        for i in range(64 * 10):  # 10 OSR periods
            ch.tick(1)
            if ch.valid:
                shadows.append(ch.get_data(0))
                ch.valid = False

        # Apply 3-stage comb
        dn1, dn3, dn5 = 0, 0, 0
        results = []
        for s in shadows:
            cn3 = (s - dn1) & 0xFFFFFFFF
            dn1 = s
            cn4 = (cn3 - dn3) & 0xFFFFFFFF
            dn3 = cn3
            cn5 = (cn4 - dn5) & 0xFFFFFFFF
            dn5 = cn4
            results.append(cn5 & 0x0FFFFFFF)

        # After 3 settling samples, output should converge to OSR^3 = 262144
        steady = results[4:]  # samples 4+ should be settled
        for val in steady:
            assert abs(val - 262144) < 100, f"SINC3 output {val} far from expected 262144"
