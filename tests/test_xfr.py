"""Tests for the XFR bus subsystem: Scratchpad and XFRBus."""

import logging
import pytest

from xfr.scratchpad import Scratchpad
from xfr.xfr_bus import XFRBus, SPAD_BANK0, SPAD_BANK1, SPAD_BANK2, IPC_SPAD


# ===========================================================================
# Scratchpad
# ===========================================================================

class TestScratchpad:
    def test_initial_data_is_zeros(self):
        sp = Scratchpad()
        assert sp.read(0, 32) == bytes(32)

    def test_write_and_read_back(self):
        sp = Scratchpad()
        sp.write(0, b"\xDE\xAD\xBE\xEF")
        assert sp.read(0, 4) == b"\xDE\xAD\xBE\xEF"

    def test_partial_write_preserves_other_bytes(self):
        sp = Scratchpad()
        sp.write(0, b"\xFF" * 32)
        sp.write(8, b"\x00\x00\x00\x00")
        assert sp.read(0, 4) == b"\xFF\xFF\xFF\xFF"
        assert sp.read(8, 4) == b"\x00\x00\x00\x00"
        assert sp.read(12, 4) == b"\xFF\xFF\xFF\xFF"

    def test_exchange_returns_old_data(self):
        sp = Scratchpad()
        sp.write(0, b"\xAA\xBB\xCC\xDD")
        old = sp.exchange(0, b"\x11\x22\x33\x44")
        assert old == b"\xAA\xBB\xCC\xDD"

    def test_exchange_stores_new_data(self):
        sp = Scratchpad()
        sp.write(0, b"\xAA\xBB\xCC\xDD")
        sp.exchange(0, b"\x11\x22\x33\x44")
        assert sp.read(0, 4) == b"\x11\x22\x33\x44"

    def test_exchange_partial_range(self):
        sp = Scratchpad()
        sp.write(4, b"\xFE\xFE")
        old = sp.exchange(4, b"\x01\x02")
        assert old == b"\xFE\xFE"
        assert sp.read(4, 2) == b"\x01\x02"

    def test_out_of_bounds_read_raises(self):
        sp = Scratchpad()
        with pytest.raises(ValueError):
            sp.read(31, 2)   # 31+2 > 32

    def test_out_of_bounds_write_raises(self):
        sp = Scratchpad()
        with pytest.raises(ValueError):
            sp.write(30, b"\x00\x00\x00")  # 30+3 > 32


# ===========================================================================
# XFRBus
# ===========================================================================

class TestXFRBus:
    def test_xout_then_xin_spad_bank0(self):
        bus = XFRBus()
        bus.xout(SPAD_BANK0, 0, b"\x01\x02\x03\x04")
        data = bus.xin(SPAD_BANK0, 0, 4)
        assert data == b"\x01\x02\x03\x04"

    def test_xout_then_xin_ipc_spad(self):
        bus = XFRBus()
        bus.xout(IPC_SPAD, 16, b"\xAB\xCD")
        data = bus.xin(IPC_SPAD, 16, 2)
        assert data == b"\xAB\xCD"

    def test_spad_bank0_and_ipc_spad_are_independent(self):
        """Writing to BANK0 must not affect IPC_SPAD and vice versa."""
        bus = XFRBus()
        bus.xout(SPAD_BANK0, 0, b"\xFF\xFF\xFF\xFF")
        bus.xout(IPC_SPAD, 0, b"\x00\x00\x00\x00")
        assert bus.xin(SPAD_BANK0, 0, 4) == b"\xFF\xFF\xFF\xFF"
        assert bus.xin(IPC_SPAD, 0, 4) == b"\x00\x00\x00\x00"

    def test_xchg_returns_old_data(self):
        bus = XFRBus()
        bus.xout(SPAD_BANK1, 0, b"\xDE\xAD\xBE\xEF")
        old = bus.xchg(SPAD_BANK1, 0, b"\x11\x11\x11\x11")
        assert old == b"\xDE\xAD\xBE\xEF"

    def test_xchg_stores_new_data(self):
        bus = XFRBus()
        bus.xout(SPAD_BANK2, 8, b"\xAA\xBB")
        bus.xchg(SPAD_BANK2, 8, b"\x55\x66")
        assert bus.xin(SPAD_BANK2, 8, 2) == b"\x55\x66"

    def test_all_banks_are_independent(self):
        bus = XFRBus()
        for device_id, marker in [
            (SPAD_BANK0, b"\x10"),
            (SPAD_BANK1, b"\x11"),
            (SPAD_BANK2, b"\x12"),
            (IPC_SPAD,   b"\x15"),
        ]:
            bus.xout(device_id, 0, marker)
        assert bus.xin(SPAD_BANK0, 0, 1) == b"\x10"
        assert bus.xin(SPAD_BANK1, 0, 1) == b"\x11"
        assert bus.xin(SPAD_BANK2, 0, 1) == b"\x12"
        assert bus.xin(IPC_SPAD,   0, 1) == b"\x15"

    def test_unknown_device_xin_returns_zeros(self):
        bus = XFRBus()
        data = bus.xin(99, 0, 8)
        assert data == bytes(8)

    def test_unknown_device_xout_logs_warning(self, caplog):
        bus = XFRBus()
        with caplog.at_level(logging.WARNING, logger="xfr.xfr_bus"):
            bus.xout(99, 0, b"\xFF")
        assert any("99" in msg for msg in caplog.messages)

    def test_unknown_device_warns_once_per_device_id(self, caplog):
        """A poll loop over an unmodelled widget must not warn every iteration."""
        bus = XFRBus()
        with caplog.at_level(logging.WARNING, logger="xfr.xfr_bus"):
            for _ in range(5):
                bus.xin(99, 0, 4)
            bus.xout(99, 0, b"\xFF")
            bus.xin(98, 0, 4)
        messages = [r.getMessage() for r in caplog.records if r.name == "xfr.xfr_bus"]
        assert len([m for m in messages if "99" in m]) == 1
        assert len([m for m in messages if "98" in m]) == 1

    def test_unknown_device_xchg_logs_warning(self, caplog):
        bus = XFRBus()
        with caplog.at_level(logging.WARNING, logger="xfr.xfr_bus"):
            result = bus.xchg(99, 0, b"\xAB")
        assert result == b"\x00"
        assert any("99" in msg for msg in caplog.messages)
