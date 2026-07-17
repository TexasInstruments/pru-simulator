"""Unit tests for MACAccelerator (device_id=0, MPY/MAC broadside accelerator)."""

import struct
import pytest

from core.registers import RegisterFile
from xfr.mac_accelerator import MACAccelerator


@pytest.fixture
def mac():
    """Fresh MACAccelerator wired to a fresh RegisterFile (R28=R29=0)."""
    regs = RegisterFile()
    return MACAccelerator(regs), regs


# ---------------------------------------------------------------------------
# Multiply-only mode (default, mac_mode=False)
# ---------------------------------------------------------------------------

class TestMultiplyOnly:
    def test_basic_product(self, mac):
        m, regs = mac
        regs.write_full(28, 50)
        regs.write_full(29, 25)
        result = m.xin(26, 8)
        low  = struct.unpack_from("<I", result, 0)[0]
        high = struct.unpack_from("<I", result, 4)[0]
        assert low == 1250
        assert high == 0

    def test_large_product_64bit_split(self, mac):
        m, regs = mac
        regs.write_full(28, 0xFFFFFFFF)
        regs.write_full(29, 0xFFFFFFFF)
        product = 0xFFFFFFFF * 0xFFFFFFFF  # = 0xFFFFFFFE_00000001
        result = m.xin(26, 8)
        low  = struct.unpack_from("<I", result, 0)[0]
        high = struct.unpack_from("<I", result, 4)[0]
        assert low  == product & 0xFFFFFFFF          # 0x00000001
        assert high == (product >> 32) & 0xFFFFFFFF  # 0xFFFFFFFE

    def test_xin_r27_returns_high_word(self, mac):
        m, regs = mac
        regs.write_full(28, 0xFFFFFFFF)
        regs.write_full(29, 0xFFFFFFFF)
        product = 0xFFFFFFFF * 0xFFFFFFFF
        result = m.xin(27, 4)
        high = struct.unpack_from("<I", result, 0)[0]
        assert high == (product >> 32) & 0xFFFFFFFF

    def test_xin_r26_length_4(self, mac):
        """XIN &R26, 4 returns only the low 4 bytes."""
        m, regs = mac
        regs.write_full(28, 7)
        regs.write_full(29, 6)
        result = m.xin(26, 4)
        assert len(result) == 4
        low = struct.unpack_from("<I", result, 0)[0]
        assert low == 42

    def test_mode_is_multiply_only_by_default(self, mac):
        m, _ = mac
        assert m.mac_mode is False

    def test_zero_operands(self, mac):
        m, regs = mac
        # R28=R29=0 by default
        result = m.xin(26, 8)
        assert result == bytes(8)


# ---------------------------------------------------------------------------
# xout R25 — mode control
# ---------------------------------------------------------------------------

class TestXoutControl:
    def test_xout_bit0_sets_mac_mode(self, mac):
        m, _ = mac
        m.xout(25, bytes([0x01]))
        assert m.mac_mode is True

    def test_xout_bit0_clear_disables_mac_and_clears_accumulator(self, mac):
        m, regs = mac
        regs.write_full(28, 10)
        regs.write_full(29, 10)
        m.xout(25, bytes([0x01]))          # enable + accumulate 100
        assert m._accumulator == 100
        m.xout(25, bytes([0x00]))          # disable → clears accumulator
        assert m._accumulator == 0
        assert m.mac_mode is False

    def test_xout_bit1_clears_acc_carry(self, mac):
        """Writing bit 1=1 to R25 clears ACC_CARRY."""
        m, regs = mac
        # Seed near max and overflow
        near_max = 0xFFFFFFFF_FFFFFFFE
        m.xout(26, (near_max & 0xFFFFFFFF).to_bytes(4, 'little') +
                   ((near_max >> 32) & 0xFFFFFFFF).to_bytes(4, 'little'))
        regs.write_full(28, 1)
        regs.write_full(29, 2)
        m.xout(25, bytes([0x01]))          # overflow → acc_carry=True
        assert m.acc_carry is True
        m.xout(25, bytes([0x03]))          # bit1=1 → clear carry; bit0=1 → stay MAC
        assert m.acc_carry is False
        assert m.mac_mode is True          # mac_mode preserved


# ---------------------------------------------------------------------------
# Multiply-and-accumulate mode
# ---------------------------------------------------------------------------

class TestMACMode:
    def test_each_xout_r25_triggers_accumulation(self, mac):
        m, regs = mac
        m.xout(25, bytes([0x01]))           # enable; acc += 0*0 = 0
        regs.write_full(28, 3)
        regs.write_full(29, 4)
        m.xout(25, bytes([0x01]))           # acc += 12
        regs.write_full(28, 5)
        regs.write_full(29, 6)
        m.xout(25, bytes([0x01]))           # acc += 30; total=42
        result = m.xin(26, 8)
        low  = struct.unpack_from("<I", result, 0)[0]
        high = struct.unpack_from("<I", result, 4)[0]
        assert low == 42
        assert high == 0

    def test_dot_product_1_2_3_dot_4_5_6(self, mac):
        """(1,2,3)·(4,5,6) = 4+10+18 = 32."""
        m, regs = mac
        m.xout(25, bytes([0x01]))           # enable; acc += 0
        for a, b in [(1, 4), (2, 5), (3, 6)]:
            regs.write_full(28, a)
            regs.write_full(29, b)
            m.xout(25, bytes([0x01]))
        result = m.xin(26, 8)
        low = struct.unpack_from("<I", result, 0)[0]
        assert low == 32

    def test_overflow_sets_acc_carry(self, mac):
        m, regs = mac
        near_max = 0xFFFFFFFF_FFFFFFFE
        m.xout(26, (near_max & 0xFFFFFFFF).to_bytes(4, 'little') +
                   ((near_max >> 32) & 0xFFFFFFFF).to_bytes(4, 'little'))
        regs.write_full(28, 1)
        regs.write_full(29, 2)              # product=2; near_max+2 > 64-bit max
        m.xout(25, bytes([0x01]))
        assert m.acc_carry is True

    def test_accumulator_wraps_on_overflow(self, mac):
        m, regs = mac
        near_max = 0xFFFFFFFF_FFFFFFFF
        m.xout(26, (near_max & 0xFFFFFFFF).to_bytes(4, 'little') +
                   ((near_max >> 32) & 0xFFFFFFFF).to_bytes(4, 'little'))
        regs.write_full(28, 1)
        regs.write_full(29, 1)              # product=1; max+1 wraps to 0
        m.xout(25, bytes([0x01]))
        assert m._accumulator == 0


# ---------------------------------------------------------------------------
# Seed accumulator via XOUT R26:R27
# ---------------------------------------------------------------------------

class TestSeedAccumulator:
    def test_seed_low_and_high(self, mac):
        m, regs = mac
        low_seed  = (0xDEADBEEF).to_bytes(4, 'little')
        high_seed = (0x00000001).to_bytes(4, 'little')
        m.xout(26, low_seed + high_seed)
        assert m._accumulator == 0x1_DEADBEEF

    def test_seed_clears_acc_carry(self, mac):
        m, regs = mac
        near_max = 0xFFFFFFFF_FFFFFFFE
        m.xout(26, (near_max & 0xFFFFFFFF).to_bytes(4, 'little') +
                   ((near_max >> 32) & 0xFFFFFFFF).to_bytes(4, 'little'))
        regs.write_full(28, 1)
        regs.write_full(29, 2)
        m.xout(25, bytes([0x01]))           # trigger overflow → carry=True
        assert m.acc_carry is True
        m.xout(26, bytes(8))               # seed with 0 → carry cleared
        assert m.acc_carry is False


# ---------------------------------------------------------------------------
# XIN R25 — status readback
# ---------------------------------------------------------------------------

class TestXinStatus:
    def test_initial_status_is_zero(self, mac):
        m, _ = mac
        status = m.xin(25, 1)
        assert status[0] == 0x00

    def test_status_reflects_mac_mode(self, mac):
        m, _ = mac
        m.xout(25, bytes([0x01]))
        status = m.xin(25, 1)
        assert status[0] & 0x01            # bit0 set

    def test_status_reflects_acc_carry(self, mac):
        m, regs = mac
        near_max = 0xFFFFFFFF_FFFFFFFE
        m.xout(26, (near_max & 0xFFFFFFFF).to_bytes(4, 'little') +
                   ((near_max >> 32) & 0xFFFFFFFF).to_bytes(4, 'little'))
        regs.write_full(28, 1)
        regs.write_full(29, 2)
        m.xout(25, bytes([0x01]))
        status = m.xin(25, 1)
        assert status[0] & 0x02            # bit1 set

    def test_xin_status_length_respected(self, mac):
        m, _ = mac
        result = m.xin(25, 4)
        assert len(result) == 4
        assert result[1:] == bytes(3)      # padding zeros


# ---------------------------------------------------------------------------
# Unknown start_reg and xchg
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_xin_unknown_start_reg_returns_zeros(self, mac):
        m, _ = mac
        assert m.xin(10, 4) == bytes(4)

    def test_xchg_returns_zeros(self, mac):
        m, regs = mac
        regs.write_full(28, 5)
        regs.write_full(29, 5)
        result = m.xchg(26, bytes(8))
        assert result == bytes(8)

    def test_xout_short_data_ignored_for_seed(self, mac):
        """XOUT R26 with fewer than 8 bytes does not trigger seeding."""
        m, _ = mac
        m._accumulator = 99
        m.xout(26, bytes(4))               # only 4 bytes — no seed
        assert m._accumulator == 99

    def test_xin_r27_length_8_returns_8_bytes(self, mac):
        """xin(27, 8) must return exactly 8 bytes (contract compliance)."""
        m, regs = mac
        regs.write_full(28, 0xFFFFFFFF)
        regs.write_full(29, 0xFFFFFFFF)
        result = m.xin(27, 8)
        assert len(result) == 8

    def test_xin_r25_length_0_returns_empty(self, mac):
        """xin(25, 0) must return exactly 0 bytes (contract compliance)."""
        m, _ = mac
        result = m.xin(25, 0)
        assert result == bytes(0)


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_clears_all_state(self, mac):
        m, regs = mac
        regs.write_full(28, 10)
        regs.write_full(29, 10)
        m.xout(25, bytes([0x01]))          # accumulate 100
        m.reset()
        assert m.mac_mode is False
        assert m._accumulator == 0
        assert m.acc_carry is False
