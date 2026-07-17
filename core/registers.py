"""RegisterFile — 32 × 32-bit general-purpose registers with sub-register access.

Sub-register layout (PRU convention):
  Rn      offset=0,  width=32
  Rn.b0   offset=0,  width=8
  Rn.b1   offset=8,  width=8
  Rn.b2   offset=16, width=8
  Rn.b3   offset=24, width=8
  Rn.w0   offset=0,  width=16
  Rn.w1   offset=8,  width=16
  Rn.w2   offset=16, width=16
"""


class RegisterFile:
    """32 general-purpose 32-bit registers with carry flag."""

    def __init__(self) -> None:
        self.regs: list[int] = [0] * 32
        self.carry: bool = False

    # ------------------------------------------------------------------
    # Core read / write with arbitrary offset and width
    # ------------------------------------------------------------------

    def read(self, reg: int, offset: int, width: int) -> int:
        """Extract *width* bits starting at *offset* from register *reg*."""
        mask = (1 << width) - 1
        return (self.regs[reg] >> offset) & mask

    def write(self, reg: int, offset: int, width: int, value: int) -> None:
        """Insert *value* (masked to *width* bits) at *offset* in register *reg*."""
        mask = (1 << width) - 1
        value = value & mask
        self.regs[reg] = (self.regs[reg] & ~(mask << offset)) | (value << offset)

    # ------------------------------------------------------------------
    # Convenience 32-bit helpers
    # ------------------------------------------------------------------

    def read_full(self, reg: int) -> int:
        """Return the full 32-bit value of register *reg*."""
        return self.regs[reg]

    def write_full(self, reg: int, value: int) -> None:
        """Write *value* to register *reg*, masked to 32 bits."""
        self.regs[reg] = value & 0xFFFFFFFF
