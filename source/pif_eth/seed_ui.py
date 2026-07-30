"""Seed the running UI simulator's DRAM for the pif_eth firmware.

The UI server (`python ui/server.py`) keeps a single shared Simulator, so this
WebSocket client writes control blocks into the same DRAM the browser sees.

Single-core TX workflow (`bert` / `udp`):

  1. python ui/server.py                      # terminal 1 (serves :8080)
  2. Open http://localhost:8080, pick PRU0, paste source/pif_eth/pif_eth_tx.asm,
     click Load.
  3. python3 source/pif_eth/seed_ui.py bert   # terminal 2 (seed + arm 1 frame)
  4. In the browser click Run, then Stop after a moment (Run streams batches of
     1000 instructions until you Stop or the core halts; one armed frame is
     ~20000 instructions and finishes near-instantly). Watch the Peripheral
     Interface panel; read DRAM0 @0x0410 (frame counter) and @0x0500 (frame
     bytes) in a Memory panel. Re-run seed_ui.py to arm the next frame.

Two-core 125 Mbaud TX+RX workflow (`bert125`) — see the "Run the 125 Mbaud
TX+RX demo in the browser UI" section of README.md for the full walkthrough.

Usage: python3 source/pif_eth/seed_ui.py [bert|udp|bert125] [ws-url]
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
from pif_eth.rx_driver import (C_GO, C_MODE, C_PLEN, C_RXCFG, C_SEED,  # noqa: E402
                               LUT1_ADDR, S_BITERR, S_CAPBYTES, S_CRCOK,
                               S_EOF, S_FRAMES, S_OVF, S_SYMERR, S_TOTBITS,
                               rxcfg_word)

# n_tx=2 (125.00 Mbaud) is the only rung pif_eth_tx_n2_skipchecks.asm supports;
# RX oversamples at exactly 2x, so n_rx = n_tx // 2.
N_TX_125 = 2
N_RX_125 = N_TX_125 // 2


def _u32(v: int) -> list[int]:
    return list((v & 0xFFFFFFFF).to_bytes(4, "little"))


async def seed(kind: str, url: str) -> None:
    if kind == "bert":
        mode, plen, preload = MODE_PRNG, framelib.BERT_PAYLOAD_LEN, None
    elif kind == "udp":
        udp = framelib.build_udp_frame()
        mode, plen, preload = MODE_PRELOAD, len(udp.l2), list(udp.l2)
    elif kind == "bert125":
        # RX (pif_eth_rx_o1_raw.asm) only implements BERT bit-error counting
        # (mode=0); there is no RX-side UDP path.
        mode, plen, preload = MODE_PRNG, framelib.BERT_PAYLOAD_LEN, None
    else:
        raise SystemExit(f"unknown kind {kind!r} (use 'bert', 'udp' or 'bert125')")

    writes = [
        (LUT_ADDR, list(codec.build_dram0_lut())),
        (A_NUMF, _u32(100)), (A_MODE, _u32(mode)),
        (A_SEED, _u32(DEFAULT_SEED)), (A_PLEN, _u32(plen)),
        (A_FCNT, _u32(0)), (A_BURST, _u32(0)),
    ]
    if preload is not None:
        writes.append((A_COREBUF, preload))

    if kind == "bert125":
        # PRU1's RX firmware reads mode/seed/payload_len/rxcfg once at boot
        # (before frame_loop's go_wait), so these must land in DRAM1 before
        # PRU1 is stepped/run in the browser -- run this script right after
        # Load on both cores, before clicking Run.
        writes += [
            (LUT1_ADDR, list(codec.build_dram1_decode_lut())),
            (C_MODE, _u32(mode)), (C_SEED, _u32(DEFAULT_SEED)),
            (C_PLEN, _u32(plen)), (C_RXCFG, _u32(rxcfg_word(N_RX_125))),
            (S_FRAMES, _u32(0)), (S_CAPBYTES, _u32(0)), (S_OVF, _u32(0)),
            (S_SYMERR, _u32(0)), (S_CRCOK, _u32(0)), (S_BITERR, _u32(0)),
            (S_TOTBITS, _u32(0)), (S_EOF, _u32(0)),
        ]

    writes.append((A_GOFLAG, _u32(1)))          # arm TX last
    if kind == "bert125":
        writes.append((C_GO, _u32(1)))          # arm RX last too

    async with websockets.connect(url) as ws:
        for addr, data in writes:
            await ws.send(json.dumps({"action": "write_memory", "addr": addr,
                                      "data": data}))
            msg = json.loads(await ws.recv())
            if msg.get("type") == "error":
                raise SystemExit(f"write @0x{addr:04X} failed: {msg['errors']}")

    if kind == "bert125":
        print("seeded DRAM0 (PRU0 TX) + DRAM1 (PRU1 RX, n_rx=1) and armed "
              "1 frame — click Run in the UI")
    else:
        print(f"seeded DRAM0 for '{kind}' and armed 1 frame — click Run in the UI")


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else "bert"
    url = sys.argv[2] if len(sys.argv) > 2 else "ws://localhost:8080/ws"
    asyncio.run(seed(kind, url))
