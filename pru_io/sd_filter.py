# pru_io/sd_filter.py
"""Top-level sigma-delta filter peripheral for the PRU simulator.

Owns 3 SD channels and 3 pattern generator modulators.
Handles R30/R31 interface and async clock tick advancement.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from pru_io.sd_channel import SDChannel
from pru_io.sd_modulator import SDModulator

if TYPE_CHECKING:
    from pru_io.sd_registers import SDRegisters

_NUM_CHANNELS = 3


class SigmaDeltaFilter:
    """SD filter peripheral: 3 channels with pattern generators and R30/R31 interface."""

    def __init__(self, pru_clock_mhz: float = 200.0):
        self.pru_clock_mhz = pru_clock_mhz

        # Channels and modulators
        self.channels: list[SDChannel] = [SDChannel(osr=64) for _ in range(_NUM_CHANNELS)]
        self.modulators: list[SDModulator] = [SDModulator() for _ in range(_NUM_CHANNELS)]

        # Configuration registers (wired in Task 6)
        self.registers: SDRegisters | None = None

        # R30 decoded control fields
        self.ch_sel: int = 0
        self.sd_en: bool = False
        self.snoop: bool = False
        self.data_sel: bool = False

        # Async clock accumulators (one per channel)
        self._clock_acc: list[float] = [0.0] * _NUM_CHANNELS

        # Previous R31 write value — for rising-edge command detection
        self._prev_r31_write: int = 0

    def process_r30(self, value: int) -> None:
        """Decode R30 control bits."""
        self.ch_sel = (value >> 26) & 0xF
        self.sd_en = bool((value >> 25) & 1)
        self.snoop = bool((value >> 24) & 1)
        self.data_sel = bool((value >> 23) & 1)

    def get_r31_status(self) -> int:
        """Return packed R31 status word for the selected channel.

        Format: [31:30]=00, [29]=ovf, [28]=valid, [27:0]=data
        Non-destructive: reading does NOT clear valid (combinatorial per Verilog).
        Valid is cleared only by writing R31 bit[24].
        """
        # ch_sel values >= NUM_CHANNELS (e.g. from uninitialized R30) fall back to ch0
        ch_idx = self.ch_sel if self.ch_sel < _NUM_CHANNELS else 0
        ch = self.channels[ch_idx]
        acc_sel = self.registers.get_acc_sel(ch_idx) if self.registers is not None else 0
        data = ch.get_data(acc_sel)
        return (int(ch.ovf) << 29) | (int(ch.valid) << 28) | (data & 0x0FFFFFFF)

    def process_r31_command(self, value: int) -> None:
        """Process R31 write as command per Verilog icss_g_scu_sd.v:
        bit[24]=clr_ovf and clr_valid (mx_pru_r3031_5[2])
        bit[23]=reinit (mx_pru_r3031_5[4])

        Bit 23 overlaps with the data field [27:0].  Firmware uses
        'SET r31, r31, 24' to clear valid, which echoes the current R31
        status (including data bits) back.  If shadow data >= 2^23, bit 23
        is set in the echo and would spuriously trigger reinit.

        Guard: reinit (bit 23) is only honoured when bit 24 is NOT set.
        This disambiguates 'SET r31, r31, 23' (intentional reinit, bit 24=0)
        from 'SET r31, r31, 24' (clear command, bit 23 echoed from data).
        """
        # ch_sel values >= NUM_CHANNELS (e.g. from uninitialized R30) fall back to ch0
        ch_idx = self.ch_sel if self.ch_sel < _NUM_CHANNELS else 0
        ch = self.channels[ch_idx]
        bit24 = (value >> 24) & 1
        bit23 = (value >> 23) & 1
        if bit24:
            ch.clear_valid_and_ovf()
        if bit23 and not bit24:
            ch.reinit()
        self._prev_r31_write = value

    def tick(self) -> None:
        """Advance all channels by one PRU clock tick (async clock model).

        Each channel advances 0 or more SD ticks based on its modulator's clock rate
        relative to the PRU clock.
        """
        for i in range(_NUM_CHANNELS):
            mod = self.modulators[i]
            ratio = mod.sd_clock_mhz / self.pru_clock_mhz
            self._clock_acc[i] += ratio
            while self._clock_acc[i] >= 1.0:
                self._clock_acc[i] -= 1.0
                bit = mod.next_bit()
                self.channels[i].tick(bit)

    def advance_cycles(self, cycles: int) -> None:
        """Advance independent channels over a batch of PRU cycles.

        This preserves each channel's fractional clock accumulator and bit
        sequence while avoiding a Python call for every idle PRU instruction.
        It is used only by the explicitly enabled FOC timer-wait fast path.
        """
        if cycles < 0:
            raise ValueError("SD cycles cannot be negative")
        for i in range(_NUM_CHANNELS):
            mod = self.modulators[i]
            total = self._clock_acc[i] + (mod.sd_clock_mhz / self.pru_clock_mhz) * cycles
            ticks = int(total)
            self._clock_acc[i] = total - ticks
            channel = self.channels[i]
            for _ in range(ticks):
                channel.tick(mod.next_bit())

    def _on_config_change(self, ch: int, field: str, value: int) -> None:
        """Handle config register changes."""
        if ch < 0:
            return  # global fields (share_en) handled elsewhere
        if field == "osr":
            self.channels[ch].osr = value

    def snapshot(self) -> dict:
        """Return a copy of all mutable state for step-back."""
        return {
            "ch_sel": self.ch_sel,
            "sd_en": self.sd_en,
            "snoop": self.snoop,
            "data_sel": self.data_sel,
            "clock_acc": list(self._clock_acc),
            "prev_r31_write": self._prev_r31_write,
            "regs": bytes(self.registers._data) if self.registers is not None else None,
            "channels": [ch.snapshot() for ch in self.channels],
            "modulators": [mod.snapshot() for mod in self.modulators],
        }

    def restore(self, snap: dict) -> None:
        """Restore all mutable state from a snapshot."""
        self.ch_sel = snap["ch_sel"]
        self.sd_en = snap["sd_en"]
        self.snoop = snap["snoop"]
        self.data_sel = snap["data_sel"]
        self._clock_acc = list(snap["clock_acc"])
        self._prev_r31_write = snap.get("prev_r31_write", 0)
        if snap["regs"] is not None and self.registers is not None:
            self.registers._data[:] = snap["regs"]
        for ch, ch_snap in zip(self.channels, snap["channels"]):
            ch.restore(ch_snap)
        for mod, mod_snap in zip(self.modulators, snap["modulators"]):
            mod.restore(mod_snap)

    def get_state(self) -> dict:
        """Return full state for UI broadcast."""
        return {
            "sd_en": self.sd_en,
            "ch_sel": self.ch_sel,
            "snoop": self.snoop,
            "data_sel": self.data_sel,
            "channels": [
                {
                    "id": i,
                    "acc1": ch.acc1 & 0x0FFFFFFF,
                    "acc2": ch.acc2 & 0x0FFFFFFF,
                    "acc3": ch.acc3 & 0x0FFFFFFF,
                    "shadow_acc1": ch.shadow_acc1 & 0x0FFFFFFF,
                    "shadow_acc2": ch.shadow_acc2 & 0x0FFFFFFF,
                    "shadow_acc3": ch.shadow_acc3 & 0x0FFFFFFF,
                    "valid": ch.valid,
                    "ovf": ch.ovf,
                    "selected": i == self.ch_sel,
                    "osr": ch.osr,
                }
                for i, ch in enumerate(self.channels)
            ],
            "modulators": [
                {
                    "signal": mod.signal,
                    "sd_clock_mhz": mod.sd_clock_mhz,
                    "dc_level": mod.dc_level,
                    "amplitude": mod.amplitude,
                    "period": mod.period,
                    "phase_deg": mod.phase_deg,
                }
                for mod in self.modulators
            ],
        }
