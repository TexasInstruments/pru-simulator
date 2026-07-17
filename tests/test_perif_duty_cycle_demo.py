# tests/test_perif_duty_cycle_demo.py
"""Tests for source/perif_duty_cycle_sweep.asm — 125 Mbit duty-cycle sweep demo."""
import os
import simulator

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ASM_PATH = os.path.join(_ROOT, "source", "perif_duty_cycle_sweep.asm")
_CFG_PATH = os.path.join(_ROOT, "memory_perif_125mbit_demo.cfg")

_EXPECTED_WIDTHS_NS = [8.0, 16.0, 24.0, 32.0, 40.0, 48.0, 56.0, 64.0]  # 12.5%..100%


def _run_demo():
    s = simulator.Simulator(config_path=_CFG_PATH)
    with open(_ASM_PATH) as f:
        errors = s.load("pru0", f.read())
    assert errors == []
    pru = s.cores["pru0"]
    for _ in range(4000):
        if pru.halted:
            break
        pru.step()
    return s, pru


def test_demo_config_yields_125mbit_clock():
    s = simulator.Simulator(config_path=_CFG_PATH)
    assert s._pru_clock_mhz == 250.0


def test_demo_assembles_and_halts_cleanly():
    s, pru = _run_demo()
    assert pru.halted is True
    ch0 = pru.io_port.perif.channels[0]
    assert ch0.tx_fifo == []
    assert ch0.busy is False
    assert ch0.tx_overrun is False
    assert ch0.tx_underrun is False


def test_gpcfg_and_clock_config_applied():
    s, pru = _run_demo()
    assert s._perif["pru0"].enabled is True          # GPCFG mux -> Peripheral mode
    regs = s._perif["pru0"].registers
    assert regs.get_tx_clk_sel() == 1                # core/OCP clock source
    assert regs.get_tx_div_factor() == 1             # N = 2 -> 250/2 = 125 MHz
    assert regs.get_tx_frame_size(0) == 0            # continuous mode (live FIFO refill)


def test_duty_cycle_pulse_widths_sweep_0_to_100_percent():
    """Decode the recorded TX line into pulses; widths must sweep 12.5%..100%
    of the 64 ns (8-bit @ 125 Mbit) byte period. The 0% byte produces no
    pulse at all since the line never leaves its idle-low state."""
    _, pru = _run_demo()
    ch0 = pru.io_port.perif.channels[0]
    transitions = ch0.tx_transitions

    # Pair up rising (0->1) and falling (1->0) edges into pulses.
    pulses = []
    pending_rise = None
    for t_ns, val in transitions:
        if val == 1:
            pending_rise = t_ns
        elif val == 0 and pending_rise is not None:
            pulses.append(t_ns - pending_rise)
            pending_rise = None

    assert len(pulses) == 8                          # 0% duty produces no pulse
    assert pulses == _EXPECTED_WIDTHS_NS

    duty_percent = [w / 64.0 * 100.0 for w in pulses]
    assert duty_percent == [12.5, 25.0, 37.5, 50.0, 62.5, 75.0, 87.5, 100.0]


def test_stream_is_gapless_across_all_9_bytes():
    """Continuous mode + half-empty refill must produce zero dead time
    between bytes: the 9-byte stream spans exactly 9*64 = 576 ns from the
    first bit to the last, with no underrun/overrun."""
    _, pru = _run_demo()
    ch0 = pru.io_port.perif.channels[0]
    assert ch0.tx_overrun is False
    assert ch0.tx_underrun is False
    transitions = ch0.tx_transitions
    go_ns = transitions[1][0] - 64.0     # byte0 (silent) precedes the 1st visible edge
    end_ns = transitions[-1][0]
    assert end_ns - go_ns == 576.0
