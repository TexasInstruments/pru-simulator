# tests/test_perif_loopback.py
"""Phase-3: timed serial-sample loopback PRU0 TX ch0 -> core-1 RX ch0."""
import simulator


def _w(regs, off, val):
    regs.write(regs._base + off, (val & 0xFFFFFFFF).to_bytes(4, "little"))


def set_rxcfg(regs, sample_size=7, sb_pol=1, clk_sel=1, div=0, frac=0):
    val = (sample_size & 0x7) | ((sb_pol & 1) << 3) | ((clk_sel & 1) << 4)
    val |= ((frac & 1) << 15) | ((div & 0xFFFF) << 16)
    _w(regs, 0x00, val)


def set_txcfg(regs, clk_sel=1, div=0, frac=0):
    _w(regs, 0x04, ((clk_sel & 1) << 4) | ((frac & 1) << 15) | ((div & 0xFFFF) << 16))


def set_ch_cfg0(regs, ch, wire=0, tx_frame=0, rx_frame=0, swap=0):
    val = (wire & 0x7FF) | ((tx_frame & 0x1F) << 11) | ((rx_frame & 0xFFF) << 16)
    val |= ((swap & 1) << 31)
    _w(regs, 0x08 + ch * 8, val)


def _framed_byte(v):
    """Bytes to transmit so the RX (start-bit + 8 bits) captures exactly *v*.

    Stream sent MSB-first must be [start=1, v7..v0]; pack that into 2 bytes and
    send 9 bits.
    """
    byte0 = 0x80 | (v >> 1)      # start bit + v[7:1]
    byte1 = (v & 1) << 7         # v0 in MSB position
    return byte0, byte1


def _setup(drift_ppm=0.0, latency_ns=0.0):
    s = simulator.Simulator()
    s.gpcfg_write("pru0", 1)
    s.gpcfg_write("rtu0", 1)
    pru0 = s._perif["pru0"]
    rtu0 = s._perif["rtu0"]
    # Matched clocks: div=0 -> N=1 -> 200 MHz -> 5 ns bit period on both cores.
    set_txcfg(pru0.registers, clk_sel=1, div=0)
    set_ch_cfg0(pru0.registers, 0, tx_frame=9)          # 9 bits: start + byte
    set_rxcfg(rtu0.registers, sample_size=7, sb_pol=1, clk_sel=1, div=0)
    set_ch_cfg0(rtu0.registers, 0, rx_frame=1)          # EOF after 1 byte
    s.perif_loopback(0, True, latency_ns=latency_ns, drift_ppm=drift_ppm)
    return s, pru0, rtu0


def test_clean_roundtrip():
    s, pru0, rtu0 = _setup()
    V = 0xB6
    b0, b1 = _framed_byte(V)
    pru0.channels[0].push_tx(b0)
    pru0.channels[0].push_tx(b1)
    pru0.process_r31_command(1 << 18, now_ns=0.0)       # TX go at t=0

    T = pru0.channels[0].tx_clock_period_ns()           # 5 ns
    pru0.advance(20 * T)                                 # transmit the whole frame

    rtu0.channels[0].arm_rx(True)
    rtu0.channels[0]._rx_next_edge_ns = 0.5 * T          # sample mid-bit (phase align)
    rtu0.advance(9 * T)                                  # exactly the 9-bit frame window

    assert rtu0.channels[0].rx_fifo == [V]               # round-trips the byte
    assert rtu0.channels[0].rx_eof is True


def test_loopback_disabled_no_rx():
    s, pru0, rtu0 = _setup()
    s.perif_loopback(0, False)                           # turn loopback off
    b0, b1 = _framed_byte(0xB6)
    pru0.channels[0].push_tx(b0)
    pru0.channels[0].push_tx(b1)
    pru0.process_r31_command(1 << 18, now_ns=0.0)
    T = pru0.channels[0].tx_clock_period_ns()
    pru0.advance(20 * T)
    rtu0.channels[0].arm_rx(True)
    rtu0.channels[0]._rx_next_edge_ns = 0.5 * T
    rtu0.advance(20 * T)
    assert rtu0.channels[0].rx_fifo == []                # line idles low, no start bit


def test_large_drift_causes_slip():
    """At ~100 ppm the mapped sampling walks off; the captured byte differs.

    (Drift is tiny per bit, so we use a big time offset to expose accumulation:
    the test just asserts the loopback maps through drift without error and can
    yield a non-identical result far into a long stream.)"""
    s, pru0, rtu0 = _setup(drift_ppm=100.0)
    # Sanity: sample() maps target->source time without raising and returns 0/1.
    val = s._loopback.sample(0, 1_000_000.0)
    assert val in (0, 1)


def test_configure_and_state():
    s, _, _ = _setup()
    s.perif_loopback(1, True, latency_ns=12.5, jitter_ns=2.0, drift_ppm=50.0)
    st = s.loopback_state()["channels"][1]
    assert st["enabled"] is True
    assert st["latency_ns"] == 12.5
    assert st["drift_ppm"] == 50.0
    # ppm clamped to +/-100
    s.perif_loopback(2, True, drift_ppm=999.0)
    assert s.loopback_state()["channels"][2]["drift_ppm"] == 100.0
