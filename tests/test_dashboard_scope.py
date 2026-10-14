"""The motor branch adds its complete demo and UI to the dashboard."""
from pathlib import Path


def test_dashboard_demo_scope():
    from pru_io.device_profiles import discover_device_profiles
    from pru_io.scenarios import SCENARIOS

    assert set(discover_device_profiles()) == {"ssi_encoder", "tca9538", "foc_motor"}
    assert set(SCENARIOS) == {
        "SSI encoder (single core)",
        "SSI multi-core (emulator + reader)",
        "FOC motor (open loop)",
    }
    root = Path(__file__).parents[1]
    html = (root / "ui/static/index.html").read_text()
    assert 'data-workspace-view="simulator"' in html
    assert 'data-workspace-view="io"' in html
    assert 'data-workspace-view="motor"' in html
    assert '/static/motor.js' in html
    assert '/static/motor_helpers.js' in html
    assert (root / "pru_io/foc_motor_model.py").exists()
    assert (root / "source/foc_open_loop.asm").exists()
