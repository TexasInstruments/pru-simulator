import pytest

from pru_io.device_model import PUSH_PULL
from pru_io.device_profiles import create_device, discover_device_profiles
from pru_io.foc_motor_model import FocMotorModel


def test_foc_motor_is_discoverable_and_uses_push_pull_current_pins():
    profiles = discover_device_profiles()

    assert "foc_motor" in profiles
    assert profiles["foc_motor"]["outputs"] == {
        "current_a_pin": PUSH_PULL,
        "current_b_pin": PUSH_PULL,
    }
    model = create_device("foc_motor")
    assert isinstance(model, FocMotorModel)
    assert model.nets == {3: PUSH_PULL, 4: PUSH_PULL}
    assert model.pru_output_mask == 0x7
    assert model.core_clock_hz == 250_000_000


def test_foc_motor_current_outputs_follow_pwm_pins_and_have_no_shared_feedback():
    model = FocMotorModel()
    # R30 pins 0/1/2 are real phase outputs. Unequal duty changes the modeled
    # phase currents, which the model presents on GPI pins 3/4.
    for cycle in range(1, 1500):
        drive_mask, drive_values = model.tick(cycle, (1 << 0) | (1 << 1))

    assert drive_mask == (1 << 3) | (1 << 4)
    assert isinstance(drive_values, int)
    assert set(model.get_state()["outputs"]) == {"current_a_pin", "current_b_pin"}
    assert not hasattr(model, "feedback_address")
    assert model.faults() == []


def test_foc_motor_profile_rejects_non_pru0_attachment():
    from mcp_server.server import PRUSimulatorMCP

    mcp = PRUSimulatorMCP(config_path="nonexistent.cfg")
    with pytest.raises(ValueError, match="pru0"):
        mcp.pru_device_attach(profile="foc_motor", core="pru1")


def test_foc_motor_profile_rejects_invalid_fields():
    with pytest.raises(ValueError, match="unknown foc_motor config fields"):
        create_device("foc_motor", {"feedback_address": 0x10000})


def test_foc_motor_snapshot_restores_current_pdm_and_period_state():
    model = FocMotorModel()
    for cycle in range(1, 1500):
        model.tick(cycle, 0x3)
    snapshot = model.snapshot()

    expected_drive = model.tick(1500, 0x4)
    expected_state = model.get_state()

    model.restore(snapshot)
    assert model.tick(1500, 0x4) == expected_drive
    assert model.get_state() == expected_state
