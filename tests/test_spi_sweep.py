"""Rate sweep, committed supported-rate table and generated timing doc
(tools/spi_sweep.py, pru_io/spi_rates.json, docs/spi_link_timing.md).

The full sweep (every mode x core x k x hop delay) runs from the CLI
(``python tools/spi_sweep.py --write``); here only a subset is re-run and
compared with the committed table. Every run pins the core clock through
``sim_config`` (never the repo memory.cfg).
"""
import json
from pathlib import Path

import pytest

from pru_io.spi_link import (CMD_RW, HOP_DELAY_CYCLES, PATTERNS, SpiConfig,
                             SpiLinkRuntime, controller_delay_ok,
                             load_supported, measure_timing,
                             rate_candidates, run_test_sequence)
from simulator import Simulator
from tools import spi_sweep

REPO = Path(__file__).resolve().parents[1]
RATES_JSON = REPO / "pru_io" / "spi_rates.json"
TIMING_DOC = REPO / "docs" / "spi_link_timing.md"


def _rates():
    return json.loads(RATES_JSON.read_text(encoding="utf-8"))


def _runtime(sim_config, mode, core, k, d, hop=HOP_DELAY_CYCLES):
    sim = Simulator(sim_config(pru_clock_mhz=core, pru1_clock_mhz=core))
    rt = SpiLinkRuntime(sim, SpiConfig(mode, core, k, d), hop_delay=hop)
    rt.load_controller()
    rt.load_peripheral()
    rt.wire()
    return rt


# ---------------------------------------------------------------------------
# Committed table
# ---------------------------------------------------------------------------

def test_rates_json_schema_and_coverage():
    data = _rates()
    assert data["hop_delay_supported"] == HOP_DELAY_CYCLES == 5
    assert isinstance(data["generated"], str) and "T" in data["generated"]
    keys = {"id", "mode", "core_mhz", "half_k", "spi_mhz", "hop_delay",
            "miso_delay_window", "miso_delay", "pass", "p1_cycles_per_bit"}
    rows = data["rows"]
    for r in rows:
        assert keys <= set(r), r["id"]
        assert set(r) == set(spi_sweep.SUMMARY_KEYS), r["id"]
        if r["pass"]:
            lo, hi = r["miso_delay_window"]
            assert lo <= r["miso_delay"] <= hi
            assert controller_delay_ok(r["half_k"], r["miso_delay"])
    want = {(m, c, k, h) for m in (0, 1) for c, k in rate_candidates() for h in (0, 5)}
    assert {(r["mode"], r["core_mhz"], r["half_k"], r["hop_delay"]) for r in rows} == want
    assert RATES_JSON.stat().st_size < 100_000          # pushed to the UI (R12)


def test_no_negative_miso_setup_hold_at_hop5():
    """Ruling R7a: with the sample referred to the pin, no passing row shows
    a negative MISO setup or hold."""
    for r in _rates()["rows"]:
        if r["pass"] and r["hop_delay"] == 5:
            assert r["tsu_miso_ns"] > 0 and r["th_miso_ns"] > 0, r["id"]


def test_no_50mhz_at_200():
    assert not any(r["core_mhz"] == 200 and abs(r["spi_mhz"] - 50) < 1e-9
                   for r in _rates()["rows"])
    with pytest.raises(ValueError):
        SpiConfig(1, 200, 2)
    for mode, rows in load_supported().items():
        assert not any(r["core_mhz"] == 200 and r["spi_mhz"] == 50 for r in rows)


def test_load_supported_one_row_per_rate_lowest_core():
    data = _rates()
    sup = load_supported()
    assert set(sup) == {0, 1}
    for mode, rows in sup.items():
        rates = [round(r["spi_mhz"], 6) for r in rows]
        assert rates == sorted(set(rates))
        for r in rows:
            assert r["pass"] and r["hop_delay"] == 5 and r["mode"] == mode
            same = [x for x in data["rows"] if x["mode"] == mode and x["hop_delay"] == 5
                    and x["pass"] and round(x["spi_mhz"], 6) == round(r["spi_mhz"], 6)]
            assert r["core_mhz"] == min(x["core_mhz"] for x in same)


def _subset(mode):
    """Slowest rate, fastest passing rate and fastest failing rate (if any),
    all at the supported hop delay."""
    rows = [r for r in _rates()["rows"] if r["mode"] == mode and r["hop_delay"] == 5]
    key = lambda r: (r["spi_mhz"], r["core_mhz"])  # noqa: E731
    out = [min(rows, key=key), max((r for r in rows if r["pass"]), key=key)]
    failing = [r for r in rows if not r["pass"]]
    if failing:
        out.append(max(failing, key=key))
    return out


@pytest.mark.parametrize("mode", [0, 1])
def test_supported_json_matches_fresh_sweep_subset(sim_config, mode):
    for want in _subset(mode):
        cfg = sim_config(pru_clock_mhz=want["core_mhz"], pru1_clock_mhz=want["core_mhz"])
        got = spi_sweep.sweep_row(mode, want["core_mhz"], want["half_k"],
                                  want["hop_delay"], config_path=cfg)
        assert got["pass"] == want["pass"], want["id"]
        assert got["miso_delay"] == want["miso_delay"], want["id"]
        assert got["miso_delay_window"] == want["miso_delay_window"], want["id"]
        assert got["p1_cycles_per_bit"] == want["p1_cycles_per_bit"], want["id"]


@pytest.mark.parametrize("mode", [0, 1])
def test_supported_rows_pass_full_sequence(sim_config, mode):
    fastest = max(load_supported()[mode], key=lambda r: r["spi_mhz"])
    rt = _runtime(sim_config, mode, fastest["core_mhz"], fastest["half_k"],
                  fastest["miso_delay"])
    res = run_test_sequence(rt)
    assert res["ok"], [s for s in res["steps"] if not s["ok"]]
    assert res["byte_errors"] == 0
    names = {s["step"] for s in res["steps"]}
    for p in PATTERNS:
        for op in ("write", "read", "rw"):
            assert f"{op}:{p}" in names
    assert {"unknown_cmd", "bad_addr", "abort", "back_to_back"} <= names
    b2b = next(s for s in res["steps"] if s["step"] == "back_to_back")
    assert b2b["tcsh_cycles"] >= 4 * fastest["half_k"]      # >= 2 tCLK
    rt.close()


# ---------------------------------------------------------------------------
# run_test_sequence detects errors
# ---------------------------------------------------------------------------

def test_sequence_flags_bad_miso_delay(sim_config):
    # 200 MHz k=10 mode 1: window 15..32; d=1 samples before MISO arrives
    rt = _runtime(sim_config, 1, 200, 10, 1)
    res = run_test_sequence(rt, patterns=["count"])
    assert not res["ok"]
    assert res["byte_errors"] > 0
    rt.close()


# ---------------------------------------------------------------------------
# Measured tIDLE (PRU1 idle-byte fetch, stepped)
# ---------------------------------------------------------------------------

def test_measure_timing_uses_measured_fetch():
    cfg = SpiConfig(1, 200, 4)
    rows = {r["symbol"]: r for r in measure_timing([], cfg, p1_fetch_cycles=12)}
    t = rows["tIDLE"]
    assert t["derived"] is False
    assert t["cycles"] == 12 and t["sim_ns"] == 60.0
    assert t["design_ns"] == 320.0 and t["margin_ns"] == 260.0
    assert "stepped" in t["source"]
    # default unchanged: derived design value
    assert {r["symbol"]: r for r in measure_timing([], cfg)}["tIDLE"]["derived"] is True


def test_peripheral_work_measured(sim_config):
    from pru_io.spi_link import measure_peripheral_work
    rt = _runtime(sim_config, 1, 200, 10, 23)
    rt.preload_peripheral_tx(6, PATTERNS["count"][1])
    w = measure_peripheral_work(rt, CMD_RW, 6, PATTERNS["count"][0])
    assert w["data_bit_cycles"] == 5          # Task 4, single-stepped
    assert w["fetch_cycles"] == 12
    assert w["data_byte_cycles"] == 47
    assert w["max_byte_cycles"] <= 48
    rt.close()


# ---------------------------------------------------------------------------
# Generated timing doc
# ---------------------------------------------------------------------------

def test_timing_doc_lists_every_supported_row():
    doc = TIMING_DOC.read_text(encoding="utf-8")
    data = _rates()
    assert "design budget" in doc and "PRDO1" in doc and "SPRSP65J" in doc
    sections = doc.split("\n### ")
    for r in data["rows"]:
        assert r["id"] in doc, r["id"]
        if r["pass"] and r["hop_delay"] == 5:
            sec = next((s for s in sections if s.startswith(r["id"])), None)
            assert sec is not None, r["id"]
            assert "PRDO1" in sec and "design budget" in sec, r["id"]
    for phrase in ("18 ns", "333 MHz", "not modelled", "unknown"):
        assert phrase in doc, phrase


def test_timing_doc_agrees_with_json():
    """The committed doc's results table matches the committed JSON."""
    assert spi_sweep.doc_mismatches(TIMING_DOC.read_text(encoding="utf-8"), _rates()) == []


def test_reload_controller_keeps_one_time_base(sim_config):
    """Ruling R10: after reload_controller, PRU0- and PRU1-driven edges share
    a clock, so tV,MISO matches a fresh simulator's."""
    def tv(rt):
        rt.preload_peripheral_tx(4, PATTERNS["count"][1])
        rt.transfer(CMD_RW, 4, PATTERNS["count"][0])
        rows = {r["symbol"]: r for r in measure_timing(rt.monitor.edges, rt.cfg)}
        return rows["tV,MISO"]["cycles"]

    fresh = _runtime(sim_config, 1, 200, 10, 24)
    want = tv(fresh)
    fresh.close()
    rt = _runtime(sim_config, 1, 200, 10, 23)
    rt.transfer(CMD_RW, 4, PATTERNS["a5_5a"][0])     # advance both clocks
    rt.reload_controller(SpiConfig(1, 200, 10, 24))
    p0, p1 = rt.sim.cores["pru0"], rt.sim.cores["pru1"]
    assert p0.counters.cycles == p1.counters.cycles == 0
    assert len(rt.monitor.edges) == 1                 # cleared, seeded with idle bus
    got = tv(rt)
    assert abs(got - want) <= 2, (got, want)
    rt.close()
