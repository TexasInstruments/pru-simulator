"""BERT frame builder for pif_eth_100: 200 B xorshift32 payload + 4 B FCS.

UDP was deliberately not carried over (spec section 2: BERT only).
"""

from __future__ import annotations

from dataclasses import dataclass

from .crc32 import fcs_bytes
from .prng import DEFAULT_SEED, prng_bytes

BERT_PAYLOAD_LEN = 200


@dataclass
class Frame:
    """``core`` is what gets 8b/10b coded (payload + FCS); ``l2`` is the payload."""
    kind: str
    core: bytes
    l2: bytes


def build_bert_frame(seed: int = DEFAULT_SEED) -> Frame:
    payload = prng_bytes(BERT_PAYLOAD_LEN, seed)
    return Frame(kind="bert", core=payload + fcs_bytes(payload), l2=payload)
