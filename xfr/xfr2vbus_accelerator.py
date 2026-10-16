"""PRU_ICSSG XFR2VBUS hardware accelerator (BroadSide access to CBASS0/VBUSM).

Hardware reference: AM64x/AM243x TRM SPRUIM2J section 6.4.6.3.1, Tables
6-106/6-107 (archived extract research/refs/icssg-chapter-6.4-functional.txt,
lines 7276-7549). See docs/xfr2vbus-trm-model-table.md for the full
TRM-vs-model field table, including two divergences from the NCP-127 issue
paraphrase this was built to replace, and a set of labelled simulator-only
simplifications (no VBUSM bus-latency model exists in this repo).

RD_ID0/RD_ID1 (0x60/0x61) and WR_ID0/WR_ID1 (0x62/0x63, Table 6-60) are four
independent hardware channels ("2 x XFR2VBUS RX threads" + "2 x XFR2VBUS TX
threads", line 7321-7322) sharing nothing but the backing VBUS memory; each
gets its own accelerator instance and private state, the same pattern
BSwapAccelerator uses for its three device IDs.

SILICON-MEASURED TIMING AND STATUS (LP-AM243, AM2434 ICSSG0 PRU0, MSRAM)
-------------------------------------------------------------------------
Programs run on an LP-AM243 against MSRAM fixed four behaviours the TRM leaves
open.  Each is pinned by ``TestSiliconBehaviour`` in
``tests/test_xfr2vbus_accelerator.py``.

1. **Read latency.**  A read is in flight for :func:`read_latency_cycles`
   core cycles after the XOUT that submits it, one cycle more when an
   RD_DATA pop re-issues it in RD_AUTO mode.  While in flight, status reads ``0x0B``
   (RD_BUSY, RD_CMD_FL, RD_MST_REQ); once the data has landed it reads
   ``0x05`` (RD_BUSY, RD_DATA_FL).  The latency is MSRAM's on this device;
   other targets and devices differ (AM261x MSRAM is several times slower),
   so it is a constructor argument.
2. **An early XIN of RD_DATA is harmless.**  An XIN of RD_DATA while no data
   has landed returns the channel's data register as it stands -- the last
   data that landed -- and consumes nothing.  open-pru's FFT relies on this:
   it XINs RD_DATA straight after enabling RD_AUTO, and silicon then still
   delivers the first block to the next XIN.
3. **Combined setup.**  ``XOUT 0x60, &r18, 8`` writes RD_AUTO/RD_SIZE (R18.b0)
   and RD_ADDR (R19) in one instruction and is accepted, with the
   configuration applied before the read is submitted.  R18.b1..b3 are
   ignored.
4. **WR_BUSY.**  R20[0] reads 1 for :data:`WR_BUSY_CYCLES` core cycles after
   a data-bearing write XOUT.  The write itself still lands in backing
   memory immediately (simplification: the VBUSM write is not delayed).
"""

from __future__ import annotations

from mem.memory_bus import MemoryBus
from xfr.accelerator import Accelerator

RD_ID0 = 0x60
RD_ID1 = 0x61
WR_ID0 = 0x62
WR_ID1 = 0x63

_DATA_FIRST_REG = 2
_DATA_LAST_REG = 17  # inclusive; R2..R9 is the 32B window, R2..R17 is 64B

# R18 read status bits (TRM Table 6-107).
RD_BUSY = 0x01
RD_CMD_FL = 0x02
RD_DATA_FL = 0x04
RD_MST_REQ = 0x08
#: SILICON (module docstring point 1): status while a read is in flight.
RD_STATUS_IN_FLIGHT = RD_BUSY | RD_CMD_FL | RD_MST_REQ
#: SILICON: status once the data has landed and not yet been popped.
RD_STATUS_DATA_READY = RD_BUSY | RD_DATA_FL

#: SILICON (module docstring point 1): core cycles from the submitting XOUT
#: to RD_DATA_FL, per read size, for AM243x MSRAM.  Fitted on LP-AM243.
AM243X_MSRAM_READ_LATENCY = {4: 18, 32: 20, 64: 24}

#: SILICON (module docstring point 4): core cycles WR_BUSY stays set after a
#: write XOUT.  Fitted on LP-AM243 against MSRAM.
WR_BUSY_CYCLES = 2

#: SILICON (module docstring point 1): a read re-issued by an RD_DATA pop in
#: RD_AUTO mode lands one cycle later than one submitted by XOUT -- the new
#: command leaves the cycle after the pop.
AUTO_REISSUE_EXTRA_CYCLES = 1


def read_latency_cycles(size: int) -> int:
    """AM243x MSRAM read latency for a *size*-byte XFR2VBUS read."""
    return AM243X_MSRAM_READ_LATENCY[size]


def _split_addr48(lo: bytearray, hi: bytearray) -> int:
    return int.from_bytes(bytes(lo) + bytes(hi), "little")


class XFR2VBUSWriteAccelerator(Accelerator):
    """One WR_ID0/WR_ID1 BroadSide write channel (Table 6-106)."""

    DEVICE_ID = WR_ID0

    # R10/R11 are the 32B-mode WR_ADDR pair *and* fall inside the 64B-mode
    # WR_DATA window (R2..R17) — Table 6-106 disambiguates the two only via
    # the undocumented "R17.b3 enable" bit (see docs/xfr2vbus-trm-model-table.md
    # "Corrections"). This model instead looks at the highest register any
    # single XOUT actually reaches: touching R12..R17 is only ever valid in
    # 64B mode, so R10/R11 are WR_DATA in that call; otherwise (max reg <=11)
    # they are the 32B-mode WR_ADDR pair. A call is fully one mode or the
    # other — TRM never documents mixing the two windows in one XOUT.
    _MODE64_THRESHOLD_REG = 12
    _ADDR32_LOW_REG, _ADDR32_HIGH_REG = 10, 11
    _ADDR64_LOW_REG, _ADDR64_HIGH_REG = 18, 19

    # Size validity is flow-dependent, not one global set — see
    # docs/xfr2vbus-trm-model-table.md "Write commands" for the full
    # reconciliation of the two TRM passages this splits:
    #
    # - Split flow (address XOUT, then a separate data-only XOUT): TRM
    #   section 6.4.6.3.1.6 "XFR2VBUS Programming Model" (line 7543) lists
    #   the write flow "XOUT (addr) then XOUT 32 Byte/8 Byte/4 Byte/1 Byte
    #   data" explicitly, naming 8 bytes as one of four legal data sizes.
    # - Combined flow (one XOUT carries address and data together): Table
    #   6-106's WR_ADDR notes for both windows say plainly that a combined
    #   XOUT "needs to be the full 32 bytes" (R11-R10 note, line 7438) or
    #   "the full 64 bytes" (R19-R18 note, line 7413) — never a partial
    #   size. 8 bytes is not a legal combined size under either note.
    #
    # In this model the two cases are also geometrically distinct: a
    # payload-bearing XOUT must start at &R2.b0 (checked below), and the
    # address registers sit immediately after the full 32-/64-byte data
    # window, so an XOUT can only ever reach an address register once it
    # has already supplied the *entire* preceding data window — an 8-byte
    # combined write is not constructible through this call shape at all.
    # The explicit combined/split size sets below are kept anyway as
    # defense-in-depth against that geometry changing under refactor, and
    # so the two TRM passages are each enforced by name rather than folded
    # into one tuple that would obscure which rule is being applied.
    _VALID_SPLIT_SIZES = (1, 4, 8, 32, 64)
    _VALID_COMBINED_SIZES = (32, 64)

    def __init__(self, memory: MemoryBus, device_id: int,
                 busy_cycles: int = WR_BUSY_CYCLES) -> None:
        self._memory = memory
        self.DEVICE_ID = device_id
        self.busy_cycles = busy_cycles
        self._addr_lo = bytearray(4)
        self._addr_hi = bytearray(2)
        self._busy_left = 0

    def reset(self) -> None:
        self._addr_lo = bytearray(4)
        self._addr_hi = bytearray(2)
        self._busy_left = 0

    def advance(self, cycles: int) -> None:
        """Let *cycles* core cycles pass (called from ``PRUCore.step``)."""
        self._busy_left = max(0, self._busy_left - cycles)

    def _validate_size(self, size: int, *, combined: bool) -> None:
        """Raise unless `size` is legal for the given XOUT flow shape.

        Split (`combined=False`) is exercised through `xout()` directly by
        `tests/test_xfr2vbus_accelerator.py`'s split-flow tests. Combined
        (`combined=True`) with an 8-byte size cannot be produced through
        `xout()` at all — see the geometry note above
        `_VALID_SPLIT_SIZES`/`_VALID_COMBINED_SIZES` — so this method is
        also called directly (on an accelerator instance) by the test suite
        to exercise the combined-size rule rather than only asserting it
        can never be reached.
        """
        valid = self._VALID_COMBINED_SIZES if combined else self._VALID_SPLIT_SIZES
        if size in valid:
            return
        if combined:
            raise ValueError(
                f"XFR2VBUS write device 0x{self.DEVICE_ID:02X}: a combined "
                f"address+data XOUT must carry the full WR_DATA window, "
                f"{size} is not a valid combined size (TRM Table 6-106 "
                "WR_ADDR notes: 'data needs to be the full 32/64 bytes')"
            )
        raise ValueError(
            f"XFR2VBUS write device 0x{self.DEVICE_ID:02X}: {size} is not a valid "
            "split-flow WR_DATA size (TRM section 6.4.6.3.1.6 Programming "
            "Model: 1, 4, 8, 32 or 64 bytes)"
        )

    def xin(self, start_reg: int, length: int, start_byte: int = 0) -> bytes:
        # ALL modes: R20[0] WR_BUSY (Table 6-106).  SILICON (module docstring
        # point 4): set for busy_cycles after a write.
        if start_reg != 20:
            raise ValueError(
                f"XFR2VBUS write device 0x{self.DEVICE_ID:02X} only exposes "
                f"WR_BUSY via XIN &R20 (TRM Table 6-106); got R{start_reg}.b{start_byte}"
            )
        # WR_BUSY is R20[0]: byte 0 of the status word.  An XIN starting at
        # a later byte (&R20.b1) sees only the zero upper bits.
        status = int(self._busy_left > 0).to_bytes(4, "little")
        return status[start_byte:start_byte + length].ljust(length, b"\0")

    def xout(self, start_reg: int, data: bytes, start_byte: int = 0) -> None:
        if not data:
            return
        end_abs_byte = start_reg * 4 + start_byte + len(data) - 1
        max_reg_touched = end_abs_byte // 4
        addr_low_reg, addr_high_reg = (
            (self._ADDR64_LOW_REG, self._ADDR64_HIGH_REG)
            if max_reg_touched >= self._MODE64_THRESHOLD_REG
            else (self._ADDR32_LOW_REG, self._ADDR32_HIGH_REG)
        )

        payload = bytearray()
        touched_addr = False
        for i, value in enumerate(data):
            abs_byte = start_reg * 4 + start_byte + i
            reg_idx, byte_in_reg = divmod(abs_byte, 4)
            if reg_idx == addr_low_reg:
                self._addr_lo[byte_in_reg] = value
                touched_addr = True
            elif reg_idx == addr_high_reg:
                touched_addr = True
                if byte_in_reg < 2:
                    self._addr_hi[byte_in_reg] = value
                # bytes 2-3 of the upper address register are reserved (TRM
                # documents only the low 16 bits of R11/R19 as address bits).
            elif _DATA_FIRST_REG <= reg_idx <= _DATA_LAST_REG:
                payload.append(value)
            else:
                raise ValueError(
                    f"XFR2VBUS write device 0x{self.DEVICE_ID:02X}: XOUT touched R{reg_idx}, "
                    "which is neither WR_DATA (R2..R17) nor WR_ADDR (R10:R11, R18:R19) "
                    "(TRM Table 6-106)"
                )

        if not payload:
            return  # address-only XOUT: latch the address, no VBUSM write issued

        if start_reg != _DATA_FIRST_REG or start_byte != 0:
            raise ValueError(
                f"XFR2VBUS write device 0x{self.DEVICE_ID:02X}: WR_DATA must start at "
                f"&R2.b0 (TRM Table 6-106); got &R{start_reg}.b{start_byte}"
            )

        size = len(payload)
        self._validate_size(size, combined=touched_addr)
        addr = _split_addr48(self._addr_lo, self._addr_hi)
        if size != 1 and addr % size != 0:
            raise ValueError(
                f"XFR2VBUS write device 0x{self.DEVICE_ID:02X}: address 0x{addr:X} is not "
                f"{size}-byte aligned (TRM section 6.4.6.3.1.4)"
            )
        self._memory.write(addr, bytes(payload))
        # INFERENCE: a write issued while still busy queues behind the first.
        self._busy_left += self.busy_cycles

    def xchg(self, start_reg: int, data: bytes, start_byte: int = 0) -> bytes:
        raise ValueError(
            f"XFR2VBUS write device 0x{self.DEVICE_ID:02X} has no XCHG access type "
            "(TRM Table 6-106 lists XOUT/XIN only)"
        )


class XFR2VBUSReadAccelerator(Accelerator):
    """One RD_ID0/RD_ID1 BroadSide read channel (Table 6-107)."""

    DEVICE_ID = RD_ID0

    _CONFIG_REG = 18
    _ADDR_LOW_REG = 19
    _ADDR_HIGH_REG = 20
    # R18[2:1] RD_SIZE. 1 (0b01) is Reserved (TRM Table 6-107, lines 7483-7488) —
    # the NCP-127 issue text paraphrased this as "1=32B, 2=64B", which is wrong;
    # see docs/xfr2vbus-trm-model-table.md "Corrections".
    _SIZE_BYTES = {0: 4, 2: 32, 3: 64}

    #: The channel's RD_DATA register is the 64-byte R2..R17 window.
    _DATA_REG_BYTES = 64

    def __init__(self, memory: MemoryBus, device_id: int,
                 latency=read_latency_cycles) -> None:
        self._memory = memory
        self.DEVICE_ID = device_id
        self.latency = latency
        self.reset()

    def reset(self) -> None:
        self._addr_lo = bytearray(4)
        self._addr_hi = bytearray(2)
        self._auto = False
        self._size_code = 0
        # Last data that landed.  SILICON (module docstring point 2): an XIN
        # of RD_DATA before new data lands returns this, unconsumed.
        self._data_reg = bytes(self._DATA_REG_BYTES)
        self._data_ready = False         # RD_DATA_FL
        self._pending: bytes | None = None  # read in flight
        self._cycles_left = 0

    @property
    def _busy(self) -> bool:
        # R18[0] RD_BUSY = (RD_CMD_FIFO_LEVEL!=0) or (RD_DATA_FIFO_LEVEL!=0):
        # a read in flight, or landed data not yet popped.
        return self._pending is not None or self._data_ready

    def advance(self, cycles: int) -> None:
        """Let *cycles* core cycles pass (called from ``PRUCore.step``)."""
        if self._pending is None:
            return
        self._cycles_left -= cycles
        if self._cycles_left <= 0:
            data = self._pending
            self._data_reg = data + self._data_reg[len(data):]
            self._pending = None
            self._data_ready = True

    def _status(self) -> int:
        if self._pending is not None:
            return RD_STATUS_IN_FLIGHT
        if self._data_ready:
            return RD_STATUS_DATA_READY
        return 0

    def xin(self, start_reg: int, length: int, start_byte: int = 0) -> bytes:
        if start_reg == self._CONFIG_REG:
            if start_byte != 0:
                raise ValueError(
                    f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: status is read from "
                    f"&R18.b0 (TRM Table 6-107); got &R18.b{start_byte}"
                )
            # RD_BUSY(0), RD_CMD_FL(1), RD_DATA_FL(2), RD_MST_REQ(3): SILICON
            # (module docstring point 1).
            return self._status().to_bytes(4, "little")[:length].ljust(length, b"\0")
        if _DATA_FIRST_REG <= start_reg <= _DATA_LAST_REG and start_byte == 0:
            result = self._data_reg[:length].ljust(length, b"\0")
            if not self._data_ready:
                # SILICON (module docstring point 2): nothing has landed, so
                # the XIN returns the data register as it stands and pops
                # nothing -- including straight after RD_AUTO is enabled.
                return result
            # "XIN of the read data will fully pop the data, independent of
            # XIN size" (TRM lines 7360, 7376) — RD_DATA_FL clears even if
            # length is shorter than the read.
            self._data_ready = False
            if self._auto:
                # TRM Table 6-107 R18[0] RD_AUTO: "every RD_DATA pop will
                # cause a new read command and read address to increment by
                # 0x20 ... for 32 Bytes / 0x40 ... for 64 Bytes."
                size = self._SIZE_BYTES[self._size_code]
                next_addr = _split_addr48(self._addr_lo, self._addr_hi) + size
                self._addr_lo[:] = (next_addr & 0xFFFFFFFF).to_bytes(4, "little")
                self._addr_hi[:] = ((next_addr >> 32) & 0xFFFF).to_bytes(2, "little")
                self._issue_read()
                self._cycles_left += AUTO_REISSUE_EXTRA_CYCLES
            return result
        raise ValueError(
            f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: XIN only supports RD_DATA "
            f"(&R2..&R17) or the R18 status word (TRM Table 6-107); got &R{start_reg}.b{start_byte}"
        )

    def xout(self, start_reg: int, data: bytes, start_byte: int = 0) -> None:
        if start_reg == self._CONFIG_REG:
            if start_byte != 0:
                raise ValueError(
                    f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: RD_AUTO/RD_SIZE is "
                    f"&R18.b0 (TRM Table 6-107); got &R18.b{start_byte}"
                )
            if len(data) > 12:
                raise ValueError(
                    f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: an XOUT from &R18 "
                    "reaches at most R20 (RD_ADDR upper); got "
                    f"{len(data)} bytes (TRM Table 6-107)"
                )
            self._configure(data[0])
            if len(data) > 4:
                # SILICON (module docstring point 3): config and RD_ADDR in
                # one XOUT, as open-pru's FFT does.  R18.b1..b3 are ignored.
                self.xout(self._ADDR_LOW_REG, data[4:], 0)
            return

        if start_reg in (self._ADDR_LOW_REG, self._ADDR_HIGH_REG):
            touched_low = False
            for i, value in enumerate(data):
                abs_byte = start_reg * 4 + start_byte + i
                reg_idx, byte_in_reg = divmod(abs_byte, 4)
                if reg_idx == self._ADDR_LOW_REG:
                    self._addr_lo[byte_in_reg] = value
                    touched_low = True
                elif reg_idx == self._ADDR_HIGH_REG:
                    if byte_in_reg < 2:
                        self._addr_hi[byte_in_reg] = value
                else:
                    raise ValueError(
                        f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: XOUT touched R{reg_idx}, "
                        "which is neither RD_ADDR register (R19, R20) (TRM Table 6-107)"
                    )
            # Writing R19 (the lower-32 register) submits the command; R20
            # touched alone only updates the upper-16 latch (TRM Table 6-107
            # "R20:R19 ... The address can be the full 48-bits or just the
            # lower 32-bits").
            if touched_low:
                self._issue_read()
            return

        raise ValueError(
            f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: XOUT only supports RD_AUTO/RD_SIZE "
            f"(&R18) or RD_ADDR (&R19, &R20) (TRM Table 6-107); got &R{start_reg}.b{start_byte}"
        )

    def _configure(self, byte: int) -> None:
        """R18.b0: RD_AUTO (bit 0) and RD_SIZE (bits 2:1), TRM Table 6-107."""
        auto = bool(byte & 0x1)
        size_code = (byte >> 1) & 0x3
        if size_code not in self._SIZE_BYTES:
            raise ValueError(
                f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: RD_SIZE={size_code} "
                "(0b01) is Reserved (TRM Table 6-107)"
            )
        if auto and size_code == 0:
            raise ValueError(
                f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: RD_AUTO does not support "
                "4 Byte mode (TRM section 6.4.6.3.1.3)"
            )
        self._auto = auto
        self._size_code = size_code

    def xchg(self, start_reg: int, data: bytes, start_byte: int = 0) -> bytes:
        raise ValueError(
            f"XFR2VBUS read device 0x{self.DEVICE_ID:02X} has no XCHG access type "
            "(TRM Table 6-107 lists XOUT/XIN only)"
        )

    def _issue_read(self) -> None:
        if self._busy:
            raise ValueError(
                f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: RD_ADDR written while "
                "RD_BUSY=1 (read in flight or RD_DATA not popped); wait for "
                "RD_BUSY = 0 before submitting a new read address (TRM section "
                "6.4.6.3.1.6 \"Wait RD_BUSY = 0h\")"
            )
        size = self._SIZE_BYTES[self._size_code]
        addr = _split_addr48(self._addr_lo, self._addr_hi)
        if addr % size != 0:
            raise ValueError(
                f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: address 0x{addr:X} is not "
                f"{size}-byte aligned (TRM section 6.4.6.3.1.2)"
            )
        data, _stalls = self._memory.read(addr, size)
        # SILICON (module docstring point 1): the data lands after the read
        # latency; advance() delivers it.
        self._pending = bytes(data)
        self._cycles_left = self.latency(size)
