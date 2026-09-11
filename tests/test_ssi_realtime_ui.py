"""Contract tests for the actual three-core SSI realtime dashboard panel."""

import json
from pathlib import Path

import pytest

from ui import server as srv


STATIC_DIR = Path(__file__).parents[1] / "ui" / "static"


def test_simple_ssi_profile_state_is_read_only_and_reports_build_inputs(tmp_path, monkeypatch):
    profile_path = tmp_path / "ssi_build_config.json"
    profile_path.write_text(
        json.dumps({
            "SSI_CORE_HZ": 300_000_000,
            "SSI_IEP_HZ": 300_000_000,
            "SSI_PERIOD_TICKS": 288,
            "SSI_PERIOD_NS": 960,
            "SSI_FRAME_BITS": 12,
            "SSI_CLOCK_HZ": 4_000_000,
            "SSI_POSITION_BITS": 12,
            "SSI_POSITION_STEP": 1,
            "SSI_ITERATIONS": 100_000,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(srv, "SSI_SIMPLE_PROFILE_PATH", profile_path)
    monkeypatch.setattr(srv, "_ssi_simple_result", None)
    monkeypatch.setattr(srv, "_ssi_simple_error", None)
    monkeypatch.setattr(srv, "_ssi_simple_running", False)

    state = srv._ssi_simple_state()

    assert state["loaded"] is True
    assert state["running"] is False
    assert state["profile"]["SSI_PERIOD_TICKS"] == 288
    assert state["profile"]["SSI_ITERATIONS"] == 100_000
    assert state["config_path"].endswith("ssi_hardware_config.h")


def test_simple_ssi_result_is_compact_for_browser_transport():
    result = {
        "opportunities": 100_000,
        "published": 100_000,
        "capture": [{"kind": "period_store"}] * 2_000,
        "source_hashes": {"pru0": "abc"},
        "simulator_revision": "deadbeef",
    }

    compact = srv._ssi_simple_ui_result(result)

    assert compact["published"] == 100_000
    assert "capture" not in compact
    assert compact["source_hashes"] == {"pru0": "abc"}


@pytest.mark.parametrize("value, valid", [(1, True), (100_000, True), (0, False), (100_001, False)])
def test_simple_ssi_iteration_validation(value, valid):
    if valid:
        assert srv._validate_ssi_simple_iterations(value, 100_000) == value
    else:
        with pytest.raises(ValueError):
            srv._validate_ssi_simple_iterations(value, 100_000)


def test_simple_ssi_panel_uses_actual_run_actions_and_read_only_fields():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    for element_id in (
        "ssi-simple-load",
        "ssi-simple-run",
        "ssi-simple-reset",
        "ssi-simple-refresh",
        "ssi-simple-iterations",
        "ssi-simple-status",
        "ssi-simple-profile",
        "ssi-simple-results",
    ):
        assert f'id="{element_id}"' in html, element_id

    for action in (
        "ssi_simple_load",
        "ssi_simple_run",
        "ssi_simple_read",
        "ssi_simple_reset",
    ):
        assert action in js, action

    assert "ssi_simple_state" in js
    assert "ssi_simple_error" in js
    assert "PRU0 emulator + PRU1 reader + RTU_PRU1 timer" in html
    assert '<div class="io-section-title">Simple SSI Realtime</div>' in html
    assert '<details id="ssi-runtime-legacy" hidden>' in html
