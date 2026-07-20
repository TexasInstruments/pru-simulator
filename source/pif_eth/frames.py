"""Example Ethernet frame builders.

Two frame types, matching the project brief:

* **BERT frame (option 1)** — no preamble, no Ethernet header.  128 payload
  octets from the xorshift32 PRNG, followed by a 4-octet CRC-32.  Total 132
  octets.  Intended for bit-error-rate testing; it is *not* a valid Ethernet
  frame (Wireshark will show it as malformed, which is expected).

* **UDP frame (option 2)** — a standard Ethernet/IPv4/UDP frame carrying the
  payload ``"Hello World Text"``, padded to the 60-octet Ethernet minimum, with
  a 4-octet FCS (CRC-32).  A valid frame that Wireshark dissects cleanly.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .crc32 import fcs_bytes
from .prng import DEFAULT_SEED, prng_bytes

BERT_PAYLOAD_LEN = 128

# UDP frame fixed fields
_DST_MAC = bytes.fromhex("020000000002")
_SRC_MAC = bytes.fromhex("020000000001")
_ETHERTYPE_IPV4 = 0x0800
_SRC_IP = bytes(int(x) for x in "192.168.0.1".split("."))
_DST_IP = bytes(int(x) for x in "192.168.0.2".split("."))
_SRC_PORT = 1234
_DST_PORT = 5678
_UDP_TEXT = b"Hello World Text"
_ETH_MIN_LEN = 60   # minimum Ethernet frame excluding FCS


@dataclass
class Frame:
    """A built frame.  ``core`` is the on-wire content that gets 8b/10b coded
    and transmitted (payload + FCS).  ``l2`` is the frame without FCS (what
    goes into the pcap).  ``kind`` is ``"bert"`` or ``"udp"``."""
    kind: str
    core: bytes     # bytes actually serialised over the line (includes FCS)
    l2: bytes       # frame body without FCS, for pcap


def _inet_checksum(data: bytes) -> int:
    if len(data) & 1:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def build_bert_frame(seed: int = DEFAULT_SEED) -> Frame:
    payload = prng_bytes(BERT_PAYLOAD_LEN, seed)
    core = payload + fcs_bytes(payload)
    return Frame(kind="bert", core=core, l2=payload)


def build_udp_frame(text: bytes = _UDP_TEXT) -> Frame:
    udp_len = 8 + len(text)
    ip_total = 20 + udp_len

    ip = bytearray(struct.pack(
        ">BBHHHBBH4s4s",
        0x45, 0x00, ip_total, 0x0001, 0x0000, 64, 17, 0x0000, _SRC_IP, _DST_IP))
    ip[10:12] = struct.pack(">H", _inet_checksum(bytes(ip)))

    # UDP checksum over pseudo-header + UDP header + data.
    pseudo = _SRC_IP + _DST_IP + bytes([0, 17]) + struct.pack(">H", udp_len)
    udp = bytearray(struct.pack(">HHHH", _SRC_PORT, _DST_PORT, udp_len, 0x0000)) + text
    csum = _inet_checksum(pseudo + bytes(udp))
    udp[6:8] = struct.pack(">H", csum if csum != 0 else 0xFFFF)

    l2 = _DST_MAC + _SRC_MAC + struct.pack(">H", _ETHERTYPE_IPV4) + bytes(ip) + bytes(udp)
    if len(l2) < _ETH_MIN_LEN:
        l2 = l2 + bytes(_ETH_MIN_LEN - len(l2))    # Ethernet zero padding
    core = l2 + fcs_bytes(l2)
    return Frame(kind="udp", core=core, l2=l2)
