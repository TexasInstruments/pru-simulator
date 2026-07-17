"""ICSSG_GPCFG registers — the GP configuration registers that select the
PRU I/O mux mode (TRM 6.4.14.5.3 ICSSG_GPCFG0_REG).

Only the PR1_PRUn_GP_MUX_SEL field [29:26] is modeled here:
  0h = GP (GPIO)   1h = EnDAT / Peripheral-interface mode
  2h = MII         3h = SD (Sigma-Delta)

Register offsets within PRU_ICSSG_CFG:
  GPCFG0_REG (PRU0)   = 0x26008
  GPCFG1_REG (core-1) = 0x2600C
"""

_GPCFG0 = 0x26008
_SIZE = 0x08  # covers 0x26008 (PRU0) and 0x2600C (core-1)

MUX_GP = 0x0
MUX_PERIF = 0x1
MUX_MII = 0x2
MUX_SD = 0x3


class GpcfgRegisters:
    """GPCFG0/1 registers. `on_mux_change(pru_index, mux_sel)` fires on write."""

    def __init__(self, base_addr: int = _GPCFG0):
        self._base = base_addr
        self._data = bytearray(_SIZE)
        self.on_mux_change = None  # callable(pru_index:int, mux_sel:int)

    def _check(self, addr: int, length: int) -> None:
        if addr < self._base or (addr + length) > self._base + _SIZE:
            raise ValueError(
                f"Address 0x{addr:X}+{length} out of GPCFG range "
                f"0x{self._base:X}-0x{self._base + _SIZE:X}")

    def read(self, addr: int, length: int) -> bytes:
        self._check(addr, length)
        off = addr - self._base
        return bytes(self._data[off:off + length])

    def write(self, addr: int, data: bytes) -> None:
        self._check(addr, len(data))
        off = addr - self._base
        self._data[off:off + len(data)] = data
        if self.on_mux_change is not None:
            # Notify for whichever 32-bit register(s) the write touched.
            for pru_index in (0, 1):
                reg_off = pru_index * 4
                if off <= reg_off < off + len(data) or reg_off <= off < reg_off + 4:
                    self.on_mux_change(pru_index, self.get_mux_sel(pru_index))

    def get_mux_sel(self, pru_index: int) -> int:
        off = pru_index * 4
        word = int.from_bytes(self._data[off:off + 4], "little")
        return (word >> 26) & 0xF

    def set_mux_sel(self, pru_index: int, mux_sel: int) -> None:
        """Helper for UI / tests: set the mux field and fire the callback."""
        off = pru_index * 4
        word = int.from_bytes(self._data[off:off + 4], "little")
        word = (word & ~(0xF << 26)) | ((mux_sel & 0xF) << 26)
        self.write(self._base + off, word.to_bytes(4, "little"))
