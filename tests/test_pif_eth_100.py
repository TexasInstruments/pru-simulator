"""pif_eth_100: 100 Mbaud 8b/10b BERT TX (PRU0) + RX (PRU1) at 300 MHz (n_tx=3).

Spec: docs/superpowers/specs/2026-09-30-pif-eth-100-design.md
"""
import sys
import zlib
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "source"))

from pif_eth_100 import codec, frames, rx_reference      # noqa: E402
from pif_eth_100.crc32 import fcs_bytes                   # noqa: E402
from pif_eth_100.prng import DEFAULT_SEED, prng_bytes     # noqa: E402


def _encode_burst(core: bytes) -> list[int]:
    """On-wire bits of one TX burst: 4 commas, core octets, 2 commas (no pad)."""
    rd = codec.RD_MINUS
    syms = []
    for _ in range(4):
        s, rd = codec.encode_comma(rd)
        syms.append(s)
    for b in core:
        s, rd = codec.encode_byte(b, rd)
        syms.append(s)
    for _ in range(2):
        s, rd = codec.encode_comma(rd)
        syms.append(s)
    return [(s >> i) & 1 for s in syms for i in range(9, -1, -1)]


def _bits_to_capture(bits, oversample=2, skew=0) -> bytes:
    """Oversample bits and pack 8 samples per byte, MSB = oldest (RX FIFO format)."""
    samples = [b for b in bits for _ in range(oversample)][skew:]
    samples = samples[:len(samples) // 8 * 8]
    out = bytearray()
    for i in range(0, len(samples), 8):
        v = 0
        for s in samples[i:i + 8]:
            v = (v << 1) | s
        out.append(v)
    return bytes(out)


# --- pure Python: golden models (Task 3) ------------------------------------

def test_bert_frame_is_200_payload_plus_fcs():
    f = frames.build_bert_frame(DEFAULT_SEED)
    assert frames.BERT_PAYLOAD_LEN == 200
    assert f.kind == "bert"
    assert len(f.core) == 204
    assert f.core[:200] == prng_bytes(200, DEFAULT_SEED) == f.l2
    assert f.core[200:] == fcs_bytes(f.core[:200])
    assert int.from_bytes(f.core[200:], "little") == zlib.crc32(f.core[:200]) & 0xFFFFFFFF


def test_burst_geometry_210_symbols_2104_bits_263_fifo_bytes():
    bits = _encode_burst(frames.build_bert_frame().core)
    assert len(bits) == 2100                       # 210 symbols x 10 bits
    padded = len(bits) + (-len(bits)) % 8          # flush_pad to a whole byte
    assert padded == 2104
    assert padded // 8 == 263


def test_codec_roundtrip_all_octets_both_rd():
    dm = codec.build_decode_map()
    for b in range(256):
        for rd in (codec.RD_MINUS, codec.RD_PLUS):
            sym, _ = codec.encode_byte(b, rd)
            assert dm[sym] == b


def test_dram1_decode_lut_valid_and_comma_bits():
    lut = codec.build_dram1_decode_lut()
    assert len(lut) == 2048
    entry = lambda code: int.from_bytes(lut[code * 2:code * 2 + 2], "little")  # noqa: E731
    for b in range(256):
        sym, _ = codec.encode_byte(b, codec.RD_MINUS)
        assert entry(sym) & 0xFF == b and (entry(sym) >> 8) & 1 == 1
    for sym in codec.COMMA_SYMBOLS:
        assert (entry(sym) >> 11) & 1 == 1
    assert (entry(0) >> 8) & 1 == 0


@pytest.mark.parametrize("skew", range(8))
def test_rx_reference_recovers_200b_frame_at_any_skew(skew):
    core = frames.build_bert_frame().core
    raw = _bits_to_capture(_encode_burst(core), oversample=2, skew=skew)
    res = rx_reference.decode_capture(raw, oversample=2)
    assert res.invalid_symbols == 0
    assert core in res.frames
