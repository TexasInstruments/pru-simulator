"""The first dashboard branch works without motor-control modules."""
from pathlib import Path


def test_dashboard_demo_scope():
    from pru_io.device_profiles import discover_device_profiles
    from pru_io.scenarios import SCENARIOS

    assert set(discover_device_profiles()) == {"ssi_encoder", "tca9538"}
    assert set(SCENARIOS) == {
        "SSI encoder (single core)",
        "SSI multi-core (emulator + reader)",
    }
    root = Path(__file__).parents[1]
    html = (root / "ui/static/index.html").read_text()
    assert 'data-workspace-view="simulator"' in html
    assert 'data-workspace-view="io"' in html
    assert 'data-workspace-view="motor"' not in html
    assert '/static/motor.js' not in html
    assert '/static/motor_helpers.js' not in html
    assert not (root / "pru_io/foc_motor_model.py").exists()
    assert not (root / "source/foc_open_loop.asm").exists()
