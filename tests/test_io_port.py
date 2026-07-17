"""Tests for pru_io/io_port.py — IOPort."""

import pytest
from pru_io.io_port import IOPort


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
