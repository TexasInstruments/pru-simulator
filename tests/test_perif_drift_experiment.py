# tests/test_perif_drift_experiment.py
"""TX/RX firmware over the PRU0->PRU1 perif loopback + clock-drift experiment."""
from pathlib import Path

from simulator import Simulator

_SRC = Path(__file__).parent.parent / "source"

TXCFG_PRU0 = 0x260E4
RXCFG_PRU1 = 0x26100
TXCFG_VAL = 0x00070010     # clk_sel=core, div=7 -> 25 MHz bit clock
RXCFG_VAL = 0x0007001F     # sample_size=7, sb_pol=1, clk_sel=core, div=7


def _setup_perif(sim):
    sim.gpcfg_write("pru0", 1)
    sim.gpcfg_write("pru1", 1)
    sim.write_perif_register("pru0", TXCFG_PRU0, TXCFG_VAL)
    sim.write_perif_register("pru1", RXCFG_PRU1, RXCFG_VAL)
    sim.perif_loopback(0, True)


def test_tx_pattern_roundtrip_python_rx():
    """TX firmware streams the counter pattern; a Python-armed RX decodes it."""
    sim = Simulator()
    _setup_perif(sim)
    errors = sim.load("pru0", (_SRC / "perif_tx_pattern.asm").read_text())
    assert errors == []

    ch = sim._perif["pru1"].channels[0]
    ch.arm_rx(True)

    received = []
    for _ in range(200):                      # chunked lockstep
        sim.step("pru0", 100)
        sim.cores["pru1"].io_port.perif.advance_cycles(
            sim.cores["pru0"].counters.cycles)
        while ch.rx_valid:
            received.append(ch.rx_head())
            ch.clr_val()
        if len(received) >= 32:
            break

    assert len(received) >= 32
    assert received[:32] == [i & 0xFF for i in range(32)]
