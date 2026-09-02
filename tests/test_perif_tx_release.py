"""Timestamped transmit output-enable, and the release time derived from it.

Half-duplex encoder protocols are specified on how fast a driver STOPS driving,
not only on the data it drove. `tx_out_en` existed as a level but carried no
timestamp, so there was nothing to measure a release window against — and, more
importantly, nothing a bus-contention check could be built on.

These tests use the peripheral directly rather than any protocol, so they say
what the channel does and nothing about what any particular device expects.
"""

import simulator


def _w(regs, off, val):
    regs.write(regs._base + off, (val & 0xFFFFFFFF).to_bytes(4, "little"))


def set_txcfg(regs, clk_sel=1, div=0, frac=0):
    _w(regs, 0x04, ((clk_sel & 1) << 4) | ((frac & 1) << 15) | ((div & 0xFFFF) << 16))


def set_ch_cfg0(regs, ch, tx_frame=0, rx_frame=0):
    _w(regs, 0x08 + ch * 8, ((tx_frame & 0x1F) << 11) | ((rx_frame & 0xFFF) << 16))


def _channel(tx_frame_bits=16):
    sim = simulator.Simulator()
    sim.gpcfg_write("pru0", 1)                       # MUX_PERIF
    perif = sim._perif["pru0"]
    set_txcfg(perif.registers, clk_sel=1, div=0)
    set_ch_cfg0(perif.registers, 0, tx_frame=tx_frame_bits)
    return sim, perif, perif.channels[0]


def _run_one_frame(perif, channel, payload=(0xA5, 0x3C)):
    for byte in payload:
        channel.push_tx(byte)
    perif.process_r31_command(1 << 18, now_ns=0.0)   # tx go
    period = channel.tx_clock_period_ns()
    t = 0.0
    for _ in range(400):
        t += period / 4.0
        channel.advance(t)
        if channel.fsm != "TRANSMIT" and channel.tx_out_en == 0 and t > period:
            break
    return t


def test_nothing_is_recorded_before_the_first_transmit():
    _, _, channel = _channel()
    assert channel.tx_out_en_transitions == []
    assert channel.tx_release_ns() is None


def test_one_frame_records_exactly_one_assert_and_one_release():
    """The log holds TRANSITIONS, not samples: a repeated level must not append."""
    _, perif, channel = _channel()
    _run_one_frame(perif, channel)

    levels = [v for _, v in channel.tx_out_en_transitions]
    assert levels == [1, 0], f"expected drive then release, got {levels}"


def test_the_release_timestamp_is_ordered_after_the_assert():
    _, perif, channel = _channel()
    _run_one_frame(perif, channel)

    (t_assert, hi), (t_release, lo) = channel.tx_out_en_transitions
    assert hi == 1 and lo == 0
    assert t_release > t_assert


def test_release_time_is_measured_from_the_last_transmitted_bit():
    """`tx_release_ns` is the gap between the final data transition and the
    driver letting go — which is the quantity a release-window limit is
    written against."""
    _, perif, channel = _channel()
    _run_one_frame(perif, channel)

    release = channel.tx_release_ns()
    assert release is not None
    assert release >= 0.0

    t_release = max(t for t, v in channel.tx_out_en_transitions if v == 0)
    last_data = max(t for t, _ in channel.tx_transitions if t <= t_release)
    assert release == t_release - last_data


def test_release_is_none_while_the_driver_is_still_driving():
    """A frame in flight has no release time yet, and must not invent one."""
    _, perif, channel = _channel()
    channel.push_tx(0xFF)
    channel.push_tx(0x00)
    perif.process_r31_command(1 << 18, now_ns=0.0)
    channel.advance(channel.tx_clock_period_ns() / 2.0)

    assert channel.tx_out_en == 1
    assert channel.tx_release_ns() is None


def test_a_second_frame_appends_a_second_pair():
    """Consecutive transactions must each be visible, not collapse into one."""
    _, perif, channel = _channel()
    end = _run_one_frame(perif, channel)

    for byte in (0x11, 0x22):
        channel.push_tx(byte)
    perif.process_r31_command(1 << 18, now_ns=end)
    period = channel.tx_clock_period_ns()
    t = end
    for _ in range(400):
        t += period / 4.0
        channel.advance(t)

    levels = [v for _, v in channel.tx_out_en_transitions]
    assert levels == [1, 0, 1, 0], levels
    times = [t for t, _ in channel.tx_out_en_transitions]
    assert times == sorted(times), "timestamps must be monotonic"


def test_reset_clears_the_history():
    _, perif, channel = _channel()
    _run_one_frame(perif, channel)
    assert channel.tx_out_en_transitions

    channel.reset()
    assert channel.tx_out_en_transitions == []
    assert channel.tx_release_ns() is None
