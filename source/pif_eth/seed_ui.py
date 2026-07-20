"""Seed the running UI simulator's DRAM0 for the pif_eth firmware.

The UI server (`python ui/server.py`) keeps a single shared Simulator, so this
WebSocket client writes the 8b/10b LUT + control block into the same DRAM0 the
browser sees.  Workflow:

  1. python ui/server.py                      # terminal 1 (serves :8080)
  2. Open http://localhost:8080, pick PRU0, paste source/pif_eth/pif_eth_tx.asm,
     click Load.
  3. python3 source/pif_eth/seed_ui.py bert   # terminal 2 (seed + arm 1 frame)
  4. In the browser click Run, then Stop after a moment (Run streams batches of
     1000 instructions until you Stop or the core halts; one armed frame is
     ~20000 instructions and finishes near-instantly). Watch the Peripheral
     Interface panel; read DRAM0 @0x0410 (frame counter) and @0x0500 (frame
     bytes) in a Memory panel. Re-run seed_ui.py to arm the next frame.

Usage: python3 source/pif_eth/seed_ui.py [bert|udp] [ws-url]
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import websockets

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from pif_eth import codec, frames as framelib          # noqa: E402
from pif_eth.driver import (A_BURST, A_FCNT, A_GOFLAG, A_MODE, A_NUMF,  # noqa: E402
                            A_PLEN, A_SEED, A_COREBUF, LUT_ADDR,
                            MODE_PRELOAD, MODE_PRNG)
from pif_eth.prng import DEFAULT_SEED                    # noqa: E402


def _u32(v: int) -> list[int]:
    return list((v & 0xFFFFFFFF).to_bytes(4, "little"))


async def seed(kind: str, url: str) -> None:
    if kind == "bert":
        mode, plen, preload = MODE_PRNG, framelib.BERT_PAYLOAD_LEN, None
    elif kind == "udp":
        udp = framelib.build_udp_frame()
        mode, plen, preload = MODE_PRELOAD, len(udp.l2), list(udp.l2)
    else:
        raise SystemExit(f"unknown kind {kind!r} (use 'bert' or 'udp')")

    writes = [
        (LUT_ADDR, list(codec.build_dram0_lut())),
        (A_NUMF, _u32(100)), (A_MODE, _u32(mode)),
        (A_SEED, _u32(DEFAULT_SEED)), (A_PLEN, _u32(plen)),
        (A_FCNT, _u32(0)), (A_BURST, _u32(0)),
    ]
    if preload is not None:
        writes.append((A_COREBUF, preload))
    writes.append((A_GOFLAG, _u32(1)))          # arm one frame last

    async with websockets.connect(url) as ws:
        for addr, data in writes:
            await ws.send(json.dumps({"action": "write_memory", "addr": addr,
                                      "data": data}))
            msg = json.loads(await ws.recv())
            if msg.get("type") == "error":
                raise SystemExit(f"write @0x{addr:04X} failed: {msg['errors']}")
    print(f"seeded DRAM0 for '{kind}' and armed 1 frame — click Run in the UI")


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else "bert"
    url = sys.argv[2] if len(sys.argv) > 2 else "ws://localhost:8080/ws"
    asyncio.run(seed(kind, url))
