"""PRU ICSSG BSWAP broadside accelerator.

Hardware reference: AM64x/AM243x TRM SPRUIM2J §6.4.6.2.7, Table 6-95.
The accelerator is XIN-only.  Unlike a memory transfer, it transforms the
selected PRU register bytes in place, so the read data returned here is derived
from a snapshot of the register file before the core writes it back.
"""

from __future__ import annotations

from core.registers import RegisterFile
from xfr.accelerator import Accelerator


BSWAP_BYTE_ORDER = 0xA0
BSWAP_4_8 = 0xA1
BSWAP_4_16 = 0xA2


class BSwapAccelerator(Accelerator):
    """One of the three BSWAP XIN functions."""

    # The base class requires a declared device ID.  Instances override it so
    # one stateless implementation can serve all three Table 6-95 functions.
    DEVICE_ID = BSWAP_BYTE_ORDER

    def __init__(self, register_file: RegisterFile, device_id: int) -> None:
        self._regs = register_file
        self.DEVICE_ID = device_id

    def xout(self, start_reg: int, data: bytes, start_byte: int = 0) -> None:
        raise ValueError(f"BSWAP device 0x{self.DEVICE_ID:02X} is XIN-only")

    def xin(self, start_reg: int, length: int, start_byte: int = 0) -> bytes:
        if self.DEVICE_ID == BSWAP_BYTE_ORDER:
            return self._byte_order_swap(start_reg, start_byte, length)
        if self.DEVICE_ID == BSWAP_4_8:
            self._require_exact_range(start_reg, start_byte, length, 9)
            values = [self._regs.read_full(reg) for reg in range(2, 10)]
            return b"".join(value.to_bytes(4, "little") for value in values[4:] + values[:4])
        if self.DEVICE_ID == BSWAP_4_16:
            self._require_exact_range(start_reg, start_byte, length, 17)
            values = [self._regs.read_full(reg) for reg in range(2, 18)]
            order = list(range(12, 16)) + list(range(8, 12)) + list(range(4, 8)) + list(range(0, 4))
            return b"".join(values[index].to_bytes(4, "little") for index in order)
        raise ValueError(f"unsupported BSWAP device 0x{self.DEVICE_ID:02X}")

    def xchg(self, start_reg: int, data: bytes, start_byte: int = 0) -> bytes:
        raise ValueError(f"BSWAP device 0x{self.DEVICE_ID:02X} is XIN-only")

    def reset(self) -> None:
        """BSWAP has no persistent state."""

    def _byte_order_swap(self, start_reg: int, start_byte: int, length: int) -> bytes:
        if start_reg < 0 or start_reg > 29 or start_byte < 0 or start_byte > 3 or length < 1:
            raise ValueError("BSWAP byte-order XIN must select one or more bytes in R0..R29")
        end = start_reg * 4 + start_byte + length
        if end > 30 * 4:
            raise ValueError("BSWAP byte-order XIN may not extend past R29.b3")
        result = bytearray()
        for absolute_byte in range(start_reg * 4 + start_byte, end):
            reg = absolute_byte // 4
            destination_byte = absolute_byte % 4
            result.append((self._regs.read_full(reg) >> (8 * (3 - destination_byte))) & 0xFF)
        return bytes(result)

    @staticmethod
    def _require_exact_range(start_reg: int, start_byte: int, length: int, end_reg: int) -> None:
        expected_length = (end_reg - 2 + 1) * 4
        if start_reg != 2 or start_byte != 0 or length != expected_length:
            raise ValueError(
                f"BSWAP 4_{8 if end_reg == 9 else 16} requires XIN &R2.b0, {expected_length}"
            )
