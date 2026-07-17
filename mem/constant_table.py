"""PRU constant table (C0-C31) for base-address lookups."""


class ConstantTable:
    """Holds 32 constant entries (C0-C31) used by LBCO/SBCO instructions."""

    def __init__(self):
        self.entries: list[int] = [0] * 32

    def _validate_index(self, index: int) -> None:
        if not (0 <= index <= 31):
            raise ValueError(
                f"Constant table index {index} is out of range (must be 0-31)"
            )

    def set(self, index: int, value: int) -> None:
        """Set constant entry *index* to *value*."""
        self._validate_index(index)
        self.entries[index] = value

    def resolve(self, index: int) -> int:
        """Return the value stored at constant entry *index*."""
        self._validate_index(index)
        return self.entries[index]

    def load_from_dict(self, mapping: dict[int, int]) -> None:
        """Bulk-load entries from a {index: value} dictionary."""
        for index, value in mapping.items():
            self.set(index, value)
