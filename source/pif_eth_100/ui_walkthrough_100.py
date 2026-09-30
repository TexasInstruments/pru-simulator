"""Execute USERS_GUIDE.md against a running UI server through the same HTTP and
WebSocket endpoints the browser buttons use (load, perif_loopback,
write_memory, run_multicore, read_memory, /config/clock_speed).

Prerequisite (guide step 1): the server runs with both cores at 300 MHz:
    cp config/memory_pif_eth_100.cfg memory.cfg && python3 ui/server.py
or pass --set-clock to switch a running server (PUT /config/clock_speed
rebuilds the UI simulator, exactly like the dropdown).

    python3 source/pif_eth_100/ui_walkthrough_100.py [--frames 2] [--rx base|fast]
Exit code 0 = every frame PASS.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

import websockets

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from pif_eth_100 import run_100 as r                       # noqa: E402
from pif_eth_100 import seed_ui_100 as ui                  # noqa: E402
from pif_eth_100.prng import DEFAULT_SEED                  # noqa: E402

RUN_CHUNK = 5000             # lead (PRU0) instructions per run_multicore request


def put_clock(http: str, mhz: int) -> dict:
    req = urllib.request.Request(f"{http}/config/clock_speed", method="PUT",
                                 data=json.dumps({"mhz": mhz}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


async def walkthrough(ws_url: str, http: str, frames: int, rx: str, set_clock: bool) -> bool:
    mhz = ui.check_clock(http)
    print(f"[step 1-2] UI clock {mhz:g} MHz")
    if mhz != r.CORE_MHZ:
        if not set_clock:
            raise SystemExit("server is not at 300 MHz: restart it with "
                             "config/memory_pif_eth_100.cfg copied to memory.cfg, "
                             "or pass --set-clock")
        print(f"[step 1b] PUT /config/clock_speed 300 -> {put_clock(http, 300)}")
    all_ok = True
    async with websockets.connect(ws_url, max_size=None) as ws:
        for core, name in (("pru0", r.TX_FIRMWARE), ("pru1", r.RX_FIRMWARE[rx])):
            await ui._rpc(ws, {"action": "load", "core": core,
                               "source": (_HERE / name).read_text(),
                               "filename": f"pif_eth_100/{name}"}, want="state")
            print(f"[step 4] loaded {name} -> {core}")
        for core in ("pru0", "pru1"):          # Load keeps the old PC/registers: Reset both
            await ui._rpc(ws, {"action": "reset", "core": core}, want="state")
        print("[step 4b] Reset PRU0 + PRU1 (PC/registers to 0)")
        await ui.seed_on(ws, DEFAULT_SEED)
        print(f"[step 5] seeded DRAM0/DRAM1, loopback ch0 on "
              f"(latency {r.LOOPBACK_LATENCY_NS:.4f} ns), frame 1 armed")
        for i in range(frames):
            steps = 0
            while True:
                await ui._rpc(ws, {"action": "run_multicore", "core": "pru0",
                                   "partner": "pru1", "max_steps": RUN_CHUNK},
                              want="state", count=2)
                steps += RUN_CHUNK
                stats = await ui.read_stats_on(ws)
                if stats["frames"] >= i + 1:
                    break
                if steps >= 2_000_000:
                    raise SystemExit(f"frame {i + 1}: no RX result after {steps} lead steps")
            bad = ui.verdict(stats)
            all_ok &= not bad
            print(f"[step 6-7] frame {i + 1} after {steps} lead instr: {stats} -> "
                  f"{'PASS' if not bad else 'FAIL ' + ','.join(bad)}")
            if i + 1 < frames:
                await ui.arm_on(ws)
                print("[step 8] armed next frame")
    print(f"UI WALKTHROUGH {'PASS' if all_ok else 'FAIL'} ({frames} frames, {rx} RX)")
    return all_ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="scripted pif_eth_100 UI walkthrough")
    ap.add_argument("--frames", type=int, default=2)
    ap.add_argument("--rx", choices=("base", "fast"), default="base")
    ap.add_argument("--set-clock", action="store_true")
    ap.add_argument("--ws", default="ws://localhost:8080/ws")
    ap.add_argument("--http", default="http://localhost:8080")
    args = ap.parse_args(argv)
    ok = asyncio.run(walkthrough(args.ws, args.http, args.frames, args.rx, args.set_clock))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
