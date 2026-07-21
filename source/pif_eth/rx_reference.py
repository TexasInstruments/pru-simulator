"""Host-side golden reference for the PRU1 RX path.

The perif RX FIFO captures the *raw* oversampled shift register: with
``sample_size = 7`` each captured byte is 8 consecutive line samples, MSB
first (oldest sample in bit 7).  At 2x oversampling that is 4 line bits.

Reconstruction is: expand captured bytes to samples, decimate by the
oversample factor, find the symbol boundary by locating a K28.5 comma, then
hand the aligned bit list to the existing 8b/10b decoder.

Decimation is *phase-insensitive* at zero clock drift: both samples of a bit
are identical, so sampling every Nth from any starting phase yields the same
bit sequence.  This does not hold under drift.
"""

from __future__ import annotations

from .codec import COMMA_SYMBOLS
from .decoder import DecodeResult, bits_to_symbols, decode_symbols


def samples_from_capture(raw: bytes) -> list[int]:
    """Expand captured FIFO bytes into raw line samples, oldest first."""
    out: list[int] = []
    for byte in raw:
        for i in range(7, -1, -1):
            out.append((byte >> i) & 1)
    return out


def decimate(samples: list[int], factor: int = 2) -> list[int]:
    """Reduce oversampled samples to line bits by taking every *factor*-th."""
    return samples[::factor]


def find_comma_offset(bits: list[int]) -> int | None:
    """Return the bit offset (0..9) that puts symbols on a comma boundary.

    Scans each candidate phase and returns the first whose symbol grid
    contains a K28.5 comma.  Returns None if no phase yields one.
    """
    for offset in range(10):
        for sym in bits_to_symbols(bits[offset:]):
            if sym in COMMA_SYMBOLS:
                return offset
    return None


def decode_capture(raw: bytes, oversample: int = 2,
                   decode_map: dict[int, int] | None = None) -> DecodeResult:
    """Full RX reconstruction: captured bytes -> decoded frames."""
    bits = decimate(samples_from_capture(raw), oversample)
    offset = find_comma_offset(bits)
    if offset is None:
        return DecodeResult()
    return decode_symbols(bits_to_symbols(bits[offset:]), decode_map)
