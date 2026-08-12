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
        self._prev_sda_master = True
        self._shift = 0
        self._bit_count = 0
        self._reg_ptr: int | None = None
        self._match = False

    def step(self, scl: bool, sda_master: bool) -> bool:
        """Advance the state machine by one R30-write's worth of bus
        activity. Returns the wired-AND bus SDA level."""
        slave_low = self._slave_drives_low()
        bus_sda = bool(sda_master) and not slave_low

        prev_scl = self._prev_scl
        prev_sda_master = self._prev_sda_master
        # START: SCL high, SDA falls (master release -> low due to slave or external pull)
        if prev_scl and scl and prev_sda_master and not bool(sda_master):
            self._on_start()
        # STOP: SCL high, SDA rises (master release allowing pull-up to bring SDA high)
        # Note: prev_scl is not required; STOP is detected when SCL is high and SDA rises
        elif scl and (not prev_sda_master) and bool(sda_master):
            self._on_stop()
        # SCL rising edge: sample data bit or handle ACK phase
        elif (not prev_scl) and scl:
            self._on_scl_rising(bus_sda)

        self._prev_scl = scl
        self._prev_sda = bus_sda
        self._prev_sda_master = bool(sda_master)
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
            # real "second byte" protocol violation. However, we must allow
            # STOP sequences to complete, so we don't immediately abort here.
            # Return to IDLE and let the transaction record stand.
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

    # ------------------------------------------------------------------
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
            "prev_sda_master": self._prev_sda_master,
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
        self._prev_sda_master = snap["prev_sda_master"]
        self._shift = snap["shift"]
        self._bit_count = snap["bit_count"]
        self._reg_ptr = snap["reg_ptr"]
        self._match = snap["match"]
