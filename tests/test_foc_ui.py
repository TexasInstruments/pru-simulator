"""Browser-facing contract tests for the open-loop FOC dashboard tab."""

import re
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from simulator import Simulator
from ui import server as srv


STATIC_DIR = Path(__file__).parents[1] / "ui" / "static"
client = TestClient(srv.app)


@pytest.fixture
def fresh_foc_sim(monkeypatch):
    fresh = Simulator(config_path=srv.config_path)
    monkeypatch.setattr(srv, "sim", fresh)
    monkeypatch.setattr(srv, "foc_runtime", None)
    return fresh


def test_foc_websocket_load_reference_start_stop_and_state(fresh_foc_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "foc_load", "source": "halt"})
        loaded = ws.receive_json()
        assert loaded["type"] == "foc_state"
        assert loaded["loaded"] is True
        assert loaded["control"]["enable"] == 0
        assert loaded["pwm"]["seq"] % 2 == 0
        assert loaded["fb"]["seq"] % 2 == 0
        assert loaded["clock"]["loop_frequency_hz"] == 100000
        assert loaded["telemetry"]["requested_speed_rpm"] == 0

        ws.send_json({
            "action": "foc_set_reference",
            "speed_rpm": 400,
            "id_ref": 0.125,
            "iq_ref": 0.25,
            "ramp_rate": 0.02,
        })
        staged = ws.receive_json()
        assert staged["type"] == "foc_state"
        assert staged["control"]["requested_generation"] == 1
        assert staged["control"]["speed_ref_q24"] == round(0.4 * (1 << 24))

        ws.send_json({"action": "foc_start"})
        started = ws.receive_json()
        assert started["control"]["enable"] == 1
        assert started["model"]["running"] is True
        assert "session_id" in started

        ws.send_json({
            "action": "foc_stop",
        })
        stopped = ws.receive_json()
        assert stopped["control"]["enable"] == 0
        assert stopped["model"]["running"] is False

        ws.send_json({
            "action": "foc_start",
            "speed_rpm": 400,
            "vd_ref": 0.0,
            "vq_ref": 0.25,
            "acceleration_rpm_s": 1000,
        })
        restarted = ws.receive_json()
        assert restarted["control"]["enable"] == 1
        assert restarted["control"]["speed_ref_q24"] == round(0.4 * (1 << 24))
        assert restarted["control"]["iq_ref_q24"] == round(0.25 * (1 << 24))

        ws.send_json({"action": "foc_stop"})
        stopped = ws.receive_json()
        assert stopped["control"]["enable"] == 0
        assert stopped["model"]["running"] is False


def test_foc_run_emits_live_state_samples(fresh_foc_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "foc_load", "source": "start: qba start"})
        loaded = ws.receive_json()
        assert loaded["loaded"] is True
        ws.send_json({"action": "foc_start"})
        ws.receive_json()
        ws.send_json({"action": "run", "core": "pru0", "max_steps": 70})

        messages = []
        while True:
            message = ws.receive_json()
            messages.append(message)
            if message["type"] == "state":
                break

    foc_messages = [message for message in messages if message["type"] == "foc_state"]
    assert foc_messages
    assert foc_messages[-1]["fb"]["timestamp"] >= 0


def test_foc_execution_publishes_live_core_state_for_source_disassembly(fresh_foc_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "action": "foc_load",
            "filename": "foc_open_loop/foc_open_loop.asm",
        })
        assert ws.receive_json()["loaded"] is True
        ws.send_json({"action": "foc_start"})
        started = ws.receive_json()
        assert started["type"] == "foc_state"

        messages = [ws.receive_json() for _ in range(10)]

        core_states = [message for message in messages if message["type"] == "state"]
        assert core_states
        assert any(message["instruction_count"] > 0 for message in core_states)
        assert any(message["pc"] != 0 for message in core_states)

        ws.send_json({"action": "foc_stop"})
        stopped = ws.receive_json()
        while stopped.get("type") != "foc_state" or stopped["model"]["running"]:
            stopped = ws.receive_json()
        assert stopped["model"]["running"] is False


def test_foc_disconnect_pauses_and_reconnect_enables_restart(fresh_foc_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "action": "foc_load",
            "filename": "foc_open_loop/foc_open_loop.asm",
        })
        loaded = ws.receive_json()
        assert loaded["loaded"] is True
        ws.send_json({"action": "foc_start"})
        started = ws.receive_json()
        assert started["model"]["running"] is True

    assert srv.foc_runtime is not None
    assert srv.foc_runtime.model.running is False

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "foc_state"})
        paused = ws.receive_json()
        assert paused["model"]["running"] is False
        ws.send_json({"action": "foc_start"})
        restarted = ws.receive_json()
        assert restarted["model"]["running"] is True
        ws.send_json({"action": "foc_stop"})
        stopped = ws.receive_json()
        while stopped.get("type") != "foc_state" or stopped["model"]["running"]:
            stopped = ws.receive_json()
        assert stopped["model"]["running"] is False


def test_foc_breakpoint_pauses_after_executing_actual_assembly(fresh_foc_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "action": "foc_load",
            "filename": "foc_open_loop/foc_open_loop.asm",
        })
        loaded = ws.receive_json()
        assert loaded["loaded"] is True
        wait_pc = srv.foc_runtime.sim.cores["pru0"]._parser.labels["l_wait_deadline"]
        ws.send_json({"action": "toggle_breakpoint", "core": "pru0", "addr": wait_pc})
        ws.receive_json()
        ws.send_json({"action": "foc_start"})
        paused = ws.receive_json()
        if paused["model"]["running"]:
            paused = ws.receive_json()

        assert paused["type"] == "foc_state"
        assert paused["model"]["running"] is False
        assert "breakpoint" in paused["status"].lower()
        assert srv.foc_runtime.sim.cores["pru0"].pc == wait_pc


def test_foc_execution_has_one_websocket_owner(fresh_foc_sim):
    with client.websocket_connect("/ws") as first:
        first.send_json({
            "action": "foc_load",
            "filename": "foc_open_loop/foc_open_loop.asm",
        })
        assert first.receive_json()["loaded"] is True
        first.send_json({"action": "foc_start"})
        assert first.receive_json()["model"]["running"] is True

        with client.websocket_connect("/ws") as second:
            second.send_json({"action": "foc_start"})
            error = second.receive_json()
            assert error["type"] == "foc_error"
            assert "owned" in error["error"]

        first.send_json({"action": "foc_stop"})
        stopped = first.receive_json()
        while stopped.get("type") != "foc_state" or stopped["model"]["running"]:
            stopped = first.receive_json()
        assert stopped["model"]["running"] is False


def test_foc_dial_uses_cardinal_endpoint_geometry_and_paused_state():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "Math.cos(angle)" in js
    assert "-Math.sin(angle)" in js
    for label in ("0°", "90°", "180°", "270°"):
        assert label in js
    assert "scheduleFocDialAnimation" in js
    assert "if (runningNow) scheduleFocDialAnimation();" in js

    helper = re.search(
        r"function focNeedleEndpoint\(angle, length\) \{.*?\n\}",
        js,
        re.DOTALL,
    )
    assert helper
    node_script = (
        helper.group(0)
        + "\nconsole.log(JSON.stringify(["
        + "focNeedleEndpoint(0, 1),"
        + "focNeedleEndpoint(Math.PI / 2, 1),"
        + "focNeedleEndpoint(Math.PI, 1),"
        + "focNeedleEndpoint(3 * Math.PI / 2, 1),"
        + "focNeedleEndpoint(-Math.PI / 2, 1),"
        + "focNeedleEndpoint(2 * Math.PI + Math.PI / 2, 1)]));"
    )
    points = json.loads(
        subprocess.run(
            ["node", "-e", node_script],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    expected = [
        {"x": 1, "y": 0},
        {"x": 0, "y": -1},
        {"x": -1, "y": 0},
        {"x": 0, "y": 1},
        {"x": 0, "y": 1},
        {"x": 0, "y": -1},
    ]
    for actual, wanted in zip(points, expected):
        assert actual["x"] == pytest.approx(wanted["x"])
        assert actual["y"] == pytest.approx(wanted["y"])


def test_foc_speed_display_preserves_sub_percent_throughput():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "simulated_ms_per_wall_second" in js
    assert "focFormatThroughput" in js
    assert "toFixed(4)" in js


def test_foc_tab_markup_and_actions_are_present():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    for element_id in (
        "tab-foc",
        "foc-view",
        "foc-speed-rpm",
        "foc-id-ref",
        "foc-iq-ref",
        "foc-ramp-rate",
        "foc-requested-speed-readout",
        "foc-ramped-speed-readout",
        "foc-angle-error-readout",
        "foc-loop-frequency-readout",
        "foc-pru-clock-readout",
        "foc-iep-clock-readout",
        "foc-sim-wall-readout",
        "foc-fault-readout",
        "foc-start",
        "foc-stop",
        "foc-rotor-dial",
        "foc-duty-plot",
        "foc-current-plot",
        "foc-voltage-plot",
    ):
        assert f'id="{element_id}"' in html, element_id

    for action in (
        "foc_load",
        "foc_set_reference",
        "foc_start",
        "foc_stop",
        "foc_state",
    ):
        assert action in js, action

    assert "Vd reference" in html
    assert "Vq reference" in html
    assert "RPM/s" in html
    assert "acceleration_rpm_s" in js
    assert "FOC_SAMPLE_PERIOD_SECONDS" in js
    assert "FOC_WINDOW_SECONDS" in js


def test_workspace_tabs_reserve_room_for_three_labels():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    rule = re.search(
        r"#workspace-tools #view-tabs\.glass-tab-group\s*\{(?P<body>.*?)\}",
        html,
        re.DOTALL,
    )
    assert rule, "glass workspace selector rule is missing"

    width = re.search(r"\bwidth:\s*(\d+)px", rule.group("body"))
    assert width, "workspace selector needs an explicit desktop width"
    assert int(width.group(1)) >= 276


def test_workspace_tabs_compact_at_narrow_widths():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    mobile_start = html.find(
        "    @media (max-width: 720px) {\n"
        "      #workspace-tools #view-tabs.glass-tab-group {"
    )
    assert mobile_start >= 0, "narrow workspace tabs need a compact layout rule"
    mobile_end = html.find(
        "    @media (pointer: coarse) and (max-width: 720px) {",
        mobile_start,
    )
    mobile_rule = html[mobile_start:mobile_end]
    assert "font-size: clamp(9px, 2.3vw, 10px);" in mobile_rule
    assert "padding: 4px 6px;" in mobile_rule
