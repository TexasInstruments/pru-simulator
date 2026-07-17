"""Memory-mapped SD configuration registers for ICSS-G sigma-delta filter.

Register layout matches real hardware:
  SD_CFG_REG0:        0x26044 (global)
  SD_CLK_SEL_REGn:    0x26048 + n*8 (per channel, n=0..2)
  SD_SAMPLE_SIZE_REGn: 0x2604C + n*8 (per channel, n=0..2)
"""

from typing import Callable, Optional

_BASE = 0x26044
_CLK_SEL_BASE = 0x26048
_SAMPLE_SIZE_BASE = 0x2604C
_CHANNEL_STRIDE = 8
_NUM_CHANNELS = 3

# Total register space: 0x26044 to 0x26063 (32 bytes covers all registers)
_REG_SIZE = 0x20


class SDRegisters:
    """SD filter configuration registers with field accessors and write callbacks."""

    def __init__(self):
        self._data = bytearray(_REG_SIZE)
        self.on_config_change: Optional[Callable[[int, str, int], None]] = None

    def _offset(self, addr: int) -> int:
        """Convert absolute address to internal offset."""
        return addr - _BASE

    def _check_channel(self, ch: int) -> None:
        if ch < 0 or ch >= _NUM_CHANNELS:
            raise ValueError(f"Channel {ch} out of range 0-{_NUM_CHANNELS-1}")

    def _check_addr(self, addr: int, length: int) -> None:
        end = _BASE + _REG_SIZE
        if addr < _BASE or (addr + length) > end:
            raise ValueError(f"Address 0x{addr:X}+{length} out of SD register range 0x{_BASE:X}-0x{end:X}")

    def read(self, addr: int, length: int) -> bytes:
        """Read *length* bytes from register address *addr*."""
        self._check_addr(addr, length)
        off = self._offset(addr)
        return bytes(self._data[off:off + length])

    def write(self, addr: int, data: bytes) -> None:
        """Write *data* to register address *addr* and fire callbacks."""
        self._check_addr(addr, len(data))
        off = self._offset(addr)
        old = bytes(self._data[off:off + len(data)])
        self._data[off:off + len(data)] = data
        self._fire_callbacks(addr, old)

    def _read_u32(self, addr: int) -> int:
        """Read a 32-bit register value."""
        off = self._offset(addr)
        return int.from_bytes(self._data[off:off + 4], 'little')

    def _fire_callbacks(self, addr: int, old: bytes) -> None:
        """Detect which fields changed and notify via on_config_change."""
        if self.on_config_change is None:
            return
        if addr == _BASE:
            new_val = self._read_u32(_BASE)
            old_val = int.from_bytes(old[:4], 'little') if len(old) >= 4 else 0
            if ((new_val >> 8) & 1) != ((old_val >> 8) & 1):
                self.on_config_change(-1, "share_en", (new_val >> 8) & 1)
        else:
            for ch in range(_NUM_CHANNELS):
                clk_addr = _CLK_SEL_BASE + ch * _CHANNEL_STRIDE
                ss_addr = _SAMPLE_SIZE_BASE + ch * _CHANNEL_STRIDE
                if addr == clk_addr:
                    self.on_config_change(ch, "acc_sel", self.get_acc_sel(ch))
                    self.on_config_change(ch, "clk_sel", self.get_clk_sel(ch))
                    self.on_config_change(ch, "clk_inv", int(self.get_clk_inv(ch)))
                elif addr == ss_addr:
                    self.on_config_change(ch, "osr", self.get_osr(ch))
                    self.on_config_change(ch, "fd_en", int(self.get_fd_en(ch)))
                    self.on_config_change(ch, "fd_window_size", self.get_fd_window_size(ch))

    # --- Field accessors: SD_CFG_REG0 ---

    def get_share_en(self) -> bool:
        return bool((self._read_u32(_BASE) >> 8) & 1)

    # --- Field accessors: SD_CLK_SEL_REGn ---

    def get_acc_sel(self, ch: int) -> int:
        """Return ACC_SEL[5:4]: 0=acc3 (sinc3), 1=acc2 (sinc2), 2=acc1 (sinc1)."""
        self._check_channel(ch)
        addr = _CLK_SEL_BASE + ch * _CHANNEL_STRIDE
        return (self._read_u32(addr) >> 4) & 0x3

    def get_clk_sel(self, ch: int) -> int:
        """Return CLK_SEL[1:0]."""
        self._check_channel(ch)
        addr = _CLK_SEL_BASE + ch * _CHANNEL_STRIDE
        return self._read_u32(addr) & 0x3

    def get_clk_inv(self, ch: int) -> bool:
        """Return CLK_INV[2]."""
        self._check_channel(ch)
        addr = _CLK_SEL_BASE + ch * _CHANNEL_STRIDE
        return bool((self._read_u32(addr) >> 2) & 1)

    def get_fd_zero_max_limit(self, ch: int) -> int:
        self._check_channel(ch)
        addr = _CLK_SEL_BASE + ch * _CHANNEL_STRIDE
        return (self._read_u32(addr) >> 17) & 0x1F

    def get_fd_zero_min_limit(self, ch: int) -> int:
        self._check_channel(ch)
        addr = _CLK_SEL_BASE + ch * _CHANNEL_STRIDE
        return (self._read_u32(addr) >> 11) & 0x1F

    # --- Field accessors: SD_SAMPLE_SIZE_REGn ---

    def get_sample_size(self, ch: int) -> int:
        """Return raw SAMPLE_SIZE[7:0]."""
        self._check_channel(ch)
        addr = _SAMPLE_SIZE_BASE + ch * _CHANNEL_STRIDE
        return self._read_u32(addr) & 0xFF

    def get_osr(self, ch: int) -> int:
        """Return effective OSR (sample_size + 1)."""
        self._check_channel(ch)
        return self.get_sample_size(ch) + 1

    def get_fd_en(self, ch: int) -> bool:
        self._check_channel(ch)
        addr = _SAMPLE_SIZE_BASE + ch * _CHANNEL_STRIDE
        return bool((self._read_u32(addr) >> 23) & 1)

    def get_fd_window_size(self, ch: int) -> int:
        """Return FD_WINDOW_SIZE[10:8]: 0=4 samples, 1=8, ..., 7=32."""
        self._check_channel(ch)
        addr = _SAMPLE_SIZE_BASE + ch * _CHANNEL_STRIDE
        return (self._read_u32(addr) >> 8) & 0x7

    def get_fd_one_max_limit(self, ch: int) -> int:
        self._check_channel(ch)
        addr = _SAMPLE_SIZE_BASE + ch * _CHANNEL_STRIDE
        return (self._read_u32(addr) >> 17) & 0x1F

    def get_fd_one_min_limit(self, ch: int) -> int:
        self._check_channel(ch)
        addr = _SAMPLE_SIZE_BASE + ch * _CHANNEL_STRIDE
        return (self._read_u32(addr) >> 11) & 0x1F

    def get_channel_config(self, ch: int) -> dict:
        """Return full config snapshot for channel *ch* as a dict."""
        self._check_channel(ch)
        return {
            "osr": self.get_osr(ch),
            "acc_sel": self.get_acc_sel(ch),
            "clk_sel": self.get_clk_sel(ch),
            "clk_inv": self.get_clk_inv(ch),
            "fd_en": self.get_fd_en(ch),
            "fd_window_size": self.get_fd_window_size(ch),
        }
