"""ICSSG-only address windows on the two-core ICSS-M (AM261x / AM263x).

The ICSS-M has no RTU_PRU, TX_PRU, Task Manager, PA_STATS or IEP1, and its
CFG block stops short of the ICSSG PWM trip registers.  The AM243x / AM64x
TRM (SPRUIM2J Table 6-63) places those blocks at fixed offsets in the ICSSG
address map.  On an LP-AM261 (ICSS-M0) a PRU read or a JTAG read of the first
words of each of them returns 0 with no bus error and no hang, and a 32-bit
store to them is dropped, so firmware written for the ICSSG that touches them
runs on silicon and sees zeros.

By default these windows behave that way: reads return 0 and writes are
dropped.  With ``[device] icssg_only_blocks = strict`` an access raises
instead, naming the block, so firmware that reaches for hardware the device
does not have can be caught in simulation.
"""

import logging

from mem.regions import MemoryRegion

logger = logging.getLogger(__name__)

READ_ZERO = "read_zero"
STRICT = "strict"
MODES = (READ_ZERO, STRICT)

# (name, base, size, measured)
#   base/size   SPRUIM2J Table 6-63 (distance to the next map entry; the IRAMs
#               use their stated 8 KB / 6 KB).
#   measured    True: every word of the window was read on silicon.
#               False: only +0, +4 and +0x100 were read; the rest of the
#               window is inferred from those.
ICSSG_ONLY_BLOCKS = (
    ("RTU_PRU0 IRAM", 0x00004000, 0x2000, False),
    ("RTU_PRU1 IRAM", 0x00006000, 0x2000, False),
    ("TX_PRU0 IRAM", 0x0000A000, 0x1800, False),
    ("TX_PRU1 IRAM", 0x0000C000, 0x1800, False),
    ("RTU_PRU0 Control", 0x00023000, 0x400, False),
    ("RTU_PRU0 Debug", 0x00023400, 0x400, False),
    ("RTU_PRU1 Control", 0x00023800, 0x400, False),
    ("RTU_PRU1 Debug", 0x00023C00, 0x400, False),
    ("TX_PRU0 Control", 0x00025000, 0x400, False),
    ("TX_PRU0 Debug", 0x00025400, 0x400, False),
    ("TX_PRU1 Control", 0x00025800, 0x400, False),
    ("TX_PRU1 Debug", 0x00025C00, 0x400, False),
    ("PA_STATS_QRAM", 0x00027000, 0x1000, False),
    ("TM_CFG_PRU0", 0x0002A000, 0x100, False),
    ("TM_CFG_RTU0", 0x0002A100, 0x100, False),
    ("TM_CFG_TX_PRU0", 0x0002A400, 0x100, False),
    ("TM_CFG_TX_PRU1", 0x0002A500, 0x100, False),
    ("PA_STATS_CRAM", 0x0002C000, 0x2000, False),
    ("IEP1", 0x0002F000, 0x1000, False),
    # CFG 0x130..0x16C: ICSSG PWM trip / state registers (ICSSG_PWM0..3,
    # PWMm_k).  Every word was read.
    ("CFG ICSSG PWM registers", 0x00026130, 0x40, True),
)


class IcssgOnlyBlockRegion(MemoryRegion):
    """A window that reads as zero and drops writes (or raises in strict mode)."""

    def __init__(self, name: str, base_addr: int, size: int, target: str,
                 strict: bool = False):
        # Latencies are not measured; use the other ICSS-local registers'.
        super().__init__(name, base_addr, size, 2, 1, 0)
        self.target = target
        self.strict = strict
        self._warned = False

    def _absent(self, kind: str, addr: int) -> None:
        msg = (f"{kind} of ICSSG-only block {self.name} at 0x{addr:08X}: "
               f"this block is absent on ICSS-M ({self.target})")
        if self.strict:
            raise ValueError(msg)
        if not self._warned:
            self._warned = True
            logger.warning("%s; reads return 0 and writes are ignored", msg)

    def read(self, addr: int, length: int) -> bytes:
        self._check_bounds(addr, length)
        self._absent("read", addr)
        return bytes(length)

    def write(self, addr: int, data: bytes) -> None:
        self._check_bounds(addr, len(data))
        self._absent("write", addr)


def icssg_only_regions(target: str, mode: str) -> list[IcssgOnlyBlockRegion]:
    """Build the windows for *target* (AM261x or AM263x) in *mode*."""
    if mode not in MODES:
        raise ValueError(
            f"Unknown icssg_only_blocks {mode!r}; expected one of "
            f"{', '.join(MODES)}")
    return [IcssgOnlyBlockRegion(name, base, size, target, strict=(mode == STRICT))
            for name, base, size, _ in ICSSG_ONLY_BLOCKS]
