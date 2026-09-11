"""Branch Unit for the PRU simulator.

Contains:
  - LoopState : tracks a LOOP instruction's counter and address range
  - BranchUnit : evaluates conditional branch predicates
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Loop state
# ---------------------------------------------------------------------------

@dataclass
class LoopState:
    """Tracks the state of an active PRU LOOP instruction."""

    count: int
    start_address: int
    end_address: int

    def tick(self) -> bool:
        """Decrement the loop counter.

        Returns True if the loop should jump back to *start_address*
        (i.e., count was > 1 before decrement, so count > 0 after).
        """
        self.count -= 1
        return self.count > 0

    def is_done(self) -> bool:
        """Return True when the loop counter has reached zero (or below)."""
        return self.count <= 0


# ---------------------------------------------------------------------------
# Branch unit
# ---------------------------------------------------------------------------

class BranchUnit:
    """Evaluates PRU conditional branch predicates.

    PRU branch instructions compare *op_val* (the operand / immediate in the
    instruction) against *reg_val* (the register value).  The sense of each
    comparison therefore uses op_val as the *left* operand:

        qbeq  – taken when op_val == reg_val
        qbne  – taken when op_val != reg_val
        qbgt  – taken when op_val >  reg_val
        qbge  – taken when op_val >= reg_val
        qblt  – taken when op_val <  reg_val
        qble  – taken when op_val <= reg_val
        qbbs  – taken when bit is SET   in reg_val
        qbbc  – taken when bit is CLEAR in reg_val
    """

    def qbeq(self, reg_val: int, op_val: int) -> bool:
        """Branch if op_val == reg_val."""
        return op_val == reg_val

    def qbne(self, reg_val: int, op_val: int) -> bool:
        """Branch if op_val != reg_val."""
        return op_val != reg_val

    def qbgt(self, reg_val: int, op_val: int) -> bool:
        """Branch if op_val > reg_val."""
        return op_val > reg_val

    def qbge(self, reg_val: int, op_val: int) -> bool:
        """Branch if op_val >= reg_val."""
        return op_val >= reg_val

    def qblt(self, reg_val: int, op_val: int) -> bool:
        """Branch if op_val < reg_val."""
        return op_val < reg_val

    def qble(self, reg_val: int, op_val: int) -> bool:
        """Branch if op_val <= reg_val."""
        return op_val <= reg_val

    @staticmethod
    def _bit_index(bit: int) -> int:
        """Validate a QBBS/QBBC bit position.

        Defence in depth. The parser rejects a non-literal bit operand, but the
        shift below must not be reachable with a bad value from any other path:
        a negative count raises a bare ValueError that escapes the simulator and
        kills the run with a Python traceback instead of a diagnosable error.
        """
        if not isinstance(bit, int) or bit < 0 or bit > 31:
            raise ValueError(
                f"QBBS/QBBC bit position must be 0-31, got {bit!r}")
        return bit

    def qbbs(self, reg_val: int, bit: int) -> bool:
        """Branch if bit *bit* is SET in reg_val."""
        return bool((reg_val >> self._bit_index(bit)) & 1)

    def qbbc(self, reg_val: int, bit: int) -> bool:
        """Branch if bit *bit* is CLEAR in reg_val."""
        return not bool((reg_val >> self._bit_index(bit)) & 1)
