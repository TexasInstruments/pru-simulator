"""IBM/ANSI 8b/10b line code with running-disparity tracking.

The encoder splits each octet into a 5-bit sub-block (EDCBA, the low 5 bits)
encoded 5b/6b and a 3-bit sub-block (HGF, the high 3 bits) encoded 3b/4b, per
Widmer & Franaszek.  Running disparity (RD, one of -1/+1) is carried between
symbols and between sub-blocks; the transmitted 10-bit symbol is::

    a b c d e i f g h j        (a first on the wire == bit 9)

This module is the *golden reference*: it defines exactly the mapping the PRU
firmware must reproduce, and it builds the 256-entry DRAM0 lookup table the
firmware consumes with LBCO.  Encode and decode are exact inverses over all
256 octets in both disparity contexts (verified in tests), so a byte stream
that is encoded, serialised over the perif and decoded again round-trips with
zero bit errors.

The K28.5 comma (control symbol) is provided separately; the firmware emits it
as an inter-frame delimiter / idle fill and the decoder resynchronises on it.
"""

from __future__ import annotations

RD_MINUS = -1
RD_PLUS = +1

# 5b/6b: value -> (RD- codeword abcdei, kind)
#   'N'  neutral (disparity 0), same code for both RD, RD unchanged
#   'V'  variant (RD- code has disparity +2), RD+ uses the complement, RD flips
#   'D7' the two neutral forms of D.07, chosen by RD (RD unchanged)
_T5 = {
    0: (0b100111, 'V'), 1: (0b011101, 'V'), 2: (0b101101, 'V'), 3: (0b110001, 'N'),
    4: (0b110101, 'V'), 5: (0b101001, 'N'), 6: (0b011001, 'N'), 7: (0b111000, 'D7'),
    8: (0b111001, 'V'), 9: (0b100101, 'N'), 10: (0b010101, 'N'), 11: (0b110100, 'N'),
    12: (0b001101, 'N'), 13: (0b101100, 'N'), 14: (0b011100, 'N'), 15: (0b010111, 'V'),
    16: (0b011011, 'V'), 17: (0b100011, 'N'), 18: (0b010011, 'N'), 19: (0b110010, 'N'),
    20: (0b001011, 'N'), 21: (0b101010, 'N'), 22: (0b011010, 'N'), 23: (0b111010, 'V'),
    24: (0b110011, 'V'), 25: (0b100110, 'N'), 26: (0b010110, 'N'), 27: (0b110110, 'V'),
    28: (0b001110, 'N'), 29: (0b101110, 'V'), 30: (0b011110, 'V'), 31: (0b101011, 'V'),
}

# 3b/4b: value -> (RD- codeword fghj, kind).  Value 7 ('P7') has an alternate
# form (A7 = 0111) selected to break a run of five, per the standard rule.
_T3 = {
    0: (0b1011, 'V'), 1: (0b1001, 'N'), 2: (0b0101, 'N'), 3: (0b1100, 'N'),
    4: (0b1101, 'V'), 5: (0b1010, 'N'), 6: (0b0110, 'N'), 7: (0b1110, 'P7'),
}


def _enc5b6b(x: int, rd: int) -> tuple[int, int]:
    code, kind = _T5[x]
    if kind == 'N':
        return code, rd
    if kind == 'D7':
        return (0b111000 if rd == RD_MINUS else 0b000111), rd
    # variant
    if rd == RD_MINUS:
        return code, RD_PLUS
    return (~code) & 0x3F, RD_MINUS


def _enc3b4b(y: int, x: int, rd: int) -> tuple[int, int]:
    code, kind = _T3[y]
    if kind == 'N':
        return code, rd
    if kind == 'P7':
        # Alternate D.x.A7 breaks a run of five identical bits.
        use_alt = ((rd == RD_MINUS and x in (17, 18, 20)) or
                   (rd == RD_PLUS and x in (11, 13, 14)))
        base = 0b0111 if use_alt else 0b1110
        if rd == RD_MINUS:
            return base, RD_PLUS
        return (~base) & 0xF, RD_MINUS
    # variant
    if rd == RD_MINUS:
        return code, RD_PLUS
    return (~code) & 0xF, RD_MINUS


def encode_byte(byte: int, rd: int) -> tuple[int, int]:
    """Encode one octet under running disparity *rd* (-1/+1).

    Returns ``(code10, new_rd)`` where ``code10`` is the 10-bit symbol with the
    first-on-wire bit ('a') in bit 9.
    """
    x = byte & 0x1F           # EDCBA
    y = (byte >> 5) & 0x7     # HGF
    code6, rd = _enc5b6b(x, rd)
    code4, rd = _enc3b4b(y, x, rd)
    return (code6 << 4) | code4, rd


# --- K28.5 comma (control symbol), used for idle / inter-frame framing ------
K28_5_RD_MINUS = 0b0011111010    # 001111 1010
K28_5_RD_PLUS = 0b1100000101     # 110000 0101


def encode_comma(rd: int) -> tuple[int, int]:
    """Encode a K28.5 comma; disparity always flips (+/-2 symbol)."""
    if rd == RD_MINUS:
        return K28_5_RD_MINUS, RD_PLUS
    return K28_5_RD_PLUS, RD_MINUS


def encode_bytes(data: bytes, rd: int = RD_MINUS) -> tuple[list[int], int]:
    """Encode a byte string; returns ``(list_of_code10, ending_rd)``."""
    out = []
    for b in data:
        code, rd = encode_byte(b, rd)
        out.append(code)
    return out, rd


def build_decode_map() -> dict[int, int]:
    """Map every reachable 10-bit data symbol to its octet (both RD forms).

    Raises AssertionError if two octets collide on one symbol (would mean the
    tables are inconsistent), which is exercised as a test.
    """
    dm: dict[int, int] = {}
    for b in range(256):
        for rd in (RD_MINUS, RD_PLUS):
            code, _ = encode_byte(b, rd)
            if code in dm:
                assert dm[code] == b, f"symbol 0x{code:03x} maps to {dm[code]} and {b}"
            dm[code] = b
    return dm


COMMA_SYMBOLS = frozenset({K28_5_RD_MINUS, K28_5_RD_PLUS})


def build_dram0_lut() -> bytes:
    """Build the 256-entry (4 bytes each) DRAM0 encode table for the firmware.

    Entry layout (little-endian u32) for octet ``b``::

        bits [9:0]   code10 to send when current RD is negative
        bit  [10]    RD after the symbol on the negative branch (0=neg, 1=pos)
        bits [25:16] code10 to send when current RD is positive
        bit  [26]    RD after the symbol on the positive branch
    """
    buf = bytearray(256 * 4)
    for b in range(256):
        code_n, rd_n = encode_byte(b, RD_MINUS)
        code_p, rd_p = encode_byte(b, RD_PLUS)
        word = (code_n & 0x3FF)
        word |= (1 if rd_n == RD_PLUS else 0) << 10
        word |= (code_p & 0x3FF) << 16
        word |= (1 if rd_p == RD_PLUS else 0) << 26
        buf[b * 4:b * 4 + 4] = word.to_bytes(4, "little")
    return bytes(buf)


def symbol_disparity(code10: int) -> int:
    """Disparity (ones - zeros) of a 10-bit codeword: -2, 0 or +2."""
    return 2 * bin(code10 & 0x3FF).count("1") - 10


def build_dram1_decode_lut() -> bytes:
    """Build the 1024-entry (2 bytes each) DRAM1 decode table for PRU1 RX.

    A 10-bit codeword identifies its octet without knowing the running
    disparity, so one flat table suffices (``build_decode_map`` asserts the
    absence of collisions).  RD is needed only to *validate* the stream.

    Entry layout (little-endian u16) for codeword ``c``::

        [7:0]   decoded octet
        [8]     valid (1 = legal codeword)
        [9]     disparity-neutral (1 = disparity 0, RD unchanged)
        [10]    resulting RD when not neutral (0=negative, 1=positive)
        [11]    is-comma (K28.5)
    """
    dm = build_decode_map()
    buf = bytearray(1024 * 2)
    for code in range(1024):
        octet = dm.get(code)
        is_comma = code in COMMA_SYMBOLS
        word = 0
        if octet is not None or is_comma:
            word |= 1 << 8
            if octet is not None:
                word |= octet & 0xFF
            disp = symbol_disparity(code)
            if disp == 0:
                word |= 1 << 9
            else:
                word |= (1 if disp > 0 else 0) << 10
            if is_comma:
                word |= 1 << 11
        buf[code * 2:code * 2 + 2] = word.to_bytes(2, "little")
    return bytes(buf)


def symbol_ones(code10: int) -> int:
    return bin(code10 & 0x3FF).count("1")
