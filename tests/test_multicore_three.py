"""Regression tests for selectable one-, two-, and three-core view runs."""

from pathlib import Path

import pytest

from simulator import Simulator


STATIC_DIR = Path(__file__).parents[1] / "ui" / "static"

_LOOP = """
    .global main
    .sect ".text"
main:
    jmp main
"""


@pytest.fixture
def fresh_sim(monkeypatch):
    import ui.server as srv

    fresh = Simulator(config_path=srv.config_path)
    monkeypatch.setattr(srv, "sim", fresh)
    return fresh


def test_step_paced_many_keeps_three_selected_cores_in_lockstep(fresh_sim):
    for core in ("pru0", "pru1", "rtu1"):
        assert fresh_sim.load(core, _LOOP) == []

    fresh_sim.step_paced_many("pru0", ["pru1", "rtu1"], 12)

    assert {
        core.counters.instruction_count for core in fresh_sim.cores.values()
        if core.name in {"PRU0", "PRU1", "RTU1"}
    } == {12}


def test_step_paced_many_accepts_one_selected_core(fresh_sim):
    assert fresh_sim.load("pru1", _LOOP) == []

    fresh_sim.step_paced_many("pru1", [], 4)

    assert fresh_sim.cores["pru1"].counters.instruction_count == 4


def test_multicore_selection_rejects_duplicate_cores():
    import ui.server as srv

    with pytest.raises(ValueError, match="distinct"):
        srv._multicore_cores({"cores": ["pru0", "pru0"]})


def test_multicore_view_has_three_selectable_slots_and_panels():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    layout = (STATIC_DIR / "layout.js").read_text(encoding="utf-8")

    for selector_id in ("mc-primary-select", "mc-partner-select", "mc-third-select"):
        assert f'id="{selector_id}"' in html
    for value in ("pru0", "rtu0", "pru1", "rtu1"):
        assert f'value="{value}"' in html
    assert 'value="none"' in html
    assert 'id="mc-rtu1-source-panel"' in html
    assert 'id="mc-rtu1-reg-panel"' in html
    assert "mc-rtu1-source" in layout
    assert "mc-rtu1-registers" in layout
    assert "function sendMCRun" in js
    assert "cores," in js
    assert "MC_SLOT_KEYS = [\"pru0\", \"rtu0\", \"rtu1\"]" in js


def test_three_core_capture_group_waits_for_all_selected_batches():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    server = (Path(__file__).parents[1] / "ui" / "server.py").read_text(
        encoding="utf-8"
    )

    assert '"capture_cores": emitted_cores' in server
    assert "const expectedCaptureCount = Array.isArray(msg.capture_cores)" in js
    assert "if (group.size < expectedCaptureCount) return;" in js


def test_graph_flushes_partial_capture_group_when_stopped():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    stop_start = js.index("function stopRun()")
    stop_end = js.index("function startSim()", stop_start)
    recording_start = js.index("function graphSetRecording(on)")
    recording_end = js.index("function graphClear()", recording_start)

    assert "function graphFlushPendingCaptures()" in js
    assert "graphFlushPendingCaptures();" in js[stop_start:stop_end]
    assert "graphFlushPendingCaptures();" in js[recording_start:recording_end]


def test_graph_window_size_is_per_shared_time_step_in_multicore_mode():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function graphCoreCount()" in js
    assert "signalGraph.windowSize * graphCoreCount()" in js
    assert "const perCore = newSize;" in js


def test_run_pump_is_completion_driven_and_limits_one_request():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    start = js.index("function startRun()")
    end = js.index("function startSim()", start)
    run_section = js[start:end]
    pump_start = js.index("function pumpRun()")
    pump_end = js.index("function startRun()", pump_start)
    pump_section = js[pump_start:pump_end]
    done_section = js[js.index('msg.type === "run_done"'):js.index('msg.type === "uart_inject_ok"')]

    assert "setInterval" not in run_section
    assert "runPumpTimer" in run_section
    assert "runRequestInFlight" in pump_section
    assert "simpleSsiLoaded ? 3000 : 1000" in pump_section
    assert "scheduleRunPump" in run_section
    assert "scheduleRunPump" in done_section


def test_graph_memory_refresh_is_recording_only_and_throttled():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    start = js.index("function graphRequestSnapshots(force = false)")
    end = js.index("function renderMemChannelRows()", start)
    section = js[start:end]

    assert "signalGraph.recording" in section
    assert "GRAPH_MEMORY_REFRESH_MS" in section
    assert "GRAPH_MEMORY_REFRESH_MS = 100" in js
    assert "graphMemoryInFlight" in section
