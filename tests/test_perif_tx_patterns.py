# tests/test_perif_tx_patterns.py
"""Selectable TX test patterns — `source/perif_tx_patterns.asm`.

Each PATTERN value is checked end-to-end over the ch0 loopback rather than by
reading the DRAM table, so the pre-shift start-bit framing is exercised too: if
the carry chain were wrong the receiver would byte-align one bit off and every
value below would come out rotated.
"""
from pathlib import Path

import pytest

from simulator import Simulator

_ASM = Path(__file__).parent.parent / "source" / "perif_tx_patterns.asm"

TXCFG_PRU0 = 0x260E4
RXCFG_PRU1 = 0x26100
TXCFG_VAL = 0x00070010     # clk_sel=core, div=7 -> 25 MHz bit clock
RXCFG_VAL = 0x0007001F     # sample_size=7, sb_pol=1, clk_sel=core, div=7

WALKING_1 = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80]
WALKING_0 = [0xFE, 0xFD, 0xFB, 0xF7, 0xEF, 0xDF, 0xBF, 0x7F]
SEQUENCE = [0x00, 0xFF, 0xAA, 0x55] + WALKING_1 + WALKING_0


def _receive(pattern, count, config):
    """Assemble the firmware with PATTERN=*pattern*, return *count* RX bytes."""
    text = _ASM.read_text().replace("PATTERN .set 0",
                                    f"PATTERN .set {pattern}", 1)
    sim = Simulator(config_path=config)
    sim.gpcfg_write("pru0", 1)
    sim.gpcfg_write("pru1", 1)
    sim.write_perif_register("pru0", TXCFG_PRU0, TXCFG_VAL)
    sim.write_perif_register("pru1", RXCFG_PRU1, RXCFG_VAL)
    sim.perif_loopback(0, True)
    assert sim.load("pru0", text) == []

    ch = sim._perif["pru1"].channels[0]
    ch.arm_rx(True)
    received = []
    for _ in range(2000):                     # chunked lockstep
        sim.step("pru0", 100)
        sim.cores["pru1"].io_port.perif.advance_cycles(
            sim.cores["pru0"].counters.cycles)
        while ch.rx_valid:
            received.append(ch.rx_head())
            ch.clr_val()
        if len(received) >= count:
            break
    assert len(received) >= count, f"only {len(received)} bytes received"
    return received[:count]


def test_default_pattern_is_the_sequence():
    """The shipped file must default to PATTERN 0 — the whole point is that
    loading it shows the test patterns without editing anything first."""
    assert "PATTERN .set 0" in _ASM.read_text()


def test_sequence_then_counter(nominal_config):
    """PATTERN 0: one byte of each pattern, then the counter forever."""
    rx = _receive(0, len(SEQUENCE) + 40, nominal_config)
    assert rx[:len(SEQUENCE)] == SEQUENCE
    tail = rx[len(SEQUENCE):]
    assert tail == [i & 0xFF for i in range(len(tail))]


@pytest.mark.parametrize("pattern,expected", [
    (1, [0x00]),
    (2, [0xFF]),
    (3, [0xAA]),
    (4, [0x55]),
    (5, WALKING_1),
    (6, WALKING_0),
])
def test_single_pattern_repeats_forever(pattern, expected, nominal_config):
    """PATTERN 1..6 loop their own table window and never reach the counter."""
    rx = _receive(pattern, 40, nominal_config)
    assert rx == [expected[i % len(expected)] for i in range(len(rx))]


def test_counter_only(nominal_config):
    """PATTERN 7 skips the table entirely — same stream as the original
    `perif_tx_pattern.asm`, which the drift tooling depends on."""
    rx = _receive(7, 40, nominal_config)
    assert rx == [i & 0xFF for i in range(len(rx))]
