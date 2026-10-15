"""Generate enc_lut.h -- the 8b/10b encode table as a C header.

codec.build_dram0_lut() is the source of truth; this script only reformats
its 1 KB DRAM0 image as 256 little-endian u32 words so the R5F can copy it
into DRAM0 at offset 0x0000 for the TX firmware (LBCO c24, 4 bytes).

Usage:
  python3 source/pif_eth/gen_enc_lut.py           # rewrite enc_lut.h
  python3 source/pif_eth/gen_enc_lut.py --check   # verify it is in sync
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from pif_eth import codec                               # noqa: E402

HEADER_PATH = _HERE / "r5f" / "enc_lut.h"
WORDS = 256

PREAMBLE = '''/*
 * enc_lut.h -- 8b/10b encode table for the pif_eth PRU0 TX firmware.
 *
 * GENERATED FILE -- do not edit by hand.
 * Regenerate with: python3 source/pif_eth/gen_enc_lut.py
 * Source of truth: source/pif_eth/codec.py, build_dram0_lut().
 *
 * 256 u32 entries indexed by octet, placed at DRAM0 offset 0x0000.
 * Entry layout for octet b:
 *
 *     [9:0]    code10 to send when the running disparity is negative
 *     [10]     RD after the symbol on the negative branch (1 = positive)
 *     [25:16]  code10 to send when the running disparity is positive
 *     [26]     RD after the symbol on the positive branch (1 = positive)
 */

#ifndef PIF_ETH_ENC_LUT_H
#define PIF_ETH_ENC_LUT_H

#include <stdint.h>

#define PIF_ETH_ENC_LUT_ADDR    0x0000u   /* DRAM0 offset */
#define PIF_ETH_ENC_LUT_ENTRIES 256u

static const uint32_t enc_lut[256] = {'''

EPILOGUE = """};

#endif /* PIF_ETH_ENC_LUT_H */
"""


def render(blob: bytes) -> str:
    if len(blob) != WORDS * 4:
        raise ValueError(f"expected {WORDS * 4} bytes, got {len(blob)}")
    words = [int.from_bytes(blob[i:i + 4], "little")
             for i in range(0, len(blob), 4)]
    lines = [PREAMBLE]
    for i in range(0, WORDS, 4):
        body = " ".join(f"0x{w:08X}u," for w in words[i:i + 4])
        lines.append(f"    {body}  /* octet 0x{i:02X}-0x{i + 3:02X} */")
    return "\n".join(lines) + "\n" + EPILOGUE


def main(argv: list[str]) -> int:
    text = render(codec.build_dram0_lut())
    if "--check" in argv[1:]:
        if not HEADER_PATH.exists() or HEADER_PATH.read_text() != text:
            print(f"{HEADER_PATH.name} is missing or stale -- run without --check")
            return 1
        print(f"{HEADER_PATH.name} is up to date")
        return 0
    HEADER_PATH.write_text(text)
    print(f"wrote {HEADER_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
