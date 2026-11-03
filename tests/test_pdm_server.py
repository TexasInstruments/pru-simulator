"""PDM demo API of the dashboard server (spec §11): clip list, setup with
clock forcing, chunked runs, history rebuild, execution ownership.

The server runs on a pinned temp config and the tests use short clips from a
temp clips directory (``PDM_CLIPS_DIR``) and small chunks (``PDM_CHUNK_CYCLES``),
so one chunk takes well under a second of wall time.
"""
import math
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from simulator import RunAheadRefused

import ui.server as srv

CHUNK = 200_000                  # PRU0 cycles per pdm_run in the tests (1 ms)
CLIP_FRAMES = 480                # 30 ms at 16 kHz


def _write_wav(path, hz, frames=CLIP_FRAMES):
    data = b"".join(struct.pack("<h", round(12000 * math.sin(2 * math.pi * hz * i / 16000)))
                    for i in range(frames))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(data)


@pytest.fixture
def server(sim_config, tmp_path, monkeypatch):
    """Server on a pinned 200/200 MHz config with two short clips."""
    def make(**device):
        cfg = sim_config(**{"pru_clock_mhz": 200, "pru1_clock_mhz": 200, **device})
        clips = tmp_path / "clips"
        clips.mkdir(exist_ok=True)
        _write_wav(clips / "a.wav", 1000)
        _write_wav(clips / "b.wav", 3000)
        monkeypatch.setattr(srv, "config_path", cfg)
        monkeypatch.setattr(srv, "sim", srv.Simulator(config_path=cfg))
        monkeypatch.setattr(srv, "PDM_CLIPS_DIR", clips)
        monkeypatch.setattr(srv, "PDM_CHUNK_CYCLES", CHUNK)
        monkeypatch.setattr(srv, "_pdm", None)
        monkeypatch.setattr(srv, "_pdm_clips", None)
        monkeypatch.setattr(srv, "_execution_owner", None)
        monkeypatch.setattr(srv, "_execution_mode", None)
        srv._clear_history()
        return TestClient(srv.app)
    return make


def _until(ws, kind):
    """Messages up to and including the first of type *kind* (execution
    notices of a stopped run are skipped)."""
    out = []
    while True:
        msg = ws.receive_json()
        if msg["type"] == "execution":
            continue
        out.append(msg)
        if msg["type"] == kind:
            return out


def _setup(ws, left="a.wav", right="b.wav", swap=False):
    ws.send_json({"action": "pdm_setup", "clip_left": left, "clip_right": right,
                  "swap": swap})
    return _until(ws, "pdm_status")


def _run(ws, run_id=1):
    ws.send_json({"action": "pdm_run", "run_id": run_id})
    return _until(ws, "pdm_status")


def _collect(msgs, into):
    """Append the samples of every pdm_pcm in *msgs* to *into*, checking
    that each message continues its channel without gap or overlap."""
    for m in msgs:
        if m["type"] == "pdm_pcm":
            ch = m["ch"]
            assert m["start"] == len(into[ch]), (ch, m["start"], len(into[ch]))
            into[ch].extend(m["samples"])


def test_clip_list(server, monkeypatch):
    client = server()
    monkeypatch.setattr(srv, "PDM_CLIPS_DIR", srv.SOURCE_DIR / "pdm" / "clips")  # the shipped clips
    body = client.get("/pdm/clips").json()
    names = {c["name"]: c["ms"] for c in body["clips"]}
    assert {"tone_1000.wav", "tone_3000.wav", "chirp_lin_100_7000.wav",
            "chirp_log_100_7000.wav", "speech_librivox.wav"} <= set(names)
    for name in ("tone_1000.wav", "tone_3000.wav"):
        assert names[name] == pytest.approx(250, abs=1)


def test_setup_forces_200mhz_with_notice(server):
    client = server(pru_clock_mhz=250, pru1_clock_mhz=250)
    assert client.get("/config/clock_speed").json()["mhz"] == 250
    with client.websocket_connect("/ws") as ws:
        msgs = _setup(ws)
    assert {"type": "pdm_notice", "text": "PRU clocks set to 200 MHz for the PDM demo"} in msgs
    groups = client.get("/config/clock_speed").json()["groups"]
    assert [g["mhz"] for g in groups] == [200, 200]
    status = msgs[-1]
    assert status["type"] == "pdm_status" and status["running"] is False
    assert status["clips"] == {"left": "a.wav", "right": "b.wav"}
    assert status["mode"] == "run-ahead" and status["overrun"] is None
    assert status["clip_ms"] == pytest.approx(CLIP_FRAMES / 16)
    assert srv._pdm.sim is srv.sim            # the demo works on the rebuilt simulator


def test_setup_at_200mhz_has_no_clock_notice(server):
    client = server()
    with client.websocket_connect("/ws") as ws:
        msgs = _setup(ws)
    assert not [m for m in msgs if m["type"] == "pdm_notice"]


def test_run_chunks_stream_pcm(server):
    client = server()
    got = {0: [], 1: []}
    with client.websocket_connect("/ws") as ws:
        _setup(ws)
        audio = []
        for n in range(3):
            msgs = _run(ws, run_id=f"r{n}")
            _collect(msgs, got)
            status = msgs[-1]
            assert status["run_id"] == f"r{n}" and status["running"] is True
            assert status["load"].keys() == {"pru0", "pru1"}
            audio.append(status["audio_ms"])
        assert audio[0] < audio[1] < audio[2]
        assert audio[0] == pytest.approx(1.0, abs=0.01)
    assert len(got[0]) > 0 and abs(len(got[0]) - len(got[1])) <= 1
    assert got[0] == srv._pdm.history[0] and got[1] == srv._pdm.history[1]
    assert any(got[0]) and any(got[1])


def test_history_rebuild_after_reconnect(server):
    client = server()
    streamed = {0: [], 1: []}
    with client.websocket_connect("/ws") as ws:
        _setup(ws)
        for n in range(2):
            _collect(_run(ws, n), streamed)
    assert srv._execution_owner is None               # drop of the owner paused the run
    assert srv._pdm is not None                       # and the demo persisted
    rebuilt = {0: [], 1: []}
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "pdm_history"})
        msgs = _until(ws, "pdm_status")
        assert [m["start"] for m in msgs if m["type"] == "pdm_pcm"] == [0, 0]
        _collect(msgs, rebuilt)
        assert rebuilt == streamed and rebuilt[0]
        assert msgs[-1]["running"] is False
        more = {0: list(rebuilt[0]), 1: list(rebuilt[1])}
        msgs = _run(ws, "next")
        _collect(msgs, more)                           # contiguous with the history
        assert len(more[0]) > len(rebuilt[0])
        assert more == srv._pdm.history


def test_second_client_cannot_run(server):
    client = server()
    with client.websocket_connect("/ws") as first, client.websocket_connect("/ws") as second:
        _setup(first)
        _run(first)
        assert srv._execution_owner is not None
        second.send_json({"action": "pdm_run", "run_id": 9})
        msg = _until(second, "error")[-1]
        assert msg == {"type": "error",
                       "errors": ["Execution stopped or controlled by another tab"]}
        before = srv._pdm.status()["audio_ms"]
        second.send_json({"action": "pdm_history"})     # reading stays allowed
        assert _until(second, "pdm_status")[-1]["audio_ms"] == before
        second.send_json({"action": "pdm_setup", "clip_left": "a.wav",
                          "clip_right": "b.wav", "swap": False})
        assert _until(second, "error")[-1]["type"] == "error"
        assert srv._pdm.status()["audio_ms"] == before


@pytest.mark.parametrize("bad", ["../../memory.cfg", "../a.wav", "sub/a.wav", "/etc/passwd",
                                 "..", "a.wav/..", "..\\a.wav", "nope.wav", "", 7, None])
def test_bad_clip_name_rejected(server, monkeypatch, bad):
    client = server()
    opened = []
    real_open = open

    def spy(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)
    monkeypatch.setattr("builtins.open", spy)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "pdm_setup", "clip_left": bad, "clip_right": "b.wav",
                      "swap": False})
        msg = ws.receive_json()
        ws.send_json({"action": "pdm_setup", "clip_left": "a.wav", "clip_right": bad})
        msg2 = ws.receive_json()
    assert msg["type"] == msg2["type"] == "error" and msg["errors"]
    assert not [p for p in opened if "memory.cfg" in p or "passwd" in p]
    assert srv._pdm is None


def test_unreadable_clip_gives_error_and_keeps_old_state(server):
    client = server()
    (srv.PDM_CLIPS_DIR / "bad.wav").write_bytes(b"not a wav")
    with client.websocket_connect("/ws") as ws:
        _setup(ws)
        first = srv._pdm
        ws.send_json({"action": "pdm_setup", "clip_left": "bad.wav", "clip_right": "b.wav"})
        msg = ws.receive_json()
    assert msg["type"] == "error" and "bad.wav" in msg["errors"][0]
    assert srv._pdm is first and srv._pdm_clips["left"] == "a.wav"


def test_run_before_setup_is_an_error(server):
    client = server()
    with client.websocket_connect("/ws") as ws:
        for action in ("pdm_run", "pdm_history", "pdm_reset"):
            ws.send_json({"action": action})
            msg = ws.receive_json()
            assert msg["type"] == "error" and "pdm_setup" in msg["errors"][0]
    assert srv._execution_owner is None


def test_stop_releases_execution(server):
    client = server()
    with client.websocket_connect("/ws") as ws:
        _setup(ws)
        _run(ws)
        assert srv._execution_owner is not None and srv._execution_mode == "pdm"
        ws.send_json({"action": "pdm_stop"})
        status = _until(ws, "pdm_status")[-1]
        assert status["running"] is False and srv._execution_owner is None


def test_reset_restarts_with_the_same_clips(server):
    client = server()
    first = {0: [], 1: []}
    with client.websocket_connect("/ws") as ws:
        _setup(ws, swap=True)
        _collect(_run(ws), first)
        ws.send_json({"action": "pdm_reset"})
        msgs = _until(ws, "pdm_status")
        status = msgs[-1]
        assert status["clips"] == {"left": "a.wav", "right": "b.wav"}
        assert status["audio_ms"] == 0 and status["running"] is False
        assert srv._pdm.history == {0: [], 1: []}
        again = {0: [], 1: []}
        _collect(_run(ws), again)
    assert again == first                      # a reset run is the same run


def test_run_ends_at_done_and_releases_execution(server, monkeypatch):
    client = server()
    clips = srv.PDM_CLIPS_DIR
    _write_wav(clips / "tiny.wav", 1000, frames=16)
    with client.websocket_connect("/ws") as ws:
        _setup(ws, "tiny.wav", "tiny.wav")
        for n in range(40):
            msgs = _run(ws, n)
            if msgs[-1]["done"]:
                break
        assert msgs[-1]["done"] is True and msgs[-1]["running"] is False
        assert srv._execution_owner is None
        calls = srv._pdm.sim.cores["pru0"].counters.cycles
        # run after done does nothing more
        ws.send_json({"action": "pdm_run", "run_id": "late"})
        late = _until(ws, "pdm_status")
        assert [m for m in late if m["type"] == "pdm_pcm"] == []
        assert late[-1]["running"] is False and late[-1]["run_id"] == "late"
        assert srv._pdm.sim.cores["pru0"].counters.cycles == calls


def test_chunk_stopping_at_a_breakpoint_stops_the_run(server):
    client = server()
    with client.websocket_connect("/ws") as ws:
        _setup(ws)
        srv.sim.cores["pru0"].breakpoints.add(3)
        ws.send_json({"action": "pdm_run", "run_id": "bp"})
        msgs = []
        while True:
            msg = ws.receive_json()
            if msg["type"] == "execution":
                continue
            msgs.append(msg)
            if msg["type"] == "state":
                break
        status = [m for m in msgs if m["type"] == "pdm_status"][0]
        assert status["running"] is False and status["breakpoint"] == "pru0"
        assert status["run_id"] == "bp"
        state = msgs[-1]
        assert state["core"] == "pru0" and state["at_breakpoint"] is True and state["pc"] == 3
        assert srv._execution_owner is None
        # the next pdm_run resumes past the breakpoint
        srv.sim.cores["pru0"].breakpoints.clear()
        msgs = _run(ws, "resume")
        assert msgs[-1]["running"] is True and msgs[-1]["breakpoint"] is None


def test_run_ahead_refused_mid_run_is_reported_not_fatal(server, monkeypatch):
    client = server()
    got = {0: [], 1: []}
    with client.websocket_connect("/ws") as ws:
        _setup(ws)
        _collect(_run(ws), got)

        def refuse(*args, **kwargs):
            raise RunAheadRefused(["wiring changed"])
        monkeypatch.setattr(srv._pdm.sim, "run_ahead", refuse)
        ws.send_json({"action": "pdm_run", "run_id": "x"})
        msgs = []
        while not [m for m in msgs if m["type"] == "pdm_status"]:
            msg = ws.receive_json()
            if msg["type"] != "execution":
                msgs.append(msg)
        kinds = [m["type"] for m in msgs]
        assert "pdm_notice" in kinds and "error" in kinds
        notice = next(m for m in msgs if m["type"] == "pdm_notice")
        assert "wiring changed" in notice["text"]
        assert msgs[-1]["running"] is False and msgs[-1]["run_id"] == "x"
        assert srv._execution_owner is None
        assert srv._pdm.history == got                 # history intact
        # the socket still works
        ws.send_json({"action": "pdm_history"})
        rebuilt = {0: [], 1: []}
        _collect(_until(ws, "pdm_status"), rebuilt)
        assert rebuilt == got


def test_ring_overrun_is_reported_not_fatal(server, monkeypatch):
    client = server()
    with client.websocket_connect("/ws") as ws:
        _setup(ws)

        def overrun():
            srv._pdm.overrun = "PCM ring 0 overrun: 5 samples lost"
            raise RuntimeError(srv._pdm.overrun)
        monkeypatch.setattr(srv._pdm, "run_chunk", overrun)
        ws.send_json({"action": "pdm_run", "run_id": 3})
        msgs = []
        while not [m for m in msgs if m["type"] == "pdm_status"]:
            msg = ws.receive_json()
            if msg["type"] != "execution":
                msgs.append(msg)
        assert any(m["type"] == "error" and "overrun" in m["errors"][0] for m in msgs)
        assert msgs[-1]["running"] is False and "overrun" in msgs[-1]["overrun"]
        assert srv._execution_owner is None
