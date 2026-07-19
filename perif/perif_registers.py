"""Memory-mapped configuration registers for the 3-channel Peripheral Interface.

Layout matches the AM243x/AM64x PRU_ICSSG_CFG "EDPRU" register block
(ENDAT_INTERFACE_SPEC.md section 15).  One block per PRU core:

  RXCFG    : block+0x00  (shared RX clock + sample/start-bit)
  TXCFG    : block+0x04  (shared TX clock + busy/share status)
  CHnCFG0  : block+0x08 + n*8  (wire delay, frame sizes, clk override, swap)
  CHnCFG1  : block+0x0C + n*8  (Tst delay, RX auto-arm delay)

Absolute block base: PRU0 = 0x260E0, core-1 = 0x26100.
"""

from typing import Callable, Optional

_NUM_CHANNELS = 3
_REG_SIZE = 0x20  # 8 registers x 4 bytes

# Register offsets within the block
_RXCFG = 0x00
_TXCFG = 0x04
_CH_CFG0 = 0x08
_CH_CFG1 = 0x0C
_CH_STRIDE = 0x08


class PerifRegisters:
    """Peripheral-interface CFG registers with field accessors and write callbacks.

    *base_addr* is the absolute address of the block (0x260E0 for PRU0,
    0x26100 for core-1).
    """

    def __init__(self, base_addr: int = 0x260E0):
        self._base = base_addr
        self._data = bytearray(_REG_SIZE)
        self.on_config_change: Optional[Callable[[int, str, int], None]] = None

    # ------------------------------------------------------------------
    # Raw read / write (address-based, used by the memory-bus region)
    # ------------------------------------------------------------------

    def _offset(self, addr: int) -> int:
        return addr - self._base

    def _check_addr(self, addr: int, length: int) -> None:
        end = self._base + _REG_SIZE
        if addr < self._base or (addr + length) > end:
            raise ValueError(
                f"Address 0x{addr:X}+{length} out of perif register range "
                f"0x{self._base:X}-0x{end:X}")

    def read(self, addr: int, length: int) -> bytes:
        self._check_addr(addr, length)
        off = self._offset(addr)
        return bytes(self._data[off:off + length])

    def write(self, addr: int, data: bytes) -> None:
        self._check_addr(addr, len(data))
        off = self._offset(addr)
        self._data[off:off + len(data)] = data
        self._fire_callbacks(addr)

    def _read_u32(self, off: int) -> int:
        return int.from_bytes(self._data[off:off + 4], "little")

    def _write_u32(self, off: int, value: int) -> None:
        self._data[off:off + 4] = (value & 0xFFFFFFFF).to_bytes(4, "little")

    def _fire_callbacks(self, addr: int) -> None:
        if self.on_config_change is None:
            return
        off = self._offset(addr) & ~0x3  # align to the 32-bit register
        if off == _RXCFG:
            self.on_config_change(-1, "rxcfg", self._read_u32(_RXCFG))
        elif off == _TXCFG:
            self.on_config_change(-1, "txcfg", self._read_u32(_TXCFG))
        else:
            for ch in range(_NUM_CHANNELS):
                if off == _CH_CFG0 + ch * _CH_STRIDE:
                    self.on_config_change(ch, "chcfg0", self._read_u32(off))
                elif off == _CH_CFG1 + ch * _CH_STRIDE:
                    self.on_config_change(ch, "chcfg1", self._read_u32(off))

    # ------------------------------------------------------------------
    # RXCFG fields (shared across the 3 RX channels)
    # ------------------------------------------------------------------

    def get_rx_sample_size(self) -> int:
        """RX oversampling ratio - 1 (0..7 => 1x..8x)."""
        return self._read_u32(_RXCFG) & 0x7

    def get_rx_sb_pol(self) -> int:
        """RX start-bit polarity (reset default = 1)."""
        return (self._read_u32(_RXCFG) >> 3) & 0x1

    def get_rx_clk_sel(self) -> int:
        """RX clock source: 0 = UART, 1 = OCP/core."""
        return (self._read_u32(_RXCFG) >> 4) & 0x1

    def get_rx_div_factor_frac(self) -> int:
        return (self._read_u32(_RXCFG) >> 15) & 0x1

    def get_rx_div_factor(self) -> int:
        return (self._read_u32(_RXCFG) >> 16) & 0xFFFF

    # ------------------------------------------------------------------
    # TXCFG fields
    # ------------------------------------------------------------------

    def get_tx_clk_sel(self) -> int:
        return (self._read_u32(_TXCFG) >> 4) & 0x1

    def get_share_en(self) -> int:
        return (self._read_u32(_TXCFG) >> 11) & 0x1

    def get_tx_div_factor_frac(self) -> int:
        return (self._read_u32(_TXCFG) >> 15) & 0x1

    def get_tx_div_factor(self) -> int:
        return (self._read_u32(_TXCFG) >> 16) & 0xFFFF

    def set_busy(self, ch: int, busy: bool) -> None:
        """Write the RO busy status bit [5+ch] into TXCFG (model-driven)."""
        val = self._read_u32(_TXCFG)
        bit = 1 << (5 + ch)
        val = (val | bit) if busy else (val & ~bit)
        self._write_u32(_TXCFG, val)

    # ------------------------------------------------------------------
    # Per-channel CHnCFG0 fields
    # ------------------------------------------------------------------

    def _cfg0(self, ch: int) -> int:
        return self._read_u32(_CH_CFG0 + ch * _CH_STRIDE)

    def _cfg1(self, ch: int) -> int:
        return self._read_u32(_CH_CFG1 + ch * _CH_STRIDE)

    def get_tx_wire_delay(self, ch: int) -> int:
        return self._cfg0(ch) & 0x7FF

    def get_tx_frame_size(self, ch: int) -> int:
        """TX frame size in bits (0 = continuous, 1..31 fixed)."""
        return (self._cfg0(ch) >> 11) & 0x1F

    def get_rx_frame_size(self, ch: int) -> int:
        """RX frame size in bytes (0 = immediate EOF)."""
        return (self._cfg0(ch) >> 16) & 0xFFF

    def get_clk_override_en(self, ch: int) -> int:
        return (self._cfg0(ch) >> 29) & 0x1

    def get_sw_clk_out(self, ch: int) -> int:
        return (self._cfg0(ch) >> 30) & 0x1

    def get_tx_swap_data_en(self, ch: int) -> int:
        return (self._cfg0(ch) >> 31) & 0x1

    # ------------------------------------------------------------------
    # Per-channel CHnCFG1 fields
    # ------------------------------------------------------------------

    def get_tx_tst_delay(self, ch: int) -> int:
        return self._cfg1(ch) & 0xFFFF

    def get_rx_en_count_delay(self, ch: int) -> int:
        return (self._cfg1(ch) >> 16) & 0xFFFF

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def get_channel_config(self, ch: int) -> dict:
        """Full per-channel config snapshot for the UI."""
        return {
            "tx_wire_delay": self.get_tx_wire_delay(ch),
            "tx_frame_size": self.get_tx_frame_size(ch),
            "rx_frame_size": self.get_rx_frame_size(ch),
            "tst_delay": self.get_tx_tst_delay(ch),
            "rx_en_count_delay": self.get_rx_en_count_delay(ch),
            "clk_override_en": self.get_clk_override_en(ch),
            "sw_clk_out": self.get_sw_clk_out(ch),
            "tx_swap_data_en": self.get_tx_swap_data_en(ch),
        }

    def get_shared_config(self) -> dict:
        """Shared RX/TX clock config for the UI."""
        return {
            "rx_sample_size": self.get_rx_sample_size(),
            "rx_sb_pol": self.get_rx_sb_pol(),
            "rx_clk_sel": self.get_rx_clk_sel(),
            "rx_div_factor": self.get_rx_div_factor(),
            "rx_div_factor_frac": self.get_rx_div_factor_frac(),
            "tx_clk_sel": self.get_tx_clk_sel(),
            "tx_div_factor": self.get_tx_div_factor(),
            "tx_div_factor_frac": self.get_tx_div_factor_frac(),
            "share_en": self.get_share_en(),
            "rxcfg": self._read_u32(_RXCFG),
            "txcfg": self._read_u32(_TXCFG),
            "base_addr": self._base,
        }
