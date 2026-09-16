"""Tests for four RX-lifecycle defects (perif/perif_channel.py).

Each test cites the TRM section and fails on the unmodified code, proving
the defect. See commit message for before/after failure output.

Claim: simulator-tested only, not silicon-validated.
"""
import os

import pytest

import simulator
from perif.perif_registers import PerifRegisters
from perif.perif_channel import PerifChannel

_BASE = 0x260E0


def _w(regs, off, val):
    regs.write(_BASE + off, (val & 0xFFFFFFFF).to_bytes(4, "little"))


def mk_regs():
    return PerifRegisters(_BASE)


def set_rxcfg(regs, sample_size=7, sb_pol=1, clk_sel=1, div=0, frac=0):
    val = (sample_size & 0x7) | ((sb_pol & 1) << 3) | ((clk_sel & 1) << 4)
    val |= ((frac & 1) << 15) | ((div & 0xFFFF) << 16)
    _w(regs, 0x00, val)


def set_ch_cfg0(regs, ch, wire=0, tx_frame=0, rx_frame=0, ovr_en=0, sw_clk=0, swap=0):
    val = (wire & 0x7FF) | ((tx_frame & 0x1F) << 11) | ((rx_frame & 0xFFF) << 16)
    val |= ((ovr_en & 1) << 29) | ((sw_clk & 1) << 30) | ((swap & 1) << 31)
    _w(regs, 0x08 + ch * 8, val)


def set_ch_cfg1(regs, ch, tst=0, rx_en_cnt=0):
    val = (tst & 0xFFFF) | ((rx_en_cnt & 0xFFFF) << 16)
    _w(regs, 0x0C + ch * 8, val)


def _feed(ch, bits):
    for b in bits:
        ch.rx_sample_edge(b)


# ---------------------------------------------------------------------------
# Defect 1: arm_rx(False) must reset all counters and flags (TRM SPRUIM2J
# 6.4.5.2.2.3.6.4.4 step 7 and Table 6-80 rx_en<m>).
# ---------------------------------------------------------------------------
def test_defect1_arm_rx_disable_resets_all_via_firmware():
    """TRM SPRUIM2J 6.4.5.2.2.3.6.4.4 step 7 / Table 6-80: clearing rx_en resets all.

    Firmware disables RX via R30 byte-3 (bits [26:24]), then re-enables. The
    stale FIFO byte, latched overflow, and capture counters must be cleared so
    the next frame starts clean. Pure unit test cannot show the R30 decode path.
    """
    s = simulator.Simulator()
    s.gpcfg_write("pru0", 1)
    ch = s.cores["pru0"].io_port.perif.channels[0]
    # Seed stale state as if a previous frame left data/flags behind
    ch.rx_en = True
    ch.rx_fifo = [0xAA]
    ch.rx_valid = True
    ch.rx_ovf = True
    ch.rx_eof = True
    ch._rx_started = True
    ch._rx_shift = 0xFF
    ch._rx_sample_cnt = 3
    ch._rx_byte_cnt = 5
    ch._rx_next_edge_ns = 123.0

    asm = """
        ldi r30.b3, 0x00   ; disable RX ch0 (clear bit 24) via byte3 strobe
        ldi r30.b3, 0x01   ; re-enable RX ch0 (set bit 24)
    """
    assert s.load("pru0", asm) == []
    s.step("pru0", 1)  # disable
    # After disable, TRM says all counters/flags reset
    assert ch.rx_en is False
    assert ch.rx_fifo == []
    assert ch.rx_valid is False
    assert ch.rx_ovf is False
    assert ch.rx_eof is False
    assert ch._rx_started is False
    assert ch._rx_shift == 0
    assert ch._rx_sample_cnt == 0
    assert ch._rx_byte_cnt == 0
    assert ch._rx_next_edge_ns is None
    s.step("pru0", 1)  # re-enable
    assert ch.rx_en is True
    assert ch.rx_fifo == []  # still empty at start of next frame
    assert ch.rx_valid is False
    assert ch.rx_ovf is False
    assert ch._rx_byte_cnt == 0
    assert ch._rx_started is False


def test_defect1_unit_arm_rx_disable_resets_all():
    """TRM SPRUIM2J 6.4.5.2.2.3.6.4.4 step 7 / Table 6-80: disable resets all."""
    r = mk_regs()
    ch = PerifChannel(0, r)
    ch.arm_rx(True)
    ch.rx_fifo = [0x55]
    ch.rx_valid = True
    ch.rx_ovf = True
    ch.rx_eof = True
    ch._rx_started = True
    ch._rx_shift = 0xAB
    ch._rx_sample_cnt = 2
    ch._rx_byte_cnt = 4
    ch._rx_next_edge_ns = 99.0
    ch.arm_rx(False)
    assert ch.rx_fifo == []
    assert ch.rx_valid is False
    assert ch.rx_ovf is False
    assert ch.rx_eof is False
    assert ch._rx_started is False
    assert ch._rx_shift == 0
    assert ch._rx_sample_cnt == 0
    assert ch._rx_byte_cnt == 0
    assert ch._rx_next_edge_ns is None


# ---------------------------------------------------------------------------
# Defect 2: Optional RX frame-size auto shut-off (TRM 6.4.5.2.2.3.6 feature list).
# ---------------------------------------------------------------------------
def test_defect2_auto_shutoff_stops_capturing_after_eof():
    """TRM 6.4.5.2.2.3.6 feature list: optional RX frame-size auto shut-off.

    When enabled, reaching rx_frame_size must fully disarm the receiver
    (rx_en and _rx_started) so further bytes are not captured. Default is
    disabled (explicit rx_auto_shutoff flag; no register bit in this block).
    """
    r = mk_regs()
    set_rxcfg(r, sample_size=7, sb_pol=1)
    set_ch_cfg0(r, 0, rx_frame=2)
    ch = PerifChannel(0, r)
    ch.rx_auto_shutoff = True
    ch.arm_rx(True)
    # Feed first byte: start bit + 8 bits
    _feed(ch, [1] + [(0x11 >> i) & 1 for i in range(7, -1, -1)])
    ch._rx_started = False  # reset start detect between bytes (as hardware would after shift?)
    # Actually _rx_started stays True after start; we need to simulate per-byte start again.
    # Simpler: directly call _capture_byte for second byte to trigger EOF.
    # Feed second byte that completes the 2-byte frame
    ch._rx_started = True
    ch._capture_byte(0x22)
    # After 2nd byte, EOF should be set and receiver auto-disabled
    assert ch.rx_eof is True
    assert ch.rx_en is False
    assert ch._rx_started is False
    assert ch._rx_next_edge_ns is None
    # FIFO should retain the 2 bytes, valid stays true, eof true
    assert ch.rx_fifo == [0x11, 0x22]
    # Further capture must be ignored because rx_en is False
    ch.rx_sample_edge(1)  # would be start bit if still enabled
    # No new byte should appear
    assert len(ch.rx_fifo) == 2

    # When disabled (default), receiver must keep capturing past EOF
    r2 = mk_regs()
    set_rxcfg(r2, sample_size=7, sb_pol=1)
    set_ch_cfg0(r2, 0, rx_frame=2)
    ch2 = PerifChannel(0, r2)
    # default rx_auto_shutoff is False
    assert ch2.rx_auto_shutoff is False
    ch2.arm_rx(True)
    ch2._capture_byte(0x11)
    ch2._capture_byte(0x22)
    assert ch2.rx_eof is True
    assert ch2.rx_en is True  # stays enabled when auto shut-off disabled
    ch2._capture_byte(0x33)
    assert len(ch2.rx_fifo) == 3  # kept capturing


# ---------------------------------------------------------------------------
# Defect 3: _rx_byte_cnt not reset while rx_frame_size==0 (TRM 6.4.5.2.2.3.6).
# Firmware that arms RX before programming RX_FRAME_SIZE (normal BiSS-C path)
# carries an arbitrary count into the first real frame.
# ---------------------------------------------------------------------------
def test_defect3_byte_cnt_reset_while_frame_size_zero():
    """TRM 6.4.5.2.2.3.6: RX frame-size zero must keep byte counter at zero.

    Must not reset unconditionally on every captured byte (trap: would break
    the frame_size !=0 EOF path which currently works).
    """
    r = mk_regs()
    set_rxcfg(r, sample_size=7, sb_pol=1)
    set_ch_cfg0(r, 0, rx_frame=0)  # zero = no fixed frame
    ch = PerifChannel(0, r)
    ch.arm_rx(True)
    # Simulate stale count carried from previous use
    ch._rx_byte_cnt = 5
    ch._capture_byte(0xAA)
    # While frame_size==0, counter must be reset to 0, not incremented to 6
    assert ch._rx_byte_cnt == 0
    assert ch.rx_eof is False

    # Now program a real frame size and verify EOF at correct boundary
    set_ch_cfg0(r, 0, rx_frame=2)
    # Counter should still be 0 at start of the real frame
    assert ch._rx_byte_cnt == 0
    ch._capture_byte(0x11)
    assert ch._rx_byte_cnt == 1
    assert ch.rx_eof is False
    ch._capture_byte(0x22)
    assert ch.rx_eof is True
    assert ch._rx_byte_cnt == 0
    # Ensure third byte starts a new frame correctly (counter 1, not EOF)
    # Need to clear eof as firmware would after reading? In our model eof stays
    # until next arm or disable; but byte_cnt should be 1 after next byte.
    # Simulate firmware clearing eof by re-arming? For this test, manually clear.
    ch.rx_eof = False
    ch._capture_byte(0x33)
    assert ch._rx_byte_cnt == 1
    assert ch.rx_eof is False

    # Verify the working path is not broken: frame_size=4 should need 4 bytes for EOF
    r3 = mk_regs()
    set_rxcfg(r3, sample_size=7, sb_pol=1)
    set_ch_cfg0(r3, 0, rx_frame=4)
    ch3 = PerifChannel(0, r3)
    ch3.arm_rx(True)
    for i in range(3):
        ch3._capture_byte(0x10 + i)
        assert ch3.rx_eof is False, f"EOF premature at byte {i+1}"
    ch3._capture_byte(0x13)
    assert ch3.rx_eof is True


# ---------------------------------------------------------------------------
# Defect 4: RX auto-arm via RX_EN_COUNTER (TRM 6.4.5.2.2.3.6.3.2.2).
# ---------------------------------------------------------------------------
def test_defect4_auto_arm_fires_after_delay():
    """TRM 6.4.5.2.2.3.6.3.2.2: RX auto-arm via RX_EN_COUNTER (CHnCFG1[31:16]).

    Hardware auto-enables RX after the programmed delay from the last TX bit.
    Must not fire immediately at end of TX; the delay field is the point.
    """
    r = mk_regs()
    set_ch_cfg0(r, 0, tx_frame=8)
    # RX_EN_COUNTER = 50 => delay = ceil(50/5)*5ns = 10 cycles *5ns=50ns at 200MHz
    set_ch_cfg1(r, 0, tst=0, rx_en_cnt=50)
    ch = PerifChannel(0, r, core_clock_mhz=200.0)
    ch.push_tx(0xB6)
    ch.tx_go(0.0)
    # Drive TX to completion via ns timeline (needs wire/tst=0, so immediate transmit)
    # TX of 8 bits at tx_clock_period_ns: with default div=0, period=5ns? Actually default TX div 0 => 200MHz/1=200MHz => 5ns period.
    # We'll advance until busy clears.
    # _finish_frame will have scheduled auto-arm deadline
    # Before fix, deadline never set, so rx_en stays False forever.
    assert ch.busy is True
    # Advance just enough to finish the 8-bit frame: 8 cycles *5ns =40ns plus some
    # But easier: call advance stepwise until busy False
    ns = 0.0
    while ch.busy and ns < 1000:
        ns += 5.0
        ch.advance(ns)
    assert ch.busy is False
    # At this point TX finished, auto-arm should be pending with deadline = _phase_end_ns + delay
    deadline = ch._rx_auto_arm_deadline_ns
    assert deadline is not None, "auto-arm not scheduled"
    # Immediately after TX, before delay, RX must still be disabled
    assert ch.rx_en is False
    # Advance to just before deadline
    ch.advance(deadline - 1)
    assert ch.rx_en is False, "auto-arm fired too early (should respect delay)"
    # Advance to deadline
    ch.advance(deadline)
    assert ch.rx_en is True, "auto-arm did not fire at programmed delay"
    assert ch._rx_auto_arm_deadline_ns is None

    # Negative case: zero delay means disabled, never auto-arms
    r0 = mk_regs()
    set_ch_cfg0(r0, 0, tx_frame=8)
    set_ch_cfg1(r0, 0, tst=0, rx_en_cnt=0)
    ch0 = PerifChannel(0, r0, core_clock_mhz=200.0)
    ch0.push_tx(0xAA)
    ch0.tx_go(0.0)
    ns = 0.0
    while ch0.busy and ns < 1000:
        ns += 5.0
        ch0.advance(ns)
    assert ch0.busy is False
    assert ch0._rx_auto_arm_deadline_ns is None
    ch0.advance(ns + 1000)
    assert ch0.rx_en is False, "zero RX_EN_COUNTER should not auto-arm"


# ---------------------------------------------------------------------------
# Defect 4, continued: the arms above read the deadline back out of the model
# and then check the model against it, so they hold for *any* anchor point.
# These three pin down the parts that self-consistency cannot.
# ---------------------------------------------------------------------------

def _run_tx_to_completion(ch, limit_ns=1000.0):
    """Advance the ns timeline until the TX frame finishes. Returns end time."""
    ns = 0.0
    while ch.busy and ns < limit_ns:
        ns += 5.0
        ch.advance(ns)
    assert ch.busy is False, "TX frame did not finish within the budget"
    return ns


def test_defect4_deadline_is_measured_from_the_last_tx_bit():
    """TRM 6.4.5.2.2.3.6.3.2.2 anchors the counter at the *last* TX bit.

    > ... is used to program a delay between the last TX bit sent and when
    > the RX_EN is set.

    The natural place to notice RX_EN_COUNTER is on entering TRANSMIT, which
    would anchor the delay at the FIRST bit instead. Both anchors schedule a
    deadline and both fire once, so a test that reads the deadline back out of
    the channel cannot tell them apart. This one computes the expected value
    independently: a frame that occupies the wire until t_end must arm at
    t_end + delay, which is strictly later than the delay alone.
    """
    r = mk_regs()
    set_ch_cfg0(r, 0, tx_frame=8)
    set_ch_cfg1(r, 0, tst=0, rx_en_cnt=50)
    ch = PerifChannel(0, r, core_clock_mhz=200.0)
    delay_ns = ch._delay_ns(50)

    ch.push_tx(0xB6)
    ch.tx_go(0.0)
    _run_tx_to_completion(ch)

    # _phase_end_ns is the timestamp of the last emitted bit - the same instant
    # _finish_frame() hands to _record_out_en for the output-enable release.
    last_bit_ns = ch._phase_end_ns
    assert last_bit_ns > 0.0, "frame must occupy real time for this to discriminate"

    assert ch._rx_auto_arm_deadline_ns == pytest.approx(last_bit_ns + delay_ns)
    assert ch._rx_auto_arm_deadline_ns > delay_ns, (
        "deadline anchored at the first TX bit, not the last: the receiver "
        "would arm while the master is still driving the line"
    )


def test_defect4_reset_clears_a_pending_deadline():
    """A scheduled auto-arm must not survive reset() and fire into the next run."""
    r = mk_regs()
    set_ch_cfg0(r, 0, tx_frame=8)
    set_ch_cfg1(r, 0, tst=0, rx_en_cnt=50)
    ch = PerifChannel(0, r, core_clock_mhz=200.0)
    ch.push_tx(0xB6)
    ch.tx_go(0.0)
    _run_tx_to_completion(ch)
    assert ch._rx_auto_arm_deadline_ns is not None

    ch.reset()
    assert ch._rx_auto_arm_deadline_ns is None
    assert ch.rx_auto_shutoff is False

    # And it stays disarmed however far the timeline runs.
    ch.advance(100_000.0)
    assert ch.rx_en is False


def test_defect4_pending_deadline_survives_snapshot_restore():
    """snapshot()/restore() must carry the new fields, or a save/restore drops
    a pending auto-arm and the channel never receives after reload."""
    r = mk_regs()
    set_ch_cfg0(r, 0, tx_frame=8)
    set_ch_cfg1(r, 0, tst=0, rx_en_cnt=50)
    ch = PerifChannel(0, r, core_clock_mhz=200.0)
    ch.push_tx(0xB6)
    ch.tx_go(0.0)
    _run_tx_to_completion(ch)
    ch.rx_auto_shutoff = True
    deadline = ch._rx_auto_arm_deadline_ns
    assert deadline is not None

    snap = ch.snapshot()

    fresh = PerifChannel(0, r, core_clock_mhz=200.0)
    fresh.restore(snap)
    assert fresh._rx_auto_arm_deadline_ns == pytest.approx(deadline)
    assert fresh.rx_auto_shutoff is True

    # The restored channel still arms at the original deadline.
    fresh.advance(deadline - 1.0)
    assert fresh.rx_en is False
    fresh.advance(deadline)
    assert fresh.rx_en is True
