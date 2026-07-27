"""Top-level 3-channel Peripheral Interface (protocol-agnostic SCU).

Owns 3 PerifChannels + the shared config registers, decodes the PRU R30/R31
interface, and advances the channel timelines.  Modeled on
`pru_io.sd_filter.SigmaDeltaFilter` and driven by ENDAT_INTERFACE_SPEC.md
(sections 5, 6, 7, 8).  Peripheral mode is enabled by GPCFG.PRU_GP_MUX_SEL == 1
(set via `self.enabled`); routing in IOPort is gated on it.
"""

from __future__ import annotations

from perif.perif_channel import PerifChannel

_NUM_CHANNELS = 3


class PeripheralInterface:
    def __init__(self, pru_clock_mhz: float = 200.0, uart_clock_mhz: float = 192.0):
        self.pru_clock_mhz = pru_clock_mhz
        self.uart_clock_mhz = uart_clock_mhz
        self.registers = None  # PerifRegisters, wired by the Simulator
        self.enabled = False   # set by GPCFG mux decode
        self.channels: list[PerifChannel] = []  # built after registers wired

        # R30 decoded levels
        self.ch_sel = 0
        self._now_ns = 0.0
        self._period_ns = 1000.0 / pru_clock_mhz  # nominal core-clock period

    def build_channels(self) -> None:
        """Instantiate the 3 channels once `self.registers` is wired."""
        self.channels = [
            PerifChannel(i, self.registers, self.pru_clock_mhz, self.uart_clock_mhz)
            for i in range(_NUM_CHANNELS)
        ]

    # ==================================================================
    # R30 (GPO) — spec section 5
    # ==================================================================

    def process_r30(self, value: int, wstrb: int = 0xF) -> None:
        """Decode an R30 write. *wstrb* is the 4-bit byte write-strobe mask.

        byte0 strobe -> push TX FIFO of the selected channel
        byte2 strobe -> latch clock mode of the selected channel
        byte3 strobe -> latch RX enable bits [26:24]
        channel-select [17:16] and RX-enable levels are always decoded.
        """
        if not self.channels:
            return
        self.ch_sel = (value >> 16) & 0x3
        ch = self.ch_sel if self.ch_sel < _NUM_CHANNELS else 0

        if wstrb & 0x1:  # TX FIFO data (byte 0)
            self.channels[ch].push_tx(value & 0xFF)
        if wstrb & 0x4:  # clock mode (byte 2, bits [20:19])
            self.channels[ch].set_clk_mode((value >> 19) & 0x3)
        if wstrb & 0x8:  # RX enable (byte 3, bits [26:24])
            for i in range(_NUM_CHANNELS):
                self.channels[i].arm_rx(bool((value >> (24 + i)) & 1))

    # ==================================================================
    # R31 (GPI) — spec section 6
    # ==================================================================

    def get_r31_status(self) -> int:
        """Pack the R31 status word (spec 6.2)."""
        if not self.channels:
            return 0
        status = 0
        for i, ch in enumerate(self.channels):
            byte = ch.rx_head() if ch.rx_en else ch.tx_status_byte()
            status |= (byte & 0xFF) << (i * 8)
            status |= (int(ch.rx_valid) << (24 + i))
            status |= (int(ch.rx_ovf) << (27 + i))
        return status

    def process_r31_command(self, value: int, now_ns: float | None = None) -> None:
        """Process an R31 write as a one-cycle command strobe (spec 6.1)."""
        if not self.channels:
            return
        t = self._now_ns if now_ns is None else now_ns
        go = (value >> 18) & 1
        reinit = (value >> 19) & 1
        global_start = (value >> 20) & 1
        ch = self.ch_sel if self.ch_sel < _NUM_CHANNELS else 0

        if reinit:
            for c in self.channels:
                c.tx_reinit()
        if global_start:
            for c in self.channels:
                c.tx_go(t)
        elif go:
            self.channels[ch].tx_go(t)

        for i in range(_NUM_CHANNELS):
            if (value >> (24 + i)) & 1:
                self.channels[i].clr_val()
            if (value >> (27 + i)) & 1:
                self.channels[i].clr_ovf()

    # ==================================================================
    # Reset
    # ==================================================================

    def reset(self) -> None:
        """Hardware reset: clear every channel's state and the R30 decode.

        Called from `PRUCore.reset()`, so both the per-core Reset and the UI's
        HW Reset drop stale status bits (TX overrun/underrun, RX valid/overflow,
        busy) instead of leaving them latched from the previous run.  The config
        registers hold the setup entered in the UI (clock dividers, frame sizes,
        delays) and deliberately survive a reset; only the hardware-owned busy
        bits are cleared to follow the now-idle channels.
        """
        self.ch_sel = 0
        self._now_ns = 0.0
        for ch in self.channels:
            ch.reset()
            if self.registers is not None:
                self.registers.set_busy(ch.index, False)

    # ==================================================================
    # Timeline
    # ==================================================================

    def advance(self, now_ns: float) -> None:
        self._now_ns = now_ns
        for ch in self.channels:
            ch.advance(now_ns)
            if self.registers is not None:
                self.registers.set_busy(ch.index, ch.busy)

    def advance_cycles(self, cycles: int) -> None:
        """Advance using the core cycle count and this core's nominal period."""
        self.advance(cycles * self._period_ns)

    # ==================================================================
    # Snapshot / state
    # ==================================================================

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "ch_sel": self.ch_sel,
            "now_ns": self._now_ns,
            "regs": bytes(self.registers._data) if self.registers is not None else None,
            "channels": [c.snapshot() for c in self.channels],
        }

    def restore(self, snap: dict) -> None:
        self.enabled = snap["enabled"]
        self.ch_sel = snap["ch_sel"]
        self._now_ns = snap["now_ns"]
        if snap["regs"] is not None and self.registers is not None:
            self.registers._data[:] = snap["regs"]
        for c, cs in zip(self.channels, snap["channels"]):
            c.restore(cs)

    def get_state(self) -> dict:
        state = {
            "enabled": self.enabled,
            "ch_sel": self.ch_sel,
            "channels": [c.get_state() for c in self.channels],
        }
        if self.registers is not None:
            state["shared"] = self.registers.get_shared_config()
        return state
