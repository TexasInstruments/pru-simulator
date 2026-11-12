"""PRU constant table (C0-C31) for base-address lookups."""

# C24-C31 follow the per-core CTRL registers CTBIR0, CTBIR1, CTPPR0 and CTPPR1
# (CTRL block +0x20, +0x24, +0x28, +0x2C). Verified by reading the table
# directly on an LP-AM243 (ICSSG0) and an LP-AM261 (ICSSM0): each entry equals
# the formula below after the host programmed two different register sets, and
# at the values found.
CTRL_REG_OFFSET = 0x20            # CTBIR0 within the core's CTRL block
CTRL_REG_COUNT = 4                # CTBIR0, CTBIR1, CTPPR0, CTPPR1
CTBIR0, CTBIR1, CTPPR0, CTPPR1 = range(CTRL_REG_COUNT)
PROGRAMMABLE_FIRST = 24           # C24..C31


class ConstantTable:
    """Holds 32 constant entries (C0-C31) used by LBCO/SBCO instructions.

    C0-C23 are plain entries. C24-C31 are derived from the four CTRL registers
    (all 0 at reset, so C28 is 0 until firmware programs CTPPR0):

        C24 = CTBIR0[7:0]   << 8
        C25 = 0x2000     | CTBIR0[23:16] << 8
        C26 = 0x2E000    | CTBIR1[7:0]   << 8
        C27 = 0x32000    | CTBIR1[23:16] << 8
        C28 = CTPPR0[15:0]  << 8
        C29 = 0xD0000000 | CTPPR0[31:16] << 8
        C30 = 0xE0000000 | CTPPR1[15:0]  << 8
        C31 = 0xF0000000 | CTPPR1[31:16] << 8

    Register bits outside the fields above were not measured; read-back
    returns whatever was written.
    """

    def __init__(self):
        self.entries: list[int] = [0] * 32
        self.ctrl: list[int] = [0] * CTRL_REG_COUNT
        self._refresh()

    def _validate_index(self, index: int) -> None:
        if not (0 <= index <= 31):
            raise ValueError(
                f"Constant table index {index} is out of range (must be 0-31)"
            )

    def _refresh(self) -> None:
        """Recompute C24-C31 from the CTRL registers."""
        b0, b1, p0, p1 = self.ctrl
        self.entries[24:32] = [
            (b0 & 0xFF) << 8,
            0x2000 | ((b0 >> 16) & 0xFF) << 8,
            0x2E000 | (b1 & 0xFF) << 8,
            0x32000 | ((b1 >> 16) & 0xFF) << 8,
            (p0 & 0xFFFF) << 8,
            0xD0000000 | ((p0 >> 16) & 0xFFFF) << 8,
            0xE0000000 | (p1 & 0xFFFF) << 8,
            0xF0000000 | ((p1 >> 16) & 0xFFFF) << 8,
        ]

    def set(self, index: int, value: int) -> None:
        """Set constant entry *index* to *value*.

        For C24-C31 this is a direct override (host or test injection); the
        next CTBIR/CTPPR write recomputes C24-C31 from the registers.
        """
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

    # -- CTBIR0/1, CTPPR0/1 ------------------------------------------------

    def read_ctrl(self, reg: int) -> int:
        """Return CTBIR0, CTBIR1, CTPPR0 or CTPPR1 (*reg* 0-3)."""
        return self.ctrl[reg]

    def write_ctrl(self, reg: int, value: int) -> None:
        """Write a CTRL register and update C24-C31 immediately."""
        self.ctrl[reg] = value & 0xFFFFFFFF
        self._refresh()

    def reset_ctrl(self) -> None:
        """Return the four CTRL registers to their reset value (0)."""
        self.ctrl = [0] * CTRL_REG_COUNT
        self._refresh()

    def snapshot(self) -> list[int]:
        return list(self.ctrl)

    def restore(self, snap: list[int]) -> None:
        self.ctrl = list(snap)
        self._refresh()
