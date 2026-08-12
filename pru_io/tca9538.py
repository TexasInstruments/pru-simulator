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
        if prev_scl and scl and prev_sda and not bus_sda and self.state == "IDLE":
            self._on_start()
        elif prev_scl and scl and (not prev_sda) and bus_sda and self.state in ("IDLE", "DONE"):
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
