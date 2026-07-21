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

Symbol-boundary search must *score* every one of the ten candidate bit
offsets rather than stopping at the first that contains a K28.5 comma.  A
comma's 10-bit pattern can occur by chance straddling two data symbols at a
wrong alignment -- this is data-dependent (it happens for some PRNG seeds
and payloads, not others), so "first offset with a comma" is a latent
correctness hole: it can silently lock onto a bogus phase that decodes to
garbage.  The true alignment is distinguished by having the most commas
*and* the fewest (ideally zero) invalid symbols, so both signals must be
combined -- do not simplify this back to first-match.
"""

from __future__ import annotations

from .codec import COMMA_SYMBOLS, build_decode_map
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


def find_comma_offset(bits: list[int],
                       decode_map: dict[int, int] | None = None) -> int | None:
    """Return the bit offset (0..9) that puts symbols on the comma boundary.

    Scores every candidate phase instead of taking the first with a comma:
    a K28.5 bit pattern can appear by chance straddling two data symbols at
    a wrong alignment, so "first match" can lock onto a bogus offset (see
    module docstring).  Among offsets with at least one comma, the winner is
    the one with the fewest invalid symbols, ties broken by the most commas.
    Returns None if no offset has a comma.
    """
    if decode_map is None:
        decode_map = build_decode_map()
    best = None          # (invalid, -commas, offset)
    for offset in range(10):
        commas = 0
        invalid = 0
        for sym in bits_to_symbols(bits[offset:]):
            if sym in COMMA_SYMBOLS:
                commas += 1
            elif sym not in decode_map:
                invalid += 1
        if commas == 0:
            continue
        key = (invalid, -commas, offset)
        if best is None or key < best:
            best = key
    return best[2] if best is not None else None


def decode_capture(raw: bytes, oversample: int = 2,
                   decode_map: dict[int, int] | None = None) -> DecodeResult:
    """Full RX reconstruction: captured bytes -> decoded frames."""
    if decode_map is None:
        decode_map = build_decode_map()
    bits = decimate(samples_from_capture(raw), oversample)
    offset = find_comma_offset(bits, decode_map)
    if offset is None:
        return DecodeResult()
    return decode_symbols(bits_to_symbols(bits[offset:]), decode_map)
