# tests/test_perif_drift_experiment.py
"""TX/RX firmware over the PRU0->PRU1 perif loopback + clock-drift experiment."""
from pathlib import Path

import pytest

from simulator import Simulator
from tools.perif_drift_report import run_drift

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


def test_rx_firmware_captures_pattern(tmp_path):
    """Zero drift: RX firmware reconstructs a long pattern intact."""
    count, first_bad = run_drift(0.0, 1500, str(tmp_path))
    assert count == 1500
    assert first_bad is None


@pytest.mark.parametrize("ppm", [200.0, 500.0, 1000.0])
def test_drift_breaks_reception_at_expected_length(ppm, tmp_path):
    """Failure length scales ~1/ppm (physics band: slip of ~a bit period)."""
    count, first_bad = run_drift(ppm, 1000, str(tmp_path))
    assert first_bad is not None, f"no corruption at {ppm} ppm within {count} bytes"
    bits = first_bad * 8
    # Slip-to-failure ~ initial_phase_margin / ppm; margin in (0,1) bit.
    lo = 0.05 / (ppm * 1e-6)
    hi = 2.0 / (ppm * 1e-6)
    assert lo <= bits <= hi, f"{ppm} ppm failed at {bits} bits, outside [{lo:.0f},{hi:.0f}]"


def test_drift_failure_length_monotonic(tmp_path):
    """More drift -> earlier failure."""
    results = {}
    for ppm in (200.0, 1000.0):
        _, first_bad = run_drift(ppm, 1000, str(tmp_path))
        assert first_bad is not None
        results[ppm] = first_bad
    assert results[1000.0] < results[200.0]
