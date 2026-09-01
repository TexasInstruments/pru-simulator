# tests/test_perif.py
"""Unit tests for the 3-channel Peripheral Interface backend (perif/)."""
import pytest

from perif.perif_registers import PerifRegisters
from perif.perif_channel import PerifChannel, IDLE, WIRE, TST, TRANSMIT, CLKRUN
from perif.peripheral_interface import PeripheralInterface

_BASE = 0x260E0


def mk_regs():
    return PerifRegisters(_BASE)


def _w(regs, off, val):
    regs.write(_BASE + off, (val & 0xFFFFFFFF).to_bytes(4, "little"))


def set_rxcfg(regs, sample_size=7, sb_pol=1, clk_sel=1, div=0, frac=0):
    val = (sample_size & 0x7) | ((sb_pol & 1) << 3) | ((clk_sel & 1) << 4)
    val |= ((frac & 1) << 15) | ((div & 0xFFFF) << 16)
    _w(regs, 0x00, val)


def set_txcfg(regs, clk_sel=1, div=0, frac=0):
    val = ((clk_sel & 1) << 4) | ((frac & 1) << 15) | ((div & 0xFFFF) << 16)
    _w(regs, 0x04, val)


def set_ch_cfg0(regs, ch, wire=0, tx_frame=0, rx_frame=0, ovr_en=0, sw_clk=0, swap=0):
    val = (wire & 0x7FF) | ((tx_frame & 0x1F) << 11) | ((rx_frame & 0xFFF) << 16)
    val |= ((ovr_en & 1) << 29) | ((sw_clk & 1) << 30) | ((swap & 1) << 31)
    _w(regs, 0x08 + ch * 8, val)


def set_ch_cfg1(regs, ch, tst=0, rx_en_cnt=0):
    val = (tst & 0xFFFF) | ((rx_en_cnt & 0xFFFF) << 16)
    _w(regs, 0x0C + ch * 8, val)


# ---------------------------------------------------------------------------
class TestRegisters:
    def test_field_roundtrip(self):
        r = mk_regs()
        set_rxcfg(r, sample_size=5, sb_pol=0, clk_sel=1, div=39, frac=1)
        assert r.get_rx_sample_size() == 5
        assert r.get_rx_sb_pol() == 0
        assert r.get_rx_clk_sel() == 1
        assert r.get_rx_div_factor() == 39
        assert r.get_rx_div_factor_frac() == 1

    def test_ch_cfg_fields(self):
        r = mk_regs()
        set_ch_cfg0(r, 1, wire=100, tx_frame=6, rx_frame=4, swap=1)
        set_ch_cfg1(r, 1, tst=200, rx_en_cnt=50)
        assert r.get_tx_wire_delay(1) == 100
        assert r.get_tx_frame_size(1) == 6
        assert r.get_rx_frame_size(1) == 4
        assert r.get_tx_swap_data_en(1) == 1
        assert r.get_tx_tst_delay(1) == 200
        assert r.get_rx_en_count_delay(1) == 50

    def test_shared_config_carries_raw_registers_and_base(self):
        r = mk_regs()
        _w(r, 0x00, 0x0007001F)   # RXCFG
        _w(r, 0x04, 0x00070010)   # TXCFG
        sh = r.get_shared_config()
        assert sh["rxcfg"] == 0x0007001F
        assert sh["txcfg"] == 0x00070010
        assert sh["base_addr"] == _BASE

    def test_busy_bits(self):
        r = mk_regs()
        r.set_busy(2, True)
        assert (r._read_u32(0x04) >> 7) & 1 == 1
        r.set_busy(2, False)
        assert (r._read_u32(0x04) >> 7) & 1 == 0


# ---------------------------------------------------------------------------
class TestTxFifo:
    def test_push_and_status(self):
        r = mk_regs()
        ch = PerifChannel(0, r)
        ch.push_tx(0xAA)
        ch.push_tx(0xBB)
        assert ch.tx_fifo == [0xAA, 0xBB]
        # status byte: count in [4:2]
        assert (ch.tx_status_byte() >> 2) & 0x7 == 2

    def test_overrun_on_full(self):
        r = mk_regs()
        ch = PerifChannel(0, r)
        for i in range(4):
            ch.push_tx(i)
        assert ch.tx_overrun is False
        ch.push_tx(99)  # 5th → overrun
        assert ch.tx_overrun is True
        assert ch.tx_status_byte() & 0x1 == 1


# ---------------------------------------------------------------------------
class TestTxFrameBits:
    def test_msb_first(self):
        r = mk_regs()
        set_ch_cfg0(r, 0, tx_frame=0)  # continuous
        ch = PerifChannel(0, r)
        ch.push_tx(0b10110010)
        bits = ch._build_frame_bits()
        assert bits == [1, 0, 1, 1, 0, 0, 1, 0]

    def test_swap(self):
        r = mk_regs()
        set_ch_cfg0(r, 0, tx_frame=0, swap=1)
        ch = PerifChannel(0, r)
        ch.push_tx(0b10110010)
        bits = ch._build_frame_bits()
        assert bits == [0, 1, 0, 0, 1, 1, 0, 1]  # reversed

    def test_tx_line_state_reflects_out_en_and_data(self):
        r = mk_regs()
        set_ch_cfg0(r, 0, tx_frame=8)
        ch = PerifChannel(0, r)
        assert ch.get_state()["tx_line"] == 0  # idle, out_en=0
        ch.push_tx(0b10000000)
        ch.tx_go(0.0)
        assert ch.get_state()["tx_line"] == 1  # driving bit 1
        for _ in range(7):
            ch.tx_bit_edge()
        assert ch.get_state()["tx_line"] == 0  # frame finished, out_en=0

    def test_frame_size_truncates(self):
        r = mk_regs()
        set_ch_cfg0(r, 0, tx_frame=6)  # 6 bits only
        ch = PerifChannel(0, r)
        ch.push_tx(0b10110010)
        bits = ch._build_frame_bits()
        assert bits == [1, 0, 1, 1, 0, 0]

    def test_underrun_when_short(self):
        r = mk_regs()
        set_ch_cfg0(r, 0, tx_frame=16)  # needs 2 bytes
        ch = PerifChannel(0, r)
        ch.push_tx(0xFF)  # only 1 byte
        ch._build_frame_bits()
        assert ch.tx_underrun is True


# ---------------------------------------------------------------------------
class TestTxSerialize:
    def test_go_ignored_when_empty(self):
        r = mk_regs()
        ch = PerifChannel(0, r)
        ch.tx_go(0.0)
        assert ch.fsm == IDLE
        assert ch.busy is False

    def test_transmit_emits_bits(self):
        r = mk_regs()
        set_ch_cfg0(r, 0, tx_frame=8)  # no wire/tst delay
        ch = PerifChannel(0, r)
        ch.push_tx(0b11001010)
        ch.tx_go(0.0)
        assert ch.fsm == TRANSMIT
        emitted = [ch.tx_data_pin]  # first bit set on entering transmit
        for _ in range(7):
            emitted.append(ch.tx_bit_edge())
        assert emitted == [1, 1, 0, 0, 1, 0, 1, 0]
        # one more edge finishes the TX DATA. The default clk_mode is 1, which
        # per TRM 6.4.5.2.2.3.6.3.3 keeps PERIF_CLK free-running until the RX
        # frame counter completes - the transmitter is done, the clock is not.
        ch.tx_bit_edge()
        assert ch.fsm == CLKRUN
        assert ch.busy is False
        assert ch.tx_out_en == 0
        assert ch.tx_fifo == []  # flushed

    def test_clock_mode_3_stops_high_on_last_tx_bit(self):
        """Mode 3 is the ONLY mode where TX exhaustion stops the clock."""
        r = mk_regs()
        set_ch_cfg0(r, 0, tx_frame=8)
        ch = PerifChannel(0, r)
        ch.set_clk_mode(3)
        ch.push_tx(0xFF)
        ch.tx_go(0.0)
        for _ in range(9):
            ch.tx_bit_edge()
        assert ch.fsm == IDLE
        assert ch.tx_clk_pin == 1


# ---------------------------------------------------------------------------
class TestContinuousModeStreaming:
    """tx_frame_size == 0: live byte-at-a-time FIFO consumption (TRM Table
    6-424 "tx_data": continuous mode, refill at the half-empty / 2-byte
    level). Distinct from preload-and-go, which snapshots at go-time."""

    def _run_byte(self, ch):
        """Drive exactly 8 tx_bit_edge() calls (one byte) and collect bits."""
        bits = [ch.tx_data_pin]
        for _ in range(7):
            bits.append(ch.tx_bit_edge())
        return bits

    def test_go_pops_first_byte_immediately(self):
        r = mk_regs()  # tx_frame default 0 -> continuous
        ch = PerifChannel(0, r)
        ch.push_tx(0xAA)
        ch.tx_go(0.0)
        assert ch.fsm == TRANSMIT
        assert ch.tx_fifo == []            # popped into the live shifter
        assert ch.tx_status_byte() >> 2 & 0x7 == 0   # tx_fifo_sts0 = 0 (occupancy)

    def test_pushing_mid_frame_is_seamlessly_consumed(self):
        r = mk_regs()
        ch = PerifChannel(0, r)
        ch.push_tx(0xAA)
        ch.tx_go(0.0)
        ch.push_tx(0xBB)                   # arrives while 0xAA is shifting
        assert ch.tx_fifo == [0xBB]        # queued, not yet consumed
        assert (ch.tx_status_byte() >> 2) & 0x7 == 1   # occupancy = 1

        first = self._run_byte(ch)         # 7 edges: samples idx0..idx7 of 0xAA
        assert first == [1, 0, 1, 0, 1, 0, 1, 0]       # 0xAA MSB-first
        assert ch.fsm == TRANSMIT          # byte0's own boundary not fired yet
        assert ch.tx_fifo == [0xBB]        # still queued — pop happens on the
                                            # boundary edge (the 8th edge call)

        second = [ch.tx_bit_edge()]        # boundary edge: pops 0xBB seamlessly
        assert ch.fsm == TRANSMIT          # no gap — kept going
        assert ch.tx_fifo == []
        for _ in range(7):
            second.append(ch.tx_bit_edge())
        assert second == [1, 0, 1, 1, 1, 0, 1, 1]      # 0xBB MSB-first
        assert ch.fsm == TRANSMIT          # byte1's own boundary not fired yet

        ch.tx_bit_edge()                   # byte1's boundary: no more data
        assert ch.fsm == CLKRUN            # clk_mode 1: clock still free-runs
        assert ch.busy is False

    def test_running_dry_ends_frame_without_flushing_new_pushes(self):
        r = mk_regs()
        ch = PerifChannel(0, r)
        ch.push_tx(0xAA)
        ch.tx_go(0.0)
        for _ in range(7):
            ch.tx_bit_edge()
        ch.tx_bit_edge()                   # 8th edge: no more data
        assert ch.fsm == CLKRUN            # clk_mode 1: clock still free-runs
        assert ch.busy is False
        # a byte pushed exactly as the frame ends must not be discarded
        ch.push_tx(0xCC)
        assert ch.tx_fifo == [0xCC]

    def test_half_empty_refill_keeps_streaming_across_many_bytes(self):
        """Simulate firmware behavior: keep pushing while occupancy <= 2."""
        r = mk_regs()
        ch = PerifChannel(0, r)
        payload = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66]
        it = iter(payload)
        ch.push_tx(next(it))
        ch.tx_go(0.0)
        sent = []
        while ch.fsm == TRANSMIT:
            occ = (ch.tx_status_byte() >> 2) & 0x7
            while occ <= 2:
                nxt = next(it, None)
                if nxt is None:
                    break
                ch.push_tx(nxt)
                occ = (ch.tx_status_byte() >> 2) & 0x7
            byte_bits = [ch.tx_bit_edge() for _ in range(8)]
            if ch.fsm == IDLE:
                # last edge of the final byte was consumed above; recover the
                # transmitted byte from the pin history isn't needed here —
                # correctness is checked via tx_transitions in the demo test.
                break
        assert ch.tx_overrun is False
        assert ch.tx_underrun is False

    def test_swap_applies_per_byte_in_continuous_mode(self):
        r = mk_regs()
        set_ch_cfg0(r, 0, swap=1)
        ch = PerifChannel(0, r)
        ch.push_tx(0b10110010)
        ch.tx_go(0.0)
        bits = self._run_byte(ch)
        assert bits == [0, 1, 0, 0, 1, 1, 0, 1]        # reversed, MSB-first

    def test_reinit_clears_continuous_state(self):
        r = mk_regs()
        ch = PerifChannel(0, r)
        ch.push_tx(0xAA)
        ch.push_tx(0xBB)
        ch.tx_go(0.0)
        ch.tx_reinit()
        assert ch.fsm == IDLE
        assert ch.tx_fifo == []
        assert ch._cont_byte is None


# ---------------------------------------------------------------------------
class TestClockPeriods:
    def test_divide_by_two(self):
        r = mk_regs()
        set_txcfg(r, clk_sel=1, div=1, frac=0)  # N=2
        ch = PerifChannel(0, r, core_clock_mhz=250.0)
        # 250 MHz / 2 = 125 MHz → 8 ns
        assert ch.tx_clock_period_ns() == pytest.approx(8.0)

    def test_frac_divider(self):
        r = mk_regs()
        set_txcfg(r, clk_sel=1, div=0, frac=1)  # N=2 via frac
        ch = PerifChannel(0, r, core_clock_mhz=200.0)
        assert ch.tx_clock_period_ns() == pytest.approx(10.0)  # 100 MHz


# ---------------------------------------------------------------------------
class TestRxCapture:
    def _feed(self, ch, bits):
        for b in bits:
            ch.rx_sample_edge(b)

    def test_start_bit_then_byte(self):
        """sample_size=7 (÷8): captures the 8 bits AFTER the start bit."""
        r = mk_regs()
        set_rxcfg(r, sample_size=7, sb_pol=1)
        set_ch_cfg0(r, 0, rx_frame=1)
        ch = PerifChannel(0, r)
        ch.arm_rx(True)
        V = 0b10110010
        stream = [1] + [(V >> i) & 1 for i in range(7, -1, -1)]  # start + MSB-first
        self._feed(ch, stream)  # 9 edges → capture on the 9th
        assert ch.rx_fifo == [V]
        assert ch.rx_valid is True
        assert ch.rx_eof is True  # frame_size=1

    def test_no_capture_before_start(self):
        r = mk_regs()
        set_rxcfg(r, sample_size=7, sb_pol=1)
        ch = PerifChannel(0, r)
        ch.arm_rx(True)
        self._feed(ch, [0, 0, 0, 0])  # never see sb_pol
        assert ch.rx_fifo == []

    def test_clr_val_pops(self):
        r = mk_regs()
        set_rxcfg(r, sample_size=7, sb_pol=1)
        set_ch_cfg0(r, 0, rx_frame=0)  # no EOF
        ch = PerifChannel(0, r)
        ch.arm_rx(True)
        for V in (0x11, 0x22):
            self._feed(ch, [1] + [(V >> i) & 1 for i in range(7, -1, -1)])
            # after each byte the start-detect must re-trigger; reset started
            ch._rx_started = False
        assert len(ch.rx_fifo) == 2
        ch.clr_val()
        assert ch.rx_fifo == [0x22]
        ch.clr_val()
        assert ch.rx_fifo == []
        assert ch.rx_valid is False

    def test_overflow(self):
        r = mk_regs()
        set_rxcfg(r, sample_size=7, sb_pol=1)
        ch = PerifChannel(0, r)
        ch.arm_rx(True)
        for _ in range(5):  # capture 5 bytes into a 4-deep FIFO
            ch._rx_started = False
            self._feed(ch, [1] + [1] * 8)
        assert ch.rx_ovf is True
        assert len(ch.rx_fifo) == 4


# ---------------------------------------------------------------------------
class TestTopLevel:
    def _mk(self):
        p = PeripheralInterface(pru_clock_mhz=200.0)
        p.registers = mk_regs()
        p.build_channels()
        return p

    def test_r30_fifo_push_selected_channel(self):
        p = self._mk()
        # channel_sel = 1 (bits [17:16]=01), byte0 = 0x5A, strobe byte0
        p.process_r30((1 << 16) | 0x5A, wstrb=0x1)
        assert p.channels[1].tx_fifo == [0x5A]
        assert p.channels[0].tx_fifo == []

    def test_r30_no_push_without_byte0_strobe(self):
        p = self._mk()
        p.process_r30((1 << 16) | 0x5A, wstrb=0x4)  # byte2 only
        assert p.channels[1].tx_fifo == []

    def test_r30_clkmode_and_rx_en(self):
        p = self._mk()
        # clk mode 2 (bits[20:19]=10) on channel 0, byte2 strobe
        p.process_r30((2 << 19), wstrb=0x4)
        assert p.channels[0].clk_mode == 2
        # rx_en for ch1 (bit25), byte3 strobe
        p.process_r30((1 << 25), wstrb=0x8)
        assert p.channels[1].rx_en is True

    def test_r31_status_packing(self):
        p = self._mk()
        p.channels[0].rx_fifo = [0x42]
        p.channels[0].rx_en = True
        p.channels[0].rx_valid = True
        p.channels[2].rx_ovf = True
        status = p.get_r31_status()
        assert status & 0xFF == 0x42          # ch0 rx head
        assert (status >> 24) & 1 == 1        # ch0 valid
        assert (status >> 29) & 1 == 1        # ch2 ovf

    def test_r31_reinit_and_global_start(self):
        p = self._mk()
        set_ch_cfg0(p.registers, 0, tx_frame=8)
        set_ch_cfg0(p.registers, 1, tx_frame=8)
        p.channels[0].push_tx(0xAA)
        p.channels[1].push_tx(0xBB)
        p.process_r31_command(1 << 20, now_ns=0.0)  # global start
        assert p.channels[0].fsm == TRANSMIT
        assert p.channels[1].fsm == TRANSMIT
        p.process_r31_command(1 << 19)  # reinit
        assert p.channels[0].fsm == IDLE
        assert p.channels[0].tx_fifo == []

    def test_r31_clr_val_single_pop(self):
        p = self._mk()
        p.channels[0].rx_fifo = [1, 2, 3]
        p.process_r31_command(1 << 24)  # clr_val ch0 — one pop
        assert p.channels[0].rx_fifo == [2, 3]


# ---------------------------------------------------------------------------
class TestHardwareReset:
    """`reset()` — what the UI's HW Reset / per-core Reset must clear."""

    def _mk(self):
        p = PeripheralInterface(pru_clock_mhz=200.0)
        p.registers = mk_regs()
        p.build_channels()
        return p

    def test_channel_reset_clears_tx_and_rx_status(self):
        r = mk_regs()
        set_ch_cfg0(r, 0, tx_frame=8)
        ch = PerifChannel(0, r)
        for i in range(5):
            ch.push_tx(i)              # 5th push → overrun
        ch.tx_go(0.0)
        ch.rx_en = True
        ch.rx_fifo = [0x11, 0x22]
        ch.rx_valid = True
        ch.rx_ovf = True
        ch.rx_eof = True
        assert ch.tx_overrun is True

        ch.reset()

        assert ch.tx_overrun is False
        assert ch.tx_underrun is False
        assert ch.tx_fifo == []
        assert ch.busy is False
        assert ch.fsm == IDLE
        assert ch.tx_status_byte() == 0
        assert ch.rx_en is False
        assert ch.rx_fifo == []
        assert ch.rx_valid is False
        assert ch.rx_ovf is False
        assert ch.rx_eof is False
        assert ch.tx_transitions == [(0.0, 0)]
        assert ch._last_ns == 0.0

    def test_channel_reset_keeps_loopback_wiring(self):
        r = mk_regs()
        ch = PerifChannel(0, r)
        ch.rx_line_source = lambda t: 1
        ch.reset()
        assert ch.rx_line_source is not None

    def test_reset_clears_r31_status_and_busy(self):
        p = self._mk()
        set_ch_cfg0(p.registers, 0, tx_frame=8)
        p.process_r30((1 << 16), wstrb=0x0)      # ch_sel = 1
        for i in range(5):
            p.channels[0].push_tx(i)             # overrun on ch0
        p.channels[0].tx_go(0.0)
        p.channels[2].rx_en = True
        p.channels[2].rx_valid = True
        p.channels[2].rx_ovf = True
        p.advance(1000.0)
        assert p.get_r31_status() != 0

        p.reset()

        assert p.get_r31_status() == 0
        assert p.ch_sel == 0
        # TXCFG busy bits [7:5] follow the now-idle channels.
        txcfg = int.from_bytes(p.registers.read(_BASE + 0x04, 4), "little")
        assert (txcfg >> 5) & 0x7 == 0

    def test_reset_keeps_config_registers(self):
        p = self._mk()
        set_txcfg(p.registers, clk_sel=1, div=39, frac=1)
        set_ch_cfg0(p.registers, 1, wire=100, tx_frame=6, rx_frame=4)
        p.reset()
        assert p.registers.get_tx_div_factor() == 39
        assert p.registers.get_tx_div_factor_frac() == 1
        assert p.registers.get_tx_wire_delay(1) == 100
        assert p.registers.get_tx_frame_size(1) == 6
        assert p.registers.get_rx_frame_size(1) == 4


# ---------------------------------------------------------------------------
class TestClockModeStopConditions:
    """TRM 6.4.5.2.2.3.6.3.3 "Stop Conditions", r30[20:19].

        0  free-running, stop LOW  on last RX frame
        1  free-running, stop HIGH on last RX frame   (reset default)
        2  free-run (only a reinit leaves this mode)
        3  stop HIGH on last TX bit

    The distinction matters for every read transaction: the master sends a
    short request and must keep clocking to shift the response back. A model
    that stops the clock when the TRANSMITTER runs dry can only ever do
    mode 3, so a BiSS-C/EnDat/SSI read never receives anything.
    """

    def _drain_tx(self, ch):
        for _ in range(9):
            ch.tx_bit_edge()

    def _finish_rx_frame(self, ch, frame_bytes):
        ch.arm_rx(True)
        for _ in range(frame_bytes):
            ch._capture_byte(0x00)

    @pytest.mark.parametrize("mode,stop_level", [(0, 0), (1, 1)])
    def test_free_running_until_rx_frame_then_stops(self, mode, stop_level):
        r = mk_regs()
        set_ch_cfg0(r, 0, tx_frame=8, rx_frame=2)
        ch = PerifChannel(0, r)
        ch.set_clk_mode(mode)
        ch.push_tx(0xFF)
        ch.tx_go(0.0)
        self._drain_tx(ch)

        # TX data is gone but the clock must still be running.
        assert ch.fsm == CLKRUN
        assert ch.busy is False
        assert ch.tx_out_en == 0

        self._finish_rx_frame(ch, 2)
        assert ch.rx_eof is True
        assert ch.fsm == IDLE
        assert ch.tx_clk_pin == stop_level

    def test_mode_2_free_runs_past_the_rx_frame(self):
        r = mk_regs()
        set_ch_cfg0(r, 0, tx_frame=8, rx_frame=2)
        ch = PerifChannel(0, r)
        ch.set_clk_mode(2)
        ch.push_tx(0xFF)
        ch.tx_go(0.0)
        self._drain_tx(ch)
        assert ch.fsm == CLKRUN

        self._finish_rx_frame(ch, 2)
        assert ch.rx_eof is True
        assert ch.fsm == CLKRUN          # RX frame does NOT stop mode 2

        ch.tx_reinit()                   # only a reinit leaves free-run
        assert ch.fsm == IDLE
        assert ch.clk_mode == 1          # reinit restores the reset default

    def test_free_running_clock_actually_emits_edges(self):
        """CLKRUN must produce real edges on the ns timeline, not just a state.

        This is what lets a read transaction clock its response in; asserting
        only the FSM label would pass even if the clock were frozen.
        """
        r = mk_regs()
        set_ch_cfg0(r, 0, tx_frame=8, rx_frame=64)
        ch = PerifChannel(0, r)
        ch.set_clk_mode(1)
        ch.push_tx(0xFF)
        ch.tx_go(0.0)
        period = ch.tx_clock_period_ns()
        ch.advance(period * 40)
        assert ch.fsm == CLKRUN

        toggles, prev, t = 0, ch.tx_clk_pin, period * 40
        for _ in range(40):
            t += period
            ch.advance(t)
            if ch.tx_clk_pin != prev:
                toggles += 1
                prev = ch.tx_clk_pin
        assert toggles > 10, "clock is frozen while nominally free-running"
