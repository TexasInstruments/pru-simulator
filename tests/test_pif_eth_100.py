"""pif_eth_100: 100 Mbaud 8b/10b BERT TX (PRU0) + RX (PRU1) at 300 MHz (n_tx=3).

Spec: docs/superpowers/specs/2026-09-30-pif-eth-100-design.md
"""
import asyncio
import json
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


# --- throughput + CLI (Task 6) -----------------------------------------------

def test_throughput_figures(loop_base, tx_only):
    t = r100.throughput(loop_base, tx_only)
    assert t["F1_mbaud"] == pytest.approx(100.0, abs=1e-6)
    assert t["F2_mbps"] == pytest.approx(80.0, abs=0.01)
    assert t["F2p_mbps"] == pytest.approx(76.05, abs=0.01)
    assert t["T_burst_ns"] == pytest.approx(21040.0, abs=1e-6)
    assert 0 < t["F3_e2e_mbps"] < t["F3_tx_mbps"] < t["F2p_mbps"]
    assert t["rx_post_cycles"] > 0 and t["tx_gap_ns"] > 0 and t["e2e_gap_ns"] > t["tx_gap_ns"]
    text = r100.format_throughput(t)
    assert "F1" in text and "F3" in text and "Mbit/s" in text


def test_main_smoke(capsys):
    assert r100.main(["--seeds", "1", "--frames", "2"]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "F2" in out and "FAIL" not in out


# --- UI seeding (Task 7) -----------------------------------------------------
from pif_eth_100 import seed_ui_100 as ui                # noqa: E402


def test_seed_ui_writes_cover_both_cores_and_arm_last():
    w = ui.build_writes()
    addrs = [a for a, _ in w]
    d = dict(w)
    assert w[0] == (r100.LUT0_ADDR, codec.build_dram0_lut())
    assert d[r100.LUT1_ADDR] == codec.build_dram1_decode_lut()
    assert d[r100.C_RXCFG] == r100.RXCFG_100.to_bytes(4, "little")
    assert d[r100.T_PLEN] == d[r100.C_PLEN] == (200).to_bytes(4, "little")
    assert d[r100.T_SEED] == d[r100.C_SEED] == DEFAULT_SEED.to_bytes(4, "little")
    assert d[r100.T_MODE] == d[r100.C_MODE] == bytes(4)
    for a in (r100.S_FRAMES, r100.S_CAPBYTES, r100.S_OVF, r100.S_SYMERR,
              r100.S_CRCOK, r100.S_BITERR, r100.S_TOTBITS, r100.S_EOF):
        assert d[a] == bytes(4)
    assert addrs[-2:] == [r100.T_GOFLAG, r100.C_GO]
    assert [v for _, v in w[-2:]] == [(1).to_bytes(4, "little")] * 2
    assert not any(r100.CAP_ADDR <= a < r100.FRAME_ADDR for a in addrs)


def test_seed_ui_verdict_flags_bad_stats():
    good = dict(frames=1, cap_bytes=526, rx_ovf=0, symbol_errors=0, crc_ok=1,
                bit_err=0, tot_bits=1600, eof_status=1)
    raw = b"".join(good[k].to_bytes(4, "little") for k in ui.FIELDS)
    assert ui.parse_stats(raw) == good
    assert ui.verdict(good) == []
    assert set(ui.verdict(dict(good, crc_ok=0, eof_status=0))) == {"crc_ok", "eof_status"}
    assert ui.verdict(dict(good, frames=0)) == ["frames"]


_SRC = _ROOT / "source" / "pif_eth_100"


def _ui_flow_one_frame(rx):
    """Emulate the browser: load both cores, seed with BOTH go flags set before
    either core has run a single instruction, then a paced multi-core Run.
    Nothing waits for PRU1's boot, so RX must arm within TX's frame prep."""
    from simulator import Simulator
    sim = Simulator(config_path=r100.CONFIG_PATH)
    for core, name in (("pru0", r100.TX_FIRMWARE), ("pru1", r100.RX_FIRMWARE[rx])):
        assert not sim.load(core, (_SRC / name).read_text(), include_paths=[str(_SRC)])
    sim.perif_loopback(0, True, latency_ns=r100.LOOPBACK_LATENCY_NS)
    for addr, data in ui.build_writes():
        sim.memory.write(addr, data)
    steps = 0
    while r100.ru32(sim, r100.S_FRAMES) < 1:
        assert steps < 400_000
        sim.step_paced("pru0", "pru1", 1000)
        steps += 1000
    return ui.parse_stats(sim.memory_read(r100.STATS_ADDR, 32))


def test_ui_flow_base_rx_clean():
    assert ui.verdict(_ui_flow_one_frame("base")) == []


def test_seed_refuses_wrong_clock(monkeypatch):
    monkeypatch.setattr(ui, "check_clock", lambda http: 250.0)
    with pytest.raises(SystemExit, match="300 MHz"):
        asyncio.run(ui.seed("ws://127.0.0.1:9/ws", "http://127.0.0.1:9", DEFAULT_SEED))


# --- development-report data (Task 10) ----------------------------------------
from pif_eth_100 import session_stats as ss              # noqa: E402


def _jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_session_stats_dedups_usage_and_filters_messages(tmp_path):
    u = {"input_tokens": 10, "cache_creation_input_tokens": 100,
         "cache_read_input_tokens": 1000, "output_tokens": 5}
    main_recs = [
        {"type": "user", "timestamp": "2026-09-30T11:19:49.000Z",
         "message": {"role": "user", "content": "build pif_eth_100"}},
        {"type": "user", "isMeta": True, "timestamp": "2026-09-30T11:19:50.000Z",
         "message": {"role": "user", "content": "meta"}},
        {"type": "assistant", "timestamp": "2026-09-30T11:20:00.000Z",
         "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": dict(u, output_tokens=2)}},
        {"type": "assistant", "timestamp": "2026-09-30T11:20:01.000Z",
         "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": u}},
        {"type": "user", "timestamp": "2026-09-30T11:20:02.000Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}},
        {"type": "user", "timestamp": "2026-09-30T11:30:00.000Z",
         "message": {"role": "user", "content": "<task-notification>done</task-notification>"}},
    ]
    sub_recs = [
        {"type": "user", "isSidechain": True, "timestamp": "2026-09-30T11:21:00.000Z",
         "message": {"role": "user", "content": "You are the PLANNING architect"}},
        {"type": "assistant", "timestamp": "2026-09-30T11:33:36.000Z",
         "message": {"id": "a1", "model": "claude-opus-5-5", "usage": u}},
    ]
    _jsonl(tmp_path / "s1.jsonl", main_recs)
    (tmp_path / "s1" / "subagents").mkdir(parents=True)
    _jsonl(tmp_path / "s1" / "subagents" / "agent-abc.jsonl", sub_recs)
    m, agents, human = ss.collect(tmp_path, "s1")
    assert m.usage == {"claude-sonnet-5-5": u}             # m1 counted once, max per counter
    assert [h["text"] for h in human] == ["build pif_eth_100"]
    assert len(agents) == 1 and agents[0].name == "abc"
    assert agents[0].usage == {"claude-opus-5-5": u}
    assert agents[0].wall_s == pytest.approx(12 * 60 + 36)
    md = ss.render_markdown(m, agents, human)
    assert "claude-opus-5-5" in md and "build pif_eth_100" in md and "12.6 min" in md


# --- optimised RX (Task 11) ---------------------------------------------------

def test_fast_rx_builds_decimation_lut_on_first_frame():
    sim = r100.build_sim(rx="fast")
    assert sim.memory_read(r100.NIB_LUT_ADDR, 256) == bytes(256)   # not at boot
    r100.wu32(sim, r100.C_GO, 1)
    r100.wu32(sim, r100.T_GOFLAG, 1)
    steps = 0
    while r100.ru32(sim, r100.S_FRAMES) != 1:
        assert steps < 400_000
        r100.step_paced_traced(sim, 256)
        steps += 256
    expected = bytes(((b >> 4) & 8) | ((b >> 3) & 4) | ((b >> 2) & 2) | ((b >> 1) & 1)
                     for b in range(256))
    assert sim.memory_read(r100.NIB_LUT_ADDR, 256) == expected


def test_fast_rx_ui_flow_both_go_flags_at_boot():
    assert ui.verdict(_ui_flow_one_frame("fast")) == []


@pytest.fixture(scope="module")
def loop_fast():
    return r100.run_loopback(DEFAULT_SEED, num_frames=3, rx="fast")


def test_fast_rx_clean_and_identical_to_base(loop_base, loop_fast):
    assert loop_fast.clean, loop_fast.frames
    assert loop_fast.frames == loop_base.frames


@pytest.mark.parametrize("shift", r100.EDGE_PHASE_SHIFTS_NS)
@pytest.mark.parametrize("seed", [DEFAULT_SEED, 1])
def test_fast_equals_base_all_phases(seed, shift):
    lat = r100.LOOPBACK_LATENCY_NS + shift
    b = r100.run_loopback(seed, num_frames=2, latency_ns=lat, rx="base")
    f = r100.run_loopback(seed, num_frames=2, latency_ns=lat, rx="fast")
    assert f.clean, f.frames
    assert f.frames == b.frames


@pytest.mark.parametrize("plen", [128, 252])
def test_fast_equals_base_other_payload_lengths(plen):
    b = r100.run_loopback(DEFAULT_SEED, num_frames=1, payload_len=plen, rx="base")
    f = r100.run_loopback(DEFAULT_SEED, num_frames=1, payload_len=plen, rx="fast")
    assert f.clean, f.frames
    assert f.frames == b.frames


def test_fast_rx_keeps_the_realtime_loop(loop_fast):
    assert loop_fast.hot_deltas == {1: {1}, 2: {1}, 3: {2}, 4: {1}, 5: {1}, 6: {1}}
    assert loop_fast.hot_loop_cycles_per_byte == 8
    assert loop_fast.max_rx_fifo <= 2


def test_fast_rx_post_frame_at_least_2x_faster(loop_base, loop_fast):
    base = sum(loop_base.rx_post_cycles) / len(loop_base.rx_post_cycles)
    fast = sum(loop_fast.rx_post_cycles) / len(loop_fast.rx_post_cycles)
    assert 2 * fast < base


def test_fast_rx_raises_end_to_end_goodput(loop_base, loop_fast, tx_only):
    tb = r100.throughput(loop_base, tx_only)
    tf = r100.throughput(loop_fast, tx_only)
    assert tf["F3_e2e_mbps"] > 1.5 * tb["F3_e2e_mbps"]
    assert tf["F3_e2e_mbps"] < tf["F3_tx_mbps"]


# --- PDF pipeline (Task 13) ---------------------------------------------------

def test_build_pdfs_renders_gfm_tables():
    pytest.importorskip("markdown_it")
    from pif_eth_100 import build_pdfs
    html = build_pdfs.render_html("# T\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", "T")
    assert "<table>" in html and "<h1>T</h1>" in html and 'charset="utf-8"' in html


# --- figures (make_figures.py) -------------------------------------------------

def test_figures_are_wellformed_and_carry_measured_numbers(loop_base, loop_fast, tx_only):
    import xml.etree.ElementTree as ET
    from pif_eth_100 import make_figures as mf
    tp = {"base": r100.throughput(loop_base, tx_only), "fast": r100.throughput(loop_fast, tx_only)}
    loops = {"base": loop_base, "fast": loop_fast}
    tl = mf.timeline_svg(tp, loops)
    dp = mf.datapath_svg(tp, tx_only)
    for svg in (tl, dp):
        root = ET.fromstring(svg.encode("utf-8"))
        assert root.tag.endswith("svg")
        assert root.find("{http://www.w3.org/2000/svg}title") is not None
        assert root.find("{http://www.w3.org/2000/svg}desc") is not None
    assert f"{tp['base']['F3_e2e_mbps']:.2f}" in tl and "8.97" in tl
    assert f"{tp['fast']['F3_e2e_mbps']:.2f}" in tl and "19.48" in tl
    assert f"{tp['base']['F3_tx_mbps']:.2f}" in tl
    assert "21.04" in tl and "100 Mbaud" in tl and "80 Mbit/s" in tl
    assert "TXCFG 0x00020010" in dp and "100 Mbaud" in dp and "RXCFG 0x0000801F" in dp
    assert "PRU0" in dp and "PRU1" in dp
    # the plotted segments sum to the measured frame period
    for rx in ("base", "fast"):
        period = (tp[rx]["T_burst_ns"] + tp[rx]["e2e_gap_ns"]) / 1e3
        assert f"{period:.1f} us" in tl


def test_docs_reference_existing_figures():
    import re
    folder = Path(__file__).resolve().parent.parent / "source" / "pif_eth_100"
    for doc, expected in (("README.md", {"figures/datapath.svg", "figures/frame_timeline.svg"}),
                          ("USERS_GUIDE.md", {"figures/datapath.svg", "figures/frame_timeline.svg"})):
        refs = set(re.findall(r"!\[[^\]]*\]\((figures/[^)]+)\)", (folder / doc).read_text(encoding="utf-8")))
        assert expected <= refs, (doc, refs)
        assert all((folder / r).exists() for r in refs), (doc, refs)


def test_build_pdfs_adds_base_href_for_images():
    pytest.importorskip("markdown_it")
    from pif_eth_100 import build_pdfs
    html = build_pdfs.render_html("![a](figures/x.svg)\n", "T", "file:///x/")
    assert '<base href="file:///x/">' in html and 'src="figures/x.svg"' in html
