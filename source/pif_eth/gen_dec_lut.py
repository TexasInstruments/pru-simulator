"""Generate dec_lut.h -- the 8b/10b decode table as a C header.

codec.build_dram1_decode_lut() is the source of truth; this script only
reformats its 2 KB DRAM1 image as 512 little-endian u32 words so C firmware
can memcpy it into DRAM1 at PRU1 core-local 0x0000.

Usage:
  python3 source/pif_eth/gen_dec_lut.py           # rewrite dec_lut.h
  python3 source/pif_eth/gen_dec_lut.py --check   # verify it is in sync
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from pif_eth import codec                               # noqa: E402

HEADER_PATH = _HERE / "dec_lut.h"

WORDS = 512          # 1024 u16 entries packed two per u32
WORDS_PER_LINE = 4

PREAMBLE = '''/*
 * dec_lut.h -- 8b/10b decode table for the pif_eth PRU1 RX firmware.
 *
 * GENERATED FILE -- do not edit by hand.
 * Regenerate with: python3 source/pif_eth/gen_dec_lut.py
 * Source of truth: source/pif_eth/codec.py, build_dram1_decode_lut().
 *
 * Contents are the exact 2 KB DRAM1 image the firmware expects at PRU1
 * core-local offset 0x0000 (host global 0x2000, the DRAM1 base).  The table
 * is 1024 u16 entries indexed by the raw 10-bit codeword, packed here two
 * per little-endian u32 word:
 *
 *     dec_lut[n] bits [15:0]  = entry for symbol 2n
 *     dec_lut[n] bits [31:16] = entry for symbol 2n + 1
 *
 * Entry layout (u16) for codeword c:
 *
 *     [7:0]   decoded octet
 *     [8]     valid       (1 = legal codeword)
 *     [9]     neutral     (1 = disparity 0, RD unchanged)
 *     [10]    RD after the symbol when not neutral (0 = neg, 1 = pos)
 *     [11]    comma       (K28.5)
 *
 * A 10-bit codeword identifies its octet without knowing the running
 * disparity, so one flat table suffices; RD is carried only to validate the
 * stream.  See pif_eth_rx_o1_raw.asm:
 *
 *     lsl  r24, r22, 1          ; LUT offset = symbol * 2
 *     lbco r23, c24, r24, 2     ; decode entry (c24 = own DRAM = DRAM1)
 *
 * Load it with:
 *
 *     memcpy((void *)PIF_ETH_DEC_LUT_ADDR, dec_lut, sizeof dec_lut);
 */

#ifndef PIF_ETH_DEC_LUT_H
#define PIF_ETH_DEC_LUT_H

#include <stdint.h>

/* PRU1 core-local address the RX firmware reads the table from. */
#define PIF_ETH_DEC_LUT_ADDR    0x0000u

/* Number of u16 entries (one per 10-bit codeword). */
#define PIF_ETH_DEC_LUT_ENTRIES 1024u

/* Entry field masks. */
#define PIF_ETH_DEC_OCTET_MASK  0x00FFu
#define PIF_ETH_DEC_VALID       0x0100u
#define PIF_ETH_DEC_NEUTRAL     0x0200u
#define PIF_ETH_DEC_RD_POS      0x0400u
#define PIF_ETH_DEC_COMMA       0x0800u

/* Fetch the u16 entry for a 10-bit codeword out of the packed array. */
#define PIF_ETH_DEC_ENTRY(sym) \\
    ((uint16_t)(dec_lut[(sym) >> 1] >> (((sym) & 1u) * 16)))

static const uint32_t dec_lut[512] = {'''

EPILOGUE = """};

#endif /* PIF_ETH_DEC_LUT_H */
"""


def pack_words(blob: bytes) -> list[int]:
    """Pack the u16 table image into little-endian u32 words."""
    if len(blob) != WORDS * 4:
        raise ValueError(f"expected {WORDS * 4} bytes, got {len(blob)}")
    return [int.from_bytes(blob[i:i + 4], "little")
            for i in range(0, len(blob), 4)]


def render(words: list[int]) -> str:
    lines = [PREAMBLE]
    for i in range(0, len(words), WORDS_PER_LINE):
        body = " ".join(f"0x{w:08X}u," for w in words[i:i + WORDS_PER_LINE])
        lo = i * 2
        hi = lo + WORDS_PER_LINE * 2 - 1
        lines.append(f"    {body}  /* sym 0x{lo:03X}-0x{hi:03X} */")
    return "\n".join(lines) + "\n" + EPILOGUE


def parse(text: str) -> bytes:
    """Recover the table image from a rendered header (round-trip check)."""
    body = text.split("dec_lut[512] = {", 1)[1].split("};", 1)[0]
    words = [int(v, 16) for v in re.findall(r"0x([0-9A-Fa-f]{8})u", body)]
    if len(words) != WORDS:
        raise ValueError(f"expected {WORDS} words in header, got {len(words)}")
    return b"".join(w.to_bytes(4, "little") for w in words)


def main(argv: list[str]) -> int:
    check = "--check" in argv[1:]
    blob = codec.build_dram1_decode_lut()
    text = render(pack_words(blob))

    if parse(text) != blob:
        raise SystemExit("internal error: rendered header does not round-trip")

    if check:
        if not HEADER_PATH.exists():
            print(f"{HEADER_PATH.name} is missing -- run without --check")
            return 1
        if HEADER_PATH.read_text() != text:
            print(f"{HEADER_PATH.name} is stale -- run without --check")
            return 1
        print(f"{HEADER_PATH.name} is up to date")
        return 0

    HEADER_PATH.write_text(text)
    valid = sum(1 for c in range(1024)
                if int.from_bytes(blob[c * 2:c * 2 + 2], "little") & 0x100)
    print(f"wrote {HEADER_PATH} ({HEADER_PATH.stat().st_size} bytes)")
    print(f"{WORDS} u32 words, {valid} valid codewords "
          f"({len(codec.build_decode_map())} data + "
          f"{len(codec.COMMA_SYMBOLS)} comma)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
