# tests/test_sweep_ui.py
"""Static checks for the Sweep Response UI (the drawing itself is checked in a browser)."""
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ui.server import app

STATIC = Path(__file__).resolve().parent.parent / "ui" / "static"
client = TestClient(app)


def test_page_has_the_sweep_response_controls():
    html = client.get("/").text
    for element_id in ("sweep-run-btn", "sweep-cancel-btn", "sweep-axis-btn", "sweep-core", "sweep-ch",
                       "sweep-count-addr", "sweep-ring-addr", "sweep-ring-len", "sweep-type",
                       "sweep-f-start", "sweep-f-stop", "sweep-duration", "sweep-amp", "sweep-clk",
                       "sweep-full", "sweep-delay", "sweep-window", "sweep-bar-fill", "sweep-status",
                       "sweep-response-canvas", "sweep-readout"):
        assert f'id="{element_id}"' in html, element_id


def test_app_sends_and_handles_the_sweep_messages():
    js = (STATIC / "app.js").read_text()
    for needle in ("action: 'sweep_capture'", "action: 'sweep_cancel'",
                   'msg.type === "sweep_progress"', 'msg.type === "sweep_result"',
                   "function drawSweep(", '<option value="sweep"', 'data-param="sweep_type"',
                   "function sweepPrefill(", "sweepPrefill(sd);"):
        assert needle in js, needle
    assert 'data-param="sd_clock_mhz"\n          value="${mod.sd_clock_mhz}" min="1"' in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_app_js_parses():
    subprocess.run(["node", "--check", str(STATIC / "app.js")], check=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_log_axis_ends_at_a_nice_frequency_above_the_data():
    # Found in the browser check: the log axis rounded up to the next decade
    # (100 kHz for a sweep to 16 kHz), leaving half the plot empty.
    js = (STATIC / "app.js").read_text()
    start = js.index("function sweepGeometry(")
    body = js[start:js.index("\nfunction drawSweep(", start)]
    script = (
        "const sweepState = { points: [[100, 0], [16000, -80]], logAxis: true };\n"
        "const sweepField = () => NaN;\n" + body +
        "\nconst g = sweepGeometry(800, 300);\n"
        "console.log(JSON.stringify([g.fMin, g.fMax, Math.round(g.x(16000))]));\n")
    out = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout
    f_min, f_max, x_last = __import__("json").loads(out)
    assert (f_min, f_max) == (100, 20000)
    assert x_last > 700                                   # the data reaches the right part of the plot


def test_disconnect_unlocks_the_sweep_controls():
    js = (STATIC / "app.js").read_text()
    onclose = js[js.index("ws.onclose = () => {"):]
    onclose = onclose[:onclose.index("};")]
    assert "sweepSetRunning(false)" in onclose


def test_result_status_shows_the_resolution_note():
    js = (STATIC / "app.js").read_text()
    assert "msg.resolution" in js[js.index("function sweepHandleResult("):]
