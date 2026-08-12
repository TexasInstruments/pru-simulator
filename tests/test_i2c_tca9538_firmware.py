# tests/test_i2c_tca9538_firmware.py
"""Integration test: i2c_tca9538_running_led.asm against TCA9538Device.

Uses the `nominal_config` fixture (tests/conftest.py) to pin the core
clock to 200 MHz — this firmware's DELAY_COUNT is derived against that
specific clock, and a bare `Simulator()` would inherit whatever core
speed the dashboard's UI last wrote into the repo's memory.cfg.
"""
import pathlib
import pytest
from simulator import Simulator

SOURCE = pathlib.Path("source/i2c_tca9538_running_led.asm").read_text()


def _make_sim(nominal_config):
    sim = Simulator(nominal_config)
    errors = sim.load("pru0", SOURCE)
    assert errors == [], errors
    sim.i2c_attach("pru0", True, address=0x23)
    return sim


class TestInitSequence:
    def test_config_register_becomes_all_outputs(self, nominal_config):
        sim = _make_sim(nominal_config)
        # Generous step budget for one full init transaction at ~200-300
        # cycles/bit including subroutine-call overhead; the run loop
        # never reaches HALT so an upper bound is safe here.
        sim.step("pru0", count=20_000)
        state = sim.i2c_state("pru0")
        assert state["config_reg"] == 0x00
