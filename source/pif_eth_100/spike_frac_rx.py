"""Task 0 spike: prove the 300 MHz clock plan and the fractional RX divider (1.5).

Runs the EXISTING, unmodified pif_eth firmware (read-only) at 300 MHz:
  - TX  source/pif_eth/pif_eth_tx_n2.asm, TXCFG overridden to 0x00020010 (n=3)
  - RX  source/pif_eth/pif_eth_rx_o1_raw.asm, rxcfg control word 0x0000801F
Checks S0.1..S0.5 from the design spec (section 13). Exit code 0 = all PASS.

    python3 source/pif_eth_100/spike_frac_rx.py [--base-latency-ns 0.8333333333]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "source"))

from simulator import Simulator                          # noqa: E402

from pif_eth_100 import codec, rx_reference               # noqa: E402
from pif_eth_100.crc32 import fcs_bytes                   # noqa: E402
from pif_eth_100.decoder import decode_bits               # noqa: E402
from pif_eth_100.prng import DEFAULT_SEED, prng_bytes     # noqa: E402

CONFIG_PATH = str(_ROOT / "config" / "memory_pif_eth_100.cfg")
OLD_DIR = _ROOT / "source" / "pif_eth"                   # read-only
TXCFG_100 = 0x00020010
RXCFG_100 = 0x0000801F
TXCFG_PRU0, RXCFG_PRU1 = 0x260E4, 0x26100
PLEN = 200
SEEDS = (DEFAULT_SEED, 1, 2)
SHIFTS_NS = (0.0, 10.0 / 3.0, 20.0 / 3.0)
T_NUMF, T_MODE, T_SEED, T_PLEN = 0x0400, 0x0404, 0x0408, 0x040C
T_FCNT, T_BURST, T_GOFLAG = 0x0410, 0x0414, 0x0418
LUT1_ADDR, FRAME_ADDR, STATS, CTRL = 0x2000, 0x2E00, 0x2F00, 0x2F40

RESULTS: list[bool] = []


def wu32(sim, addr, val):
    sim.memory.write(addr, (val & 0xFFFFFFFF).to_bytes(4, "little"))


def ru32(sim, addr):
    return int.from_bytes(sim.memory_read(addr, 4), "little")


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def step_paced_traced(sim, count, on_follow, guard_ns=20.0):
    """Mirror of Simulator.step_paced('pru0','pru1',count) reporting each PRU1 instruction."""
    lead, follow = sim.cores["pru0"], sim.cores["pru1"]
    lp, fp = sim._perif["pru0"], sim._perif["pru1"]
    for _ in range(count):
        if not lead.halted and lead.pc < len(lead.instructions):
            lead.step()
        target = lp._now_ns - guard_ns
        safety = 1000
        while (fp._now_ns < target and safety > 0 and not follow.halted
               and follow.pc < len(follow.instructions)):
            pc, cyc = follow.pc, follow.counters.cycles
            follow.step()
            safety -= 1
            on_follow(pc, follow.counters.cycles - cyc, follow)


def tx_sim(seed, num_frames, latency_ns):
    sim = Simulator(config_path=CONFIG_PATH)
    sim.memory.write(0x0000, codec.build_dram0_lut())
    for a, v in ((T_NUMF, num_frames), (T_MODE, 0), (T_SEED, seed), (T_PLEN, PLEN),
                 (T_FCNT, 0), (T_BURST, 0), (T_GOFLAG, 0)):
        wu32(sim, a, v)
    sim.gpcfg_write("pru0", 1)
    sim.gpcfg_write("pru1", 1)
    sim.perif_loopback(0, True, latency_ns=latency_ns, jitter_ns=0.0, drift_ppm=0.0)
    errs = sim.load("pru0", (OLD_DIR / "pif_eth_tx_n2.asm").read_text(),
                    include_paths=[str(OLD_DIR)])
    if errs:
        raise RuntimeError(f"TX load: {errs}")
    sim.step("pru0", 60)                                      # prologue -> hs_wait
    sim.write_perif_register("pru0", TXCFG_PRU0, TXCFG_100)   # old fw hardcodes n=2
    return sim


def s01_s02():
    sim = Simulator(config_path=CONFIG_PATH)
    rx = sim._perif["pru1"].channels[0]
    tx = sim._perif["pru0"].channels[0]
    check("S0.1 cores at 300 MHz",
          sim.cores["pru0"].clock_mhz == 300.0 and sim.cores["pru1"].clock_mhz == 300.0
          and rx.core_clock_mhz == 300.0 and tx.core_clock_mhz == 300.0,
          f"pru0={sim.cores['pru0'].clock_mhz} pru1={sim.cores['pru1'].clock_mhz}")
    sim.write_perif_register("pru1", RXCFG_PRU1, RXCFG_100)
    sim.write_perif_register("pru0", TXCFG_PRU0, TXCFG_100)
    r = rx.regs
    ok = (r.get_rx_div_factor() == 0 and r.get_rx_div_factor_frac() == 1
          and r.get_rx_sample_size() == 7 and r.get_rx_sb_pol() == 1
          and r.get_rx_clk_sel() == 1 and rx.rx_clock_period_ns() == 5.0
          and tx.tx_clock_period_ns() == 10.0)
    check("S0.2 fractional RX divider 1.5", ok,
          f"rx_period={rx.rx_clock_period_ns()} ns tx_period={tx.tx_clock_period_ns()} ns")


def s03():
    dm = codec.build_decode_map()
    for seed in SEEDS:
        sim = tx_sim(seed, 1, 0.0)
        wu32(sim, T_GOFLAG, 1)
        steps = 0
        while ru32(sim, T_FCNT) != 1 and steps < 400_000:
            sim.step("pru0", 100)
            steps += 100
        ch = sim._perif["pru0"].channels[0]
        pushed = ru32(sim, T_BURST)
        ups = [t for t, v in ch.tx_out_en_transitions if v == 1]
        downs = [t for t, v in ch.tx_out_en_transitions if v == 0]
        t_burst = (downs[0] - ups[0]) if ups and downs else -1.0
        go, period = ch._go_ns, ch.tx_clock_period_ns()
        bits = [ch.tx_line_at(go + (k + 0.5) * period) for k in range(pushed * 8)]
        dec = decode_bits(bits, dm)
        payload = prng_bytes(PLEN, seed)
        ok = (pushed == 263 and abs(t_burst - 21040.0) < 1e-6 and dec.invalid_symbols == 0
              and bool(dec.frames) and dec.frames[0] == payload + fcs_bytes(payload))
        check(f"S0.3 TX n=3 @300MHz seed={seed}", ok,
              f"pushed={pushed} T_burst={t_burst:.6f}ns invalid={dec.invalid_symbols}")


def python_armed_capture(sim, max_steps=400_000):
    """Arm PRU1 ch0 RX from the host (no RX firmware) and drain it every 12 PRU0 instructions."""
    ch = sim._perif["pru1"].channels[0]
    rx_perif = sim.cores["pru1"].io_port.perif
    sim.write_perif_register("pru1", RXCFG_PRU1, RXCFG_100)
    rx_perif.advance_cycles(sim.cores["pru0"].counters.cycles)   # align RX timeline to TX
    arm_ns = rx_perif._now_ns
    ch.arm_rx(True)
    wu32(sim, T_GOFLAG, 1)
    raw, zeros, steps = bytearray(), 0, 0
    while steps < max_steps:
        sim.step("pru0", 12)          # FIFO fills in 4 x 12 = 48 cycles; drain well before
        steps += 12
        rx_perif.advance_cycles(sim.cores["pru0"].counters.cycles)
        if ch.rx_ovf:
            raise RuntimeError("RX FIFO overflowed between host drains")
        while ch.rx_valid:
            b = ch.rx_head()
            ch.clr_val()
            raw.append(b)
            zeros = zeros + 1 if b == 0 else 0
        if zeros >= 2 and ru32(sim, T_FCNT) == 1:
            return bytes(raw).rstrip(b"\x00"), arm_ns
    raise RuntimeError("no EOF within step budget")


def count_edge_ties(sim, latency_ns, arm_ns):
    """RX samples sit at arm_ns + 5k (target time) = arm_ns + 5k - latency (source time)."""
    ties = 0
    for t, _ in sim._perif["pru0"].channels[0].tx_transitions[1:]:
        y = (t + latency_ns - arm_ns) / 5.0
        if abs(y - round(y)) * 5.0 < 1e-6:
            ties += 1
    return ties


def s04(base_latency):
    for shift in SHIFTS_NS:
        lat = base_latency + shift
        for seed in SEEDS:
            sim = tx_sim(seed, 1, lat)
            try:
                raw, arm_ns = python_armed_capture(sim)
            except RuntimeError as exc:
                check(f"S0.4 python-armed RX lat={lat:.4f} seed={seed}", False, str(exc))
                continue
            res = rx_reference.decode_capture(raw, oversample=2)
            payload = prng_bytes(PLEN, seed)
            ok = res.invalid_symbols == 0 and (payload + fcs_bytes(payload)) in res.frames
            check(f"S0.4 python-armed RX lat={lat:.4f} seed={seed}", ok,
                  f"cap={len(raw)}B invalid={res.invalid_symbols} "
                  f"edge_ties={count_edge_ties(sim, lat, arm_ns)}")


def fw_sim(seed, frames, lat):
    sim = tx_sim(seed, frames, lat)
    sim.memory.write(LUT1_ADDR, codec.build_dram1_decode_lut())
    for off, v in ((0x00, 0), (0x04, seed), (0x08, PLEN), (0x0C, 0), (0x10, RXCFG_100)):
        wu32(sim, CTRL + off, v)
    for off in range(0, 0x20, 4):
        wu32(sim, STATS + off, 0)
    errs = sim.load("pru1", (OLD_DIR / "pif_eth_rx_o1_raw.asm").read_text(),
                    include_paths=[str(OLD_DIR)])
    if errs:
        raise RuntimeError(f"RX load: {errs}")
    target = sim.cores["pru1"]._parser.labels["go_wait"]
    hit = {"v": False}

    def boot(pc, dc, core):
        if pc == target:
            hit["v"] = True

    n = 0
    while not hit["v"]:
        if n > 200_000:
            raise RuntimeError("PRU1 never reached go_wait")
        step_paced_traced(sim, 16, boot)
        n += 16
    return sim


def s05(base_latency):
    for shift in SHIFTS_NS:
        lat = base_latency + shift
        for seed in SEEDS:
            sim = fw_sim(seed, 2, lat)
            poll = sim.cores["pru1"]._parser.labels["poll"]
            rxch = sim._perif["pru1"].channels[0]
            tr = {"max_fifo": 0, "deltas": {}}

            def cb(pc, dc, core, tr=tr, poll=poll, rxch=rxch):
                tr["max_fifo"] = max(tr["max_fifo"], len(rxch.rx_fifo))
                if poll + 1 <= pc <= poll + 6:
                    tr["deltas"].setdefault(pc - poll, set()).add(dc)

            stream = prng_bytes(PLEN * 2, seed)
            all_clean, st = True, []
            for i in range(2):
                wu32(sim, CTRL + 0x0C, 1)
                wu32(sim, T_GOFLAG, 1)
                n = 0
                while ru32(sim, STATS) != i + 1:
                    if n > 4_000_000:
                        raise RuntimeError("frame timeout")
                    step_paced_traced(sim, 16, cb)
                    n += 16
                st = [ru32(sim, STATS + o) for o in range(0, 0x20, 4)]
                payload = stream[i * PLEN:(i + 1) * PLEN]
                frame_ok = sim.memory_read(FRAME_ADDR, PLEN + 4) == payload + fcs_bytes(payload)
                # st = frames cap ovf symerr crc biterr totbits eof
                all_clean &= (st[2] == 0 and st[3] == 0 and st[4] == 1 and st[5] == 0
                              and st[6] == PLEN * 8 and st[7] == 1 and frame_ok)
            hot = 1 + sum(max(v) for v in tr["deltas"].values())
            check(f"S0.5 firmware RX lat={lat:.4f} seed={seed}", all_clean and hot <= 12,
                  f"hot_loop={hot}cyc/byte max_rx_fifo={tr['max_fifo']} last_stats={st}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-latency-ns", type=float, default=0.0)
    args = ap.parse_args(argv)
    s01_s02()
    s03()
    s04(args.base_latency_ns)
    s05(args.base_latency_ns)
    print(f"\nSPIKE {'PASS' if all(RESULTS) else 'FAIL'}: "
          f"{sum(RESULTS)}/{len(RESULTS)} checks, base latency {args.base_latency_ns} ns")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
