# tests/test_sd_fast_detect.py
"""Tests for the Fast Detect sliding-window monitor."""
import pytest
from pru_io.sd_channel import SDChannel


class TestFastDetect:
    """Sliding window counts zeros/ones and compares against thresholds."""

    def test_fd_disabled_by_default(self):
        ch = SDChannel(osr=64)
        assert ch.fd_en is False
        for _ in range(100):
            ch.tick(1)
        assert ch.fd_one_max is False
        assert ch.fd_zero_max is False

    def test_fd_one_max_triggers(self):
        """When ones in window >= threshold, fd_one_max asserts."""
        ch = SDChannel(osr=256)
        ch.fd_en = True
        ch.fd_window_size = 0  # 4 samples
        ch.fd_one_max_limit = 2  # threshold = 3
        # Feed 4 ones → window is [1,1,1,1] → 4 ones >= 3 → triggers
        for _ in range(4):
            ch.tick(1)
        assert ch.fd_one_max is True

    def test_fd_zero_max_triggers(self):
        """When zeros in window >= threshold, fd_zero_max asserts."""
        ch = SDChannel(osr=256)
        ch.fd_en = True
        ch.fd_window_size = 0  # 4 samples
        ch.fd_zero_max_limit = 2  # threshold = 3
        for _ in range(4):
            ch.tick(0)
        assert ch.fd_zero_max is True

    def test_fd_flags_sticky(self):
        """FD flags remain set after triggering."""
        ch = SDChannel(osr=256)
        ch.fd_en = True
        ch.fd_window_size = 0
        ch.fd_one_max_limit = 0  # threshold = 1
        ch.tick(1)
        ch.tick(1)
        ch.tick(1)
        ch.tick(1)  # window full, ones=4 >= 1 → triggers
        assert ch.fd_one_max is True
        # Continue with zeros — flag should remain set
        for _ in range(8):
            ch.tick(0)
        assert ch.fd_one_max is True  # sticky

    def test_fd_sliding_window(self):
        """Window slides: old bits drop out."""
        ch = SDChannel(osr=256)
        ch.fd_en = True
        ch.fd_window_size = 0  # 4 samples
        ch.fd_one_max_limit = 2  # trigger at >= 3 ones
        # Feed 4 zeros — 0 ones, no trigger
        for _ in range(4):
            ch.tick(0)
        assert ch.fd_one_max is False
        # Feed 3 ones — window becomes [1,1,1,0] (newest first) → 3 ones >= 3 → triggers
        ch.tick(1)
        ch.tick(1)
        ch.tick(1)
        assert ch.fd_one_max is True

    def test_fd_window_size_8(self):
        """fd_window_size=1 means 8-sample window."""
        ch = SDChannel(osr=256)
        ch.fd_en = True
        ch.fd_window_size = 1  # 8 samples
        ch.fd_one_max_limit = 5  # threshold = 6
        # Feed 8 ones → 8 >= 6 → triggers
        for _ in range(8):
            ch.tick(1)
        assert ch.fd_one_max is True

    def test_fd_not_trigger_before_window_full(self):
        """No FD trigger until window_size samples accumulated."""
        ch = SDChannel(osr=256)
        ch.fd_en = True
        ch.fd_window_size = 0  # 4 samples
        ch.fd_one_max_limit = 0  # threshold = 1 (very sensitive)
        # Feed only 3 ones — window not full yet
        for _ in range(3):
            ch.tick(1)
        assert ch.fd_one_max is False
        # 4th sample fills window → now triggers
        ch.tick(1)
        assert ch.fd_one_max is True

    def test_fd_window_size_32(self):
        """fd_window_size=7 means 32-sample window."""
        ch = SDChannel(osr=256)
        ch.fd_en = True
        ch.fd_window_size = 7  # 32 samples = 4*(7+1)
        ch.fd_one_max_limit = 29  # threshold = 30
        # Feed 32 ones → 32 >= 30 → triggers
        for _ in range(32):
            ch.tick(1)
        assert ch.fd_one_max is True

    def test_fd_window_size_linear_progression(self):
        """Window sizes follow 4*(n+1) pattern: 0→4, 1→8, ..., 7→32."""
        for size in range(8):
            ch = SDChannel(osr=256)
            ch.fd_en = True
            ch.fd_window_size = size
            expected_window = 4 * (size + 1)
            ch.fd_one_max_limit = 0  # trigger at >= 1 one
            # Feed exactly (expected_window - 1) zeros - should not trigger yet
            for _ in range(expected_window - 1):
                ch.tick(0)
            assert ch.fd_one_max is False, f"size={size}: triggered before window full"
            # Feed 1 one - window now full but all zeros except 1
            ch.tick(1)
            assert ch.fd_one_max is True, f"size={size}: did not trigger with 1 one in {expected_window} window"

    def test_reinit_preserves_sticky_flags(self):
        """reinit() clears FD internal state but preserves sticky flags."""
        ch = SDChannel(osr=256)
        ch.fd_en = True
        ch.fd_window_size = 0
        ch.fd_one_max_limit = 0  # threshold = 1
        for _ in range(4):
            ch.tick(1)
        assert ch.fd_one_max is True
        ch.reinit()
        assert ch.fd_one_max is True  # sticky flag preserved
        # Internal counters reset — need full window again for next trigger
        # But flag is still True from before
