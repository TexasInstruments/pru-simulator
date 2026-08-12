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


class TestProtocolErrors:
    def test_wrong_address_nacks_and_does_not_write(self):
        dev = TCA9538Device(address=0x23)
        acks = _write_register(dev, 0x24, 0x03, 0x00)
        assert acks[0] is False
        assert dev.config_reg == 0xFF          # untouched
        assert dev.last_transaction["ack"] is False

    def test_read_request_nacks_even_with_correct_address(self):
        dev = TCA9538Device(address=0x23)
        _start(dev)
        ack = _send_byte(dev, (0x23 << 1) | 1)   # R/W=1 (read)
        assert ack is False
        _stop(dev)
        assert dev.output_reg == 0xFF
        assert dev.config_reg == 0xFF

    def test_stop_mid_address_byte_aborts_cleanly(self):
        dev = TCA9538Device(address=0x23)
        _start(dev)
        dev.step(False, True)
        dev.step(False, False)          # 1 data bit clocked, byte incomplete
        dev.step(True, False)
        _stop(dev)
        assert dev.state == "IDLE"
        assert dev.config_reg == 0xFF
        assert dev.output_reg == 0xFF

    def test_stop_after_ack_reg_before_data_aborts_with_no_write(self):
        dev = TCA9538Device(address=0x23)
        _start(dev)
        _send_byte(dev, (0x23 << 1) | 0)
        _send_byte(dev, 0x01)            # register pointer only
        _stop(dev)                        # no data byte sent
        assert dev.state == "IDLE"
        assert dev.output_reg == 0xFF     # unchanged — no data was written

    def test_second_data_byte_before_stop_is_rejected(self):
        dev = TCA9538Device(address=0x23)
        _start(dev)
        _send_byte(dev, (0x23 << 1) | 0)
        _send_byte(dev, 0x01)
        first_ack = _send_byte(dev, 0x05)   # first data byte commits 0x05
        assert first_ack is True
        assert dev.output_reg == 0x05
        # keep clocking a second byte instead of stopping
        second_ack = _send_byte(dev, 0xAA)
        assert second_ack is False
        assert dev.output_reg == 0x05        # second byte never committed
        _stop(dev)
        assert dev.state == "IDLE"


class TestStateSerialization:
    def test_get_state_shape(self):
        dev = TCA9538Device(address=0x23)
        _write_register(dev, 0x23, 0x01, 0x07)
        state = dev.get_state()
        assert state == {
            "address": 0x23, "output_reg": 0x07, "config_reg": 0xFF,
            "polarity_reg": 0x00, "saw_start": True,
            "last_transaction": {"address": 0x23, "reg": 0x01, "data": 0x07, "ack": True},
        }

    def test_snapshot_restore_round_trip_mid_transaction(self):
        dev = TCA9538Device(address=0x23)
        _start(dev)
        _send_byte(dev, (0x23 << 1) | 0)
        _send_byte(dev, 0x01)              # paused after ACK_REG -> DATA
        snap = dev.snapshot()

        # perturb, then restore
        _send_byte(dev, 0xFF)
        assert dev.output_reg == 0xFF

        dev.restore(snap)
        assert dev.state == "DATA"
        assert dev.output_reg == 0xFF or dev.output_reg == 0xFF  # unchanged by restore itself
        # completing the transaction from the restored state must still work
        ack = _send_byte(dev, 0x11)
        _stop(dev)
        assert ack is True
        assert dev.output_reg == 0x11
