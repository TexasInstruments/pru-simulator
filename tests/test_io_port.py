"""Tests for pru_io/io_port.py — IOPort."""

import pytest
from pru_io.io_port import IOPort
from pru_io.tca9538 import TCA9538Device


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_initial_gpo_is_zero():
    port = IOPort()
    assert port.gpo == 0


def test_initial_gpi_is_zero():
    port = IOPort()
    assert port.gpi == 0


def test_initial_gpo_pins_all_zero():
    port = IOPort()
    assert port.get_gpo_pins() == [0] * 20


def test_initial_gpi_pins_all_zero():
    port = IOPort()
    assert port.get_gpi_pins() == [0] * 20


# ---------------------------------------------------------------------------
# write_r30 / GPO
# ---------------------------------------------------------------------------

def test_write_r30_sets_gpo():
    port = IOPort()
    port.write_r30(0xABCDE)
    assert port.gpo == 0xABCDE


def test_write_r30_masks_to_20_bits():
    port = IOPort()
    port.write_r30(0xFFFFFFF)   # more than 20 bits
    assert port.gpo == 0x000FFFFF


def test_write_r30_only_20_bits_stored():
    port = IOPort()
    port.write_r30(0x100000)    # bit 20 should be masked away
    assert port.gpo == 0


# ---------------------------------------------------------------------------
# read_r31 / GPI
# ---------------------------------------------------------------------------

def test_read_r31_returns_gpi():
    port = IOPort()
    port.gpi = 0x55555
    assert port.read_r31() == 0x55555


def test_read_r31_masks_to_20_bits():
    port = IOPort()
    port.gpi = 0x1FFFFF          # 21 bits set
    assert port.read_r31() == 0x000FFFFF


# ---------------------------------------------------------------------------
# set_gpi_pin
# ---------------------------------------------------------------------------

def test_set_gpi_pin_sets_bit():
    port = IOPort()
    port.set_gpi_pin(0, True)
    assert port.gpi == 0x00001
    port.set_gpi_pin(19, True)
    assert port.gpi & (1 << 19)


def test_set_gpi_pin_clears_bit():
    port = IOPort()
    port.set_gpi_word(0xFFFFF)
    port.set_gpi_pin(0, False)
    assert port.gpi == 0xFFFFE
    port.set_gpi_pin(19, False)
    assert not (port.gpi & (1 << 19))


def test_set_gpi_pin_out_of_range_raises():
    port = IOPort()
    with pytest.raises(ValueError):
        port.set_gpi_pin(20, True)
    with pytest.raises(ValueError):
        port.set_gpi_pin(-1, True)


# ---------------------------------------------------------------------------
# set_gpi_word
# ---------------------------------------------------------------------------

def test_set_gpi_word_sets_all_bits():
    port = IOPort()
    port.set_gpi_word(0xFFFFF)
    assert port.gpi == 0xFFFFF


def test_set_gpi_word_masks_to_20_bits():
    port = IOPort()
    port.set_gpi_word(0x1FFFFF)
    assert port.gpi == 0xFFFFF


# ---------------------------------------------------------------------------
# get_gpo_pins
# ---------------------------------------------------------------------------

def test_get_gpo_pins_returns_20_elements():
    port = IOPort()
    pins = port.get_gpo_pins()
    assert len(pins) == 20


def test_get_gpo_pins_correct_values():
    port = IOPort()
    port.write_r30(0b10000000000000000001)   # bits 0 and 19 set
    pins = port.get_gpo_pins()
    assert pins[0]  == 1
    assert pins[19] == 1
    assert pins[1]  == 0
    assert pins[18] == 0


def test_get_gpo_pins_all_set():
    port = IOPort()
    port.write_r30(0xFFFFF)
    assert port.get_gpo_pins() == [1] * 20


# ---------------------------------------------------------------------------
# get_gpi_pins
# ---------------------------------------------------------------------------

def test_get_gpi_pins_returns_20_elements():
    port = IOPort()
    assert len(port.get_gpi_pins()) == 20


def test_get_gpi_pins_correct_values():
    port = IOPort()
    port.set_gpi_word(0b00000000000000000010)   # bit 1 set
    pins = port.get_gpi_pins()
    assert pins[1] == 1
    assert pins[0] == 0


def test_get_gpi_pins_all_set():
    port = IOPort()
    port.set_gpi_word(0xFFFFF)
    assert port.get_gpi_pins() == [1] * 20


# ---------------------------------------------------------------------------
# I2C wiring (opt-in TCA9538Device on SCL=bit0/SDA=bit1)
# ---------------------------------------------------------------------------

class TestI2CWiring:
    def test_attach_sets_device(self):
        port = IOPort()
        dev = TCA9538Device(address=0x23)
        port.attach_i2c_device(dev)
        assert port.i2c_device is dev

    def test_detach_clears_device(self):
        port = IOPort()
        port.attach_i2c_device(TCA9538Device())
        port.attach_i2c_device(None)
        assert port.i2c_device is None

    def test_both_released_bus_reads_high(self):
        port = IOPort()
        port.attach_i2c_device(TCA9538Device(address=0x23))
        port.write_r30(0b11)          # SCL=1, SDA=1 (both released)
        assert port.read_r31() & 0b10 == 0b10

    def test_master_drives_sda_low_bus_reads_low(self):
        port = IOPort()
        port.attach_i2c_device(TCA9538Device(address=0x23))
        port.write_r30(0b11)
        port.write_r30(0b01)          # SCL=1, SDA=0 (master drives low)
        assert port.read_r31() & 0b10 == 0

    def test_slave_ack_pulls_sda_low_even_though_master_released(self):
        port = IOPort()
        port.attach_i2c_device(TCA9538Device(address=0x23))
        # Drive a full START + matching address byte to reach ACK_ADDR,
        # where the slave itself pulls SDA low even though bit1 of the
        # written R30 value keeps SDA released.
        port.write_r30(0b11)                  # idle
        port.write_r30(0b01)                  # SDA falls (SCL still 1) -> START
        port.write_r30(0b00)                  # SCL falls
        addr_byte = (0x23 << 1) | 0
        for i in range(7, -1, -1):
            bit = (addr_byte >> i) & 1
            sda = bit                          # 1=released bit high, 0=drive low
            port.write_r30(0b00 | (sda << 1))  # SCL low, SDA=bit
            port.write_r30(0b01 | (sda << 1))  # SCL high -> sampled
            port.write_r30(0b00 | (sda << 1))  # SCL low again
        # 9th clock: master releases SDA (bit=1) for the ACK
        port.write_r30(0b10)                   # SCL low, SDA released
        port.write_r30(0b11)                   # SCL high -> slave should ACK (drive low)
        assert port.read_r31() & 0b10 == 0     # bus is low despite master's bit1=1

    def test_detach_restores_plain_gpio_on_bits_0_1(self):
        port = IOPort()
        port.attach_i2c_device(TCA9538Device(address=0x23))
        port.write_r30(0b11)          # idle, both released -> device forces bus high
        assert port.read_r31() & 0b10 == 0b10
        port.attach_i2c_device(None)
        port.write_r30(0b00)          # would pull SDA low if still attached
        # Detached: write_r30 no longer touches gpi at all (plain GPIO has
        # no GPO->GPI path without an explicit loopback group), so bit1
        # still reflects the device's last output rather than this new
        # write — proving the device's grip on gpi is fully released.
        assert port.read_r31() & 0b10 == 0b10
