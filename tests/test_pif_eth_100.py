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


# --- TX firmware + driver core (Task 4) --------------------------------------
from perif.perif_channel import PerifChannel             # noqa: E402
from perif.perif_registers import PerifRegisters         # noqa: E402
from pif_eth_100 import run_100 as r100                  # noqa: E402


def test_register_words_decode_to_the_clock_plan():
    assert (r100.TXCFG_100, r100.RXCFG_100) == (0x00020010, 0x0000801F)
    regs = PerifRegisters(base_addr=0x26100)
    regs.write(0x26100, r100.RXCFG_100.to_bytes(4, "little"))
    regs.write(0x26104, r100.TXCFG_100.to_bytes(4, "little"))
    assert regs.get_rx_div_factor() == 0 and regs.get_rx_div_factor_frac() == 1
    assert regs.get_rx_sample_size() == 7 and regs.get_rx_sb_pol() == 1
    assert regs.get_rx_clk_sel() == 1
    assert regs.get_tx_div_factor() == 2 and regs.get_tx_div_factor_frac() == 0
    assert regs.get_tx_clk_sel() == 1
    ch = PerifChannel(0, regs, core_clock_mhz=300.0)
    assert ch.tx_clock_period_ns() == 10.0
    assert ch.rx_clock_period_ns() == 5.0


@pytest.mark.parametrize("n,ok", [(200, True), (252, True), (4, True), (0, False),
                                  (202, False), (253, False), (256, False)])
def test_validate_payload_len(n, ok):
    if ok:
        r100.validate_payload_len(n)
    else:
        with pytest.raises(ValueError):
            r100.validate_payload_len(n)


def test_burst_fifo_bytes():
    assert r100.burst_fifo_bytes(200) == 263 == r100.BURST_FIFO_BYTES
    assert r100.burst_fifo_bytes(128) == 173


def test_tx_self_configures_n3_at_300mhz():
    sim = r100.build_sim(load_rx=False)
    assert sim.cores["pru0"].clock_mhz == 300.0 == sim.cores["pru1"].clock_mhz
    regs = sim._perif["pru0"].registers
    assert regs.get_tx_div_factor() == 2 and regs.get_tx_div_factor_frac() == 0
    assert regs.get_tx_clk_sel() == 1
    assert sim._perif["pru0"].channels[0].tx_clock_period_ns() == 10.0


def test_run_until_label_raises_on_budget():
    sim = r100.build_sim(load_rx=False)
    assert not sim.load("pru1", "spin:\n    jmp spin\nnever:\n    jmp never\n")
    with pytest.raises(RuntimeError, match="never"):
        r100.run_until_label(sim, "never", max_lead_steps=64)


@pytest.fixture(scope="module")
def tx_only():
    return r100.run_tx_only(DEFAULT_SEED, num_frames=3)


def test_tx_only_frames_decode(tx_only):
    assert tx_only.frames_ok == [True, True, True]
    assert tx_only.invalid_symbols == [0, 0, 0]
    assert tx_only.burst_pushed == [263, 263, 263]
    assert tx_only.clean


def test_tx_bit_period_and_gapless_bursts(tx_only):
    assert tx_only.tx_period_ns == 10.0
    assert tx_only.min_spacing_ns == pytest.approx(10.0, abs=1e-6)
    assert tx_only.spacing_on_grid
    assert len(tx_only.t_go_ns) == 3 == len(tx_only.t_end_ns)
    for go, end in zip(tx_only.t_go_ns, tx_only.t_end_ns):
        assert end - go == pytest.approx(21040.0, abs=1e-6)


# --- RX baseline over the loopback (Task 5) ----------------------------------

def test_rx_self_configures_fractional_divider():
    sim = r100.build_sim(rx="base")
    regs = sim._perif["pru1"].registers
    assert regs.get_rx_div_factor() == 0 and regs.get_rx_div_factor_frac() == 1
    assert regs.get_rx_sample_size() == 7
    assert sim._perif["pru1"].channels[0].rx_clock_period_ns() == 5.0


def test_rx_defaults_rxcfg_when_control_word_is_zero():
    sim = r100.build_sim(rx="base", rxcfg=0)
    assert sim._perif["pru1"].registers.get_shared_config()["rxcfg"] == r100.RXCFG_100


def test_build_sim_rejects_bad_payload_len():
    with pytest.raises(ValueError):
        r100.build_sim(payload_len=256)


@pytest.fixture(scope="module")
def loop_base():
    return r100.run_loopback(DEFAULT_SEED, num_frames=3, rx="base")


def test_loopback_frames_clean(loop_base):
    assert loop_base.clean, loop_base.frames
    assert not loop_base.anchor_risk
    for i, f in enumerate(loop_base.frames):
        assert f.frames == i + 1
        assert f.frame_ok and f.eof_status == 1 and f.tot_bits == 1600
        assert 520 <= f.cap_bytes <= 532
    assert len(loop_base.t_go_ns) == 3 == len(loop_base.t_end_ns)


def test_rx_hot_loop_is_8_cycles_and_fifo_never_fills(loop_base):
    # and, mov r31, sbbo(+1 write stall), add, qbeq, jmp  (+1 for the qbbc fall-through)
    assert loop_base.hot_deltas == {1: {1}, 2: {1}, 3: {2}, 4: {1}, 5: {1}, 6: {1}}
    assert loop_base.hot_loop_cycles_per_byte == 8
    assert loop_base.hot_loop_cycles_per_byte <= 12          # budget: 8 samples x 1.5
    assert loop_base.max_rx_fifo <= 2
    assert len(loop_base.rx_post_cycles) == 3
    assert all(c > 0 for c in loop_base.rx_post_cycles)


@pytest.mark.parametrize("shift", r100.EDGE_PHASE_SHIFTS_NS)
def test_all_edge_phases_clean(shift):
    r = r100.run_loopback(DEFAULT_SEED, num_frames=1,
                          latency_ns=r100.LOOPBACK_LATENCY_NS + shift)
    assert r.clean, r.frames


@pytest.mark.parametrize("seed", [1, 2])
def test_other_seeds_clean(seed):
    r = r100.run_loopback(seed, num_frames=2)
    assert r.clean, r.frames
