"""One channel of the 3-channel Peripheral Interface.

Models (per ENDAT_INTERFACE_SPEC.md):
  * TX: 4x8 FIFO, wire/Tst delay FSM, MSB-first serializer, frame size,
        bit-swap, overrun/underrun, clock modes 0-3.
  * RX: 4x8 FIFO, start-bit detection, oversample capture, frame-size EOF,
        valid/overflow.

TX has two FIFO operating modes per the AM243x TRM (Table 6-424, "tx_data"):
  * Preload-and-go (tx_frame_size > 0, < 32 bits): the whole frame is
    snapshotted from the FIFO at go-time (`_build_frame_bits`); the FIFO
    flushes when the frame completes.
  * Continuous mode (tx_frame_size == 0): bytes are popped from the FIFO
    ONE AT A TIME as each one finishes shifting out, so R31's tx_fifo_stsN
    occupancy field reflects the true live queue depth. Software is
    expected to keep the FIFO fed — refilling at the half-empty (occupancy
    <= 2, of 4) level per the TRM guidance — to avoid gaps; running dry
    ends the frame.

Two driving levels are provided:
  * Edge-level primitives (`tx_bit_edge`, `rx_sample_edge`) — deterministic,
    used by unit tests.
  * `advance(now_ns)` — ns-timeline wrapper used by the loopback / core step
    that converts elapsed time into sample-clock edges.

Clock frequency = source / ((frac+1) * (div_factor+1))  (spec section 4.4).
Delay counters increment by +5 per core clock (spec section 7.3); real time
therefore scales with the configured core-clock period.
"""

from __future__ import annotations
import math

_FIFO_DEPTH = 4
_UART_CLOCK_MHZ = 192.0

# TX FSM states
# CLKRUN: TX data is exhausted but PERIF<m>_CLK is still free-running. Required
# by clk_mode 0/1/2 (TRM 6.4.5.2.2.3.6.3.3) — the clock is NOT stopped by the
# transmitter running out of data, it is stopped by the RX frame counter.
IDLE, WIRE, TST, TRANSMIT, CLKRUN = "IDLE", "WIRE", "TST", "TRANSMIT", "CLKRUN"


class PerifChannel:
    def __init__(self, index: int, registers, core_clock_mhz: float = 200.0,
                 uart_clock_mhz: float = _UART_CLOCK_MHZ):
        self.index = index
        self.regs = registers
        self.core_clock_mhz = core_clock_mhz
        self.uart_clock_mhz = uart_clock_mhz

        # --- TX state ---
        self.tx_fifo: list[int] = []
        self.tx_overrun = False
        self.tx_underrun = False
        self.busy = False
        self.fsm = IDLE
        self.clk_mode = 1            # reset default: stop-high after RX EOF
        self.tx_clk_pin = 1          # idle high
        self.tx_data_pin = 0
        self.tx_out_en = 0           # 1 = driving (active-high per spec section 11)

        self._frame_bits: list[int] = []   # bit sequence still to send (MSB-first)
        self._bit_index = 0
        self._phase_end_ns = 0.0           # end of the current wire/tst phase
        self._tx_half_toggles = 0          # half-period toggles since transmit
        self._go_ns = 0.0

        # Continuous mode (tx_frame_size == 0): live byte-at-a-time FIFO
        # consumption, independent of _frame_bits/_bit_index above.
        self._cont_byte: int | None = None
        self._cont_bit_idx = 0

        # Timestamped serial-line history for the loopback: (t_ns, line_value).
        self.tx_transitions: list[tuple[float, int]] = [(0.0, 0)]
        self._tx_hist_cap = 4096

        # --- RX state ---
        self.rx_en = False
        self.rx_fifo: list[int] = []
        self.rx_valid = False
        self.rx_ovf = False
        self.rx_eof = False
        self._rx_shift = 0
        self._rx_started = False
        self._rx_sample_cnt = 0
        self._rx_byte_cnt = 0
        self._rx_next_edge_ns: float | None = None

        # RX input source: callable(t_ns) -> bit (0/1). Default: idle low.
        self.rx_line_source = None

        self._last_ns = 0.0

    # ==================================================================
    # Clock helpers
    # ==================================================================

    def _src_mhz(self, clk_sel: int) -> float:
        return self.core_clock_mhz if clk_sel == 1 else self.uart_clock_mhz

    def tx_clock_period_ns(self) -> float:
        n = (self.regs.get_tx_div_factor_frac() + 1) * (self.regs.get_tx_div_factor() + 1)
        f = self._src_mhz(self.regs.get_tx_clk_sel()) / n
        return 1000.0 / f

    def rx_clock_period_ns(self) -> float:
        n = (self.regs.get_rx_div_factor_frac() + 1) * (self.regs.get_rx_div_factor() + 1)
        f = self._src_mhz(self.regs.get_rx_clk_sel()) / n
        return 1000.0 / f

    def _core_period_ns(self) -> float:
        return 1000.0 / self.core_clock_mhz

    def _delay_ns(self, reg_value: int) -> float:
        """Delay-counter value (+5 per core cycle) -> real ns for this core clock."""
        if reg_value <= 0:
            return 0.0
        cycles = math.ceil(reg_value / 5.0)
        return cycles * self._core_period_ns()

    # ==================================================================
    # TX — control / FIFO
    # ==================================================================

    def push_tx(self, byte: int) -> None:
        """Push one byte into the TX FIFO (R30 byte-0 write strobe)."""
        if len(self.tx_fifo) < _FIFO_DEPTH:
            self.tx_fifo.append(byte & 0xFF)
        else:
            self.tx_overrun = True

    def _pop_tx_byte(self) -> int | None:
        """Pop the oldest byte from the FIFO (continuous mode), applying swap."""
        if not self.tx_fifo:
            return None
        byte = self.tx_fifo.pop(0)
        if self.regs.get_tx_swap_data_en(self.index):
            byte = int(f"{byte:08b}"[::-1], 2)
        return byte

    def set_clk_mode(self, mode: int) -> None:
        self.clk_mode = mode & 0x3

    def _build_frame_bits(self) -> list[int]:
        """Assemble the MSB-first bit sequence to transmit.

        frame_size == 0 -> continuous: send every byte in the FIFO.
        frame_size  > 0 -> send exactly that many bits; flag underrun if the
        FIFO holds fewer bytes than required.
        """
        frame_size = self.regs.get_tx_frame_size(self.index)
        swap = self.regs.get_tx_swap_data_en(self.index)
        nbits = frame_size if frame_size > 0 else len(self.tx_fifo) * 8
        nbytes_needed = math.ceil(nbits / 8) if nbits else 0
        if frame_size > 0 and len(self.tx_fifo) < nbytes_needed:
            self.tx_underrun = True

        bits: list[int] = []
        for byte in self.tx_fifo:
            b = byte
            if swap:
                b = int(f"{byte:08b}"[::-1], 2)
            for i in range(7, -1, -1):      # MSB first
                bits.append((b >> i) & 1)
        return bits[:nbits] if nbits else []

    def tx_go(self, now_ns: float) -> None:
        """Start a TX transaction (ignored if the FIFO is empty)."""
        if not self.tx_fifo:
            return
        continuous = self.regs.get_tx_frame_size(self.index) == 0
        if continuous:
            self._cont_byte = self._pop_tx_byte()
            self._cont_bit_idx = 0
        else:
            self._frame_bits = self._build_frame_bits()
            self._bit_index = 0
        self._go_ns = now_ns
        self.busy = True
        wire = self._delay_ns(self.regs.get_tx_wire_delay(self.index))
        tst = self._delay_ns(self.regs.get_tx_tst_delay(self.index))
        if wire > 0:
            self.fsm = WIRE
            self.tx_clk_pin = 1          # clock HIGH during wire delay
            self._phase_end_ns = now_ns + wire
        elif tst > 0:
            self.fsm = TST
            self.tx_clk_pin = 0          # clock LOW during Tst delay
            self._phase_end_ns = now_ns + tst
        else:
            self._enter_transmit(now_ns)
        self.tx_out_en = 1

    def _enter_transmit(self, now_ns: float) -> None:
        self.fsm = TRANSMIT
        self._phase_end_ns = now_ns
        self._tx_half_toggles = 0
        self.tx_out_en = 1
        if self.regs.get_tx_frame_size(self.index) == 0:
            self._cont_bit_idx = 0
            if self._cont_byte is not None:
                self.tx_data_pin = (self._cont_byte >> 7) & 1
        else:
            self._bit_index = 0
            if self._frame_bits:
                self.tx_data_pin = self._frame_bits[0]
        self._record_line(now_ns)

    def _record_line(self, t_ns: float) -> None:
        """Append the current serial-line level at *t_ns* (if it changed)."""
        val = self.tx_line_value()
        if self.tx_transitions and self.tx_transitions[-1][1] == val:
            return
        self.tx_transitions.append((t_ns, val))
        if len(self.tx_transitions) > self._tx_hist_cap:
            self.tx_transitions = self.tx_transitions[-self._tx_hist_cap:]

    def tx_line_at(self, t_ns: float) -> int:
        """Serial-line level at source-time *t_ns* (latest transition <= t)."""
        val = 0
        for tt, v in self.tx_transitions:
            if tt <= t_ns:
                val = v
            else:
                break
        return val

    def _finish_frame(self) -> None:
        """TX data done. Whether the CLOCK stops here depends on clk_mode.

        Preload-and-go mode flushes the FIFO (spec 7.1). Continuous mode
        never snapshots the FIFO, so it is already at its true live depth —
        any bytes pushed right at the end stay queued for the next go.
        """
        self.busy = False
        self.tx_out_en = 0
        if self.regs.get_tx_frame_size(self.index) == 0:
            self._cont_byte = None
            self._cont_bit_idx = 0
        else:
            self.tx_fifo = []
            self._frame_bits = []

        # TRM 6.4.5.2.2.3.6.3.3, "Stop Conditions", r30[20:19]:
        #   0  free-running, stop LOW  on last RX frame
        #   1  free-running, stop HIGH on last RX frame   (reset default)
        #   2  free-run (only a reinit leaves this mode)
        #   3  stop HIGH on last TX bit
        # Only mode 3 stops the clock when the transmitter runs out of data.
        # In 0/1/2 the clock keeps running so the far end can be clocked in —
        # which is the whole point for a read transaction, where the master
        # sends a short request and then clocks a long response back.
        if self.clk_mode == 3:
            self.fsm = IDLE
            self.tx_clk_pin = 1          # stop high on last TX bit
        else:
            self.fsm = CLKRUN            # keep PERIF<m>_CLK free-running

    def _stop_clock_on_rx_frame(self) -> None:
        """Modes 0/1 stop condition: the RX frame counter completed."""
        self.fsm = IDLE
        self.tx_clk_pin = 0 if self.clk_mode == 0 else 1

    def tx_reinit(self) -> None:
        """Soft reset of the TX side (R31 bit19)."""
        self.tx_fifo = []
        self._frame_bits = []
        self._bit_index = 0
        self._cont_byte = None
        self._cont_bit_idx = 0
        self.fsm = IDLE
        self.busy = False
        self.tx_overrun = False
        self.tx_underrun = False
        self.tx_clk_pin = 1
        self.tx_out_en = 0
        self.clk_mode = 1

    def reset(self) -> None:
        """Hardware reset of the channel — clears all volatile TX and RX state.

        Wider than `tx_reinit()` (the R31 bit19 soft reset, which is TX-only):
        this also drops the RX FIFO and its valid/overflow/EOF flags, the
        capture progress, the recorded line history and the ns timeline, so a
        reset channel behaves like one that has never been driven.  The config
        registers live in PerifRegisters and are not touched here, and
        `rx_line_source` (the loopback wiring) is left connected.
        """
        self.tx_reinit()
        self.tx_data_pin = 0
        self._bit_index = 0
        self._phase_end_ns = 0.0
        self._go_ns = 0.0
        self.tx_transitions = [(0.0, 0)]

        self.rx_en = False
        self.rx_fifo = []
        self.rx_valid = False
        self.rx_ovf = False
        self.rx_eof = False
        self._rx_shift = 0
        self._rx_started = False
        self._rx_sample_cnt = 0
        self._rx_byte_cnt = 0
        self._rx_next_edge_ns = None

        self._last_ns = 0.0

    def tx_bit_edge(self) -> int:
        """Advance the serializer by one TX sample-clock bit. Returns the data bit.

        Toggles the clock pin and drives the next frame bit. Calls
        `_finish_frame()` when all frame bits have been sent.
        """
        if self.fsm != TRANSMIT:
            return self.tx_data_pin
        self._tx_advance_data()
        if self.fsm == TRANSMIT:
            self.tx_clk_pin ^= 1
        return self.tx_data_pin

    def _tx_advance_data(self) -> int:
        """Advance the serializer by one DATA bit, without touching the clock.

        Split out from `tx_bit_edge` so the ns-timeline path can drive the
        clock at its own (half-period) granularity while still emitting
        exactly one data bit per full clock cycle.
        """
        if self.fsm != TRANSMIT:
            return self.tx_data_pin
        if self.regs.get_tx_frame_size(self.index) == 0:
            return self._tx_bit_edge_continuous()
        # bit 0 is already on the wire (set on entering TRANSMIT); advance first.
        self._bit_index += 1
        if self._bit_index < len(self._frame_bits):
            self.tx_data_pin = self._frame_bits[self._bit_index]
        else:
            self._finish_frame()
        return self.tx_data_pin

    def _tx_bit_edge_continuous(self) -> int:
        """Continuous-mode bit edge: shift the current byte, then pop the
        next one from the (live) FIFO once 8 bits are out. Ends the frame
        if the FIFO has run dry (spec: software must refill by half-empty)."""
        # Data only - the clock is toggled by the caller (`tx_bit_edge` for the
        # edge-level API, `advance()` for the ns timeline), so that one data
        # bit corresponds to exactly one full clock cycle in both paths.
        self._cont_bit_idx += 1
        if self._cont_bit_idx < 8:
            self.tx_data_pin = (self._cont_byte >> (7 - self._cont_bit_idx)) & 1
        else:
            nxt = self._pop_tx_byte()
            if nxt is not None:
                self._cont_byte = nxt
                self._cont_bit_idx = 0
                self.tx_data_pin = (nxt >> 7) & 1
            else:
                self._finish_frame()
        return self.tx_data_pin

    def tx_line_value(self) -> int:
        """Current serial data-line level presented to the loopback."""
        return self.tx_data_pin if self.tx_out_en else 0

    # ==================================================================
    # RX — control / capture
    # ==================================================================

    def arm_rx(self, enabled: bool) -> None:
        prev = self.rx_en
        self.rx_en = enabled
        if enabled and not prev:
            self._rx_started = False
            self._rx_shift = 0
            self._rx_sample_cnt = 0
            self._rx_byte_cnt = 0
            self.rx_eof = False
            self._rx_next_edge_ns = None
        elif not enabled:
            self.rx_eof = False

    def clr_val(self) -> None:
        """Pop one byte from the RX FIFO (R31 bit24/25/26)."""
        if self.rx_fifo:
            self.rx_fifo.pop(0)
        self.rx_valid = len(self.rx_fifo) > 0

    def clr_ovf(self) -> None:
        self.rx_ovf = False

    def rx_sample_edge(self, bit: int) -> None:
        """Process one RX oversample-clock edge with input *bit* (0/1).

        Shifts the input, detects the start bit, and captures a byte every
        (sample_size + 1) edges after the start (spec section 8.3).
        """
        if not self.rx_en:
            return
        bit &= 1
        # Shift register: newest in bit0, oldest in bit7 (spec note 6).
        self._rx_shift = ((self._rx_shift << 1) | bit) & 0xFF
        sb_pol = self.regs.get_rx_sb_pol()
        if not self._rx_started:
            if bit == sb_pol:
                self._rx_started = True
                self._rx_sample_cnt = 0
            return
        sample_size = self.regs.get_rx_sample_size()
        if self._rx_sample_cnt >= sample_size:
            self._rx_sample_cnt = 0
            self._capture_byte(self._rx_shift)
        else:
            self._rx_sample_cnt += 1

    def _capture_byte(self, value: int) -> None:
        if len(self.rx_fifo) < _FIFO_DEPTH:
            self.rx_fifo.append(value & 0xFF)
        else:
            self.rx_ovf = True
        self.rx_valid = len(self.rx_fifo) > 0
        # Frame-size EOF (bytes). 0 => immediate EOF handled by caller.
        frame_size = self.regs.get_rx_frame_size(self.index)
        self._rx_byte_cnt += 1
        if frame_size != 0 and self._rx_byte_cnt >= frame_size:
            self.rx_eof = True
            self._rx_byte_cnt = 0
            # TRM stop condition for clk_mode 0/1: "the clock will remain
            # free-running until the receive module has received the number of
            # bits indicated in rx_frame_counter". Mode 2 free-runs until a
            # reinit; mode 3 already stopped on the last TX bit.
            if self.fsm == CLKRUN and self.clk_mode in (0, 1):
                self._stop_clock_on_rx_frame()

    # ==================================================================
    # ns-timeline advance (used by the loopback / core step)
    # ==================================================================

    def advance(self, now_ns: float) -> None:
        """Advance TX FSM and RX oversampler from the last time to *now_ns*."""
        # --- TX FSM phase transitions ---
        if self.fsm == WIRE and now_ns >= self._phase_end_ns:
            tst = self._delay_ns(self.regs.get_tx_tst_delay(self.index))
            if tst > 0:
                self.fsm = TST
                self.tx_clk_pin = 0
                self._phase_end_ns = now_ns + tst
            else:
                self._enter_transmit(now_ns)
        if self.fsm == TST and now_ns >= self._phase_end_ns:
            self._enter_transmit(now_ns)

        # --- TX transmit: emit bits at the TX sample-clock rate ---
        # PERIF<m>_CLK frequency is source/((frac+1)*(div_factor+1)) (spec 4.4),
        # so a full cycle takes tx_clock_period_ns() and the pin must toggle
        # TWICE in that time. Toggling once per period emits a square wave at
        # half the programmed rate and stretches every bit cell to two clock
        # periods, which the receiver then oversamples into two FIFO bytes.
        # The data bit still advances once per full cycle.
        if self.fsm == TRANSMIT:
            half = self.tx_clock_period_ns() / 2.0
            while self.fsm == TRANSMIT and now_ns >= self._phase_end_ns + half:
                self._phase_end_ns += half
                self._tx_half_toggles += 1
                self.tx_clk_pin ^= 1
                if self._tx_half_toggles % 2 == 0:
                    self._tx_advance_data()      # one data bit per full cycle
                self._record_line(self._phase_end_ns)

        # --- Free-running clock after TX data is exhausted (clk_mode 0/1/2) ---
        # The RX oversampler below consumes these edges; without them a read
        # transaction can never clock its response in and the RX frame counter
        # never reaches its stop condition.
        if self.fsm == CLKRUN:
            period = self.tx_clock_period_ns()
            while self.fsm == CLKRUN and now_ns >= self._phase_end_ns + period:
                self._phase_end_ns += period
                self.tx_clk_pin ^= 1

        # --- RX: sample the input line at the RX oversample-clock rate ---
        if self.rx_en:
            period = self.rx_clock_period_ns()
            if self._rx_next_edge_ns is None:
                self._rx_next_edge_ns = self._last_ns + period
            while now_ns >= self._rx_next_edge_ns:
                bit = self._sample_line(self._rx_next_edge_ns)
                self.rx_sample_edge(bit)
                self._rx_next_edge_ns += period

        self._last_ns = now_ns

    def _sample_line(self, t_ns: float) -> int:
        if self.rx_line_source is not None:
            return self.rx_line_source(t_ns) & 1
        return 0

    # ==================================================================
    # Status / R31
    # ==================================================================

    def tx_status_byte(self) -> int:
        """TX status byte (spec 6.3): [0]=overrun,[1]=underrun,[4:2]=count,[5]=busy."""
        count = len(self.tx_fifo) & 0x7
        return ((int(self.tx_overrun)) | (int(self.tx_underrun) << 1) |
                (count << 2) | (int(self.busy) << 5))

    def rx_head(self) -> int:
        return self.rx_fifo[0] if self.rx_fifo else 0

    # ==================================================================
    # Snapshot / state
    # ==================================================================

    def snapshot(self) -> dict:
        return {
            "tx_fifo": list(self.tx_fifo), "tx_overrun": self.tx_overrun,
            "tx_underrun": self.tx_underrun, "busy": self.busy, "fsm": self.fsm,
            "clk_mode": self.clk_mode, "tx_clk_pin": self.tx_clk_pin,
            "tx_data_pin": self.tx_data_pin, "tx_out_en": self.tx_out_en,
            "frame_bits": list(self._frame_bits), "bit_index": self._bit_index,
            "cont_byte": self._cont_byte, "cont_bit_idx": self._cont_bit_idx,
            "phase_end_ns": self._phase_end_ns, "go_ns": self._go_ns,
            "tx_transitions": list(self.tx_transitions),
            "rx_en": self.rx_en, "rx_fifo": list(self.rx_fifo),
            "rx_valid": self.rx_valid, "rx_ovf": self.rx_ovf, "rx_eof": self.rx_eof,
            "rx_shift": self._rx_shift, "rx_started": self._rx_started,
            "rx_sample_cnt": self._rx_sample_cnt, "rx_byte_cnt": self._rx_byte_cnt,
            "rx_next_edge_ns": self._rx_next_edge_ns, "last_ns": self._last_ns,
        }

    def restore(self, s: dict) -> None:
        self.tx_fifo = list(s["tx_fifo"]); self.tx_overrun = s["tx_overrun"]
        self.tx_underrun = s["tx_underrun"]; self.busy = s["busy"]; self.fsm = s["fsm"]
        self.clk_mode = s["clk_mode"]; self.tx_clk_pin = s["tx_clk_pin"]
        self.tx_data_pin = s["tx_data_pin"]; self.tx_out_en = s["tx_out_en"]
        self._frame_bits = list(s["frame_bits"]); self._bit_index = s["bit_index"]
        self._cont_byte = s.get("cont_byte"); self._cont_bit_idx = s.get("cont_bit_idx", 0)
        self._phase_end_ns = s["phase_end_ns"]; self._go_ns = s["go_ns"]
        self.tx_transitions = list(s.get("tx_transitions", [(0.0, 0)]))
        self.rx_en = s["rx_en"]; self.rx_fifo = list(s["rx_fifo"])
        self.rx_valid = s["rx_valid"]; self.rx_ovf = s["rx_ovf"]; self.rx_eof = s["rx_eof"]
        self._rx_shift = s["rx_shift"]; self._rx_started = s["rx_started"]
        self._rx_sample_cnt = s["rx_sample_cnt"]; self._rx_byte_cnt = s["rx_byte_cnt"]
        self._rx_next_edge_ns = s["rx_next_edge_ns"]; self._last_ns = s["last_ns"]

    def get_state(self) -> dict:
        st = {
            "id": self.index,
            "tx_fifo": list(self.tx_fifo),
            "rx_fifo": list(self.rx_fifo),
            "fsm": self.fsm,
            "busy": self.busy,
            "tx_overrun": self.tx_overrun,
            "tx_underrun": self.tx_underrun,
            "rx_en": self.rx_en,
            "rx_valid": self.rx_valid,
            "rx_ovf": self.rx_ovf,
            "rx_eof": self.rx_eof,
            "clk_mode": self.clk_mode,
            "tx_clk_pin": self.tx_clk_pin,
            "tx_data_pin": self.tx_data_pin,
            "tx_out_en": self.tx_out_en,
            "tx_line": self.tx_line_value(),
        }
        if self.regs is not None:
            st["config"] = self.regs.get_channel_config(self.index)
        return st
