"""Ethernet FCS (CRC-32) — the golden reference the PRU firmware reproduces.

Standard reflected CRC-32: poly 0xEDB88320, init 0xFFFFFFFF, input/output
reflected, final XOR 0xFFFFFFFF.  This is exactly ``zlib.crc32``.  The 4 FCS
octets are appended little-endian (register LSByte first), matching the
bit-serial firmware implementation.
"""

from __future__ import annotations

import struct
import zlib

POLY_REFLECTED = 0xEDB88320


def crc32(data: bytes) -> int:
    """Return the Ethernet FCS value over *data* (== zlib.crc32)."""
    return zlib.crc32(data) & 0xFFFFFFFF


def fcs_bytes(data: bytes) -> bytes:
    """Return the 4 FCS octets to append after *data* (little-endian)."""
    return struct.pack("<I", crc32(data))


def crc32_bitwise(data: bytes) -> int:
    """Reference bit-serial CRC-32, identical to the firmware inner loop."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ POLY_REFLECTED
            else:
                crc >>= 1
    return crc ^ 0xFFFFFFFF
