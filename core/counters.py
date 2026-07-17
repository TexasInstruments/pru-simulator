"""CycleCounters — lightweight performance counters for the PRU simulator."""


class CycleCounters:
    """Track cycles, stall cycles, and retired instructions."""

    def __init__(self) -> None:
        self.cycles: int = 0
        self.stall_cycles: int = 0
        self.instruction_count: int = 0

    # ------------------------------------------------------------------
    # Mutation methods
    # ------------------------------------------------------------------

    def tick(self, n: int = 1) -> None:
        """Advance *n* instruction cycles (increments cycles and instruction_count)."""
        self.cycles += n
        self.instruction_count += n

    def stall(self, n: int) -> None:
        """Record *n* stall cycles (increments cycles and stall_cycles only)."""
        self.cycles += n
        self.stall_cycles += n

    # ------------------------------------------------------------------
    # Derived property
    # ------------------------------------------------------------------

    @property
    def ipc(self) -> float:
        """Instructions per cycle.  Returns 0.0 when no cycles have elapsed."""
        if self.cycles == 0:
            return 0.0
        return self.instruction_count / self.cycles

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Zero all counters."""
        self.cycles = 0
        self.stall_cycles = 0
        self.instruction_count = 0
