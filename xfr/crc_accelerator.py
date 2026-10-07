"""PRU ICSSG CRC16/32 broadside accelerator (device_id=1).

Hardware reference: AM64x/AM243x TRM (SPRUIM2H) Section 6.4.6.2.2.1
"PRU and CRC16/32 Interface", Table 6-429 "CRC Register to PRU Port
Mapping". Same fixed broadside window as the MAC accelerator (R25-R29;
see xfr/mac_accelerator.py) -- the window is shared hardware, selected
per-device by the XIN/XOUT device_id, not a per-accelerator range.

Register mapping:
  R25  CRC_CFG              W(4, byte0 used): bit0=CRC32_ENABLE,
                             bit2=CRC16_MOD_ENABLE. Auto-(re)initializes
                             the seed to the mode default (0x0000_0000 for
                             CRC16, 0xFFFF_FFFF for CRC32) and loads
                             crc_reg from it.
  R26  (unused/reserved -- not part of the documented mapping)
  R27  CRC_DATA_8_BFLIP      R: same byte order as CRC_DATA, each byte's
                             8 bits individually mirrored. No auto reset.
  R28  CRC_SEED              W(4): overrides the seed value and reloads
                             crc_reg from it.
       CRC_DATA_32_BFLIP     R: full 32-bit mirror of CRC_DATA (bit0<->bit31,
                             etc). No auto reset.
  R29  CRC_DATA              W(1/2/4): pushes data through the engine; a
                             session must use one fixed width throughout.
                             R(1/2/4): current CRC value (crc_out), LSB
                             first -- and, per the TRM, this READ resets
                             crc_reg back to the CRC_SEED state.

The RTL bit-reverses each data word and feeds it into an MSB-first
(non-reflected) shift register using the normal polynomials, so data is
consumed LSB-first per byte but crc_reg -- and the CRC_DATA read -- is NOT
reflected. The conventional reflected value (e.g. zlib CRC-32 before its
final complement) is what CRC_DATA_32_BFLIP returns. Verified on AM64x
silicon: DE AD BE EF CA FE BA BE reads 0xC9D7 (CRC-16, half-word writes)
and 0x383AD48D (CRC-32, word writes).
Because data is LSB-first per byte, 1/2/4-byte writes are all equivalent to
one per-byte update loop.
"""

import struct

from core.registers import RegisterFile
from xfr.accelerator import Accelerator

# Normal (MSB-first) polynomials
_POLY_CRC16_STD = 0x8005       # x^16+x^15+x^2+1
_POLY_CRC16_MOD = 0x1021       # x^16+x^12+x^5+1
_POLY_CRC32     = 0x04C11DB7   # IEEE 802.3


def _reverse_bits(value: int, width: int) -> int:
    result = 0
    for i in range(width):
        if value & (1 << i):
            result |= 1 << (width - 1 - i)
    return result


class CRCAccelerator(Accelerator):
    """CRC-16 (standard/mod) and CRC-32 accelerator for PRU ICSSG."""

    DEVICE_ID = 1

    def __init__(self, register_file: RegisterFile) -> None:
        self._regs = register_file
        self.crc32_mode: bool = False
        self.mod_en: bool = False
        self.seed: int = 0
        self.crc_reg: int = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _width_mask(self) -> int:
        return 0xFFFFFFFF if self.crc32_mode else 0xFFFF

    def _mode_default_seed(self) -> int:
        return 0xFFFFFFFF if self.crc32_mode else 0x0000

    def _poly(self) -> int:
        if self.crc32_mode:
            return _POLY_CRC32
        return _POLY_CRC16_MOD if self.mod_en else _POLY_CRC16_STD

    def _update_byte(self, byte: int) -> None:
        poly = self._poly()
        mask = self._width_mask()
        top = 31 if self.crc32_mode else 15
        crc = self.crc_reg
        for i in range(8):
            feedback = ((crc >> top) ^ (byte >> i)) & 1
            crc = (crc << 1) & mask
            if feedback:
                crc ^= poly
        self.crc_reg = crc

    def _data_8_bflip(self) -> int:
        """Same byte order as crc_reg, each byte's bits individually mirrored."""
        result = 0
        for byte_index in range(4):
            byte = (self.crc_reg >> (byte_index * 8)) & 0xFF
            result |= _reverse_bits(byte, 8) << (byte_index * 8)
        return result

    def _data_32_bflip(self) -> int:
        """Full 32-bit mirror of crc_reg (bit0<->bit31, etc)."""
        return _reverse_bits(self.crc_reg, 32)

    # ------------------------------------------------------------------
    # Accelerator interface
    # ------------------------------------------------------------------

    def xout(self, start_reg: int, data: bytes, start_byte: int = 0) -> None:
        if start_reg == 25 and len(data) >= 1:
            ctrl = data[0]
            self.crc32_mode = bool(ctrl & 0x01)
            self.mod_en = bool(ctrl & 0x04)
            self.seed = self._mode_default_seed()
            self.crc_reg = self.seed

        elif start_reg == 28 and len(data) >= 4:
            self.seed = struct.unpack_from("<I", data, 0)[0] & self._width_mask()
            self.crc_reg = self.seed

        elif start_reg == 29 and len(data) in (1, 2, 4):
            for byte in data:
                self._update_byte(byte)

    def xin(self, start_reg: int, length: int, start_byte: int = 0) -> bytes:
        if start_reg == 27:
            full4 = struct.pack("<I", self._data_8_bflip())
            return full4[:length]

        if start_reg == 28:
            full4 = struct.pack("<I", self._data_32_bflip())
            return full4[:length]

        if start_reg == 29:
            full4 = struct.pack("<I", self.crc_reg)
            result = full4[:length]
            self.crc_reg = self.seed  # destructive read, per the TRM
            return result

        return bytes(length)

    def xchg(self, start_reg: int, data: bytes, start_byte: int = 0) -> bytes:
        # XCHG has no documented semantic for the CRC hardware
        return bytes(len(data))

    def reset(self) -> None:
        self.crc32_mode = False
        self.mod_en = False
        self.seed = 0
        self.crc_reg = 0
