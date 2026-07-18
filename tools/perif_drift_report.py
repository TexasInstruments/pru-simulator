#!/usr/bin/env python3
"""Clock-drift experiment: at what pattern length does perif RX break?

Streams the counter pattern PRU0 -> PRU1 over the ch0 loopback with
PRU1's clock offset by N ppm, and reports the first corrupted byte index.
Usage: python3 tools/perif_drift_report.py
"""
import configparser
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator import Simulator  # noqa: E402

_ROOT = Path(__file__).parent.parent
TXCFG_PRU0 = 0x260E4
RXCFG_PRU1 = 0x26100
TXCFG_VAL = 0x00070010     # clk_sel=core, div=7 -> 25 MHz bit clock
RXCFG_VAL = 0x0007001F     # sample_size=7, sb_pol=1, clk_sel=core, div=7
COUNT_ADDR = 0x3FF8
BUF_ADDR = 0x2000
CYCLES_PER_BYTE = 64       # 8 bits x 8 core cycles/bit at div=7


def make_config(ppm: float, out_dir: str) -> str:
    """Copy memory.cfg with pru1_clock_mhz offset by *ppm*."""
    cfg = configparser.ConfigParser()
    cfg.read(_ROOT / "memory.cfg")
    base = float(cfg["device"].get("pru_clock_mhz", "200"))
    cfg["device"]["pru1_clock_mhz"] = repr(base * (1.0 + ppm / 1e6))
    path = os.path.join(out_dir, f"memory_{int(ppm)}ppm.cfg")
    with open(path, "w") as f:
        cfg.write(f)
    return path


def run_drift(ppm: float, max_bytes: int, tmp_dir: str):
    """Run the experiment; return (received_count, first_bad_index or None)."""
    sim = Simulator(make_config(ppm, tmp_dir))
    sim.gpcfg_write("pru0", 1)
    sim.gpcfg_write("pru1", 1)
    sim.write_perif_register("pru0", TXCFG_PRU0, TXCFG_VAL)
    sim.write_perif_register("pru1", RXCFG_PRU1, RXCFG_VAL)
    sim.perif_loopback(0, True)
    assert sim.load("pru0", (_ROOT / "source" / "perif_tx_pattern.asm").read_text()) == []
    assert sim.load("pru1", (_ROOT / "source" / "perif_rx_capture.asm").read_text()) == []

    budget = max_bytes * CYCLES_PER_BYTE + 20_000
    stepped = 0
    count = 0
    # PRU1's RX must only ever sample line history PRU0 has already recorded,
    # so PRU1 trails PRU0's perif time by a small guard band. PRU1 steps can
    # advance more ns than instructions suggest (sbbo stall cycles), so the
    # band (20 ns) exceeds the worst single-step advance.
    guard_ns = 20.0
    perif0 = sim._perif["pru0"]
    perif1 = sim._perif["pru1"]
    while stepped < budget:
        sim.step("pru0", 200)      # TX first so the line history leads RX
        t0 = perif0._now_ns
        while perif1._now_ns < t0 - guard_ns:
            sim.step("pru1", 1)
        stepped += 200
        count = int.from_bytes(bytes(sim.memory_read(COUNT_ADDR, 4)), "little")
        if count >= max_bytes:
            break

    count = min(count, max_bytes)
    data = list(sim.memory_read(BUF_ADDR, count)) if count else []
    first_bad = next((i for i, b in enumerate(data) if b != (i & 0xFF)), None)
    return count, first_bad


def main():
    print(f"{'ppm':>8} | {'received':>8} | {'first bad byte':>14} | {'bits':>8}")
    print("-" * 48)
    with tempfile.TemporaryDirectory() as td:
        for ppm in (0, 50, 100, 200, 500, 1000, 2000):
            max_bytes = 2000 if ppm < 200 else 1000
            count, first_bad = run_drift(ppm, max_bytes, td)
            bad = "-" if first_bad is None else str(first_bad)
            bits = "-" if first_bad is None else str(first_bad * 8)
            print(f"{ppm:>8} | {count:>8} | {bad:>14} | {bits:>8}")


if __name__ == "__main__":
    main()
