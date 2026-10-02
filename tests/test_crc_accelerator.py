"""Unit tests for CRCAccelerator (device_id=1, CRC-16/CRC-32 broadside accelerator).

Register map per AM64x/AM243x TRM Table 6-429: R25=CRC_CFG(W),
R27=CRC_DATA_8_BFLIP(R), R28=CRC_SEED(W)/CRC_DATA_32_BFLIP(R),
R29=CRC_DATA(RW, read resets crc_reg to the seed state).
"""

import struct
import zlib

import pytest

from core.registers import RegisterFile
from xfr.crc_accelerator import CRCAccelerator, _POLY_CRC16_MOD, _POLY_CRC16_STD, _POLY_CRC32


def _reflected_crc(data: bytes, poly: int, width: int, init: int) -> int:
    """Reference reflected bit-serial CRC, same shape as pif_eth's crc32_bitwise."""
    mask = (1 << width) - 1
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ poly
            else:
                crc >>= 1
        crc &= mask
    return crc


def _reverse_bits(value: int, width: int) -> int:
    result = 0
    for i in range(width):
        if value & (1 << i):
            result |= 1 << (width - 1 - i)
    return result


FRAME = bytes.fromhex("DEADBEEFCAFEBABE")


@pytest.fixture
def crc():
    """Fresh CRCAccelerator wired to a fresh RegisterFile."""
    regs = RegisterFile()
    return CRCAccelerator(regs), regs


# ---------------------------------------------------------------------------
# CRC_CFG (xout R25)
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_mode_is_crc16_standard(self, crc):
        c, _ = crc
        assert c.crc32_mode is False
        assert c.mod_en is False

    def test_cfg_sets_crc32_mode(self, crc):
        c, _ = crc
        c.xout(25, bytes([0x01]))
        assert c.crc32_mode is True

    def test_cfg_sets_mod_en(self, crc):
        c, _ = crc
        c.xout(25, bytes([0x04]))
        assert c.crc32_mode is False
        assert c.mod_en is True

    def test_cfg_auto_seeds_crc16_to_zero(self, crc):
        c, _ = crc
        c.seed = 0x1234
        c.crc_reg = 0x1234
        c.xout(25, bytes([0x00]))
        assert c.seed == 0x0000
        assert c.crc_reg == 0x0000

    def test_cfg_auto_seeds_crc32_to_all_ones(self, crc):
        c, _ = crc
        c.xout(25, bytes([0x01]))
        assert c.seed == 0xFFFFFFFF
        assert c.crc_reg == 0xFFFFFFFF


# ---------------------------------------------------------------------------
# CRC_SEED (xout R28)
# ---------------------------------------------------------------------------

class TestSeed:
    def test_seed_write_overrides_seed_and_crc_reg(self, crc):
        c, _ = crc
        c.xout(28, struct.pack("<I", 0xBEEF))
        assert c.seed == 0xBEEF
        assert c.crc_reg == 0xBEEF

    def test_seed_masked_to_16_bits_in_crc16_mode(self, crc):
        c, _ = crc
        c.xout(28, struct.pack("<I", 0xDEADBEEF))
        assert c.crc_reg == 0xBEEF

    def test_seed_full_32_bits_in_crc32_mode(self, crc):
        c, _ = crc
        c.xout(25, bytes([0x01]))       # select CRC-32
        c.xout(28, struct.pack("<I", 0xDEADBEEF))
        assert c.crc_reg == 0xDEADBEEF

    def test_seed_short_data_ignored(self, crc):
        c, _ = crc
        c.crc_reg = 0x1111
        c.seed = 0x1111
        c.xout(28, bytes(2))            # fewer than 4 bytes -> no seed update
        assert c.crc_reg == 0x1111
        assert c.seed == 0x1111


# ---------------------------------------------------------------------------
# CRC_DATA writes (xout R29) and correctness vs. reference implementation
# ---------------------------------------------------------------------------

class TestDataWriteCorrectness:
    def test_crc16_standard_byte_at_a_time(self, crc):
        c, _ = crc
        for b in FRAME:
            c.xout(29, bytes([b]))
        expected = _reflected_crc(FRAME, _POLY_CRC16_STD, 16, 0x0000)
        assert c.crc_reg == expected

    def test_crc16_mod_byte_at_a_time(self, crc):
        c, _ = crc
        c.xout(25, bytes([0x04]))       # select mod polynomial
        for b in FRAME:
            c.xout(29, bytes([b]))
        expected = _reflected_crc(FRAME, _POLY_CRC16_MOD, 16, 0x0000)
        assert c.crc_reg == expected

    def test_crc32_word_at_a_time(self, crc):
        c, _ = crc
        c.xout(25, bytes([0x01]))       # select CRC-32
        for i in range(0, len(FRAME), 4):
            c.xout(29, FRAME[i:i + 4])
        expected = _reflected_crc(FRAME, _POLY_CRC32, 32, 0xFFFFFFFF)
        assert c.crc_reg == expected

    def test_crc32_matches_zlib_crc32_after_final_complement(self, crc):
        """Hardware does not auto-apply the final XOR; raw crc_reg == zlib.crc32 ^ 0xFFFFFFFF."""
        c, _ = crc
        c.xout(25, bytes([0x01]))
        for b in FRAME:
            c.xout(29, bytes([b]))
        assert (c.crc_reg ^ 0xFFFFFFFF) == zlib.crc32(FRAME)

    def test_illegal_length_write_is_ignored(self, crc):
        c, _ = crc
        c.xout(29, bytes([0x01]))
        before = c.crc_reg
        c.xout(29, bytes(3))            # not 1/2/4 bytes -> no documented error bit; ignored
        assert c.crc_reg == before


# ---------------------------------------------------------------------------
# Width invariance: 1x4-byte, 2x2-byte, 4x1-byte writes must converge
# ---------------------------------------------------------------------------

class TestWidthInvariance:
    def test_crc16_standard_byte_vs_halfword_vs_word(self, crc):
        results = []
        for step in (1, 2, 4):
            c = CRCAccelerator(RegisterFile())
            for i in range(0, len(FRAME), step):
                c.xout(29, FRAME[i:i + step])
            results.append(c.crc_reg)
        assert results[0] == results[1] == results[2]

    def test_crc32_byte_vs_word(self, crc):
        results = []
        for step in (1, 4):
            c = CRCAccelerator(RegisterFile())
            c.xout(25, bytes([0x01]))
            for i in range(0, len(FRAME), step):
                c.xout(29, FRAME[i:i + step])
            results.append(c.crc_reg)
        assert results[0] == results[1]


# ---------------------------------------------------------------------------
# Destructive read of CRC_DATA (xin R29) vs. non-resetting bit-flip mirrors
# ---------------------------------------------------------------------------

class TestDestructiveRead:
    def test_reading_crc_data_resets_to_default_seed(self, crc):
        c, _ = crc
        for b in FRAME:
            c.xout(29, bytes([b]))
        assert c.crc_reg != 0
        c.xin(29, 2)
        assert c.crc_reg == 0x0000

    def test_reading_crc_data_resets_to_custom_seed(self, crc):
        c, _ = crc
        c.xout(28, struct.pack("<I", 0xABCD))
        for b in FRAME:
            c.xout(29, bytes([b]))
        c.xin(29, 2)
        assert c.crc_reg == 0xABCD

    def test_reading_crc_data_returns_value_before_reset(self, crc):
        c, _ = crc
        for b in FRAME:
            c.xout(29, bytes([b]))
        expected = c.crc_reg
        result = c.xin(29, 2)
        assert struct.unpack("<H", result)[0] == expected

    def test_reading_bflip_mirrors_does_not_reset(self, crc):
        c, _ = crc
        for b in FRAME:
            c.xout(29, bytes([b]))
        before = c.crc_reg
        c.xin(27, 4)
        c.xin(28, 4)
        assert c.crc_reg == before


# ---------------------------------------------------------------------------
# Bit-flip mirror registers: CRC_DATA_8_BFLIP (R27) / CRC_DATA_32_BFLIP (R28)
# ---------------------------------------------------------------------------

class TestBitFlipMirrors:
    def test_8bit_bflip_reverses_each_byte_independently(self, crc):
        c, _ = crc
        c.crc_reg = 0x12345678
        result = struct.unpack("<I", c.xin(27, 4))[0]
        expected = (
            _reverse_bits(0x12, 8) << 24 |
            _reverse_bits(0x34, 8) << 16 |
            _reverse_bits(0x56, 8) << 8 |
            _reverse_bits(0x78, 8)
        )
        assert result == expected

    def test_32bit_bflip_reverses_the_full_word(self, crc):
        c, _ = crc
        c.crc_reg = 0x12345678
        result = struct.unpack("<I", c.xin(28, 4))[0]
        assert result == _reverse_bits(0x12345678, 32)

    def test_crc16_32bflip_puts_reversed_value_in_upper_half(self, crc):
        """For CRC16, only CRC_DATA_32_BFLIP[31:16] are valid per the TRM."""
        c, _ = crc
        c.crc_reg = 0xEB93                  # 16-bit CRC value, upper 16 bits are 0
        result = struct.unpack("<I", c.xin(28, 4))[0]
        assert result & 0xFFFF == 0          # lower half stays 0
        assert (result >> 16) == _reverse_bits(0xEB93, 16)


# ---------------------------------------------------------------------------
# xchg, unknown registers, and reset()
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_xchg_returns_zeros(self, crc):
        c, _ = crc
        assert c.xchg(29, bytes([0x01])) == bytes(1)

    def test_xin_unknown_start_reg_returns_zeros(self, crc):
        c, _ = crc
        assert c.xin(10, 4) == bytes(4)
        assert c.xin(26, 4) == bytes(4)     # R26 is unused/reserved

    def test_reset_clears_all_state(self, crc):
        c, _ = crc
        c.xout(25, bytes([0x01]))       # CRC-32
        c.xout(28, struct.pack("<I", 0xDEAD))
        c.xout(29, bytes([0x01, 0x02, 0x03, 0x04]))
        c.reset()
        assert c.crc32_mode is False
        assert c.mod_en is False
        assert c.seed == 0
        assert c.crc_reg == 0
