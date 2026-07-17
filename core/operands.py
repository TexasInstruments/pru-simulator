"""Operand parsing for the PRU assembler / simulator.

Supported forms (all case-insensitive; trailing commas are stripped):

  r5          → Register(index=5, offset=0, width=32)
  r5.b0       → Register(5, 0, 8)   .b1→(5,8,8)  .b2→(5,16,8)  .b3→(5,24,8)
  r5.w0       → Register(5, 0, 16)  .w1→(5,8,16) .w2→(5,16,16)
  r5.t7       → BitField(reg=5, bit=7)
  &r5         → Register(5, 0, 32)   (ampersand prefix for burst ops)
  0xFF/0XFF   → Immediate(255)
  123         → Immediate(123)
  anything    → returned as the original string (label reference)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Register:
    index: int    # 0-31
    offset: int   # bit offset into the register (0, 8, 16, 24)
    width: int    # 8, 16, or 32


@dataclass(frozen=True)
class Immediate:
    value: int


@dataclass(frozen=True)
class BitField:
    reg: int
    bit: int


@dataclass(frozen=True)
class Label:
    name: str
    resolved_addr: int = field(default=-1)


@dataclass(frozen=True)
class MVIOperand:
    """Operand for MVIx (register file indirect) instructions.

    sel maps to the PDSP field selector: 0=b0, 1=b1, 2=b2, 3=b3,
    4=w0, 5=w1, 6=w2, 7=full.  For indirect operands sel must be 0-3
    (pointer is a byte field of R1).
    """
    reg: int        # register number 0-31
    sel: int        # field selector 0-7
    indirect: bool  # True = pointer mode (*Reg)
    predec: bool    # pre-decrement (--Reg)
    postinc: bool   # post-increment (Reg++)


# ---------------------------------------------------------------------------
# Sub-field tables
# ---------------------------------------------------------------------------

_BYTE_FIELDS = {
    "b0": (0, 8),
    "b1": (8, 8),
    "b2": (16, 8),
    "b3": (24, 8),
}

_WORD_FIELDS = {
    "w0": (0, 16),
    "w1": (8, 16),
    "w2": (16, 16),
}

# Pre-compiled regex fragments
_REG_RE = re.compile(
    r"^&?r(\d+)(?:\.([btw]\w+))?$",
    re.IGNORECASE,
)

_HEX_RE = re.compile(r"^0[xX][0-9a-fA-F]+$")
_DEC_RE = re.compile(r"^\d+$")

# Matches tokens that look like constant expressions (contain digits AND operators)
_EXPR_RE = re.compile(r"^[\d\s+\-*/()%<>&|^~xXa-fA-F]+$")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_operand(token: str) -> "Register | Immediate | BitField | str":
    """Parse a single assembly operand token into its typed representation.

    Trailing commas are stripped before parsing.  The token comparison is
    case-insensitive for register/field names.
    """
    # Strip trailing comma
    token = token.rstrip(",")

    # Hex immediate
    if _HEX_RE.match(token):
        return Immediate(int(token, 16))

    # Decimal immediate
    if _DEC_RE.match(token):
        return Immediate(int(token, 10))

    # Register / BitField
    m = _REG_RE.match(token)
    if m:
        reg_idx = int(m.group(1))
        sub = m.group(2)

        if sub is None:
            # Plain register – full 32-bit
            return Register(reg_idx, 0, 32)

        sub_lower = sub.lower()

        if sub_lower in _BYTE_FIELDS:
            offset, width = _BYTE_FIELDS[sub_lower]
            return Register(reg_idx, offset, width)

        if sub_lower in _WORD_FIELDS:
            offset, width = _WORD_FIELDS[sub_lower]
            return Register(reg_idx, offset, width)

        # Bit field: t<N>
        bit_m = re.match(r"^t(\d+)$", sub_lower)
        if bit_m:
            return BitField(reg=reg_idx, bit=int(bit_m.group(1)))

    # Constant expression: e.g. "1 + 256", "(1 << 5)", "0xFF & 0x0F"
    if _EXPR_RE.match(token) and any(c in token for c in "+-*/<>&|^~"):
        try:
            val = int(eval(token, {"__builtins__": {}}, {}))  # noqa: S307
            return Immediate(val)
        except Exception:
            pass

    # Tokens starting with a digit can never be a valid label (labels must
    # start with a letter or underscore - see parser._LABEL_RE), so anything
    # that reaches here starting with a digit is a malformed numeric literal
    # (e.g. "0xabcv1234") rather than an identifier.
    if token and token[0].isdigit():
        raise ValueError(f"Invalid numeric literal: {token!r}")

    # Anything else is treated as a label / unresolved identifier
    return token
