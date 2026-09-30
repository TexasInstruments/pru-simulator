"""Arm the browser UI's shared simulator for the pif_eth_100 TX+RX demo.

The UI server (python3 ui/server.py) keeps ONE shared Simulator; this client
writes the same DRAM the browser shows, over the server's WebSocket.

  python3 source/pif_eth_100/seed_ui_100.py          # seed: loopback + DRAM + arm frame 1
  python3 source/pif_eth_100/seed_ui_100.py arm      # arm the next frame
  python3 source/pif_eth_100/seed_ui_100.py status   # read + judge the RX stats block

Run `seed` AFTER loading both cores and BEFORE the first Run: both firmwares
read their control blocks once, at boot. See USERS_GUIDE.md.
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

from pif_eth_100 import codec                              # noqa: E402
from pif_eth_100 import run_100 as r                       # noqa: E402
from pif_eth_100.prng import DEFAULT_SEED                  # noqa: E402

UI_NUM_FRAMES = 100          # TX frame budget; each frame still needs its own `arm`
FIELDS = ("frames", "cap_bytes", "rx_ovf", "symbol_errors", "crc_ok",
          "bit_err", "tot_bits", "eof_status")
EXPECTED = {"rx_ovf": 0, "symbol_errors": 0, "crc_ok": 1, "bit_err": 0,
            "tot_bits": r.PAYLOAD_LEN * 8, "eof_status": 1}


def _u32(v: int) -> bytes:
    return (v & 0xFFFFFFFF).to_bytes(4, "little")


def arm_writes() -> list[tuple[int, bytes]]:
    """TX go first, RX go last. Both writes land before either core steps, so the
    order is harmless (run_100.py arms RX first for the same reason); after a
    go, RX arms in a few cycles and TX needs ~2.7k cycles of frame prep."""
    return [(r.T_GOFLAG, _u32(1)), (r.C_GO, _u32(1))]


def build_writes(seed: int = DEFAULT_SEED) -> list[tuple[int, bytes]]:
    w = [(r.LUT0_ADDR, codec.build_dram0_lut()),
         (r.T_NUMF, _u32(UI_NUM_FRAMES)), (r.T_MODE, _u32(0)), (r.T_SEED, _u32(seed)),
         (r.T_PLEN, _u32(r.PAYLOAD_LEN)), (r.T_FCNT, _u32(0)), (r.T_BURST, _u32(0)),
         (r.LUT1_ADDR, codec.build_dram1_decode_lut()),
         (r.C_MODE, _u32(0)), (r.C_SEED, _u32(seed)), (r.C_PLEN, _u32(r.PAYLOAD_LEN)),
         (r.C_RXCFG, _u32(r.RXCFG_100))]
    w += [(a, _u32(0)) for a in (r.S_FRAMES, r.S_CAPBYTES, r.S_OVF, r.S_SYMERR,
                                 r.S_CRCOK, r.S_BITERR, r.S_TOTBITS, r.S_EOF)]
    return w + arm_writes()


def parse_stats(data: bytes) -> dict:
    return {n: int.from_bytes(data[4 * i:4 * i + 4], "little") for i, n in enumerate(FIELDS)}


def verdict(stats: dict) -> list[str]:
    """Names of the fields that are wrong for a clean frame; [] = PASS."""
    bad = [] if stats["frames"] >= 1 else ["frames"]
    return bad + [k for k, v in EXPECTED.items() if stats[k] != v]


def check_clock(http: str) -> float:
    with urllib.request.urlopen(f"{http}/config/clock_speed", timeout=10) as resp:
        return float(json.load(resp)["mhz"])


async def _rpc(ws, msg: dict, want: str, count: int = 1, timeout: float = 60.0) -> list[dict]:
    """Send *msg*, return the next *count* replies of type *want* (others skipped)."""
    await ws.send(json.dumps(msg))
    got: list[dict] = []
    while len(got) < count:
        reply = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if reply.get("type") == "error":
            raise SystemExit(f"{msg.get('action')} failed: {reply.get('errors')}")
        if reply.get("type") == want:
            got.append(reply)
    return got


async def seed_on(ws, seed_value: int = DEFAULT_SEED) -> None:
    await _rpc(ws, {"action": "perif_loopback", "core": "pru0", "channel": 0,
                    "enabled": True, "latency_ns": r.LOOPBACK_LATENCY_NS,
                    "jitter_ns": 0.0, "drift_ppm": 0.0}, want="perif_ok")
    for addr, data in build_writes(seed_value):
        await _rpc(ws, {"action": "write_memory", "addr": addr, "data": list(data)},
                   want="memory_written")


async def arm_on(ws) -> None:
    for addr, data in arm_writes():
        await _rpc(ws, {"action": "write_memory", "addr": addr, "data": list(data)},
                   want="memory_written")


async def read_stats_on(ws) -> dict:
    (msg,) = await _rpc(ws, {"action": "read_memory", "addr": r.STATS_ADDR,
                             "length": 32, "tag": "seed_ui_100"}, want="memory")
    return parse_stats(bytes(msg["data"]))


async def seed(ws_url: str, http: str, seed_value: int = DEFAULT_SEED) -> None:
    mhz = check_clock(http)
    if mhz != r.CORE_MHZ:
        raise SystemExit(f"UI simulator runs at {mhz:g} MHz; pif_eth_100 needs 300 MHz. "
                         f"See USERS_GUIDE.md step 1 (copy config/memory_pif_eth_100.cfg "
                         f"to memory.cfg, or pick 300 MHz in the dropdown BEFORE loading).")
    async with websockets.connect(ws_url, max_size=None) as ws:
        await seed_on(ws, seed_value)
    print(f"seeded DRAM0 (TX) + DRAM1 (RX, RXCFG 0x{r.RXCFG_100:08X}), loopback ch0 "
          f"latency {r.LOOPBACK_LATENCY_NS:.4f} ns, frame 1 armed -- click Run")


async def arm(ws_url: str) -> None:
    async with websockets.connect(ws_url, max_size=None) as ws:
        await arm_on(ws)
    print("armed the next frame -- click Run")


async def status(ws_url: str) -> bool:
    async with websockets.connect(ws_url, max_size=None) as ws:
        stats = await read_stats_on(ws)
    for k in FIELDS:
        print(f"  {k:<14} {stats[k]}")
    bad = verdict(stats)
    print("PASS" if not bad else f"FAIL ({', '.join(bad)}) -- see USERS_GUIDE.md troubleshooting")
    return not bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="seed/arm/status for the pif_eth_100 UI demo")
    ap.add_argument("command", nargs="?", default="seed", choices=("seed", "arm", "status"))
    ap.add_argument("--ws", default="ws://localhost:8080/ws")
    ap.add_argument("--http", default="http://localhost:8080")
    ap.add_argument("--seed", type=lambda s: int(s, 0), default=DEFAULT_SEED)
    args = ap.parse_args(argv)
    if args.command == "seed":
        asyncio.run(seed(args.ws, args.http, args.seed))
        return 0
    if args.command == "arm":
        asyncio.run(arm(args.ws))
        return 0
    return 0 if asyncio.run(status(args.ws)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
