# tests/test_sweep_capture.py
"""Simulator.sweep_capture and the sweep_capture / sweep_cancel websocket actions."""
import pytest
from fastapi.testclient import TestClient

from pru_io.ring_recorder import RingOverrun, RingRecorder
from simulator import Simulator

# Test firmware: every ~600 instructions store k into ring[k mod 16] (0x10) and
# the count k + 1 into 0x04. Plumbing only; it does not use the SD channel
# (the SD modulators advance every instruction regardless).
RING_WRITER = """
    ldi     r0, 0
    ldi     r1, 0
LOOP:
    ldi     r3, 300
WAIT:
    sub     r3, r3, 1
    qbne    WAIT, r3, 0
    and     r4, r1, 15
    lsl     r4, r4, 2
    add     r4, r4, 0x10
    sbbo    &r1, r4, 0, 4
    add     r1, r1, 1
    sbbo    &r1, r0, 4, 4
    qba     LOOP
"""
SWEEP = dict(f_start=1000.0, f_stop=5000.0, duration_s=0.002, sweep_type="linear",
             amplitude=0.5, sd_clock_mhz=20.0)


def loaded_sim():
    sim = Simulator()
    assert sim.load("pru0", RING_WRITER) == []
    return sim


def drain(gen):
    progress = []
    while True:
        try:
            progress.append(next(gen))
        except StopIteration as stop:
            return progress, stop.value


def test_capture_records_the_whole_sweep_in_chunks():
    sim = loaded_sim()
    sim.step("pru0", 5000)                                  # firmware already running
    rec = RingRecorder(0x04, 0x10, 16)
    progress, stopped = drain(sim.sweep_capture("pru0", 0, rec, SWEEP, chunk_steps=5000, tail_s=0.0))
    assert stopped == "done"
    mod = sim.cores["pru0"].io_port.sd_filter.modulators[0]
    assert mod.signal == "sweep" and mod.time_s() >= 0.002
    assert len(progress) >= 10
    assert progress[-1]["captured"] == len(rec.samples) > 300
    assert rec.samples == list(range(rec.samples[0], rec.samples[0] + len(rec.samples)))
    assert [p["f_now"] for p in progress] == sorted(p["f_now"] for p in progress)


def test_chunks_longer_than_the_ring_overrun():
    sim = loaded_sim()
    rec = RingRecorder(0x04, 0x10, 16)
    with pytest.raises(RingOverrun):
        drain(sim.sweep_capture("pru0", 0, rec, SWEEP, chunk_steps=20000))


def test_max_steps_and_breakpoints_stop_the_capture():
    sim = loaded_sim()
    _, stopped = drain(sim.sweep_capture("pru0", 0, RingRecorder(0x04, 0x10, 16), SWEEP,
                                         chunk_steps=1000, max_steps=3000))
    assert stopped == "max_steps"
    sim.cores["pru0"].breakpoints.add(2)                      # LOOP
    _, stopped = drain(sim.sweep_capture("pru0", 0, RingRecorder(0x04, 0x10, 16), SWEEP,
                                         chunk_steps=1000))
    assert stopped == "breakpoint"


def test_a_core_without_sd_filter_cannot_sweep():
    sim = loaded_sim()
    sim.cores["pru0"].io_port.sd_filter = None
    with pytest.raises(ValueError, match="SD filter"):
        drain(sim.sweep_capture("pru0", 0, RingRecorder(0x04, 0x10, 16), SWEEP))


# ---- websocket ---------------------------------------------------------------

def _server():
    from ui import server
    server.sim = Simulator(config_path=server.config_path)
    assert server.sim.load("pru0", RING_WRITER) == []
    return TestClient(server.app)


def _capture_msg(**kw):
    msg = dict(action="sweep_capture", core="pru0", channel=0, count_addr=0x04, ring_addr=0x10,
               ring_len=16, sweep=SWEEP, full_scale=1000, delay_ms=0.0, window=128,
               chunk_steps=5000, tail_ms=0.0)
    msg.update(kw)
    return msg


def _until_result(ws):
    progress = []
    while True:
        m = ws.receive_json()
        if m.get("type") == "sweep_progress":
            progress.append(m)
        elif m.get("type") == "sweep_result":
            return progress, m


def test_websocket_capture_streams_progress_then_a_result():
    with _server().websocket_connect("/ws") as ws:
        ws.send_json(_capture_msg())
        progress, result = _until_result(ws)
        assert len(progress) >= 10
        assert result["stopped"] == "done" and result["captured"] > 300
        assert result["points"] and all(len(p) == 2 for p in result["points"])
        from ui import server
        clock_hz = server.sim.cores["pru0"].clock_mhz * 1e6               # memory.cfg: 250 MHz
        assert result["fs_hz"] == pytest.approx(clock_hz / 608, rel=0.05)  # one sample per ~608 instr
        assert ws.receive_json()["pc"] is not None            # state update follows


def test_websocket_cancel_returns_a_partial_result():
    with _server().websocket_connect("/ws") as ws:
        ws.send_json(_capture_msg(sweep=dict(SWEEP, duration_s=0.05)))
        ws.receive_json()                                   # first progress
        ws.send_json({"action": "sweep_cancel", "core": "pru0"})
        _, result = _until_result(ws)
        assert result["stopped"] == "cancel"


def test_websocket_overrun_and_busy():
    with _server().websocket_connect("/ws") as ws:
        ws.send_json(_capture_msg(chunk_steps=20000))
        _, result = _until_result(ws)
        assert result["stopped"] == "overrun" and "ring holds 16" in result["message"]
        ws.receive_json()                                   # state
        ws.send_json(_capture_msg(sweep=dict(SWEEP, duration_s=0.05)))
        ws.receive_json()                                   # first progress
        ws.send_json({"action": "step", "core": "pru0"})
        while True:
            m = ws.receive_json()
            if m.get("type") == "error":
                assert "sweep capture is running" in m["errors"][0]
                break
        ws.send_json({"action": "sweep_cancel", "core": "pru0"})
        _until_result(ws)
