"""PRU ICSSG MPY/MAC broadside accelerator (device_id=0).

Hardware reference: AM64x/AM243x TRM §6.4.6.2.1

Register mapping:
  R25  MAC_CTRL_STATUS  bit0=MAC_MODE  bit1=ACC_CARRY (write-1-to-clear)
  R26  Lower 32 bits of 64-bit result (XIN read / XOUT seed low word)
  R27  Upper 32 bits of 64-bit result (XIN read / XOUT seed high word)
  R28  Operand A — auto-sampled directly from PRU register file
  R29  Operand B — auto-sampled directly from PRU register file
"""

import struct

from core.registers import RegisterFile
from xfr.accelerator import Accelerator

_MASK64 = 0xFFFF_FFFF_FFFF_FFFF


class MACAccelerator(Accelerator):
    """Multiply-only and multiply-accumulate accelerator for PRU ICSSG."""

    DEVICE_ID = 0

    def __init__(self, register_file: RegisterFile) -> None:
        self._regs = register_file
        self.mac_mode: bool = False
        self._accumulator: int = 0
        self.acc_carry: bool = False

    # ------------------------------------------------------------------
    # Accelerator interface
    # ------------------------------------------------------------------

    def xout(self, start_reg: int, data: bytes, start_byte: int = 0) -> None:
        if start_reg == 25 and len(data) >= 1:
            ctrl = data[0]
            if ctrl & 0x01:
                # MAC_MODE=1: trigger one accumulation of R28*R29
                a = self._regs.read_full(28)
                b = self._regs.read_full(29)
                product = a * b
                self._accumulator += product
                if self._accumulator > _MASK64:
                    self._accumulator &= _MASK64
                    self.acc_carry = True
                self.mac_mode = True
            else:
                # MAC_MODE=0: clear accumulator and enter multiply-only
                self._accumulator = 0
                self.mac_mode = False
            if ctrl & 0x02:
                self.acc_carry = False

        elif start_reg == 26 and len(data) >= 8:
            # Seed the accumulator from R26:R27 bytes; clear carry
            low  = struct.unpack_from("<I", data, 0)[0]
            high = struct.unpack_from("<I", data, 4)[0]
            self._accumulator = (high << 32) | low
            self.acc_carry = False

    def xin(self, start_reg: int, length: int, start_byte: int = 0) -> bytes:
        if start_reg == 25:
            ctrl = (0x01 if self.mac_mode else 0) | (0x02 if self.acc_carry else 0)
            return (bytes([ctrl]) + bytes(max(0, length - 1)))[:length]

        if start_reg in (26, 27):
            if self.mac_mode:
                result64 = self._accumulator
            else:
                a = self._regs.read_full(28)
                b = self._regs.read_full(29)
                result64 = a * b

            low  = result64 & 0xFFFF_FFFF
            high = (result64 >> 32) & 0xFFFF_FFFF
            full8 = struct.pack("<II", low, high)

            if start_reg == 26:
                return full8[:length]
            else:  # start_reg == 27
                return (full8[4:] + bytes(max(0, length - 4)))[:length]

        return bytes(length)

    def xchg(self, start_reg: int, data: bytes, start_byte: int = 0) -> bytes:
        # XCHG has no meaningful semantic for MAC hardware
        return bytes(len(data))

    def reset(self) -> None:
        self.mac_mode = False
        self._accumulator = 0
        self.acc_carry = False
