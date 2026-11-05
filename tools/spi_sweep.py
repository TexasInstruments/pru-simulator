#!/usr/bin/env python3
"""SPI link rate sweep: PRU0 controller <-> PRU1 edge-polling peripheral.

For every (mode, core clock, half period k) of ``rate_candidates()`` and every
hop delay (default 0 and the calibrated 5 cycles) this:

1. single-steps one PRU1 frame and measures its work per bit / byte and the
   idle-byte TX fetch (``measure_peripheral_work``);
2. finds the passing PRU0 ``miso_delay`` window (``find_miso_window``) and picks
   the value nearest its middle (Ruling R9, ``choose_miso_delay``);
3. on a fresh simulator at that delay runs the spec section 9 acceptance
   sequence (``run_test_sequence``) with every pattern, and measures the
   timing table of the back-to-back frame pair.

"Supported" = passes at hop delay 5 (Ruling R6). ``--write`` writes
``pru_io/spi_rates.json`` and ``docs/spi_link_timing.md``; without it the
result is only printed. ``--render`` re-renders the doc from the committed
JSON; ``--check`` compares the committed doc with what it renders to.

Usage: python tools/spi_sweep.py [--write] [--jobs N] [--render] [--check]
"""
from __future__ import annotations

import argparse
import configparser
import datetime as _dt
import json
import os
import shutil
import sys
import tempfile
import time
from multiprocessing import Pool
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pru_io.spi_link import (CMD_RW, HOP_DELAY_CYCLES, MISO_Q_MAX,  # noqa: E402
                             PATTERNS, RATES_PATH, SKEW_NS, SpiConfig,
                             SpiLinkRuntime, choose_miso_delay,
                             controller_delay_ok, load_supported,
                             measure_peripheral_work, measure_timing,
                             rate_candidates, run_test_sequence)

DOC_PATH = REPO / "docs" / "spi_link_timing.md"
HOP_DELAYS = (0, HOP_DELAY_CYCLES)


def row_id(mode: int, core: int, k: int, hop: int) -> str:
    return f"m{mode}_c{core}_k{k}_h{hop}"


def make_config(core: int, workdir: str) -> str:
    """Throw-away memory.cfg pinned to ``core`` MHz on both PRUs (like the
    tests' ``sim_config``); the repo memory.cfg is only read."""
    cfg = configparser.ConfigParser()
    cfg.read(REPO / "memory.cfg")
    if "device" not in cfg:
        cfg["device"] = {}
    cfg["device"]["pru_clock_mhz"] = str(core)
    cfg["device"]["pru1_clock_mhz"] = str(core)
    path = os.path.join(workdir, "memory.cfg")
    with open(path, "w") as f:
        cfg.write(f)
    if (REPO / "config").is_dir():
        shutil.copytree(REPO / "config", os.path.join(workdir, "config"),
                        dirs_exist_ok=True)
    return path


def _runtime(config_path: str, cfg: SpiConfig, hop: int) -> SpiLinkRuntime:
    from simulator import Simulator
    rt = SpiLinkRuntime(Simulator(config_path), cfg, hop_delay=hop)
    rt.load_controller()
    rt.load_peripheral()
    rt.wire()
    return rt


def _round(v):
    return round(v, 3) if isinstance(v, float) else v


def sweep_row(mode: int, core: int, k: int, hop: int,
              config_path: str | None = None) -> dict:
    """One sweep row; ``config_path`` must pin ``core`` (made if None)."""
    if config_path is None:
        with tempfile.TemporaryDirectory() as tmp:
            return sweep_row(mode, core, k, hop, make_config(core, tmp))
    first_ok = next(d for d in range(1, 2 * k) if controller_delay_ok(k, d))
    # 1. PRU1 work (independent of PRU0's sample point) and the MISO window
    rt = _runtime(config_path, SpiConfig(mode, core, k, first_ok), hop)
    rt.preload_peripheral_tx(5, PATTERNS["count"][1])
    work = measure_peripheral_work(rt, CMD_RW, 5, PATTERNS["count"][0])
    passing = rt.find_miso_window(contiguous=True)
    rt.close()
    d = choose_miso_delay(passing)
    row = {
        "id": row_id(mode, core, k, hop), "mode": mode, "core_mhz": core,
        "half_k": k, "spi_mhz": round(core / (2 * k), 6), "hop_delay": hop,
        "miso_delay_window": [min(passing), max(passing)] if passing else None,
        "miso_delay_pass": passing, "miso_delay": d, "pass": False,
        "p1_cycles_per_bit": work["data_bit_cycles"],
        "p1_bit_cycles_max": work["data_bit_cycles_max"],
        "p1_byte_cycles": work["data_byte_cycles"],
        "p1_max_byte_cycles": work["max_byte_cycles"],
        "p1_byte_budget": 16 * k,
        "p1_byte_slack": 16 * k - work["max_byte_cycles"],
        "p1_fetch_cycles": work["fetch_cycles"],
        "seq_byte_errors": None, "seq_failed_steps": [], "tcsh_cycles": None,
        "worst": None, "limit": None, "timing": None,
    }
    if d is None:
        row["limit"] = ("no miso_delay (Q <= %d) reads PRU1's MISO frame correctly"
                        % MISO_Q_MAX)
        return row
    # 2. acceptance sequence on a fresh simulator at the chosen delay (a
    # reloaded PRU0 restarts its cycle counter, so the monitor's timestamps
    # would no longer line up with PRU1's)
    cfg = SpiConfig(mode, core, k, d)
    rt = _runtime(config_path, cfg, hop)
    seq = run_test_sequence(rt)
    timing = measure_timing(rt.monitor.edges, cfg, frame_index=-2,
                            p1_fetch_cycles=work["fetch_cycles"])
    rt.close()
    b2b = next(s for s in seq["steps"] if s["step"] == "back_to_back")
    worst = next(r for r in timing if r["worst"])
    row.update({
        "pass": seq["ok"],
        "seq_byte_errors": seq["byte_errors"],
        "seq_failed_steps": [s["step"] for s in seq["steps"] if not s["ok"]],
        "tcsh_cycles": b2b["tcsh_cycles"],
        "worst": {"symbol": worst["symbol"], "margin_ns": _round(worst["margin_ns"])},
    })
    if seq["ok"]:
        row["limit"] = (f"MISO window {len(passing)} values [{min(passing)}..{max(passing)}]; "
                        f"PRU1 slack {row['p1_byte_slack']} of {16 * k} cycles/byte; "
                        f"worst margin {worst['symbol']} {worst['margin_ns']:.2f} ns")
        if hop == HOP_DELAY_CYCLES:
            row["timing"] = [{key: _round(v) for key, v in r.items()} for r in timing]
    else:
        row["limit"] = (f"test sequence failed: {', '.join(row['seq_failed_steps'])} "
                        f"({seq['byte_errors']} byte errors)")
    return row


def _job(args):
    t = time.time()
    row = sweep_row(*args)
    return row, time.time() - t


def sweep(hop_delays=HOP_DELAYS, jobs: int | None = None, progress=None) -> list[dict]:
    """Every (mode, core, k, hop) row, sorted by mode, rate, core, hop."""
    work = [(m, c, k, h) for m in (0, 1) for c, k in rate_candidates() for h in hop_delays]
    jobs = jobs or min(len(work), os.cpu_count() or 1)
    rows = []
    if jobs == 1:
        results = map(_job, work)
    else:
        pool = Pool(jobs)
        results = pool.imap_unordered(_job, work)
    for i, (row, dt) in enumerate(results):
        rows.append(row)
        if progress:
            progress(f"[{i + 1}/{len(work)}] {row['id']} pass={row['pass']} "
                     f"d={row['miso_delay']} ({dt:.1f} s)")
    if jobs != 1:
        pool.close()
        pool.join()
    rows.sort(key=lambda r: (r["mode"], r["spi_mhz"], r["core_mhz"], r["hop_delay"]))
    return rows


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _f(v, nd=2):
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _rate(r):
    return f"{r['spi_mhz']:.3f}"


def _supported_view(data):
    hop = data["hop_delay_supported"]
    out = {0: [], 1: []}
    for mode in out:
        best = {}
        for r in data["rows"]:
            if r["mode"] == mode and r["hop_delay"] == hop and r["pass"]:
                key = round(r["spi_mhz"], 6)
                if key not in best or r["core_mhz"] < best[key]["core_mhz"]:
                    best[key] = r
        out[mode] = [best[key] for key in sorted(best)]
    return out


def render_doc(data: dict) -> str:
    rows = data["rows"]
    hop = data["hop_delay_supported"]
    sup = _supported_view(data)
    by_mode_hop = {(m, h): [r for r in rows if r["mode"] == m and r["hop_delay"] == h]
                   for m in (0, 1) for h in sorted({r["hop_delay"] for r in rows})}
    L = []
    a = L.append
    a("# SPI link timing: PRU0 controller ↔ PRU1 edge-polling peripheral")
    a("")
    a(f"Generated by `python tools/spi_sweep.py --write` on {data['generated']} "
      f"from `pru_io/spi_rates.json` (sweep wall time {data.get('sweep_wall_s', '–')} s). "
      "Do not edit by hand. Spec: `docs/superpowers/specs/2026-11-04-spi-pru0-pru1-design.md`.")
    a("")
    a("## How a row is decided")
    a("")
    a("For every mode, core clock and half period k (SPI CLK = core / 2k, 10–50 MHz, "
      "never 50 MHz at 200 MHz) and every hop delay, the sweep:")
    a("")
    a("1. single-steps one PRU1 R/W frame and measures PRU1's work per bit and per byte, "
      "not counting poll iterations that loop back while waiting for an edge, and the "
      "idle-byte TX fetch;")
    a("2. reassembles PRU0 for every realisable `miso_delay` (`controller_delay_ok`: "
      "`d mod 2k` not 0 or k, pipelining ≤ 6 bits) until the passing window has closed, "
      "one R/W frame each, and picks the passing value nearest the middle of the window "
      "(Ruling R9; the window has holes at `d mod 2k ∈ {0, k}`);")
    a("3. on a fresh simulator at that delay runs the spec §9 acceptance sequence: WRITE, "
      "READ of a preloaded TX block and R/W full duplex with every pattern "
      f"({', '.join(PATTERNS)}), an unknown command (0x04), an out-of-range address (0x80), "
      "a mid-frame /CS abort after 76 bits, and two back-to-back R/W frames. Every MISO "
      "byte PRU0 receives, every MOSI byte on the bus, all 128 PRU1 RX blocks and the "
      "PRU1 status counters are checked.")
    a("")
    a(f"**Supported** = zero byte errors and every step right at hop delay **{hop} "
      "cycles** on both cores (Ruling R6). Rows at hop delay 0 are reported for comparison "
      "only.")
    a("")
    a("## Sources and modelling assumptions")
    a("")
    a("- AM243 data only from SPRSP65J (AM243x datasheet) §6.11.5.16.1:")
    a("  - Table 6-112 (PRU timing conditions): input slew rate SR<sub>I</sub> 1–3 V/ns; "
      "output load C<sub>L</sub> 2–30 pF.")
    a("  - Table 6-113 (direct output mode), PRDO1: t<sub>sk(GPO-GPO)</sub> skew GPO to GPO, "
      f"max {SKEW_NS:g} ns.")
    a("  - There is no direct-input-mode timing table. Input setup and hold rows are "
      "therefore a **design budget**: measured value − 2 ns output skew (PRDO1) − one core "
      "cycle of polling uncertainty. They are not datasheet limits.")
    a(f"- Hop delay: **{hop} core cycles** pin to pin, applied on the GPI input side of both "
      "cores at every core clock. It comes from **one scope measurement at 333 MHz**: "
      "`wbs` on an input followed by `set` on an output took **18 ns** (6 cycles) on "
      "silicon, against 1 cycle in the simulator with no delay. The split between input "
      "synchroniser, pads and output path is **unknown**; since every SPI net is one output "
      "to one input, putting the whole delay on the input side is equivalent here. At "
      "core clocks below 333 MHz, 5 cycles is more than 15 ns, so the model is cautious "
      "there.")
    a("- Board trace and jumper-wire delay between the LP-AM243 headers are **not "
      "modelled** (a few cm of wire is well under 1 ns).")
    a("- The simulator models no pad skew: t<sub>sk</sub> is shown as 0 in sim and is a "
      "derived row (never the worst).")
    a("- \"Measured in sim\" comes from the bus monitor on PRU0's pins, last back-to-back "
      "frame pair (first frame; t<sub>CSH</sub> is the gap to the second). The MISO rows are "
      "taken at PRU0's pin: PRU0's GPI sees each MISO change a further hop delay later, so "
      "the setup PRU0's sample actually gets is t<sub>SU,MISO</sub> − hop and the hold is "
      "t<sub>H,MISO</sub> + hop. A negative MISO hold margin in a passing row is this "
      "pin-referenced view, not a failed sample. t<sub>V,MISO</sub> is measured from the "
      "latest launch edge before the change, so once hop + PRU1 reaction + hop exceeds a "
      "bit period (the MISO sample is pipelined) it wraps modulo t<sub>CLK</sub>.")
    a("- Simulator limitation (recorded, not fixed): a GPI change that arrives while the "
      "reading core is inside a multi-cycle instruction is stamped at the end of that "
      "instruction (`io_port._record_gpi` uses the reader's cycle counter), so it can land "
      "late and two edges inside one stall can merge. The PRU0↔PRU1 runs here are not "
      "affected: PRU1's longest in-frame instruction is a 3-cycle `lbbo` (stepped), "
      "shorter than any half period, and PRU0 does no loads or stores inside a frame.")
    a("")
    a("## Controller /CS-high gap")
    a("")
    a("The spec minimum gap between frames is 2 t<sub>CLK</sub> (4k cycles). PRU0's "
      "controller first holds /CS high for at least 4k cycles, then realigns and stores "
      "the MISO samples, updates the control block and polls `go` again, so the shortest "
      "gap it produces is longer than 2 t<sub>CLK</sub>. The back-to-back step sets `go` "
      "for the second frame as soon as the first reports done, i.e. at that controller "
      "minimum (column t<sub>CSH</sub> cycles below, measured on the bus). PRU1 itself "
      "keeps up with the strict 2 t<sub>CLK</sub> gap: `tests/test_spi_peripheral.py::"
      "test_min_cs_gap_no_frame_lost` drives PRU1's GPI directly at a 4k-cycle gap (k = 3 "
      "and 6, both modes, hop 5). The minimum PRU1 needs is 9 cycles (mode 1) / 5 (mode 0) "
      "at k = 3.")
    a("")
    a("## PRU1 cycles (single-stepped)")
    a("")
    a("| mode | data bit (polls + MISO drive) | data bit (max, with gap work) | steady data byte | "
      "max byte 0–17 | idle-byte TX fetch |")
    a("|---|---|---|---|---|---|")
    for m in (0, 1):
        r = by_mode_hop[(m, hop)][0]
        a(f"| {m} | {r['p1_cycles_per_bit']} | {r['p1_bit_cycles_max']} | "
          f"{r['p1_byte_cycles']} | {r['p1_max_byte_cycles']} | {r['p1_fetch_cycles']} |")
    a("")
    a("PRU1's work does not depend on the rate (it waits for each edge); the numbers are "
      "the same in every row. A byte lasts 16k cycles; the PRU1 slack per byte is "
      "16k − max byte. At k = 3 (48 cycles) that is the tightest PRU1 budget.")
    a("")
    a("Limiting instruction sequence (the per-bit polls of `source/spi/spi_peripheral.asm`, "
      "stepped in `tests/test_spi_peripheral.py::test_per_bit_cycles`): mode 1 data bit = "
      "launch-edge poll 2 + MISO drive (`lsl r30.w0`) 1 + capture-edge poll 2 = 5 cycles; "
      "a data byte adds the 7-op gather (xor/shift) of the 8 capture snapshots, 47 cycles. "
      "Mode 0 data bit = capture poll 2 + launch poll 1 + MISO drive 1 = 4 cycles; a byte "
      "adds the gather and one `xor 0xFF`, 40 cycles. The max-byte column also counts the "
      "TX fetch and commit-pointer work placed in the gaps of the header bytes. Every poll is "
      "a single masked R31 read plus a branch, so a bit cannot cost fewer than two cycles "
      "per edge; PRU1 was not optimised further because no catalogue rate is limited by it "
      "(see below).")
    a("")
    a("## Supported rates (UI list)")
    a("")
    a("One entry per SPI rate and mode: the lowest core clock that passes at hop delay "
      f"{hop} (`spi_link.load_supported()`).")
    a("")
    for m in (0, 1):
        a(f"### Mode {m} supported rates")
        a("")
        a("| SPI MHz | core MHz | k | miso_delay | window | PRU1 slack/byte | worst margin | row |")
        a("|---|---|---|---|---|---|---|---|")
        for r in sup[m]:
            w = r["worst"]
            a(f"| {_rate(r)} | {r['core_mhz']} | {r['half_k']} | {r['miso_delay']} | "
              f"{r['miso_delay_window'][0]}..{r['miso_delay_window'][1]} "
              f"({len(r['miso_delay_pass'])}) | {r['p1_byte_slack']} | "
              f"{w['symbol']} {_f(w['margin_ns'])} ns | `{r['id']}` |")
        a("")
    a("## Fastest supported rate per mode")
    a("")
    for m in (0, 1):
        rs = [r for r in by_mode_hop[(m, hop)] if r["pass"]]
        if not rs:
            a(f"- Mode {m}: none.")
            continue
        top = max(r["spi_mhz"] for r in rs)
        for r in [r for r in rs if r["spi_mhz"] == top]:
            a(f"- Mode {m}: **{_rate(r)} MHz** at {r['core_mhz']} MHz, k = {r['half_k']} "
              f"(`{r['id']}`). Limits: {r['limit']}.")
        fails = [r for r in by_mode_hop[(m, hop)] if not r["pass"]]
        if fails:
            for r in fails:
                a(f"  - fails: `{r['id']}` ({_rate(r)} MHz): {r['limit']}.")
        else:
            slack = min(r["p1_byte_slack"] for r in rs)
            a(f"  - every mode {m} combination of the test matrix passes; a faster rate "
              "would need k < 3 or > 50 MHz, outside the matrix. "
              + (f"PRU1 itself is exactly at its limit at k = 3 ({slack} cycles slack "
                 "per byte), and the MISO window is the narrowest there."
                 if slack == 0 else
                 f"PRU1 still has {slack} cycle(s) of slack per byte at k = 3; the MISO "
                 "window is the narrowest there."))
    a("")
    a("## All results")
    a("")
    a("Every (mode, rate, core, hop) combination. Window = passing realisable "
      "`miso_delay` values (lo..hi, count).")
    a("")
    a("| row | mode | core MHz | k | SPI MHz | hop | pass | window | miso_delay | "
      "PRU1 cyc/bit | PRU1 slack/byte | t<sub>CSH</sub> cycles | limiting factor |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        win = (f"{r['miso_delay_window'][0]}..{r['miso_delay_window'][1]} "
               f"({len(r['miso_delay_pass'])})" if r["miso_delay_window"] else "none")
        a(f"| `{r['id']}` | {r['mode']} | {r['core_mhz']} | {r['half_k']} | {_rate(r)} | "
          f"{r['hop_delay']} | {'pass' if r['pass'] else 'FAIL'} | {win} | "
          f"{_f(r['miso_delay'])} | {r['p1_cycles_per_bit']} | {r['p1_byte_slack']} | "
          f"{_f(r['tcsh_cycles'])} | {r['limit']} |")
    a("")
    a(f"## Timing per supported combination (hop delay {hop})")
    a("")
    a("Columns as spec §6. Design values and margins in ns; cycles at the core clock. "
      "Rows marked *derived* are not measured from edges and never count as the worst "
      "margin; the worst measured margin is in bold.")
    a("")
    for r in rows:
        if not (r["pass"] and r["hop_delay"] == hop):
            continue
        a(f"### {r['id']}: mode {r['mode']}, {_rate(r)} MHz at {r['core_mhz']} MHz, "
          f"k = {r['half_k']}, miso_delay = {r['miso_delay']}")
        a("")
        a("| parameter | symbol | design ns | PRU cycles | sim ns | AM243 min | AM243 max | "
          "source | margin ns |")
        a("|---|---|---|---|---|---|---|---|---|")
        for t in r["timing"]:
            name = t["param"] + (" *(derived)*" if t["derived"] else "")
            margin = _f(t["margin_ns"])
            if t["worst"]:
                margin = f"**{margin}**"
            a(f"| {name} | {t['symbol']} | {_f(t['design_ns'])} | {_f(t['cycles'])} | "
              f"{_f(t['sim_ns'])} | {_f(t['am243_min'])} | {_f(t['am243_max'])} | "
              f"{t['source']} | {margin} |")
        a("")
    return "\n".join(L)


def dump_rates(data: dict) -> str:
    """spi_rates.json text: header keys, then one row per line."""
    head = {key: v for key, v in data.items() if key != "rows"}
    lines = [json.dumps(r, separators=(",", ":")) for r in data["rows"]]
    return (json.dumps(head)[:-1] + ', "rows": [\n' + ",\n".join(lines) + "\n]}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true",
                    help="write pru_io/spi_rates.json and docs/spi_link_timing.md")
    ap.add_argument("--jobs", type=int, default=None, help="worker processes")
    ap.add_argument("--render", action="store_true",
                    help="re-render the doc from the committed JSON (no sweep)")
    ap.add_argument("--check", action="store_true",
                    help="check the committed doc against the committed JSON only")
    args = ap.parse_args(argv)
    if args.render:
        data = json.loads(RATES_PATH.read_text(encoding="utf-8"))
        DOC_PATH.write_text(render_doc(data), encoding="utf-8")
        print(f"wrote {DOC_PATH.relative_to(REPO)}")
        return 0
    if args.check:
        data = json.loads(RATES_PATH.read_text(encoding="utf-8"))
        ok = render_doc(data) == DOC_PATH.read_text(encoding="utf-8")
        print("doc up to date" if ok else "doc out of date: run --write")
        return 0 if ok else 1
    t0 = time.time()
    rows = sweep(jobs=args.jobs, progress=lambda s: print(s, flush=True))
    wall = round(time.time() - t0, 1)
    data = {"generated": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "hop_delay_supported": HOP_DELAY_CYCLES, "sweep_wall_s": wall, "rows": rows}
    for m, rs in _supported_view(data).items():
        print(f"mode {m}: {len(rs)} supported rates, fastest "
              f"{max((r['spi_mhz'] for r in rs), default=None)} MHz")
    print(f"sweep wall time {wall} s")
    if args.write:
        RATES_PATH.write_text(dump_rates(data), encoding="utf-8")
        DOC_PATH.write_text(render_doc(data), encoding="utf-8")
        assert load_supported() == _supported_view(data)
        print(f"wrote {RATES_PATH.relative_to(REPO)} and {DOC_PATH.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
