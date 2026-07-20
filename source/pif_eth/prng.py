"""xorshift32 PRNG — golden reference for the firmware payload generator.

The BER-test payload bytes are the low 8 bits of successive xorshift32 states
starting from a defined seed.  The firmware reproduces this exactly with three
shift/xor pairs on a 32-bit register.
"""

from __future__ import annotations

MASK32 = 0xFFFFFFFF
DEFAULT_SEED = 0x1BADC0DE


def xorshift32(state: int) -> int:
    """Advance one xorshift32 step and return the new 32-bit state."""
    state &= MASK32
    state ^= (state << 13) & MASK32
    state ^= (state >> 17)
    state ^= (state << 5) & MASK32
    return state & MASK32


def prng_bytes(n: int, seed: int = DEFAULT_SEED) -> bytes:
    """Return *n* payload bytes: low byte of each successive state."""
    out = bytearray(n)
    state = seed & MASK32
    for i in range(n):
        state = xorshift32(state)
        out[i] = state & 0xFF
    return bytes(out)
