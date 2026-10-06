"""End-to-end: real PRU firmware driving a device through the generic DeviceBus.

The unit tests in test_device_model.py drive the bus by hand. This one runs
`source/i2c_tca9538_running_led.asm` — actual PRU assembly, executed by the
core — against a TCA9538 attached through `IOPort.attach_device` rather than
the hardcoded `attach_i2c_device` slot.

The equivalence test below is the load-bearing one. `attach_i2c_device` is a
known-good path with existing coverage (test_i2c_tca9538_firmware.py), so
requiring the generic path to reach the SAME firmware-visible outcome is what
shows DeviceBus did not quietly change behaviour. Without that arm, a passing
test on the new path only proves the new path does something.
"""
import pathlib

import pytest

from pru_io.device_model import OPEN_DRAIN
from pru_io.tca9538_device_model import TCA9538Model
from simulator import Simulator

SOURCE = pathlib.Path("source/i2c_tca9538_running_led.asm").read_text()

# The firmware's DELAY_COUNT is derived against 200 MHz; a bare Simulator()
# would inherit whatever core speed the dashboard last wrote into memory.cfg.
# Same reasoning as test_i2c_tca9538_firmware.py.
STEPS = 20_000


def _load(nominal_config):
    sim = Simulator(nominal_config)
    errors = sim.load("pru0", SOURCE)
    assert errors == [], errors
    return sim


def test_firmware_configures_expander_through_the_generic_bus(nominal_config):
    sim = _load(nominal_config)
    model = sim.cores["pru0"].io_port.attach_device(
        TCA9538Model(address=0x23, scl_pin=0, sda_pin=1))

    sim.step("pru0", count=STEPS)

    assert model.device.config_reg == 0x00, (
        "firmware's init transaction did not reach the device through "
        "DeviceBus")


def test_generic_bus_matches_the_hardcoded_attach_path(nominal_config):
    """Equivalence arm. Both paths, same firmware, same visible outcome."""
    legacy = _load(nominal_config)
    legacy.i2c_attach("pru0", True, address=0x23)
    legacy.step("pru0", count=STEPS)

    generic = _load(nominal_config)
    model = generic.cores["pru0"].io_port.attach_device(
        TCA9538Model(address=0x23, scl_pin=0, sda_pin=1))
    generic.step("pru0", count=STEPS)

    assert (model.device.config_reg
            == legacy.i2c_state("pru0")["config_reg"])
    assert model.device.output_reg == legacy.i2c_state("pru0")["output_reg"]


def test_real_firmware_raises_no_protocol_faults(nominal_config):
    """Control arm for the fault checks.

    This firmware is known-good, so a fault here means the MODEL is wrong, not
    the firmware. Without this, the falsification test in test_device_model.py
    would be satisfied by a model that faults on everything.
    """
    sim = _load(nominal_config)
    model = sim.cores["pru0"].io_port.attach_device(TCA9538Model())
    sim.step("pru0", count=STEPS)

    assert model.faults() == []
    assert sim.cores["pru0"].io_port.device_bus.faults() == []


def test_firmware_produces_start_and_stop_events(nominal_config):
    """The decode has to see real traffic, not just avoid crashing."""
    sim = _load(nominal_config)
    model = sim.cores["pru0"].io_port.attach_device(TCA9538Model())
    sim.step("pru0", count=STEPS)

    kinds = [e["kind"] for e in model.events()]
    assert "start" in kinds
    assert "stop" in kinds


def test_unattached_bus_leaves_gpi_alone(nominal_config):
    """An empty DeviceBus must be invisible. Every existing test in this repo
    depends on that, so it is asserted rather than assumed."""
    sim = _load(nominal_config)
    port = sim.cores["pru0"].io_port
    port.set_gpi_word(0xABCDE)
    port.tick_devices(0)
    assert port.gpi == 0xABCDE


def test_devices_only_write_back_pins_they_drive(nominal_config):
    """Attaching a device on pins 0/1 must not disturb pin 5, which nothing
    drives - otherwise a device silently takes over the whole port."""
    sim = _load(nominal_config)
    port = sim.cores["pru0"].io_port
    port.attach_device(TCA9538Model(scl_pin=0, sda_pin=1))

    port.set_gpi_pin(5, True)
    port.tick_devices(0)
    assert (port.gpi >> 5) & 1 == 1


def test_two_devices_share_an_open_drain_net(nominal_config):
    """The reason DeviceBus exists rather than another hardcoded slot."""
    sim = _load(nominal_config)
    port = sim.cores["pru0"].io_port
    a = port.attach_device(TCA9538Model(address=0x23, name="expander_a"))
    b = port.attach_device(TCA9538Model(address=0x24, name="expander_b"))

    sim.step("pru0", count=STEPS)

    # Both slaves sit on the same SDA/SCL and both must have seen the traffic;
    # only the addressed one should have been configured by it.
    assert a.events() and b.events()
    assert a.device.config_reg == 0x00
    assert b.device.config_reg != 0x00 or b.device.address == 0x23
