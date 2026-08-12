# I2C Master → TCA9538 Running LED Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a TCA9538 I2C-IO-expander device model to the PRU simulator and a 1 Mbit/s bit-bang I2C master firmware that drives a walking-bit "running LED" pattern across the TCA9538's 8 output ports, fully observable in the dashboard and via the MCP server.

**Architecture:** A new `TCA9538Device` Python state machine (`pru_io/tca9538.py`) models the slave's I2C protocol and its Output/Configuration/Polarity registers. `IOPort` gains an opt-in open-drain wiring hook on R30/R31 bits 0/1 (SCL/SDA) that runs the device's `step()` on every R30 write and folds the wired-AND bus level back into GPI. PRU firmware (`source/i2c_tca9538_running_led.asm`) bit-bangs START/address/register-pointer/data/STOP using `JAL`/`JMP` subroutines, first configuring all 8 pins as outputs, then looping a single bit 0x01→0x80 into the Output Port register forever. The simulator, MCP server, and dashboard UI are wired up last so the whole path is drivable both by an automated test and by hand from the browser.

**Tech Stack:** Python (pytest), PRU assembler (this project's own ISA — see `source/*.asm` for precedent), vanilla JS/HTML (`ui/static/app.js`, `ui/static/index.html`), FastAPI websocket server (`ui/server.py`).

## Global Constraints

- PRU core clock: 200 MHz. I2C bit rate: 1 Mbit/s → 200 cycles/bit, split ~100/100 across SCL-low/SCL-high (confirm and tune in Task 7 — do not trust hand-tallied cycle counts without single-stepping, per this project's standing rule).
- TCA9538 slave address: `0x23` (7-bit).
- SCL = R30/R31 bit 0. SDA = R30/R31 bit 1. Open-drain convention: firmware writes **1 = release**, **0 = drive low**, for both lines.
- The I2C device is **opt-in** (`IOPort.attach_i2c_device()` / `None`) — bits 0/1 already mean SCLK/MOSI in `source/spi_master_tx.asm`; the device must never be silently always-on.
- Write-only slave: no Input Port register, no read-transaction support. A read request (R/W=1) NACKs.
- Single-byte register writes only: a second data byte before STOP is a protocol error (transaction aborted, no register write).
- TCA9538 real reset defaults: Output Port = 0xFF, Polarity Inversion = 0x00, Configuration = 0xFF (all pins inputs).
- Design doc: `docs/superpowers/specs/2026-08-12-i2c-tca9538-running-led-design.md` — consult it for the full state-machine rationale if anything here is ambiguous.

---

### Task 1: `TCA9538Device` — reset defaults + valid CONFIG/OUTPUT write transactions

**Files:**
- Create: `pru_io/tca9538.py`
- Test: `tests/test_tca9538.py`

**Interfaces:**
- Produces: `TCA9538Device(address: int = 0x23)` with attributes `.address`, `.output_reg`, `.config_reg`, `.polarity_reg`, `.state` (str), `.saw_start` (bool), `.last_transaction` (dict | None); method `.step(scl: bool, sda_master: bool) -> bool` (returns the wired-AND bus SDA level).

- [ ] **Step 1: Write the failing tests for construction defaults and a full CONFIG write**

```python
# tests/test_tca9538.py
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_tca9538.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pru_io.tca9538'`

- [ ] **Step 3: Implement `TCA9538Device`**

```python
# pru_io/tca9538.py
"""TCA9538Device — an I2C slave model of the TCA9538 8-bit IO expander.

Write-only: implements Configuration (0x03), Output Port (0x01) and
Polarity Inversion (0x02) registers. No Input Port register, no read
transactions (a read request NACKs). Single-byte register writes only —
a second data byte before STOP aborts the transaction.

Stepped once per R30 write by IOPort (see io_port.py), sampling SCL/SDA
at instruction granularity: START/STOP are SDA transitions while SCL
stays high; data bits are sampled on the SCL rising edge.
"""

from __future__ import annotations


class TCA9538Device:
    def __init__(self, address: int = 0x23):
        self.address = address & 0x7F
        self.output_reg = 0xFF
        self.polarity_reg = 0x00
        self.config_reg = 0xFF

        self.state = "IDLE"
        self.saw_start = False
        self.last_transaction: dict | None = None

        self._prev_scl = True
        self._prev_sda = True
        self._shift = 0
        self._bit_count = 0
        self._reg_ptr: int | None = None
        self._match = False

    def step(self, scl: bool, sda_master: bool) -> bool:
        """Advance the state machine by one R30-write's worth of bus
        activity. Returns the wired-AND bus SDA level."""
        slave_low = self._slave_drives_low()
        bus_sda = bool(sda_master) and not slave_low

        prev_scl, prev_sda = self._prev_scl, self._prev_sda
        if prev_scl and scl and prev_sda and not bus_sda:
            self._on_start()
        elif prev_scl and scl and (not prev_sda) and bus_sda:
            self._on_stop()
        elif (not prev_scl) and scl:
            self._on_scl_rising(bus_sda)

        self._prev_scl, self._prev_sda = scl, bus_sda
        return bus_sda

    # ------------------------------------------------------------------
    def _slave_drives_low(self) -> bool:
        if self.state == "ACK_ADDR":
            return self._match
        if self.state in ("ACK_REG", "ACK_DATA"):
            return True
        return False

    def _on_start(self) -> None:
        self.saw_start = True
        self.state = "ADDR"
        self._shift = 0
        self._bit_count = 0
        self._match = False

    def _on_stop(self) -> None:
        self.state = "IDLE"
        self._shift = 0
        self._bit_count = 0

    def _on_scl_rising(self, bus_sda: bool) -> None:
        if self.state in ("ADDR", "REGPTR", "DATA"):
            self._shift = ((self._shift << 1) | (1 if bus_sda else 0)) & 0xFF
            self._bit_count += 1
            if self._bit_count == 8:
                self._bit_count = 0
                self._complete_byte()
        elif self.state == "ACK_ADDR":
            if self._match:
                self.state = "REGPTR"
            else:
                self.last_transaction = {
                    "address": self._shift >> 1, "reg": None,
                    "data": None, "ack": False,
                }
                self.state = "IDLE"
            self._shift = 0
        elif self.state == "ACK_REG":
            self.state = "DATA"
            self._shift = 0
        elif self.state == "ACK_DATA":
            # This edge is the ACK bit for the byte _complete_byte() just
            # committed — the write already happened. Only a STOP is valid
            # from here (single-byte writes only), so move to DONE rather
            # than treating this normal ACK cycle as an error.
            self.state = "DONE"
        elif self.state == "DONE":
            # Firmware kept clocking instead of issuing STOP — this is the
            # real "second byte" protocol violation.
            if self.last_transaction is not None:
                self.last_transaction["ack"] = False
            self.state = "IDLE"

    def _complete_byte(self) -> None:
        if self.state == "ADDR":
            addr = self._shift >> 1
            rw_write = (self._shift & 1) == 0
            self._match = (addr == self.address) and rw_write
            self.state = "ACK_ADDR"
        elif self.state == "REGPTR":
            self._reg_ptr = self._shift
            self.state = "ACK_REG"
        elif self.state == "DATA":
            if self._reg_ptr == 0x01:
                self.output_reg = self._shift
            elif self._reg_ptr == 0x02:
                self.polarity_reg = self._shift
            elif self._reg_ptr == 0x03:
                self.config_reg = self._shift
            self.last_transaction = {
                "address": self.address, "reg": self._reg_ptr,
                "data": self._shift, "ack": True,
            }
            self.state = "ACK_DATA"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_tca9538.py -v`
Expected: PASS (all `TestDefaults`/`TestValidTransactions` tests)

- [ ] **Step 5: Commit**

```bash
git add pru_io/tca9538.py tests/test_tca9538.py
git commit -m "feat(i2c): add TCA9538 device model with valid write transactions"
```

---

### Task 2: `TCA9538Device` — protocol error paths

**Files:**
- Modify: `pru_io/tca9538.py`
- Test: `tests/test_tca9538.py`

**Interfaces:**
- Consumes: `TCA9538Device`, `_start`/`_send_byte`/`_stop`/`_write_register` helpers from Task 1's test file.
- Produces: no new public surface — this task hardens existing behavior against malformed sequences.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_tca9538.py

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
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_tca9538.py::TestProtocolErrors -v`
Expected: PASS. Task 1's implementation already includes the `DONE` state
(reached after a data byte's own ACK cycle) that these tests exercise:
wrong address and read-request both fail the `_match` check in
`_complete_byte`/`ADDR` so the slave never ACKs; a STOP before a byte's
8th bit completes means `_complete_byte` never ran, so no register write
happened; and a second byte clocked after `DONE` (instead of a STOP) hits
`_on_scl_rising`'s `DONE` branch, which is the real "protocol violation"
path distinct from the byte's own ACK-completion edge. This task adds the
tests that pin that behavior down — no new implementation should be
needed. If any test fails, that means Task 1's state machine has a real
bug: inspect which assertion failed, fix `_on_scl_rising`/`_complete_byte`
in `pru_io/tca9538.py`, and re-run until this command's actual output is
PASS — don't assume it passes without running it.

- [ ] **Step 3: Commit**

```bash
git add pru_io/tca9538.py tests/test_tca9538.py
git commit -m "test(i2c): confirm TCA9538 protocol error handling (NACK, mid-transaction STOP, repeated data byte)"
```

---

### Task 3: `TCA9538Device` — `get_state()` / `snapshot()` / `restore()`

**Files:**
- Modify: `pru_io/tca9538.py`
- Test: `tests/test_tca9538.py`

**Interfaces:**
- Produces: `.get_state() -> dict` (UI-facing snapshot: `address`, `output_reg`, `config_reg`, `polarity_reg`, `saw_start`, `last_transaction`), `.snapshot() -> dict` / `.restore(dict) -> None` (full internal state, for step-back — same convention as `SigmaDeltaFilter.snapshot()`/`.restore()` in `pru_io/sd_filter.py`).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_tca9538.py

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_tca9538.py::TestStateSerialization -v`
Expected: FAIL — `AttributeError: 'TCA9538Device' object has no attribute 'get_state'`

- [ ] **Step 3: Implement the three methods**

```python
# append to pru_io/tca9538.py, inside class TCA9538Device

    def get_state(self) -> dict:
        """UI-facing snapshot — deliberately excludes bit-level shift
        register / edge-tracking internals."""
        return {
            "address": self.address,
            "output_reg": self.output_reg,
            "config_reg": self.config_reg,
            "polarity_reg": self.polarity_reg,
            "saw_start": self.saw_start,
            "last_transaction": dict(self.last_transaction) if self.last_transaction else None,
        }

    def snapshot(self) -> dict:
        """Full internal state, for step-back (see ui/server.py's
        `_snapshot`/`_restore`, matching SigmaDeltaFilter's convention)."""
        return {
            "output_reg": self.output_reg,
            "config_reg": self.config_reg,
            "polarity_reg": self.polarity_reg,
            "state": self.state,
            "saw_start": self.saw_start,
            "last_transaction": dict(self.last_transaction) if self.last_transaction else None,
            "prev_scl": self._prev_scl,
            "prev_sda": self._prev_sda,
            "shift": self._shift,
            "bit_count": self._bit_count,
            "reg_ptr": self._reg_ptr,
            "match": self._match,
        }

    def restore(self, snap: dict) -> None:
        self.output_reg = snap["output_reg"]
        self.config_reg = snap["config_reg"]
        self.polarity_reg = snap["polarity_reg"]
        self.state = snap["state"]
        self.saw_start = snap["saw_start"]
        self.last_transaction = dict(snap["last_transaction"]) if snap["last_transaction"] else None
        self._prev_scl = snap["prev_scl"]
        self._prev_sda = snap["prev_sda"]
        self._shift = snap["shift"]
        self._bit_count = snap["bit_count"]
        self._reg_ptr = snap["reg_ptr"]
        self._match = snap["match"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_tca9538.py -v`
Expected: PASS (full file — all three test classes plus this one)

- [ ] **Step 5: Commit**

```bash
git add pru_io/tca9538.py tests/test_tca9538.py
git commit -m "feat(i2c): add TCA9538Device get_state/snapshot/restore"
```

---

### Task 4: `IOPort` — opt-in open-drain I2C wiring

**Files:**
- Modify: `pru_io/io_port.py`
- Test: `tests/test_io_port.py`

**Interfaces:**
- Consumes: `TCA9538Device` from `pru_io.tca9538` (Task 1), specifically `.step(scl: bool, sda_master: bool) -> bool`.
- Produces: `IOPort.i2c_device` attribute; `IOPort.attach_i2c_device(device: TCA9538Device | None) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_io_port.py
from pru_io.tca9538 import TCA9538Device


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
            port.write_r30(0b10 | (sda << 1))  # SCL high -> sampled
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_io_port.py::TestI2CWiring -v`
Expected: FAIL — `AttributeError: 'IOPort' object has no attribute 'attach_i2c_device'`

- [ ] **Step 3: Implement the wiring in `pru_io/io_port.py`**

```python
# pru_io/io_port.py — add near the top, alongside the existing TYPE_CHECKING import
if TYPE_CHECKING:
    from pru_io.sd_filter import SigmaDeltaFilter
    from pru_io.tca9538 import TCA9538Device

_MASK_20 = 0x000FFFFF
_I2C_SCL_BIT = 0
_I2C_SDA_BIT = 1
```

```python
# in IOPort.__init__, alongside the other peripheral slots
        self.i2c_device = None        # type: TCA9538Device | None
```

```python
# new method, placed near set_loopback_group
    def attach_i2c_device(self, device: "TCA9538Device | None") -> None:
        """Attach (or detach with None) an I2C slave model on SCL=bit0/SDA=bit1.

        Opt-in: bits 0/1 already mean SCLK/MOSI to spi_master_tx.asm, so an
        attached device must never be on by default.
        """
        self.i2c_device = device
```

```python
# write_r30 — append after the existing loopback_mask block, so the device
# (a real wired-AND model) wins over the generic bit-mirror loopback if a
# user somehow enables both on bits 0/1 at once.
    def write_r30(self, value: int, wstrb: int = 0xF) -> None:
        self.gpo = value & _MASK_20
        if self.perif is not None:
            self.perif.process_r30(value, wstrb)
        if self.sd_filter is not None:
            self.sd_filter.process_r30(value)
        if not (value & (1 << 25)) and self.loopback_mask:
            self.gpi = (self.gpi & ~self.loopback_mask) | (self.gpo & self.loopback_mask)
        if self.i2c_device is not None:
            scl = bool(value & (1 << _I2C_SCL_BIT))
            sda_master = bool(value & (1 << _I2C_SDA_BIT))
            bus_sda = self.i2c_device.step(scl, sda_master)
            if bus_sda:
                self.gpi |= (1 << _I2C_SDA_BIT)
            else:
                self.gpi &= ~(1 << _I2C_SDA_BIT)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_io_port.py -v`
Expected: PASS (all of `test_io_port.py`, including the pre-existing tests — confirms the new hook doesn't disturb plain-GPIO/SD/perif behavior when `i2c_device is None`)

- [ ] **Step 5: Commit**

```bash
git add pru_io/io_port.py tests/test_io_port.py
git commit -m "feat(i2c): opt-in open-drain SCL/SDA wiring in IOPort"
```

---

### Task 5: `Simulator` + MCP tool wiring

**Files:**
- Modify: `simulator.py`
- Modify: `mcp_server/server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `IOPort.attach_i2c_device` (Task 4), `TCA9538Device` (Task 1/3).
- Produces: `Simulator.i2c_attach(core: str, enabled: bool, address: int = 0x23) -> None`, `Simulator.i2c_state(core: str) -> dict | None`; `PRUSimulatorMCP.pru_i2c_attach(core: str = "pru0", enabled: bool = True, address: int = 0x23) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_mcp_server.py, inside class TestMCPTools
# (match the existing style: self.mcp = PRUSimulatorMCP() is set up in setUp/fixture — follow whatever this file already does for the other tests in the class)

    def test_pru_i2c_attach(self):
        result = self.mcp.pru_i2c_attach(core="pru0", enabled=True, address=0x23)
        assert result["success"] is True
        assert result["address"] == 0x23
        io_state = self.mcp.sim.i2c_state("pru0")
        assert io_state["address"] == 0x23
        assert io_state["config_reg"] == 0xFF

        detach = self.mcp.pru_i2c_attach(core="pru0", enabled=False)
        assert detach["success"] is True
        assert self.mcp.sim.i2c_state("pru0") is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_mcp_server.py -k test_pru_i2c_attach -v`
Expected: FAIL — `AttributeError: 'PRUSimulatorMCP' object has no attribute 'pru_i2c_attach'`

- [ ] **Step 3: Implement `Simulator.i2c_attach`/`i2c_state`**

```python
# simulator.py — add near sd_state/perif_state (imports section: add
# `from pru_io.tca9538 import TCA9538Device` near the top with the other
# pru_io imports)

    def i2c_attach(self, core: str, enabled: bool, address: int = 0x23) -> None:
        """Attach or detach a TCA9538 device model on *core*'s SCL/SDA
        (R30/R31 bits 0/1)."""
        pru = self._get_core(core)
        pru.io_port.attach_i2c_device(TCA9538Device(address) if enabled else None)

    def i2c_state(self, core: str) -> dict | None:
        """Return TCA9538 device state for *core*, or None if not attached."""
        dev = self._get_core(core).io_port.i2c_device
        return dev.get_state() if dev is not None else None
```

- [ ] **Step 4: Implement `PRUSimulatorMCP.pru_i2c_attach`**

```python
# mcp_server/server.py — add alongside pru_set_input

    def pru_i2c_attach(self, core: str = "pru0", enabled: bool = True, address: int = 0x23) -> dict:
        """Attach or detach a TCA9538 I2C device model on SCL=bit0/SDA=bit1 of the specified core."""
        self.sim.i2c_attach(core, enabled, address)
        return {"success": True, "core": core, "enabled": enabled, "address": address}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_mcp_server.py -k test_pru_i2c_attach -v`
Expected: PASS

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `pytest -q`
Expected: PASS (all prior tests plus the new ones)

- [ ] **Step 7: Commit**

```bash
git add simulator.py mcp_server/server.py tests/test_mcp_server.py
git commit -m "feat(i2c): wire TCA9538 attach through Simulator and MCP server"
```

---

### Task 6: Firmware — `i2c_tca9538_running_led.asm` + init-sequence test

**Files:**
- Create: `source/i2c_tca9538_running_led.asm`
- Test: `tests/test_i2c_tca9538_firmware.py`

**Interfaces:**
- Consumes: `Simulator.i2c_attach` (Task 5), `Simulator.load`/`Simulator.step` (existing), `Simulator.i2c_state` (Task 5).
- Produces: `source/i2c_tca9538_running_led.asm` (loadable PRU program).

- [ ] **Step 1: Write the firmware**

```asm
; source/i2c_tca9538_running_led.asm
; PRU0 @ 200 MHz — bit-bang I2C master, 1 Mbit/s, "direct mode" GPIO
; (R30/R31 as plain GPIO — GPCFG.PRU_GP_MUX_SEL = 0, the reset default).
;
; Drives a TCA9538 8-bit IO expander (address 0x23) at 1 Mbit/s:
; configures all 8 ports as outputs, then loops a single bit
; 0x01 -> 0x02 -> ... -> 0x80 -> 0x01 ... into the Output Port register
; forever — a running LED across the expander's 8 physical pins.
;
; Open-drain convention (see pru_io/io_port.py): writing 1 to a bit
; RELEASES the line (pulled high externally / by the slave); writing 0
; DRIVES it low. This is not push-pull GPIO.
;
; Pins (R30/R31):
;   SCL = bit 0
;   SDA = bit 1  (bidirectional: master drives during address/reg/data,
;                 releases for the ACK bit so the slave can pull it low)
;
; Timing: 200 MHz / 1 Mbit/s = 200 cycles/bit, split ~100 cycles
; SCL-low / ~100 cycles SCL-high. DELAY_COUNT below is a starting
; estimate (2 cycles per SUB+QBNE iteration, ~4 cycles fixed overhead
; per phase) — Task 7 single-steps the first byte and tunes it against
; the actual per-edge cycle count before trusting the full run loop.
;
; Call convention: single level of JAL/JMP subroutines (i2c_start,
; i2c_stop, i2c_write_byte), all returning via r1 — none of them call
; each other, so one return-address register is enough (no nesting).
;
; Register map:
;   r0   scratch (error-flag value in run_nack)
;   r1   subroutine return address
;   r2   byte being shifted out by i2c_write_byte / loaded before each call
;   r3   delay-loop counter (i2c_start / i2c_stop / i2c_write_byte)
;   r6   bit counter within i2c_write_byte (8 downto 1)
;   r19  DRAM0 error-flag address scratch
;   r20  ACK/NACK result after i2c_write_byte: 0 = ACK, 2 = NACK (raw
;        bit1 of the sampled R31, not normalized to 0/1)
;   r21  running-LED pattern byte (0x01 .. 0x80, wraps)
;   r30  GPO — SCL=bit0, SDA=bit1
;   r31  GPI — SDA read-back on bit1

SCL_BIT     .set 0
SDA_BIT     .set 1
DELAY_COUNT .set 48          ; ~100 cycles/half-bit; tune in Task 7
ADDR_W      .set 0x46        ; TCA9538 addr 0x23, R/W=0 (write): (0x23<<1)|0
CFG_REG     .set 0x03
OUT_REG     .set 0x01
ERR_ADDR    .set 0x0FFE

start:
    ldi  r30, 0x0003          ; idle bus: SCL=1, SDA=1 (both released)

    jal  r1, i2c_start
    ldi  r2, ADDR_W
    jal  r1, i2c_write_byte
    qbne init_fail, r20, 0

    ldi  r2, CFG_REG
    jal  r1, i2c_write_byte
    qbne init_fail, r20, 0

    ldi  r2, 0x00              ; all 8 pins = outputs
    jal  r1, i2c_write_byte
    qbne init_fail, r20, 0

    jal  r1, i2c_stop

    ldi  r21, 0x01             ; running-LED pattern, starts at P0

run_loop:
    jal  r1, i2c_start
    ldi  r2, ADDR_W
    jal  r1, i2c_write_byte
    qbne run_nack, r20, 0

    ldi  r2, OUT_REG
    jal  r1, i2c_write_byte
    qbne run_nack, r20, 0

    mov  r2, r21
    jal  r1, i2c_write_byte
    qbne run_nack, r20, 0

    jal  r1, i2c_stop
    jmp  advance_pattern

run_nack:
    ldi  r0, 1
    ldi  r19, ERR_ADDR
    sbco &r0, c24, r19, 1      ; DRAM0[0x0FFE] = 1 (soft error flag)
    jal  r1, i2c_stop          ; release the bus, retry next iteration

advance_pattern:
    lsl  r21, r21, 1
    qbne run_loop, r21, 0x100  ; branch back unless we just wrapped past P7
    ldi  r21, 0x01
    jmp  run_loop

init_fail:
    halt                       ; nothing useful can happen without CONFIG set

; ---------------------------------------------------------------------
; i2c_start — from idle (SDA=1, SCL=1): generate a START condition.
i2c_start:
    set  r30, r30, SCL_BIT
    set  r30, r30, SDA_BIT
    ldi  r3, DELAY_COUNT
is_d1:
    sub  r3, r3, 1
    qbne is_d1, r3, 0
    clr  r30, r30, SDA_BIT     ; SDA falls while SCL=1 -> START
    ldi  r3, DELAY_COUNT
is_d2:
    sub  r3, r3, 1
    qbne is_d2, r3, 0
    clr  r30, r30, SCL_BIT     ; SCL low, ready to clock the first bit
    jmp  r1

; ---------------------------------------------------------------------
; i2c_stop — from SCL=0: generate a STOP condition, leaves bus idle.
i2c_stop:
    clr  r30, r30, SDA_BIT     ; SDA low while SCL low (setup)
    ldi  r3, DELAY_COUNT
ps_d1:
    sub  r3, r3, 1
    qbne ps_d1, r3, 0
    set  r30, r30, SCL_BIT
    ldi  r3, DELAY_COUNT
ps_d2:
    sub  r3, r3, 1
    qbne ps_d2, r3, 0
    set  r30, r30, SDA_BIT     ; SDA rises while SCL=1 -> STOP
    jmp  r1

; ---------------------------------------------------------------------
; i2c_write_byte — shift r2 out MSB-first, then sample the ACK bit.
; Returns: r20 = 0 (ACK) or 2 (NACK).
i2c_write_byte:
    ldi  r6, 8
wb_bit:
    clr  r30, r30, SCL_BIT
    qbbc wb_clear, r2, 7
    set  r30, r30, SDA_BIT
    jmp  wb_delay1
wb_clear:
    clr  r30, r30, SDA_BIT
wb_delay1:
    ldi  r3, DELAY_COUNT
wb_d1:
    sub  r3, r3, 1
    qbne wb_d1, r3, 0
    set  r30, r30, SCL_BIT     ; slave samples SDA
    ldi  r3, DELAY_COUNT
wb_d2:
    sub  r3, r3, 1
    qbne wb_d2, r3, 0
    lsl  r2, r2, 1
    sub  r6, r6, 1
    qbne wb_bit, r6, 0

    ; 9th clock: release SDA, sample the ACK bit
    clr  r30, r30, SCL_BIT
    set  r30, r30, SDA_BIT
    ldi  r3, DELAY_COUNT
wb_d3:
    sub  r3, r3, 1
    qbne wb_d3, r3, 0
    set  r30, r30, SCL_BIT
    ldi  r3, DELAY_COUNT
wb_d4:
    sub  r3, r3, 1
    qbne wb_d4, r3, 0
    and  r20, r31, 2           ; 0 = ACK (slave drove low), 2 = NACK
    clr  r30, r30, SCL_BIT
    jmp  r1
```

- [ ] **Step 2: Write the failing integration test**

```python
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
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/test_i2c_tca9538_firmware.py -v`
Expected: FAIL — likely `AssertionError` (config_reg still 0xFF) if the
step budget is wrong, or a parser error if the assembler rejects any
instruction used above (e.g. `qbbc`/`jal`/`jmp` operand forms). Read the
actual failure before assuming which.

- [ ] **Step 4: Fix whatever the failure reveals**

If it's a parse/assemble error, fix the offending instruction in
`source/i2c_tca9538_running_led.asm` to match this project's actual ISA
(cross-check the exact operand syntax against `source/spi_master_tx.asm`
for `clr`/`set`/`qbbc`/`jal`/`jmp` — this plan's code was written from
that file's precedent but must be verified against the real assembler,
not assumed correct). If it's a step-budget issue, increase `count` and
re-run. Iterate until:

Run: `pytest tests/test_i2c_tca9538_firmware.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add source/i2c_tca9538_running_led.asm tests/test_i2c_tca9538_firmware.py
git commit -m "feat(i2c): add 1 Mbit/s I2C master firmware for TCA9538 running LED"
```

---

### Task 7: Firmware — run-loop pattern cycling, NACK error flag, and cycle-timing verification

**Files:**
- Modify: `source/i2c_tca9538_running_led.asm` (only if Task 6's stepwise verification finds `DELAY_COUNT` needs adjustment)
- Modify: `tests/test_i2c_tca9538_firmware.py`

**Interfaces:**
- Consumes: everything from Task 6.
- Produces: no new interfaces — hardens the firmware's steady-state behavior and confirms its bit timing empirically instead of by hand-arithmetic (per this project's standing rule that cycle-tight timing claims must be sim-validated).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_i2c_tca9538_firmware.py

class TestRunLoop:
    def test_output_register_cycles_through_walking_bit_pattern(self, nominal_config):
        sim = _make_sim(nominal_config)
        sim.step("pru0", count=20_000)          # clear the init sequence
        seen = []
        for _ in range(10):
            sim.step("pru0", count=8_000)        # one output-port transaction
            seen.append(sim.i2c_state("pru0")["output_reg"])
        assert seen[:8] == [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80]
        assert seen[8] == 0x01                   # wrapped

    def test_wrong_address_sets_dram0_error_flag(self, nominal_config):
        sim = Simulator(nominal_config)
        errors = sim.load("pru0", SOURCE)
        assert errors == []
        sim.i2c_attach("pru0", True, address=0x24)  # firmware targets 0x23 -> mismatch
        sim.step("pru0", count=20_000)
        err_byte = sim.memory_read(0x0FFE, 1)
        assert err_byte[0] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_i2c_tca9538_firmware.py::TestRunLoop -v`
Expected: Likely FAIL on `test_output_register_cycles_through_walking_bit_pattern`
if the `count=8_000` per-transaction step budget doesn't line up with the
actual cycles-per-transaction (three `i2c_write_byte` calls at up to ~200
cycles/bit × 9 bits + `i2c_start`/`i2c_stop` overhead). Record the actual
failure — don't guess at the fix.

- [ ] **Step 3: Single-step the first byte via the MCP tools to measure real timing**

This is interactive, not a checked-in test — do it directly against the
simulator (either via a scratch Python script calling `Simulator` the same
way `mcp_server/server.py`'s `pru_step` does, or via the MCP tools
`pru_load` / `pru_step` / `pru_registers` if running through an MCP
client). Either way, construct the `Simulator` against a config pinned to
200 MHz (e.g. reuse the `nominal_config` fixture from a throwaway script,
or check the repo's `memory.cfg` `[device] pru_clock_mhz` value and set it
to `200` before starting the dashboard/MCP server) — measuring against
whatever clock the dashboard last had selected would produce numbers that
don't match this firmware's 200 MHz assumption.

1. Load `source/i2c_tca9538_running_led.asm` on `pru0`, attach the I2C
   device at `0x23`.
2. Step one instruction at a time through `i2c_start` and the first
   `i2c_write_byte` call (the address byte). After each `set r30.../clr
   r30...` that touches `SCL_BIT`, note the cycle count (`sim.cores["pru0"].counters.cycles`
   or the MCP `pru_registers`/status equivalent).
3. Compute the actual cycles between consecutive SCL rising edges (one
   full bit period) and between a falling and the next rising edge (one
   half-bit). Compare against the 100-cycle-per-half-bit target (1 Mbit/s
   at 200 MHz = 200 cycles/bit).
4. If the measured half-bit period is off by more than ~5%, adjust
   `DELAY_COUNT` in `source/i2c_tca9538_running_led.asm` (each unit of
   `DELAY_COUNT` costs 2 cycles in the `SUB`+`QBNE` delay loop) and
   re-measure. Repeat until within tolerance.
5. Record the final measured cycles/bit in a one-line comment next to
   `DELAY_COUNT .set N` (replace the "tune in Task 7" note with the
   actual measured value, e.g. `; measured Xc/half-bit at N=48`).

- [ ] **Step 4: Adjust the test step budgets to match measured timing, then re-run**

Update `count=8_000` in `test_output_register_cycles_through_walking_bit_pattern`
(and `count=20_000` anywhere else in this file) to a value comfortably
above the measured per-transaction cycle count from Step 3 (headroom for
`i2c_start`/`i2c_stop` overhead and the 9-bit ACK cycle on top of 8 data
bits, times 3 bytes per transaction).

Run: `pytest tests/test_i2c_tca9538_firmware.py -v`
Expected: PASS (both `TestInitSequence` and `TestRunLoop`)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add source/i2c_tca9538_running_led.asm tests/test_i2c_tca9538_firmware.py
git commit -m "test(i2c): verify running-LED pattern cycling and NACK error flag against measured bit timing"
```

---

### Task 8: UI backend — `ui/server.py` state push, step-back, and attach action

**Files:**
- Modify: `ui/server.py`

**Interfaces:**
- Consumes: `Simulator.i2c_attach`/`i2c_state` (Task 5), `TCA9538Device.snapshot`/`restore` (Task 3).
- Produces: `io_section["i2c"]` in the websocket `"state"` message payload; a new `"i2c_attach"` websocket action.

- [ ] **Step 1: Add I2C to `_snapshot`/`_restore` (step-back support)**

```python
# ui/server.py — in _snapshot(), alongside sd/perif
def _snapshot(core: str) -> dict:
    c = sim.cores[core]
    ls = c.loop_state
    sd = c.io_port.sd_filter
    perif = c.io_port.perif
    i2c = c.io_port.i2c_device
    return {
        # ...existing keys unchanged...
        "sd": sd.snapshot() if sd is not None else None,
        "perif": perif.snapshot() if perif is not None else None,
        "i2c": i2c.snapshot() if i2c is not None else None,
    }
```

```python
# ui/server.py — in _restore(), alongside sd/perif
    if snap.get("sd") is not None and c.io_port.sd_filter is not None:
        c.io_port.sd_filter.restore(snap["sd"])
    if snap.get("perif") is not None and c.io_port.perif is not None:
        c.io_port.perif.restore(snap["perif"])
    if snap.get("i2c") is not None and c.io_port.i2c_device is not None:
        c.io_port.i2c_device.restore(snap["i2c"])
```

- [ ] **Step 2: Add `io.i2c` to `_send_state`'s payload**

```python
# ui/server.py — in _send_state(), alongside sd_data/perif_data
    sd_data = sim.sd_state(core)
    perif_data = sim.perif_state(core)
    i2c_data = sim.i2c_state(core)
    mux_sel = sim.gpcfg_state(core)["mux_sel"]
    mode = _io_mode(core, sd_data=sd_data, perif_data=perif_data, mux_sel=mux_sel)
    io_section = {
        "mode": mode,
        "mux_sel": mux_sel,
        "gpo_pins": gpo_pins,
        "gpi_pins": c.io_port.get_gpi_pins(),
    }
    if sd_data is not None:
        io_section["sd"] = sd_data
    if perif_data is not None:
        io_section["perif"] = perif_data
        io_section["loopback"] = sim.loopback_state()
    if i2c_data is not None:
        io_section["i2c"] = i2c_data
```

- [ ] **Step 3: Add the `i2c_attach` websocket action**

```python
# ui/server.py — in the websocket_endpoint message loop, alongside set_loopback
            elif action == "i2c_attach":
                sim.i2c_attach(core, bool(msg.get("enabled", False)), int(msg.get("address", 0x23)))
                await _send_state(websocket, core)
```

- [ ] **Step 4: Manually verify with the running dev server**

```bash
python ui/server.py
```

Open `http://localhost:8080`, open the browser devtools Network/WS tab,
and manually send `{"action": "i2c_attach", "core": "pru0", "enabled":
true, "address": 35}` over the `/ws` connection (or temporarily add a
`console.log` / call from the browser JS console via the existing
`sendAction` helper once Task 9 wires up a button — if Task 9 isn't done
yet, this step is just confirming the server doesn't error and the next
`"state"` push includes an `"i2c"` key under `"io"`). Confirm no
exceptions in the server's terminal output.

- [ ] **Step 5: Commit**

```bash
git add ui/server.py
git commit -m "feat(i2c): wire TCA9538 attach/state into the websocket state push and step-back"
```

---

### Task 9: UI frontend — IO panel section + attach toggle

**Files:**
- Modify: `ui/static/index.html`
- Modify: `ui/static/app.js`

**Interfaces:**
- Consumes: `io.i2c` from the `"state"` websocket message (Task 8): `{address, output_reg, config_reg, polarity_reg, saw_start, last_transaction}`.
- Produces: a rendered "TCA9538" IO-panel section; a `sendAction({action: 'i2c_attach', ...})` call wired to a toggle button.

- [ ] **Step 1: Add the HTML section and attach toggle**

```html
<!-- ui/static/index.html — insert after the existing perif-interface
     block (after the closing </div> that follows perif-lb-status),
     before the <hr class="uart-divider" /> -->
          <!-- TCA9538 I2C IO expander (shown once a valid START is seen; coexists with plain GPIO since SCL/SDA are just bits 0/1) -->
          <div class="io-section i2c-interface" id="i2c-interface" style="display:none;">
            <div class="i2c-mode-bar">
              <span class="i2c-mode-badge">I2C · TCA9538</span>
              <span class="i2c-mode-info" id="i2c-mode-info"></span>
            </div>
            <div class="i2c-leds" id="i2c-leds"></div>
            <div class="i2c-log" id="i2c-log"></div>
          </div>
```

```html
<!-- ui/static/index.html — in the io-mux-row section, add the attach toggle
     next to the existing loopback strip -->
          <div class="lb-strip" id="i2c-attach-strip">
            <span class="lb-label">I2C</span>
            <button class="lb-btn" id="i2c-attach-btn">Attach TCA9538 @ 0x23 (SCL=bit0, SDA=bit1)</button>
          </div>
```

- [ ] **Step 2: Add minimal CSS for the LED row (reuse existing pin-grid styling as a base)**

```css
/* ui/static/index.html <style> block — alongside the existing .sd-channel-card / .pin-grid rules */
.i2c-leds { display: flex; gap: 6px; margin: 6px 0; }
.i2c-led { width: 18px; height: 18px; border-radius: 3px; background: #333; border: 1px solid #555; }
.i2c-led.on { background: #66bb6a; border-color: #66bb6a; }
.i2c-log { font-family: monospace; font-size: 11px; color: #bbb; white-space: pre-wrap; }
```

- [ ] **Step 3: Add `updateI2CPanel` and the attach-toggle handler in `app.js`**

```javascript
// ui/static/app.js — alongside updateSDPanel/updatePerifPanel
function updateI2CPanel(io) {
  const section = document.getElementById('i2c-interface');
  if (!section) return;

  const i2c = io && io.i2c;
  if (!i2c || !i2c.saw_start) {
    section.style.display = 'none';
    return;
  }
  section.style.display = '';

  const infoEl = document.getElementById('i2c-mode-info');
  if (infoEl) {
    infoEl.textContent = `addr=0x${i2c.address.toString(16).toUpperCase()} ` +
      `CONFIG=0x${i2c.config_reg.toString(16).toUpperCase().padStart(2, '0')}`;
  }

  const ledContainer = document.getElementById('i2c-leds');
  if (ledContainer) {
    ledContainer.innerHTML = '';
    const driven = i2c.output_reg & (~i2c.config_reg & 0xFF);
    for (let i = 0; i < 8; i++) {
      const led = document.createElement('div');
      led.className = 'i2c-led' + (((driven >> i) & 1) ? ' on' : '');
      led.title = `P${i}`;
      ledContainer.appendChild(led);
    }
  }

  const logEl = document.getElementById('i2c-log');
  if (logEl && i2c.last_transaction) {
    const t = i2c.last_transaction;
    const regStr = t.reg === null || t.reg === undefined ? '--' : `0x${t.reg.toString(16).toUpperCase().padStart(2, '0')}`;
    const dataStr = t.data === null || t.data === undefined ? '--' : `0x${t.data.toString(16).toUpperCase().padStart(2, '0')}`;
    logEl.textContent = `addr=0x${t.address.toString(16).toUpperCase()} reg=${regStr} data=${dataStr} ${t.ack ? 'ACK' : 'NACK'}`;
  }
}
```

```javascript
// ui/static/app.js — alongside onLoopbackToggle / the loopback-strip listener wiring
function onI2CAttachToggle(btn) {
  const enabled = !btn.classList.contains('active');
  btn.classList.toggle('active', enabled);
  sendAction({ action: 'i2c_attach', core: currentCore, enabled, address: 0x23 });
  if (!enabled) {
    const section = document.getElementById('i2c-interface');
    if (section) section.style.display = 'none';
  }
}

const _i2cAttachBtn = document.getElementById('i2c-attach-btn');
if (_i2cAttachBtn) {
  _i2cAttachBtn.addEventListener('click', () => onI2CAttachToggle(_i2cAttachBtn));
}
```

- [ ] **Step 4: Call `updateI2CPanel` from both state-handling call sites**

```javascript
// ui/static/app.js — at both existing call sites (line ~452-454 and ~3316-3317)
  updatePins(state.io);
  updateSDPanel(state.io);
  updatePerifPanel(state.io);
  updateI2CPanel(state.io);
```

- [ ] **Step 5: Manually verify in the browser**

```bash
python ui/server.py
```

1. Open `http://localhost:8080`, open `source/i2c_tca9538_running_led.asm`
   via the Editor panel, click **Load & Assemble**.
2. In the IO panel, click **Attach TCA9538 @ 0x23 (SCL=bit0, SDA=bit1)**.
3. Set the step interval to something visible (e.g. `0.01` s) and click
   **Run**.
4. Confirm the **I2C · TCA9538** section appears once the first START is
   seen, `CONFIG=0x00` after the init transaction, and the 8 LED
   indicators walk one lit LED left-to-right (P0→P7) and wrap, matching
   `output_reg`'s value each time a transaction completes.
5. Detach the toggle and confirm the section disappears and normal
   GPIO/loopback behavior on bits 0/1 is unaffected (e.g. load
   `mvi_gpio_loopback.asm` afterward and confirm it still behaves as
   documented in `getting_started.md`'s Example 7).

If any of this doesn't match, fix `app.js`/`server.py` before proceeding
— this is the actual end-to-end proof the feature works, not just that
the unit tests pass.

- [ ] **Step 6: Commit**

```bash
git add ui/static/index.html ui/static/app.js
git commit -m "feat(i2c): add TCA9538 IO panel section and attach toggle"
```

---

### Task 10: Docs — `getting_started.md` example

**Files:**
- Modify: `getting_started.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Add "Example 10" following the existing style, after Example 9 and before the "Signal Graph" section**

```markdown
## Example 10 — I2C Master + TCA9538 Running LED (`i2c_tca9538_running_led.asm`)

**What it does:** Bit-bangs I2C at 1 Mbit/s on SCL=R30/R31 bit0, SDA=bit1
(open-drain: writing 1 releases the line, writing 0 drives it low) against
a simulated TCA9538 8-bit IO expander at address 0x23. Configures all 8
ports as outputs, then walks a single bit 0x01→0x80 into the Output Port
register forever — a running LED across the expander's 8 physical pins.

**Steps:**

1. Open `source/i2c_tca9538_running_led.asm` and click **Load & Assemble**.
2. In the IO panel, click **Attach TCA9538 @ 0x23 (SCL=bit0, SDA=bit1)**.
3. Set the SIM step interval to `0.01` (seconds) and click **Run**.
4. The **I2C · TCA9538** section appears once the firmware issues its
   first START condition.

**What to observe:**
- `CONFIG=0x00` in the I2C section's mode bar once the init transaction
  (address, Configuration register 0x03, data 0x00) completes.
- The 8 LED indicators show one lit LED walking left to right, wrapping
  from P7 back to P0, in step with each completed Output Port write.
- The transaction log line under the LEDs shows the address, register
  pointer, data byte and ACK/NACK of the most recently completed
  transaction.
- Detaching the toggle hides the section and restores bits 0/1 to plain
  GPIO — safe to then load `spi_master_tx.asm` or `mvi_gpio_loopback.asm`,
  which use those same bits for SCLK/MOSI and loopback respectively.

---
```

- [ ] **Step 2: Verify the doc renders sensibly**

Read the diff and confirm the new section sits between Example 9 and the
`## Signal Graph` heading, with the same `---` separators used by every
other example in the file.

- [ ] **Step 3: Commit**

```bash
git add getting_started.md
git commit -m "docs: add Example 10 for the I2C TCA9538 running-LED demo"
```
