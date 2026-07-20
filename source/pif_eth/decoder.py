"""Decode an 8b/10b line-coded bit stream back into frames.

The transmitter emits, on channel 0, a continuous run of 10-bit symbols:
``[comma...] frame0-symbols [comma...] frame1-symbols ...`` where the comma is
K28.5 and serves as idle fill / inter-frame delimiter.  Symbol boundaries are
recovered from the known start of transmission (bit 0 of the first symbol);
the decoder then splits data runs on comma boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .codec import COMMA_SYMBOLS, build_decode_map


@dataclass
class DecodeResult:
    frames: list[bytes] = field(default_factory=list)
    invalid_symbols: int = 0        # symbols not in the code (line errors)
    total_symbols: int = 0


def bits_to_symbols(bits: list[int]) -> list[int]:
    """Chunk an on-wire bit list (first bit first) into 10-bit symbols.

    A trailing partial group (< 10 bits) is dropped.
    """
    n = len(bits) // 10
    syms = []
    for k in range(n):
        v = 0
        for b in bits[k * 10:k * 10 + 10]:
            v = (v << 1) | (b & 1)
        syms.append(v)
    return syms


def decode_symbols(symbols: list[int], decode_map: dict[int, int] | None = None
                   ) -> DecodeResult:
    if decode_map is None:
        decode_map = build_decode_map()
    res = DecodeResult(total_symbols=len(symbols))
    cur = bytearray()
    for sym in symbols:
        if sym in COMMA_SYMBOLS:
            if cur:
                res.frames.append(bytes(cur))
                cur = bytearray()
            continue
        byte = decode_map.get(sym)
        if byte is None:
            res.invalid_symbols += 1
            continue
        cur.append(byte)
    if cur:
        res.frames.append(bytes(cur))
    return res


def decode_bits(bits: list[int], decode_map: dict[int, int] | None = None
                ) -> DecodeResult:
    return decode_symbols(bits_to_symbols(bits), decode_map)
