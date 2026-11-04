"""Tests for pru_io.spi_link: config, rates, patterns, bus monitor, timing."""
import pytest

from pru_io import spi_link as sl
from pru_io.spi_link import SpiBusMonitor, SpiConfig, measure_timing


def test_rate_candidates_exclude_50_at_200():
    cands = sl.rate_candidates()
    assert (200, 2) not in cands
    assert all(2 * k >= 6 for _, k in cands)


def test_rate_candidates_include():
    rates = {c: SpiConfig(1, c[0], c[1]).spi_mhz for c in sl.rate_candidates()}
    assert rates[(300, 3)] == 50.0
    assert rates[(225, 3)] == 37.5
    assert rates[(200, 3)] == pytest.approx(33.333, abs=1e-3)
    order = [(SpiConfig(1, c, k).spi_mhz, c) for c, k in sl.rate_candidates()]
    assert order == sorted(order)


def test_config_validation():
    with pytest.raises(ValueError):
        SpiConfig(2, 200, 4)
    with pytest.raises(ValueError):
        SpiConfig(1, 200, 2)
    with pytest.raises(ValueError):
        SpiConfig(1, 210, 5)
    with pytest.raises(ValueError):
        SpiConfig(1, 200, 11)  # 9.09 MHz
    assert SpiConfig(1, 300, 3).spi_mhz == 50.0
    assert SpiConfig(1, 300, 3, 7).id == "m1_c300_k3_d7"


def test_patterns_directions_differ():
    assert set(sl.PATTERNS) == {"a5_5a", "walking1", "walking0", "count", "toggle"}
    for tx, rx in sl.PATTERNS.values():
        assert len(tx) == 16 and len(rx) == 16 and tx != rx
    assert sl.PATTERNS["count"] == (bytes(range(16)), bytes(range(0xF0, 0x100)))
    assert sl.PATTERNS["walking1"][0][:9] == bytes([1, 2, 4, 8, 16, 32, 64, 128, 1])


def test_render_header():
    c = sl.render_header(SpiConfig(1, 300, 3, 7))
    assert "SPI_MODE .set 1" in c and "HALF_K .set 3" in c and "MISO_DELAY .set 7" in c
    assert "m1_c300_k3_d7" in c
    p = sl.render_header(SpiConfig(1, 300, 3, 7), "peripheral")
    assert "SPI_MODE .set 1" in p and "HALF_K" not in p and "MISO_DELAY" not in p


def _bus(cs, clk, mosi, miso):
    return (cs << 17) | (clk << 16) | (mosi << 13) | (miso << 14)


def _word(cs, clk, mosi, miso):
    return cs | (clk << 1) | (mosi << 2) | (miso << 3)


def _waveform(mode, data, mosi_data=None, half=4, t0=100, bits=None, miso=None):
    """Ideal frame as (cycle, word) records. Returns (edges, end_cycle)."""
    nbits = len(data) * 8 if bits is None else bits
    seq = [(t0, _word(1, 0, 0, 0))]
    t = t0 + 10
    bitlist = [(data[i // 8] >> (7 - i % 8)) & 1 for i in range(len(data) * 8)]
    mi = [0] * len(bitlist) if miso is None else \
        [(miso[i // 8] >> (7 - i % 8)) & 1 for i in range(len(miso) * 8)]
    first = bitlist[0]
    seq.append((t, _word(0, 0, first if mode == 0 else 0, mi[0] if mode == 0 else 0)))
    t += 2 * half
    for i in range(nbits):
        b, m = bitlist[i], mi[i]
        if mode == 1:
            seq.append((t, _word(0, 1, b, m)))           # launch
            seq.append((t + half, _word(0, 0, b, m)))    # capture
        else:
            seq.append((t, _word(0, 1, bitlist[i], mi[i])))   # capture
            nb = bitlist[i + 1] if i + 1 < len(bitlist) else 0
            nm = mi[i + 1] if i + 1 < len(bitlist) else 0
            seq.append((t + half, _word(0, 0, nb, nm)))       # launch next
        t += 2 * half
    t += half
    seq.append((t, _word(1, 0, 0, 0)))
    return seq, t


def _feed(mon, seq):
    for cyc, w in seq:
        mon.tick(cyc, _bus(w & 1, (w >> 1) & 1, (w >> 2) & 1, (w >> 3) & 1))


FRAME = bytes([3, 2, 0]) + sl.PATTERNS["walking1"][0]


@pytest.mark.parametrize("mode", [0, 1])
def test_monitor_decodes_synthetic_frame(mode):
    seq, _ = _waveform(mode, FRAME)
    mon = SpiBusMonitor()
    assert mon.nets == {} and mon.time_driven is False
    _feed(mon, seq)
    fr = mon.frames(mode)
    assert len(fr) == 1
    assert fr[0]["mosi"] == FRAME
    assert fr[0]["miso"] == bytes(19)
    assert fr[0]["bits"] == 152 and fr[0]["aborted"] is False
    assert [e["kind"] for e in mon.events()] == ["frame"]
    assert mon.faults() == []


def test_monitor_decodes_miso():
    seq, _ = _waveform(1, FRAME, miso=bytes(3) + sl.PATTERNS["walking0"][0])
    mon = SpiBusMonitor()
    _feed(mon, seq)
    assert mon.frames(1)[0]["miso"] == bytes(3) + sl.PATTERNS["walking0"][0]


def test_monitor_flags_abort():
    seq, _ = _waveform(1, FRAME, bits=20)
    mon = SpiBusMonitor()
    _feed(mon, seq)
    f = mon.frames(1)[0]
    assert f["aborted"] is True and f["bits"] == 20


def test_monitor_fault_clk_while_cs_high():
    mon = SpiBusMonitor()
    _feed(mon, [(0, _word(1, 0, 0, 0)), (5, _word(1, 1, 0, 0))])
    assert any("CLK edge while /CS high" in f for f in mon.faults())


def test_monitor_snapshot_restore():
    seq, _ = _waveform(1, FRAME)
    mon = SpiBusMonitor()
    _feed(mon, seq[:40])
    snap = mon.snapshot()
    before = list(mon.edges)
    _feed(mon, seq[40:60])
    assert mon.edges != before
    mon.restore(snap)
    assert mon.edges == before
    _feed(mon, seq[40:])
    assert mon.frames(1)[0]["mosi"] == FRAME
    mon.clear()
    assert mon.edges == []
    mon.reset()
    assert mon.events() == [] and mon.faults() == []
    assert mon.get_state()["frames"] == 0


@pytest.mark.parametrize("mode", [0, 1])
def test_measure_timing_synthetic(mode):
    cfg = SpiConfig(mode, 200, 4)           # 25 MHz at 200 MHz: half = 20 ns
    seq1, end = _waveform(mode, FRAME, half=4, miso=bytes(3) + sl.PATTERNS["walking0"][0])
    seq2, _ = _waveform(mode, FRAME, half=4, t0=end + 8,
                        miso=bytes(3) + sl.PATTERNS["walking0"][0])
    rows = measure_timing(seq1 + seq2, cfg, 0)
    by = {r["symbol"]: r for r in rows}
    assert by["tCLK"]["sim_ns"] == 40.0
    assert by["tH/tL"]["sim_ns"] == 20.0
    assert by["tLEAD"]["sim_ns"] == 40.0
    assert by["tLAG"]["sim_ns"] == 40.0
    assert by["tCSH"]["sim_ns"] == 18 * 5.0   # 8 gap + 10 lead-in cycles
    assert by["tSU,MOSI"]["sim_ns"] == 20.0
    assert by["tH,MOSI"]["sim_ns"] == 20.0
    assert by["tsk(GPO-GPO)"]["am243_max"] == 2.0
    assert by["tsk(GPO-GPO)"]["source"] == "Table 6-113 PRDO1"
    assert by["tSU,MOSI"]["source"] == "design budget"
    assert by["tSU,MISO"]["source"] == "design budget"
    assert by["SRI"]["am243_min"] == 1.0 and by["SRI"]["am243_max"] == 3.0
    assert by["CL"]["am243_max"] == 30.0 and by["CL"]["source"] == "Table 6-112"
    assert sum(1 for r in rows if r["worst"]) == 1
    for r in rows:
        assert set(r) == {"param", "symbol", "design_ns", "cycles", "sim_ns",
                          "am243_min", "am243_max", "source", "margin_ns", "worst", "derived"}
    # setup budget: 20 - 2 skew - 5 ns polling = 13
    assert by["tSU,MOSI"]["margin_ns"] == 13.0


def test_measure_timing_tcsh_needs_two_frames():
    cfg = SpiConfig(1, 200, 4)
    seq, _ = _waveform(1, FRAME)
    rows = measure_timing(seq, cfg)
    assert {r["symbol"]: r for r in rows}["tCSH"]["sim_ns"] is None
    assert sum(1 for r in rows if r["worst"]) == 1


def test_measure_timing_no_frames():
    rows = measure_timing([], SpiConfig(1, 200, 4))
    assert all(r["sim_ns"] is None for r in rows if not r["derived"] and r["symbol"] not in ("HOP", "MISO_DELAY"))


def _mode1_miso_frame(lag, half=4, nbytes=3):
    """Mode-1 frame, 25 MHz at 200 MHz; MISO alternates 1,0,1,0.. changing
    `lag` cycles after each launch edge."""
    t = 100
    seq = [(t, _word(1, 0, 0, 0)), (t + 10, _word(0, 0, 0, 0))]
    t += 10 + 2 * half
    miso = 0
    for i in range(nbytes * 8):
        seq.append((t, _word(0, 1, 0, miso)))
        miso ^= 1
        if lag:
            seq.append((t + lag, _word(0, 1, 0, miso)))
        else:
            seq[-1] = (t, _word(0, 1, 0, miso))
        seq.append((t + half, _word(0, 0, 0, miso)))
        t += 2 * half
    seq.append((t + half, _word(1, 0, 0, 0)))
    return seq


@pytest.mark.parametrize("delay,su,hd", [(3, 5.0, 35.0), (0, None, None), (6, 20.0, 20.0)])
def test_measure_timing_miso_rows_use_sample_point(delay, su, hd):
    cfg = SpiConfig(1, 200, 4, delay)
    rows = measure_timing(_mode1_miso_frame(lag=2), cfg)
    by = {r["symbol"]: r for r in rows}
    assert by["tV,MISO"]["sim_ns"] == 10.0
    assert by["MISO_DELAY"]["cycles"] == delay
    assert by["MISO_DELAY"]["sim_ns"] == delay * 5.0
    assert by["HOP"]["cycles"] == sl.HOP_DELAY_CYCLES
    assert by["HOP"]["sim_ns"] == 25.0
    if su is None:
        # delay 0: sample point precedes every change, so no bit has a change before it
        assert by["tSU,MISO"]["sim_ns"] is None
    else:
        # pipelined (delay 6 > half period 4): sample lands after next launch
        assert by["tSU,MISO"]["sim_ns"] == su
        assert by["tH,MISO"]["sim_ns"] == hd


def test_derived_rows_never_worst():
    rows = measure_timing(_waveform(1, FRAME)[0], SpiConfig(1, 200, 4))
    der = {r["symbol"] for r in rows if r["derived"]}
    assert der == {"tsk(GPO-GPO)", "tIDLE"}
    assert not any(r["worst"] for r in rows if r["derived"])
    assert sum(r["worst"] for r in rows) == 1
    by = {r["symbol"]: r for r in rows}
    assert by["tsk(GPO-GPO)"]["sim_ns"] == 0.0
    assert by["tIDLE"]["sim_ns"] == by["tIDLE"]["design_ns"] == 320.0
