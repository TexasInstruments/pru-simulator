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


def _load_demo_firmware(sim):
    assert sim.load("pru0", (_SRC / "perif_tx_pattern.asm").read_text()) == []
    assert sim.load("pru1", (_SRC / "perif_rx_capture.asm").read_text()) == []


def test_step_paced_produces_clean_capture():
    """Regression: instruction-lockstep corrupted the capture within bytes
    (observed 00 01 81 01 82 ...); step_paced must yield the clean pattern."""
    sim = Simulator()
    _setup_perif(sim)
    _load_demo_firmware(sim)
    for _ in range(60):
        sim.step_paced("pru0", "pru1", 1000)
    count = int.from_bytes(bytes(sim.memory_read(0x3FF8, 4)), "little")
    assert count >= 64
    data = list(sim.memory_read(0x2000, 64))
    assert data == [i & 0xFF for i in range(64)]


def test_step_paced_follow_never_leads():
    sim = Simulator()
    _setup_perif(sim)
    _load_demo_firmware(sim)
    for _ in range(50):
        sim.step_paced("pru0", "pru1", 100)
        t0 = sim._perif["pru0"]._now_ns
        t1 = sim._perif["pru1"]._now_ns
        assert t1 <= t0, f"follow leads: pru1 {t1} > pru0 {t0}"


def test_tx_firmware_self_configures():
    """No host setup at all: the TX firmware writes GPCFG0/TXCFG/CH0CFG0."""
    sim = Simulator()
    assert sim.load("pru0", (_SRC / "perif_tx_pattern.asm").read_text()) == []
    sim.step("pru0", 40)
    assert sim.gpcfg_state("pru0")["mux_sel"] == 1
    regs = sim._perif["pru0"].registers
    assert regs.get_shared_config()["txcfg"] == 0x00070010
    assert regs.get_tx_frame_size(0) == 0


def test_step_paced_rtu0_fallback_is_one_to_one():
    """No perif on rtu0: both cores advance exactly count instructions."""
    sim = Simulator()
    prog = "start:\n        add r2, r2, 1\n        jmp start\n"
    assert sim.load("pru0", prog) == []
    assert sim.load("rtu0", prog) == []
    sim.step_paced("pru0", "rtu0", 250)
    assert sim.cores["pru0"].counters.cycles == 250
    assert sim.cores["rtu0"].counters.cycles == 250
