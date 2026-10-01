"""Two-pass assembly parser for the PRU simulator.

Pass 1: Scan preprocessed lines for label definitions and build the label table.
Pass 2: Parse each instruction line into an Instruction object with fully resolved
        operands.

Label syntax accepted:
    loop:           (label-only line – no address consumed)
    start: ldi r0, 1  (label + instruction – label maps to current address)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.preprocessor import Preprocessor
from core.operands import parse_operand, Immediate, Label, Register, MVIOperand

# Expected operand counts per opcode.  Opcodes not listed are not validated.
# A tuple means any of those counts is acceptable (e.g. SET/CLR have 1- and 3-operand forms).
_EXPECTED_OPS: dict[str, int | tuple[int, ...]] = {
    # Memory
    "LBBO": 4, "SBBO": 4, "LBCO": 4, "SBCO": 4,
    # XFR
    "XIN": 3, "XOUT": 3, "XCHG": 3,
    # ALU – 3 operands
    "ADD": 3, "ADC": 3, "SUB": 3, "SUC": 3, "RSB": 3, "RSC": 3,
    "AND": 3, "OR":  3, "XOR": 3,
    "LSL": 3, "LSR": 3,
    "MIN": 3, "MAX": 3, "LMBD": 3,
    # SET/CLR: 1-operand bit-field form (SET r5.t3) or 3-operand form (SET Rd, Rn, bn)
    "SET": (1, 3), "CLR": (1, 3),
    # ALU – 2 operands
    "LDI": 2, "MOV": 2, "NOT": 2,
    # Branches
    "JMP": 1, "QBA": 1,
    "JAL": 2,
    "QBEQ": 3, "QBNE": 3, "QBGT": 3, "QBGE": 3,
    "QBLT": 3, "QBLE": 3, "QBBS": 3, "QBBC": 3,
    "LOOP": 2,
    # Misc
    "ZERO": 2, "FILL": 2,
    "WBS":  2, "WBC":  2, "SLP": 1,
    "HALT": 0, "NOP":  0,
    # Register file indirect
    "MVIB": 2, "MVIW": 2, "MVID": 2,
}

# Operand range constraints per opcode.
# Each entry: list of (operand_index, max_value, description).
# Only applied to Immediate operands; Registers/Labels are unchecked.
# Opcodes whose constrained operand is a bit index or shift amount. These are
# the only slots where a symbol cannot possibly be meant - everywhere else a
# label or .set constant is ordinary assembler.
_BIT_POSITION_OPCODES = frozenset({"LSL", "LSR", "SET", "CLR", "QBBS", "QBBC"})

_OPERAND_RANGES: dict[str, list[tuple[int, int, str]]] = {
    # LDI: 16-bit immediate
    "LDI": [(1, 65535, "16-bit immediate (0-65535)")],
    # ALU 3-op: 3rd operand is OP(255)
    "ADD": [(2, 255, "8-bit immediate (0-255)")],
    "ADC": [(2, 255, "8-bit immediate (0-255)")],
    "SUB": [(2, 255, "8-bit immediate (0-255)")],
    "SUC": [(2, 255, "8-bit immediate (0-255)")],
    "RSB": [(2, 255, "8-bit immediate (0-255)")],
    "RSC": [(2, 255, "8-bit immediate (0-255)")],
    "AND": [(2, 255, "8-bit immediate (0-255)")],
    "OR":  [(2, 255, "8-bit immediate (0-255)")],
    "XOR": [(2, 255, "8-bit immediate (0-255)")],
    "LMBD": [(2, 255, "8-bit immediate (0-255)")],
    "MIN": [(2, 255, "8-bit immediate (0-255)")],
    "MAX": [(2, 255, "8-bit immediate (0-255)")],
    # Shifts/bits: OP(31)
    "LSL": [(2, 31, "shift amount (0-31)")],
    "LSR": [(2, 31, "shift amount (0-31)")],
    "SET": [(2, 31, "bit position (0-31)")],
    "CLR": [(2, 31, "bit position (0-31)")],
    # Conditional branches: 3rd operand OP(255), except QBBS/QBBC use OP(31)
    "QBEQ": [(2, 255, "8-bit immediate (0-255)")],
    "QBNE": [(2, 255, "8-bit immediate (0-255)")],
    "QBGT": [(2, 255, "8-bit immediate (0-255)")],
    "QBGE": [(2, 255, "8-bit immediate (0-255)")],
    "QBLT": [(2, 255, "8-bit immediate (0-255)")],
    "QBLE": [(2, 255, "8-bit immediate (0-255)")],
    "QBBS": [(2, 31, "bit position (0-31)")],
    "QBBC": [(2, 31, "bit position (0-31)")],
    # Loop
    "LOOP": [(1, 256, "loop count (0-256)")],
    # Memory ops: 3rd is OP(255), 4th is IM(124)
    "LBBO": [(2, 255, "8-bit offset (0-255)"), (3, 124, "transfer length (0-124)")],
    "SBBO": [(2, 255, "8-bit offset (0-255)"), (3, 124, "transfer length (0-124)")],
    "LBCO": [(2, 255, "8-bit offset (0-255)"), (3, 124, "transfer length (0-124)")],
    "SBCO": [(2, 255, "8-bit offset (0-255)"), (3, 124, "transfer length (0-124)")],
    # XFR: 1st is IM(253), 3rd is IM(124)
    "XIN":  [(0, 253, "device ID (0-253)"), (2, 124, "transfer length (0-124)")],
    "XOUT": [(0, 253, "device ID (0-253)"), (2, 124, "transfer length (0-124)")],
    "XCHG": [(0, 253, "device ID (0-253)"), (2, 124, "transfer length (0-124)")],
    # ZERO/FILL: 2nd is IM(124)
    "ZERO": [(1, 124, "byte count (0-124)")],
    "FILL": [(1, 124, "byte count (0-124)")],
    # SLP
    "SLP": [(0, 1, "sleep mode (0 or 1)")],
    # JMP/JAL address
    "JMP": [(0, 65535, "16-bit address (0-65535)")],
    "JAL": [(1, 65535, "16-bit address (0-65535)")],
}


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

@dataclass
class Instruction:
    address: int        # word address (PC value, 0-based)
    opcode: str         # upper-cased mnemonic: "ADD", "QBGT", "LBBO", …
    operands: list      # typed operand objects (Register, Immediate, BitField, Label)
    source_line: int    # original 1-based source line number (before preprocessing)
    source_text: str    # original raw source text of the line


# ---------------------------------------------------------------------------
# MVI operand parser
# ---------------------------------------------------------------------------

# Matches: optional *, optional --, register (with optional .field), optional ++
_MVI_TOKEN_RE = re.compile(
    r'^(\*)?(--)?(r\d+(?:\.[bw]\w+)?)(\+\+)?$',
    re.IGNORECASE,
)

# Map (bit_offset, width) → MVI field selector (0-7)
_FIELD_TO_SEL = {
    (0, 8): 0, (8, 8): 1, (16, 8): 2, (24, 8): 3,
    (0, 16): 4, (8, 16): 5, (16, 16): 6,
    (0, 32): 7,
}


def _parse_mvi_operand(token: str) -> MVIOperand:
    """Parse one MVIx operand token into an MVIOperand.

    Accepted forms (case-insensitive):
      r2           direct, full register
      r2.b1        direct, byte field
      r2.w0        direct, word field
      *r1.b0       indirect, no update
      *r1.b0++     indirect, post-increment
      *--r1.b0     indirect, pre-decrement
    """
    m = _MVI_TOKEN_RE.match(token.strip())
    if not m:
        raise SyntaxError(f"Invalid MVI operand: {token!r}")

    indirect = m.group(1) is not None
    predec   = m.group(2) is not None
    reg_str  = m.group(3)
    postinc  = m.group(4) is not None

    reg_op = parse_operand(reg_str)
    if not isinstance(reg_op, Register):
        raise SyntaxError(f"Expected register in MVI operand: {reg_str!r}")

    sel = _FIELD_TO_SEL.get((reg_op.offset, reg_op.width), 7)
    return MVIOperand(reg=reg_op.index, sel=sel,
                      indirect=indirect, predec=predec, postinc=postinc)


# ---------------------------------------------------------------------------
# Label-line regex
# ---------------------------------------------------------------------------
# Matches an optional leading label (word chars + colon) followed by optional
# remainder of the line.
_LABEL_RE = re.compile(r"^([A-Za-z_]\w*)\s*:(.*)", re.DOTALL)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class Parser:
    def __init__(self) -> None:
        self.preprocessor = Preprocessor()
        self.labels: dict[str, int] = {}
        self.instructions: list[Instruction] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_file(self, filepath: str) -> list[Instruction]:
        """Parse an assembly source file and return ordered Instruction list."""
        with open(filepath, "r", encoding="utf-8") as fh:
            text = fh.read()
        return self.parse_text(text)

    def parse_text(self, text: str, include_paths: list[str] | None = None) -> list[Instruction]:
        """Parse raw assembly text and return ordered Instruction list.

        Runs the preprocessor first, then does two passes over the output.
        """
        # Reset state for re-use
        self.labels = {}
        self.instructions = []

        # Preprocessor returns clean lines (comments/blanks stripped, macros
        # and defines expanded).  We also preserve the original source text so
        # we can store it on each Instruction.
        preprocessed = self.preprocessor.process_text(text, include_paths)

        # Expand pseudo-ops (LDI32 → two LDI instructions)
        preprocessed = self._expand_pseudo_ops(preprocessed)

        # Build a mapping from preprocessed line index → original source line
        # number.  The preprocessor doesn't expose this directly, so we
        # construct a best-effort mapping by scanning the raw source.
        source_lines = text.splitlines()

        # Pass 1 – collect labels
        self._pass1(preprocessed)

        # Pass 2 – build instruction list
        self._pass2(preprocessed, source_lines)

        return self.instructions

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _expand_pseudo_ops(self, lines: list[str]) -> list[str]:
        """Expand pseudo-ops like LDI32 into real instructions."""
        result = []
        ldi32_re = re.compile(
            r'^((\w+):\s*)?ldi32\s+(r\d+)\s*,\s*(0x[0-9a-fA-F]+|\d+)\s*$',
            re.IGNORECASE
        )
        for line in lines:
            m = ldi32_re.match(line.strip())
            if m:
                label = m.group(2)
                reg = m.group(3).lower()
                val_str = m.group(4)
                val = int(val_str, 16) if val_str.lower().startswith('0x') else int(val_str)
                lo = val & 0xFFFF
                hi = (val >> 16) & 0xFFFF
                # Expand to two LDI: lower 16 into .w0, upper 16 into .w2
                line1 = f"ldi {reg}.w0, {lo}"
                line2 = f"ldi {reg}.w2, {hi}"
                if label:
                    line1 = f"{label}: {line1}"
                result.append(line1)
                result.append(line2)
            else:
                result.append(line)
        return result

    def _pass1(self, lines: list[str]) -> None:
        """Assign addresses to labels without emitting instructions."""
        address = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue

            m = _LABEL_RE.match(line)
            if m:
                label_name = m.group(1)
                rest = m.group(2).strip()
                # The label maps to the *current* address regardless of whether
                # there is an instruction on the same line.
                self.labels[label_name] = address
                # If there's an instruction after the colon, it consumes one address.
                if rest:
                    address += 1
            else:
                # Plain instruction line.
                address += 1

    def _pass2(self, lines: list[str], source_lines: list[str]) -> None:
        """Build the Instruction list with resolved operands."""
        address = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue

            m = _LABEL_RE.match(line)
            if m:
                rest = m.group(2).strip()
                if not rest:
                    # Label-only line – no instruction, no address advance.
                    continue
                # Instruction part follows the label.
                instr_text = rest
            else:
                instr_text = line

            # Find the best matching source line for the instruction text.
            # We scan the original source for the instruction text to give a
            # meaningful source_line value.
            src_lineno = self._find_source_line(instr_text, source_lines)

            # Parse opcode + operands
            instr = self._parse_instruction(address, instr_text, src_lineno, instr_text)
            self.instructions.append(instr)
            address += 1

    def _parse_instruction(
        self,
        address: int,
        text: str,
        source_line: int,
        source_text: str,
    ) -> Instruction:
        """Parse a single instruction line into an Instruction object."""
        parts = text.split(None, 1)  # split into opcode and the rest
        opcode = parts[0].upper()
        operands: list = []

        if len(parts) > 1:
            raw_operands = parts[1]

            if opcode in ('MVIB', 'MVIW', 'MVID'):
                tokens = self._split_operands(raw_operands)
                for tok in tokens:
                    tok = tok.strip()
                    if tok:
                        operands.append(_parse_mvi_operand(tok))
            else:
                tokens = self._split_operands(raw_operands)
                for tok in tokens:
                    tok = tok.strip()
                    if not tok:
                        continue
                    parsed = parse_operand(tok)
                    if isinstance(parsed, str):
                        # Unresolved token – look up in label table
                        resolved = self.labels.get(parsed, -1)
                        operands.append(Label(name=parsed, resolved_addr=resolved))
                    else:
                        operands.append(parsed)

        expected = _EXPECTED_OPS.get(opcode)
        if expected is not None:
            counts = (expected,) if isinstance(expected, int) else expected
            if len(operands) not in counts:
                hint = " (missing comma between operands?)" if len(operands) < min(counts) else ""
                allowed = "/".join(str(c) for c in counts)
                raise SyntaxError(
                    f"line {source_line}: {opcode} expects {allowed} operand"
                    f"{'s' if max(counts) != 1 else ''}, got {len(operands)}{hint}"
                )

        self._validate_operand_ranges(opcode, operands, source_line)

        return Instruction(
            address=address,
            opcode=opcode,
            operands=operands,
            source_line=source_line,
            source_text=source_text,
        )

    @staticmethod
    def _validate_operand_ranges(opcode: str, operands: list, source_line: int) -> None:
        """Raise SyntaxError if any immediate operand exceeds ISA-defined range."""
        constraints = _OPERAND_RANGES.get(opcode)
        if not constraints:
            return
        for idx, max_val, desc in constraints:
            if idx >= len(operands):
                continue
            op = operands[idx]
            # A symbol is legitimate in most constrained slots - a JMP target, a
            # .set constant used as an LDI immediate or a memory offset. It is
            # NEVER legitimate as a bit position or shift amount, and that is
            # exactly where letting one through crashes the simulator: the
            # operand arrives at core/branch.py as resolved_addr = -1 and
            # `reg_val >> -1` raises a bare ValueError that escapes the run.
            # So reject it only there, and leave every other slot alone.
            if isinstance(op, Label) and opcode in _BIT_POSITION_OPCODES:
                raise SyntaxError(
                    f"line {source_line}: {opcode} operand {idx + 1} is the "
                    f"symbol '{op.name}', but this operand must be a literal "
                    f"{desc}"
                    + ("" if op.resolved_addr >= 0 else " (symbol is undefined)"))
            if not isinstance(op, Immediate):
                continue
            if op.value < 0 or op.value > max_val:
                raise SyntaxError(
                    f"line {source_line}: {opcode} operand {idx + 1} value "
                    f"{op.value} (0x{op.value:X}) out of range for {desc}"
                )

    @staticmethod
    def _split_operands(raw: str) -> list[str]:
        """Split a comma-separated operand string into individual tokens.

        Handles whitespace around commas and preserves tokens like ``&r2``.
        """
        return [tok.strip() for tok in raw.split(",")]

    @staticmethod
    def _find_source_line(instr_text: str, source_lines: list[str]) -> int:
        """Return the 1-based source line number that best matches *instr_text*.

        Falls back to 0 when not found.
        """
        needle = instr_text.strip().lower()
        for idx, sline in enumerate(source_lines, start=1):
            # Strip inline comments and whitespace from the source line.
            clean = re.sub(r";.*", "", sline).strip().lower()
            if needle in clean or clean.endswith(needle):
                return idx
        return 0
