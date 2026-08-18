"""Regression tests for the dashboard's static startup assets."""

from pathlib import Path


STATIC_DIR = Path(__file__).parents[1] / "ui" / "static"


def test_gpio_wire_controls_exist_before_app_initializes():
    """The wire listener must not abort app.js before initUI can run."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    for element_id in ("btn-add-wire", "wire-tbody", "wire-empty"):
        assert f'id="{element_id}"' in html
