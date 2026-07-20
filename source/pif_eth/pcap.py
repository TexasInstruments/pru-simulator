"""Minimal classic-format (.pcap) writer, Ethernet link type.

No external dependency: emits the 24-byte global header + per-record headers so
the decoded frames open directly in Wireshark/tshark.
"""

from __future__ import annotations

import struct
from typing import Iterable

_MAGIC = 0xA1B2C3D4
_VERSION = (2, 4)
_LINKTYPE_EN10MB = 1


def write_pcap(path: str, frames: Iterable[bytes], ts_step_us: int = 100) -> int:
    """Write *frames* (each raw L2 bytes) to *path*.  Returns frame count."""
    count = 0
    with open(path, "wb") as fh:
        fh.write(struct.pack("<IHHiIII", _MAGIC, _VERSION[0], _VERSION[1],
                             0, 0, 65535, _LINKTYPE_EN10MB))
        usec = 0
        for frame in frames:
            sec, us = divmod(usec, 1_000_000)
            fh.write(struct.pack("<IIII", sec, us, len(frame), len(frame)))
            fh.write(frame)
            usec += ts_step_us
            count += 1
    return count
