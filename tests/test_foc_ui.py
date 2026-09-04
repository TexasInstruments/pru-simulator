"""Browser-facing contract tests for the open-loop FOC dashboard tab."""

import re
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
