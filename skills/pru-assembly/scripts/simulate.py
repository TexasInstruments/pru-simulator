#!/usr/bin/env python3
"""Run PRU code in TI's pru-simulator and print a short, fixed-format summary.

Use this instead of hand-written simulator calls (SKILL.md, step 6).

Usage:
    python3 scripts/simulate.py run   <file.asm|file.out> [--target am243x|am263x] [--core pru0]
                                      [--until COND] [--max-steps N] [--max-cycles N] [--include DIR]
    python3 scripts/simulate.py edges <file.asm|file.out> --pin N [--edges 4] [--target ...]
                                      [--core pru0] [--max-steps N]

    run    Executes until COND (default: halt) using the simulator's headless runner.
           COND: halt, cycles>=N, reg:rN==V, reg:rN!=V, mem32:ADDR==V, mem32:ADDR!=V,
                 gpi:N==V, gpo:N==V
    edges  Records the core cycle of each change on GPO pin N and prints the
           cycles between edges, for checking a timing budget on a pin.

Exit codes: 0 ok, 2 load/input error, 3 condition not met or budget exhausted,
4 simulator error, 5 simulator not installed.

Install (once):
    git clone https://github.com/TexasInstruments/pru-simulator.git
    pip install -r pru-simulator/requirements.txt
    export PRU_SIM_DIR=/path/to/pru-simulator     (default: ./pru-simulator or ~/pru-simulator)

The simulator models AM243x (PRU_ICSSG) and AM263x (PRU-ICSS) only. Its timing is
one cycle per instruction plus configured memory latencies; confirm tight
budgets on hardware.
"""
import argparse
import json
import os
import subprocess
import sys

TARGET_CONFIGS = {"am243x": "config/memory_am243x.cfg", "am263x": "config/memory_am263x.cfg"}


def find_sim_dir():
    candidates = [os.environ.get("PRU_SIM_DIR"), "pru-simulator", os.path.expanduser("~/pru-simulator")]
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, "tools", "headless_runner.py")):
            return os.path.abspath(c)
    return None


def config_path(sim_dir, target):
    return os.path.join(sim_dir, TARGET_CONFIGS[target]) if target else os.path.join(sim_dir, "memory.cfg")


def cmd_run(args, sim_dir):
    src = os.path.abspath(args.file)
    kind = "--elf" if src.endswith(".out") else "--assembly"
    cmd = [sys.executable, "-m", "tools.headless_runner", "--json", kind, src,
           "--core", args.core, "--until", args.until, "--max-steps", str(args.max_steps),
           "--config", config_path(sim_dir, args.target)]
    if args.max_cycles:
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
    print(f"result:       {'PASS' if result.get('success') else 'FAIL'} ({result.get('reason')})")
    if result.get("errors"):
        for e in result["errors"]:
            print(f"  error: {e}")
    print(f"cycles:       {state.get('cycles')} (stall {state.get('stall_cycles')}, "
          f"instructions {state.get('instruction_count')})")
    print(f"halted:       {state.get('halted')}  pc: {state.get('pc')}")
    regs = state.get("registers") or []
    nonzero = [f"r{i}={v}" for i, v in enumerate(regs) if int(v, 16) != 0]
    print(f"registers≠0:  {', '.join(nonzero) if nonzero else 'none'}")
    return proc.returncode


def cmd_edges(args, sim_dir):
    sys.path.insert(0, sim_dir)
    os.chdir(sim_dir)
    from mcp_server.server import PRUSimulatorMCP  # noqa: E402

    mcp = PRUSimulatorMCP(config_path(sim_dir, args.target))
    src = args.file  # already absolute (resolved before chdir)
    if src.endswith(".out"):
        loaded = mcp.pru_elf_load(core=args.core, path=src)
    else:
        with open(src) as fh:
            loaded = mcp.pru_load(fh.read(), core=args.core)
    if loaded.get("errors"):
        print("load errors:", *loaded["errors"], sep="\n  ")
        return 2
    level = mcp.pru_io(core=args.core)["gpo_pins"][args.pin]
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
    print(f"cycles between edges: {gaps}")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)
    for name in ("run", "edges"):
        s = sub.add_parser(name)
        s.add_argument("file")
        s.add_argument("--target", choices=sorted(TARGET_CONFIGS))
        s.add_argument("--core", default="pru0")
        s.add_argument("--max-steps", type=int, default=100_000)
        if name == "run":
            s.add_argument("--until", default="halt")
            s.add_argument("--max-cycles", type=int)
            s.add_argument("--include", action="append", default=[])
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
