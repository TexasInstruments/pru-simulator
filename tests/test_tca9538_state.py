"""TCA9538Model.get_state(): what the dashboard needs to draw the expander."""
import pathlib

from pru_io.tca9538_device_model import TCA9538Model
from simulator import Simulator

SOURCE = pathlib.Path("source/i2c_tca9538_running_led.asm").read_text()
STEPS = 20_000   # same budget as test_device_bus_firmware.py


def _load(nominal_config):
    sim = Simulator(nominal_config)
    errors = sim.load("pru0", SOURCE)
    assert errors == [], errors
    return sim


def test_tca9538_state_reports_wiring_and_reset_values():
    state = TCA9538Model(address=0x24, scl_pin=4, sda_pin=5, name="io").get_state()

    assert state["name"] == "io"
    assert state["model"] == "tca9538"
    assert (state["address"], state["scl_pin"], state["sda_pin"]) == (0x24, 4, 5)
    assert (state["output_reg"], state["polarity_reg"], state["config_reg"]) == (
        0xFF, 0x00, 0xFF)
    # Reset config is all inputs, so no pin is driven and every level reads 0.
    assert state["driven_mask"] == 0
    assert state["levels"] == [0] * 8
    assert state["faults"] == 0
    assert state["events"] == 0
    assert state["last_transaction"] is None


def test_tca9538_state_levels_follow_firmware_writes(nominal_config):
    sim = _load(nominal_config)
    sim.set_gpio_drive_mask("pru0", 0b11)
    model = sim.cores["pru0"].io_port.attach_device(TCA9538Model())
    sim.step("pru0", count=STEPS)

    state = model.get_state()
    assert state["config_reg"] == 0x00            # all pins configured as outputs
    assert state["driven_mask"] == 0xFF
    assert state["levels"] == [(state["output_reg"] >> bit) & 1 for bit in range(8)]
    assert state["saw_start"] is True
    assert state["events"] > 0
    assert state["last_transaction"]["ack"] is True
    # Same numbers reach the dashboard through the generic bus state.
    listed = sim.device_state()["devices"][0]
    assert listed["model"] == "tca9538" and listed["levels"] == state["levels"]


def test_tca9538_state_survives_snapshot_restore():
    model = TCA9538Model()
    model.device.config_reg = 0xF0
    model.device.output_reg = 0xA5
    snap = model.snapshot()
    model.reset()
    assert model.get_state()["config_reg"] == 0xFF
    model.restore(snap)
    state = model.get_state()
    assert state["output_reg"] == 0xA5 and state["driven_mask"] == 0x0F
    assert state["levels"] == [1, 0, 1, 0, 0, 0, 0, 0]
