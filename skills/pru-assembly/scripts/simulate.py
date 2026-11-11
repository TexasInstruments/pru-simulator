#!/usr/bin/env python3
"""Run PRU code in TI's pru-simulator and print a short, fixed-format summary.

Use this instead of hand-written simulator calls (SKILL.md, step 6).

Usage:
    python3 scripts/simulate.py run   <file.asm|file.out> [--target NAME] [--clock-mhz F] [--core pru0]
                                      [--until COND] [--max-steps N] [--max-cycles N] [--include DIR]
    python3 scripts/simulate.py edges <file.asm|file.out> --pin N [--edges 4] [--target NAME]
                                      [--clock-mhz F] [--core pru0] [--max-steps N] [--include DIR]

    --target NAME   Any config/memory_NAME.cfg in the simulator checkout (`ls config/`).
                    Default: the simulator's memory.cfg.
    --clock-mhz F   PRU core clock to simulate. Default: pru_clock_mhz from the config in
                    use (200 MHz if the config sets none). Every ns figure uses this clock,
                    so pass the clock your firmware really runs at.

    run    Executes until COND (default: halt) using the simulator's headless runner.
           COND: halt, cycles>=N, reg:rN==V, reg:rN!=V, mem32:ADDR==V, mem32:ADDR!=V,
                 gpi:N==V, gpo:N==V
    edges  Records the core cycle of each change on GPO pin N and prints the
           cycles and ns between edges, for checking a timing budget on a pin.

Exit codes: 0 ok, 2 load/input error, 3 condition not met or budget exhausted,
4 simulator error, 5 simulator not installed.

Simulator warnings (for example an XFR device ID that is not modelled: reads return
zeros and writes are discarded) are printed under "warnings:". A PASS with warnings
does not prove the code that triggered them.

Install (once):
    git clone https://github.com/TexasInstruments/pru-simulator.git
    pip install -r pru-simulator/requirements.txt
    export PRU_SIM_DIR=/path/to/pru-simulator     (default: ./pru-simulator or ~/pru-simulator)

Which devices and peripherals are modelled depends on the simulator version; see its
readme.md. Its timing is one cycle per instruction plus configured memory latencies;
confirm tight budgets on hardware.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

DEFAULT_CLOCK_MHZ = 200.0   # the simulator's own default when a config sets no pru_clock_mhz
_CLOCK_RE = re.compile(r"^[ \t]*pru_clock_mhz[ \t]*=[ \t]*([0-9.]+)", re.MULTILINE)


def find_sim_dir():
    candidates = [os.environ.get("PRU_SIM_DIR"), "pru-simulator", os.path.expanduser("~/pru-simulator")]
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, "tools", "headless_runner.py")):
            return os.path.abspath(c)
    return None


def available_targets(sim_dir):
    paths = glob.glob(os.path.join(sim_dir, "config", "memory_*.cfg"))
    return sorted(os.path.basename(p)[len("memory_"):-len(".cfg")] for p in paths)


def config_path(sim_dir, target):
    """Config file for TARGET, or the simulator's default memory.cfg. None if TARGET is unknown."""
    if not target:
        return os.path.join(sim_dir, "memory.cfg")
    path = os.path.join(sim_dir, "config", f"memory_{target}.cfg")
    return path if os.path.isfile(path) else None


def config_clock_mhz(cfg):
    with open(cfg, encoding="utf-8") as fh:
        m = _CLOCK_RE.search(fh.read())
    return float(m.group(1)) if m else DEFAULT_CLOCK_MHZ


def with_clock(cfg, clock_mhz):
    """Copy CFG with pru_clock_mhz set to CLOCK_MHZ; returns the temporary file's path."""
    with open(cfg, encoding="utf-8") as fh:
        text = fh.read()
    line = f"pru_clock_mhz = {clock_mhz:g}"
    if _CLOCK_RE.search(text):
        text = _CLOCK_RE.sub(line, text, count=1)
    else:
        text, n = re.subn(r"^\[device\][ \t]*\r?\n", lambda m: m.group(0) + line + "\n", text,
                          count=1, flags=re.MULTILINE)
        if not n:
            raise ValueError(f"{cfg} has no [device] section to set the clock in")
    fd, path = tempfile.mkstemp(suffix=".cfg", prefix="pru_sim_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def resolve_config(args, sim_dir):
    """Return (config path, clock MHz, temp file to delete or None); print an error and return None on bad input."""
    cfg = config_path(sim_dir, args.target)
    if cfg is None:
        print(f"unknown target '{args.target}'. Available: {', '.join(available_targets(sim_dir)) or 'none'}")
        return None
    if args.clock_mhz is not None:
        if args.clock_mhz <= 0:
            print("--clock-mhz must be greater than zero")
            return None
        tmp = with_clock(cfg, args.clock_mhz)
        return tmp, args.clock_mhz, tmp
    return cfg, config_clock_mhz(cfg), None


def clock_line(mhz):
    return f"{mhz:g} MHz (1 cycle = {1000.0 / mhz:g} ns)"


def cmd_run(args, sim_dir):
    resolved = resolve_config(args, sim_dir)
    if resolved is None:
        return 2
    cfg, clock_mhz, tmp = resolved
    try:
        return run_headless(args, sim_dir, cfg, clock_mhz)
    finally:
        if tmp:
            os.remove(tmp)


def run_headless(args, sim_dir, cfg, clock_mhz):
    src = os.path.abspath(args.file)
    kind = "--elf" if src.endswith(".out") else "--assembly"
    cmd = [sys.executable, "-m", "tools.headless_runner", "--json", kind, src,
           "--core", args.core, "--until", args.until, "--max-steps", str(args.max_steps),
           "--config", cfg]
    if args.max_cycles is not None:   # forward 0 too: the runner rejects it instead of running unbounded
        cmd += ["--max-cycles", str(args.max_cycles)]
    for inc in args.include:
        cmd += ["--include", os.path.abspath(inc)]
    proc = subprocess.run(cmd, cwd=sim_dir, capture_output=True, text=True)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"simulator error (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
        return 4
    state = result.get("state", {})
    print(f"target:       {args.target or 'memory.cfg default'}  core: {args.core}")
    print(f"clock:        {clock_line(clock_mhz)}")
    print(f"result:       {'PASS' if result.get('success') else 'FAIL'} ({result.get('reason')})")
    if result.get("errors"):
        for e in result["errors"]:
            print(f"  error: {e}")
    cycles = state.get("cycles")
    time_ns = f" = {cycles * 1000.0 / clock_mhz:g} ns" if isinstance(cycles, (int, float)) else ""
    print(f"cycles:       {cycles}{time_ns} (stall {state.get('stall_cycles')}, "
          f"instructions {state.get('instruction_count')})")
    print(f"halted:       {state.get('halted')}  pc: {state.get('pc')}")
    regs = state.get("registers") or []
    nonzero = [f"r{i}={v}" for i, v in enumerate(regs) if int(v, 16) != 0]
    print(f"nonzero regs: {', '.join(nonzero) if nonzero else 'none'}")
    warnings = [ln for ln in proc.stderr.splitlines() if ln.strip()]
    if warnings:
        print("warnings:     (a PASS does not prove the code these refer to)")
        for ln in warnings:
            print(f"  {ln}")
    return proc.returncode


def cmd_edges(args, sim_dir):
    resolved = resolve_config(args, sim_dir)
    if resolved is None:
        return 2
    cfg, clock_mhz, tmp = resolved
    try:
        return measure_edges(args, sim_dir, cfg, clock_mhz)
    finally:
        if tmp:
            os.remove(tmp)


def measure_edges(args, sim_dir, cfg, clock_mhz):
    sys.path.insert(0, sim_dir)
    os.chdir(sim_dir)
    from mcp_server.server import PRUSimulatorMCP  # noqa: E402

    mcp = PRUSimulatorMCP(cfg)
    src = args.file  # already absolute (resolved before chdir)
    if src.endswith(".out"):
        loaded = mcp.pru_elf_load(core=args.core, path=src)
    else:
        # Same include directories as the headless runner: the source's own folder, then --include.
        includes = [os.path.dirname(src)] + [os.path.abspath(i) for i in args.include]
        with open(src, encoding="utf-8") as fh:
            loaded = mcp.pru_load(fh.read(), core=args.core, include_paths=includes)
    if loaded.get("errors"):
        print("load errors:", *loaded["errors"], sep="\n  ")
        return 2
    gpo_pins = mcp.pru_io(core=args.core)["gpo_pins"]
    if not 0 <= args.pin < len(gpo_pins):
        print(f"--pin must be 0..{len(gpo_pins) - 1}, got {args.pin}")
        return 2
    level = gpo_pins[args.pin]
    edges = []
    for _ in range(args.edges):
        target = 0 if level else 1
        r = mcp.pru_run_until(core=args.core, condition=f"gpo:{args.pin}=={target}",
                              max_steps=args.max_steps)
        if not r.get("condition_met"):
            print(f"pin {args.pin} stopped changing after {len(edges)} edge(s) ({r.get('reason')})")
            return 3
        edges.append((r["cycles"], target))
        level = target
    print(f"pin {args.pin} edges (cycle, level): {edges}")
    gaps = [b[0] - a[0] for a, b in zip(edges, edges[1:])]
    print(f"clock: {clock_line(clock_mhz)}")
    print(f"cycles between edges: {gaps}")
    print(f"ns between edges:     {[round(g * 1000.0 / clock_mhz, 3) for g in gaps]}")
    return 0


def main():
    for stream in (sys.stdout, sys.stderr):   # never crash on a cp1252 Windows console
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)
    for name in ("run", "edges"):
        s = sub.add_parser(name)
        s.add_argument("file")
        s.add_argument("--target", help="config/memory_<target>.cfg in the simulator checkout")
        s.add_argument("--clock-mhz", type=float, help="PRU core clock (default: the config's pru_clock_mhz)")
        s.add_argument("--core", default="pru0")
        s.add_argument("--max-steps", type=int, default=100_000)
        s.add_argument("--include", action="append", default=[], metavar="DIR")
        if name == "run":
            s.add_argument("--until", default="halt")
            s.add_argument("--max-cycles", type=int)
        else:
            s.add_argument("--pin", type=int, required=True)
            s.add_argument("--edges", type=int, default=4)
    args = p.parse_args()

    sim_dir = find_sim_dir()
    if not sim_dir:
        print("pru-simulator not found. Install it (see this script's header) and set PRU_SIM_DIR.")
        return 5
    args.file = os.path.abspath(args.file)
    return cmd_run(args, sim_dir) if args.mode == "run" else cmd_edges(args, sim_dir)


if __name__ == "__main__":
    sys.exit(main())
