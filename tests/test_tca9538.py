"""Tests for pru_io/tca9538.py — TCA9538Device I2C slave model."""
import pytest
from pru_io.tca9538 import TCA9538Device


def _clock_bit(dev, bit_value: bool) -> bool:
    """Drive one full SCL low->high->low cycle with SDA=bit_value held
    stable through the high phase (the only time it's sampled).
    Returns the bus SDA level seen during the high phase."""
    dev.step(False, True)          # SCL low, SDA released (setup)
    dev.step(False, bit_value)     # SCL still low, SDA settles to bit_value
    bus = dev.step(True, bit_value)  # SCL rises -> bit sampled
    dev.step(True, bit_value)        # hold high
    return bus


def _send_byte(dev, byte: int) -> bool:
    """Clock out 8 bits MSB-first, then clock the 9th (ACK) cycle with
    SDA released. Returns True if the slave ACKed (drove SDA low)."""
    for i in range(7, -1, -1):
        bit = bool((byte >> i) & 1)
        _clock_bit(dev, bit)
        dev.step(False, True)      # SCL falls, SDA released for next bit
    # 9th clock: master releases SDA, watches for slave ACK
    dev.step(False, True)
    ack_bus = dev.step(True, True)
    dev.step(True, True)
    dev.step(False, True)
    return ack_bus is False


def _start(dev) -> None:
    dev.step(True, True)           # idle: SCL=1, SDA=1
    dev.step(True, False)          # SDA falls while SCL=1 -> START
    dev.step(False, False)         # SCL falls, ready to clock


def _stop(dev) -> None:
    dev.step(False, False)         # SDA low while SCL low (setup)
    dev.step(True, False)          # SCL rises
    dev.step(True, True)           # SDA rises while SCL=1 -> STOP


def _write_register(dev, address: int, reg: int, data: int) -> list[bool]:
    """Full single-register-write transaction. Returns the 3 ACK bits
    (address, register pointer, data) in order."""
    _start(dev)
    acks = [_send_byte(dev, (address << 1) | 0)]
    acks.append(_send_byte(dev, reg))
    acks.append(_send_byte(dev, data))
    _stop(dev)
    return acks


class TestDefaults:
    def test_reset_defaults(self):
        dev = TCA9538Device(address=0x23)
        assert dev.output_reg == 0xFF
        assert dev.polarity_reg == 0x00
        assert dev.config_reg == 0xFF
        assert dev.state == "IDLE"
        assert dev.saw_start is False
        assert dev.last_transaction is None


class TestValidTransactions:
    def test_config_write_updates_config_reg(self):
        dev = TCA9538Device(address=0x23)
        acks = _write_register(dev, 0x23, 0x03, 0x00)
        assert acks == [True, True, True]
        assert dev.config_reg == 0x00
        assert dev.state == "IDLE"
        assert dev.saw_start is True

    def test_output_write_updates_output_reg(self):
        dev = TCA9538Device(address=0x23)
        _write_register(dev, 0x23, 0x03, 0x00)   # outputs enabled first
        acks = _write_register(dev, 0x23, 0x01, 0x05)
        assert acks == [True, True, True]
        assert dev.output_reg == 0x05

    def test_last_transaction_records_completed_write(self):
        dev = TCA9538Device(address=0x23)
        _write_register(dev, 0x23, 0x01, 0x42)
        assert dev.last_transaction == {
            "address": 0x23, "reg": 0x01, "data": 0x42, "ack": True,
        }
