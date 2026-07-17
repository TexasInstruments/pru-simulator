# tests/test_sd_modulator.py
"""Tests for the 2nd-order sigma-delta modulator pattern generator."""
import pytest
from pru_io.sd_modulator import SDModulator


class TestSDModulatorDC:
    """DC input should produce a bitstream with density proportional to level."""

    def test_dc_zero_produces_roughly_half_ones(self):
        """DC level 0.0 maps to mid-scale: ~50% ones in the bitstream."""
        mod = SDModulator(signal="dc", dc_level=0.0)
        bits = [mod.next_bit() for _ in range(1000)]
        ones = sum(bits)
        assert 400 < ones < 600

    def test_dc_positive_produces_more_ones(self):
        """DC level +0.8 should produce ~90% ones."""
        mod = SDModulator(signal="dc", dc_level=0.8)
        bits = [mod.next_bit() for _ in range(2000)]
        ones = sum(bits)
        assert ones > 1500  # > 75%

    def test_dc_negative_produces_fewer_ones(self):
        """DC level -0.8 should produce ~10% ones."""
        mod = SDModulator(signal="dc", dc_level=-0.8)
        bits = [mod.next_bit() for _ in range(2000)]
        ones = sum(bits)
        assert ones < 500  # < 25%

    def test_output_is_binary(self):
        """Every bit must be 0 or 1."""
        mod = SDModulator(signal="dc", dc_level=0.5)
        bits = [mod.next_bit() for _ in range(100)]
        assert all(b in (0, 1) for b in bits)


class TestSDModulatorSine:
    """Sine input should produce periodic variation in bit density."""

    def test_sine_full_period_average_near_half(self):
        """Over a full period, average bit density should be ~0.5 (zero-mean sine)."""
        mod = SDModulator(signal="sine", amplitude=0.8, period=1024, phase_deg=0.0)
        bits = [mod.next_bit() for _ in range(1024)]
        ones = sum(bits)
        assert 400 < ones < 624  # roughly 50% +/- tolerance

    def test_sine_phase_offset(self):
        """Two modulators with 180-degree phase offset should be anti-correlated."""
        mod_a = SDModulator(signal="sine", amplitude=0.8, period=1024, phase_deg=0.0)
        mod_b = SDModulator(signal="sine", amplitude=0.8, period=1024, phase_deg=180.0)
        bits_a = [mod_a.next_bit() for _ in range(1024)]
        bits_b = [mod_b.next_bit() for _ in range(1024)]
        # First half of mod_a (positive sine) should have more ones than mod_b
        first_half_a = sum(bits_a[:512])
        first_half_b = sum(bits_b[:512])
        assert first_half_a > first_half_b

    def test_period_parameter(self):
        """Shorter period should complete cycle faster."""
        mod = SDModulator(signal="sine", amplitude=0.8, period=64, phase_deg=0.0)
        bits = [mod.next_bit() for _ in range(640)]  # 10 full cycles
        ones = sum(bits)
        assert 250 < ones < 390  # roughly 50%


class TestSDModulatorLiveConfig:
    """Parameters can be changed at runtime without reset."""

    def test_switch_dc_level(self):
        """Changing dc_level mid-stream affects subsequent output."""
        mod = SDModulator(signal="dc", dc_level=0.8)
        [mod.next_bit() for _ in range(100)]  # warm up
        mod.dc_level = -0.8
        bits = [mod.next_bit() for _ in range(1000)]
        ones = sum(bits)
        assert ones < 400  # should now produce mostly zeros

    def test_switch_signal_type(self):
        """Switching from dc to sine changes output pattern."""
        mod = SDModulator(signal="dc", dc_level=0.0)
        [mod.next_bit() for _ in range(100)]
        mod.signal = "sine"
        mod.amplitude = 0.8
        mod.period = 64
        bits = [mod.next_bit() for _ in range(640)]
        ones = sum(bits)
        assert 250 < ones < 390

    def test_reset_clears_state(self):
        """Reset zeroes integrators and sample index."""
        mod = SDModulator(signal="dc", dc_level=0.9)
        [mod.next_bit() for _ in range(500)]
        mod.reset()
        assert mod._integrator1 == 0.0
        assert mod._integrator2 == 0.0
        assert mod._sample_index == 0
