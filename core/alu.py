"""ALU (Arithmetic Logic Unit) for the PRU simulator.

All arithmetic operations return a (result, carry) tuple.
Logic/shift/bit operations return just an int result.
"""


class ALU:
    """Static methods class implementing PRU ALU operations."""

    # ------------------------------------------------------------------
    # Arithmetic operations – return (result: int, carry: bool)
    # ------------------------------------------------------------------

    @staticmethod
    def add(a: int, b: int, width: int) -> tuple[int, bool]:
        """Unsigned addition.  Overflow wraps at *width* bits; carry is set
        when the mathematical sum exceeds the width-bit range."""
        mask = (1 << width) - 1
        result_full = (a & mask) + (b & mask)
        carry = result_full > mask
        return result_full & mask, carry

    @staticmethod
    def adc(a: int, b: int, carry_in: bool, width: int) -> tuple[int, bool]:
        """Add with carry-in."""
        mask = (1 << width) - 1
        result_full = (a & mask) + (b & mask) + (1 if carry_in else 0)
        carry = result_full > mask
        return result_full & mask, carry

    @staticmethod
    def sub(a: int, b: int, width: int) -> tuple[int, bool]:
        """Unsigned subtraction a - b.  carry=True when no borrow (a >= b)."""
        mask = (1 << width) - 1
        a_m = a & mask
        b_m = b & mask
        carry = a_m >= b_m         # no borrow → carry set (standard convention)
        result = (a_m - b_m) & mask
        return result, carry

    @staticmethod
    def suc(a: int, b: int, carry_in: bool, width: int) -> tuple[int, bool]:
        """Subtract with carry: a - b - ~carry_in (borrow from carry)."""
        mask = (1 << width) - 1
        a_m = a & mask
        b_m = b & mask
        c = 0 if carry_in else 1   # ~carry: borrow=1 when carry=0
        result_full = a_m - b_m - c
        carry = result_full >= 0    # carry set when no underflow
        return result_full & mask, carry

    @staticmethod
    def rsb(a: int, b: int, width: int) -> tuple[int, bool]:
        """Reverse subtract: b - a."""
        return ALU.sub(b, a, width)

    @staticmethod
    def rsc(a: int, b: int, carry_in: bool, width: int) -> tuple[int, bool]:
        """Reverse subtract with carry: b - a - carry_in."""
        return ALU.suc(b, a, carry_in, width)

    # ------------------------------------------------------------------
    # Logic operations – return int
    # ------------------------------------------------------------------

    @staticmethod
    def and_(a: int, b: int) -> int:
        return a & b

    @staticmethod
    def or_(a: int, b: int) -> int:
        return a | b

    @staticmethod
    def xor_(a: int, b: int) -> int:
        return a ^ b

    @staticmethod
    def not_(a: int) -> int:
        """Bitwise NOT masked to 32 bits."""
        return (~a) & 0xFFFF_FFFF

    # ------------------------------------------------------------------
    # Shift operations – return int, masked to 32 bits
    # ------------------------------------------------------------------

    @staticmethod
    def lsl(value: int, shift: int) -> int:
        """Logical shift left, result masked to 32 bits. Shift uses 5 LSBs."""
        return (value << (shift & 0x1F)) & 0xFFFF_FFFF

    @staticmethod
    def lsr(value: int, shift: int) -> int:
        """Logical shift right (unsigned), result masked to 32 bits. Shift uses 5 LSBs."""
        return (value & 0xFFFF_FFFF) >> (shift & 0x1F)

    # ------------------------------------------------------------------
    # Bit manipulation – return int
    # ------------------------------------------------------------------

    @staticmethod
    def set_bit(value: int, bit: int) -> int:
        """Set bit *bit* in *value*. Bit position uses 5 LSBs."""
        return value | (1 << (bit & 0x1F))

    @staticmethod
    def clr_bit(value: int, bit: int) -> int:
        """Clear bit *bit* in *value*. Bit position uses 5 LSBs."""
        return value & ~(1 << (bit & 0x1F))

    @staticmethod
    def lmbd(value: int, target: int) -> int:
        """Left-Most Bit Detection.

        Scans from bit 31 down to bit 0.  Returns the position of the
        first bit that matches *target* & 1.  Returns 32 if no such bit
        is found.
        """
        t = target & 1
        value = value & 0xFFFF_FFFF
        for i in range(31, -1, -1):
            if ((value >> i) & 1) == t:
                return i
        return 32

    # ------------------------------------------------------------------
    # Comparison helpers – return int
    # ------------------------------------------------------------------

    @staticmethod
    def min_(a: int, b: int) -> int:
        return a if a < b else b

    @staticmethod
    def max_(a: int, b: int) -> int:
        return a if a > b else b
