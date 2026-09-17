"""Regression tests for the dashboard's static startup assets."""

from pathlib import Path


STATIC_DIR = Path(__file__).parents[1] / "ui" / "static"


def test_gpio_wire_controls_exist_before_app_initializes():
    """The wire listener must not abort app.js before initUI can run."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    for element_id in ("btn-add-wire", "wire-tbody", "wire-empty"):
        assert f'id="{element_id}"' in html


def test_getting_started_documents_simple_graph_controls():
    text = (STATIC_DIR.parent.parent / "getting_started.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "click **REC**",
        "Set the Signal Graph window",
        "Fit frame",
        "Export CSV",
        "Sample rate.",
    ):
        assert phrase in text


def test_signal_graph_can_shrink_to_its_panel_height():
    """A short tile must shrink the plot instead of scrolling the panel body."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    panel_rule_start = html.index("#signal-graph-panel > .panel-body")
    panel_rule = html[panel_rule_start : html.index("}", panel_rule_start) + 1]
    wrap_rule_start = html.index("#signal-graph-panel .graph-canvas-wrap")
    wrap_rule = html[wrap_rule_start : html.index("}", wrap_rule_start) + 1]

    assert "display: flex" in panel_rule
    assert "flex-direction: column" in panel_rule
    assert "min-height: 0" in panel_rule
    assert "flex: 0 1 auto" in wrap_rule
    assert "min-height: 0" in wrap_rule


def test_memory_auto_refresh_starts_from_the_checkbox_state():
    """A restored checked checkbox must be active before its first change event."""
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "let memAutoRefresh = memAutoRefreshBox.checked;" in js
    assert "let memAutoRefresh2 = memAutoRefreshBox2.checked;" in js


def test_source_updates_reuse_the_cached_listing_for_delta_states():
    """A live PC update must not re-hash and rebuild unchanged source text."""
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "let _renderedSourceInstructions = null;" in js
    assert "const sourceChanged = instructions !== _renderedSourceInstructions" in js
    assert "let mcRenderedSourceInstructions = { pru0: null, rtu0: null, rtu1: null };" in js
    assert "const sourceChanged = instructions !== mcRenderedSourceInstructions[core]" in js
