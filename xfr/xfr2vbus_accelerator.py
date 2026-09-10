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

    def __init__(self, memory: MemoryBus, device_id: int) -> None:
        self._memory = memory
        self.DEVICE_ID = device_id
        self._addr_lo = bytearray(4)
        self._addr_hi = bytearray(2)

    def reset(self) -> None:
        self._addr_lo = bytearray(4)
        self._addr_hi = bytearray(2)

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
        # ALL modes: R20[0] WR_BUSY (Table 6-106). The simulator retires a
        # write synchronously inside xout() below, so by the time software
        # can poll this the command/data FIFOs it reflects are always
        # drained — see docs/xfr2vbus-trm-model-table.md "Simplifications".
        if start_reg != 20:
            raise ValueError(
                f"XFR2VBUS write device 0x{self.DEVICE_ID:02X} only exposes "
                f"WR_BUSY via XIN &R20 (TRM Table 6-106); got R{start_reg}.b{start_byte}"
            )
        return bytes(length)

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

    def __init__(self, memory: MemoryBus, device_id: int) -> None:
        self._memory = memory
        self.DEVICE_ID = device_id
        self._addr_lo = bytearray(4)
        self._addr_hi = bytearray(2)
        self._auto = False
        self._size_code = 0
        self._buffer: bytes | None = None

    def reset(self) -> None:
        self._addr_lo = bytearray(4)
        self._addr_hi = bytearray(2)
        self._auto = False
        self._size_code = 0
        self._buffer = None

    @property
    def _busy(self) -> bool:
        # R18[0] RD_BUSY = (RD_CMD_FIFO_LEVEL!=0) or (RD_DATA_FIFO_LEVEL!=0).
        # The read is fetched synchronously in _issue_read() below, so the
        # command-FIFO term is always 0 by the time this is observable; only
        # "unread data still latched" can make this true.
        return self._buffer is not None

    def xin(self, start_reg: int, length: int, start_byte: int = 0) -> bytes:
        if start_reg == self._CONFIG_REG:
            if start_byte != 0:
                raise ValueError(
                    f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: status is read from "
                    f"&R18.b0 (TRM Table 6-107); got &R18.b{start_byte}"
                )
            # RD_BUSY(0), RD_CMD_FL(1)=0 always, RD_DATA_FL(2), RD_MST_REQ(3)=0 always —
            # see docs/xfr2vbus-trm-model-table.md "Simplifications".
            busy = int(self._busy)
            status = busy | (busy << 2)
            return status.to_bytes(length, "little")
        if _DATA_FIRST_REG <= start_reg <= _DATA_LAST_REG and start_byte == 0:
            if self._buffer is None:
                raise ValueError(
                    f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: XIN RD_DATA with no read "
                    "pending (RD_DATA_FL=0); XOUT RD_ADDR first (TRM section 6.4.6.3.1.6)"
                )
            # "XIN of the read data will fully pop the data, independent of
            # XIN size" (TRM lines 7360, 7376) — the whole buffer is
            # discarded even if length < len(self._buffer).
            result = self._buffer[:length]
            self._buffer = None
            if self._auto:
                # TRM Table 6-107 R18[0] RD_AUTO: "every RD_DATA pop will
                # cause a new read command and read address to increment by
                # 0x20 ... for 32 Bytes / 0x40 ... for 64 Bytes."
                size = self._SIZE_BYTES[self._size_code]
                next_addr = _split_addr48(self._addr_lo, self._addr_hi) + size
                self._addr_lo[:] = (next_addr & 0xFFFFFFFF).to_bytes(4, "little")
                self._addr_hi[:] = ((next_addr >> 32) & 0xFFFF).to_bytes(2, "little")
                self._issue_read()
            return result
        raise ValueError(
            f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: XIN only supports RD_DATA "
            f"(&R2..&R17) or the R18 status word (TRM Table 6-107); got &R{start_reg}.b{start_byte}"
        )

    def xout(self, start_reg: int, data: bytes, start_byte: int = 0) -> None:
        if start_reg == self._CONFIG_REG:
            if start_byte != 0 or len(data) != 1:
                raise ValueError(
                    f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: RD_AUTO/RD_SIZE is a "
                    f"single byte at &R18.b0 (TRM Table 6-107); got &R18.b{start_byte}, "
                    f"{len(data)} byte(s)"
                )
            byte = data[0]
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

    def xchg(self, start_reg: int, data: bytes, start_byte: int = 0) -> bytes:
        raise ValueError(
            f"XFR2VBUS read device 0x{self.DEVICE_ID:02X} has no XCHG access type "
            "(TRM Table 6-107 lists XOUT/XIN only)"
        )

    def _issue_read(self) -> None:
        if self._busy:
            raise ValueError(
                f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: RD_ADDR written while "
                "RD_BUSY=1 (undrained RD_DATA); XIN RD_DATA before submitting a new "
                "read address (TRM section 6.4.6.3.1.6 \"Wait RD_BUSY = 0h\")"
            )
        size = self._SIZE_BYTES[self._size_code]
        addr = _split_addr48(self._addr_lo, self._addr_hi)
        if addr % size != 0:
            raise ValueError(
                f"XFR2VBUS read device 0x{self.DEVICE_ID:02X}: address 0x{addr:X} is not "
                f"{size}-byte aligned (TRM section 6.4.6.3.1.2)"
            )
        data, _stalls = self._memory.read(addr, size)
        self._buffer = data
