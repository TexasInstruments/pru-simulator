"""PRU assembler preprocessor.

Handles:
  - ; comment stripping
  - empty-line removal
  - NAME .set VALUE    (bounded integer expressions, TI label-field syntax)
  - .macro / .endm     (with optional parameters)
  - .if EXPR / .else / .endif
  - .include "file"
  - flat TI .struct / .sassign, and legacy .struct / .u8 / .u16 / .u32 / .ends
"""

from __future__ import annotations

import ast
import operator
import os
import re
from typing import Optional


class SourceLine(str):
    """A cleaned line retaining its location through assembler expansion."""

    def __new__(cls, text: str, source_line: int):
        result = super().__new__(cls, text)
        result.source_line = source_line
        return result


class Preprocessor:
    def __init__(self) -> None:
        self.defines: dict[str, str] = {}
        self.macros: dict[str, tuple[list[str], list[str]]] = {}  # name → (params, body_lines)
        self.structs: dict[str, int] = {}   # flat map: "StructName.field" → byte_offset
        self.struct_fields: dict[str, list[tuple[str, int, int]]] = {}
        self.include_paths: list[str] = ["."]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_file(self, filepath: str) -> list[str]:
        """Process an assembly source file, returning cleaned instruction lines."""
        with open(filepath, "r", encoding="utf-8") as fh:
            text = fh.read()
        # Make sure relative .include directives resolve relative to the file
        old_paths = list(self.include_paths)
        dir_of_file = os.path.dirname(os.path.abspath(filepath))
        if dir_of_file not in self.include_paths:
            self.include_paths.insert(0, dir_of_file)
        result = self.process_text(text)
        self.include_paths = old_paths
        return result

    def process_text(self, text: str, include_paths: list[str] | None = None) -> list[str]:
        """Process raw assembly text, returning cleaned instruction lines."""
        if include_paths is not None:
            old_paths = self.include_paths
            self.include_paths = list(include_paths)
            try:
                return self._process_lines(text.splitlines())
            finally:
                self.include_paths = old_paths
        return self._process_lines(text.splitlines())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_lines(self, lines: list[str]) -> list[str]:
        """Core processing loop – handles all directives."""
        output: list[str] = []
        i = 0

        # State for .struct processing
        current_struct: Optional[str] = None
        struct_line = 0
        struct_offset: int = 0
        legacy_struct = False

        # Conditional-assembly stack: list of (emit: bool, seen_else: bool)
        cond_stack: list[tuple[bool, bool]] = []

        while i < len(lines):
            line = lines[i]
            i += 1

            # Strip inline ; comments
            line = _strip_comment(line).strip()
            source_line = i

            if not line:
                continue

            # ------------------------------------------------------------------
            # Conditional assembly: .if / .else / .endif
            # ------------------------------------------------------------------
            if _match_directive(line, ".if"):
                expr = line.split(None, 1)[1].strip() if len(line.split(None, 1)) > 1 else ""
                result = self._eval_condition(expr)
                cond_stack.append((result, False))
                continue

            if _match_directive(line, ".else"):
                if cond_stack:
                    emit, seen_else = cond_stack[-1]
                    cond_stack[-1] = (not emit, True)
                continue

            if _match_directive(line, ".endif"):
                if cond_stack:
                    cond_stack.pop()
                continue

            # If we're inside a false branch, skip (but still track nesting)
            if cond_stack and not cond_stack[-1][0]:
                continue

            # Flat TI structures, plus the historical simulator spelling.
            begin = re.fullmatch(r"(\w+)\s+\.struct\s*", line, re.I)
            legacy = re.fullmatch(r"\.struct\s+(\w+)", line, re.I)
            if begin or legacy:
                if current_struct is not None:
                    raise SyntaxError(f"line {i}: nested .struct is unsupported")
                current_struct = (begin or legacy).group(1)
                if current_struct in self.struct_fields:
                    raise SyntaxError(f"line {i}: redefinition of structure {current_struct}")
                struct_line = i
                legacy_struct = legacy is not None
                self.struct_fields[current_struct] = []
                struct_offset = 0
                continue
            end = re.fullmatch(r"(?:(\w+)\s+)?\.endstruct", line, re.I)
            if end or line.lower() == ".ends":
                if current_struct is None:
                    raise SyntaxError(f"line {i}: structure end without .struct")
                if end and end.group(1):
                    self.defines[end.group(1)] = str(struct_offset)
                current_struct = None
                continue
            if current_struct is not None:
                field = re.fullmatch(r"(\w+)\s+\.(ubyte|ushort|uint)", line, re.I)
                if legacy_struct:
                    old = re.fullmatch(r"\.(u8|u16|u32)\s+(\w+)", line, re.I)
                    if old:
                        field_name, kind = old.group(2), old.group(1).lower()
                    else:
                        raise SyntaxError(f"line {i}: unsupported structure member: {line}")
                elif field:
                    field_name, kind = field.group(1), field.group(2).lower()
                else:
                    raise SyntaxError(f"line {i}: unsupported structure member: {line}")
                size = {"u8": 1, "u16": 2, "u32": 4,
                        "ubyte": 1, "ushort": 2, "uint": 4}[kind]
                key = f"{current_struct}.{field_name}"
                if any(f[0] == field_name for f in self.struct_fields[current_struct]):
                    raise SyntaxError(f"line {i}: duplicate structure member {field_name}")
                self.structs[key] = struct_offset
                self.defines[key] = str(struct_offset)
                self.struct_fields[current_struct].append((field_name, struct_offset, size))
                struct_offset += size
                continue

            assign = re.fullmatch(r"(\w+)\s+\.sassign\s+r(\d+),\s*(\w+)", line, re.I)
            if assign:
                alias, reg, name = assign.groups()
                fields = self.struct_fields.get(name)
                if not fields:
                    raise SyntaxError(f"line {i}: .sassign requires a defined nonempty structure: {name}")
                for member, offset, size in fields:
                    byte = int(reg) * 4 + offset
                    index, sub = divmod(byte, 4)
                    if index > 31 or sub + size > 4 or (size == 4 and sub):
                        raise SyntaxError(f"line {i}: unsupported .sassign register layout for {alias}.{member}")
                    suffix = "" if size == 4 else f".{'b' if size == 1 else 'w'}{sub}"
                    self.defines[f"{alias}.{member}"] = f"r{index}{suffix}"
                continue
            if re.search(r"\.(?:struct|sassign|endstruct)\b", line, re.I):
                raise SyntaxError(f"line {i}: unsupported structure syntax: {line}")

            # ------------------------------------------------------------------
            # NAME .set VALUE / NAME .equ VALUE
            # (symbol is a label; per TI PRU assembler syntax the symbol must
            #  appear in the label field, not after the directive)
            # ------------------------------------------------------------------
            m = re.match(r"^(\w+)\s+\.(?:set|equ)\b\s*(.+)$", line, re.IGNORECASE)
            if m:
                if m.group(1) in self.defines:
                    raise SyntaxError(f"line {i}: redefinition of .set/.equ symbol {m.group(1)}")
                value = self._apply_defines(m.group(2)).strip()
                try:
                    if re.fullmatch(r"r(?:[0-9]|[12][0-9]|3[01])", value, re.I):
                        # Whole-register alias, e.g. "Counter .set r5".
                        self.defines[m.group(1)] = value
                    else:
                        self.defines[m.group(1)] = str(_eval_integer(value))
                except (ValueError, SyntaxError, ArithmeticError, RecursionError) as exc:
                    raise SyntaxError(f"line {i}: unsupported .set/.equ expression for {m.group(1)}: {exc}") from exc
                continue

            # ------------------------------------------------------------------
            # .asg VALUE, NAME  (TI alias: reversed argument order vs .set)
            # ------------------------------------------------------------------
            if _match_directive(line, ".asg"):
                rest = line.split(None, 1)[1].strip()
                parts = re.split(r",\s*", rest, maxsplit=1)
                if len(parts) == 2:
                    self.defines[parts[1].strip()] = parts[0].strip()
                continue

            # ------------------------------------------------------------------
            # .macro NAME [param1 param2 ...]
            # ------------------------------------------------------------------
            ti_macro = re.fullmatch(r"(\w+)\s+\.macro(?:\s+(.*))?", line, re.I)
            if ti_macro or _match_directive(line, ".macro"):
                # Split on whitespace only for the first two tokens
                # (.macro  NAME  param1, param2, ...)
                if ti_macro:
                    line = f".macro {ti_macro.group(1)} {ti_macro.group(2) or ''}".strip()
                parts = line.split(None, 2)
                macro_name = parts[1] if len(parts) > 1 else ""
                if len(parts) > 2:
                    # params may be comma-separated and/or space-separated
                    params = [p.strip() for p in re.split(r"[,\s]+", parts[2]) if p.strip()]
                else:
                    params = []
                body: list[str] = []
                while i < len(lines):
                    mline = lines[i]
                    i += 1
                    mline_stripped = _strip_comment(mline).strip()
                    if _match_directive(mline_stripped, ".endm"):
                        break
                    body.append(mline_stripped)
                else:
                    raise SyntaxError(f"line {source_line}: unterminated .macro {macro_name}")
                self.macros[macro_name.lower()] = (params, body)
                continue

            # ------------------------------------------------------------------
            # .include "filename"
            # ------------------------------------------------------------------
            if _match_directive(line, ".include"):
                m = re.match(r'\.include\s+"([^"]+)"', line, re.IGNORECASE)
                if m:
                    fname = m.group(1)
                    fpath = self._resolve_include(fname)
                    if fpath:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            inc_text = fh.read()
                        inc_lines = self._process_lines(inc_text.splitlines())
                        output.extend(inc_lines)
                continue

            # ------------------------------------------------------------------
            # Apply defines (text substitution) on the current line
            # ------------------------------------------------------------------
            line = self._apply_defines(line)

            # ------------------------------------------------------------------
            # Macro invocation
            # ------------------------------------------------------------------
            first_token = line.split()[0] if line.split() else ""
            if first_token.lower() in self.macros:
                expanded = self._expand_macro(first_token.lower(), line)
                output.extend(SourceLine(item, source_line) for item in expanded)
                continue

            # ------------------------------------------------------------------
            # Regular instruction / label line
            # ------------------------------------------------------------------
            output.append(SourceLine(line, source_line))

        if current_struct is not None:
            raise SyntaxError(f"line {struct_line}: unterminated .struct {current_struct}")
        return output

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _apply_defines(self, line: str) -> str:
        """Replace defined symbols in *line* using whole-word substitution."""
        for name, value in self.defines.items():
            # Use word-boundary replacement so we don't mangle sub-strings
            end = r"(?![\w.])" if "." in name else r"\b"
            line = re.sub(r"\b" + re.escape(name) + end, value, line)
        return line

    def _eval_condition(self, expr: str) -> bool:
        """Evaluate a .if expression using current defines.

        After substituting known defines, try to evaluate the expression
        numerically.  If the expression still contains unresolved identifiers
        (NameError / SyntaxError), we treat it as *False* because an undefined
        symbol means the condition guard was not satisfied.
        """
        substituted = self._apply_defines(expr)
        try:
            return bool(eval(substituted, {"__builtins__": {}}, {}))  # noqa: S307
        except (NameError, SyntaxError):
            # Unresolved identifier or bad syntax → condition not met
            return False
        except Exception:
            return False

    def _expand_macro(self, name: str, invocation: str) -> list[str]:
        """Expand macro *name* given the full invocation line."""
        params, body = self.macros[name.lower()]
        # Args are everything after the macro name
        parts = invocation.split(None, 1)
        raw_args = parts[1] if len(parts) > 1 else ""
        args = [a.strip() for a in raw_args.split(",")]

        expanded: list[str] = []
        for bline in body:
            subst = bline
            for param, arg in zip(params, args):
                subst = re.sub(r"\b" + re.escape(param) + r"\b", arg, subst)
            subst = self._apply_defines(subst)
            if subst.strip():
                expanded.append(subst.strip())
        return expanded

    def _resolve_include(self, fname: str) -> Optional[str]:
        for directory in self.include_paths:
            candidate = os.path.join(directory, fname)
            if os.path.isfile(candidate):
                return candidate
        return None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _strip_comment(line: str) -> str:
    """Remove everything from the first unquoted ';' to end of line."""
    in_str = False
    for idx, ch in enumerate(line):
        if ch == '"':
            in_str = not in_str
        if ch == ";" and not in_str:
            return line[:idx]
    return line


def _match_directive(line: str, directive: str) -> bool:
    """Return True if *line* starts with *directive* (case-insensitive)."""
    parts = line.split(None, 1)
    return bool(parts) and parts[0].lower() == directive.lower()


def _eval_integer(expr: str) -> int:
    """Evaluate the supported integer subset without executing source text.

    Values and intermediate results may range from -2^31 to 2^32-1, so unsigned
    32-bit masks are accepted. Division truncates toward zero, including for
    negative operands.
    Unsupported TI expression forms are deliberately load errors.
    """
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor}
    unary = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Invert: operator.invert}

    def visit(node):
        value = evaluate(node)
        if not -(1 << 31) <= value < (1 << 32):
            raise ValueError("integer expression is outside the supported range -2^31 to 2^32-1")
        return value

    def evaluate(node):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in unary:
            return unary[type(node.op)](visit(node.operand))
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            kind = type(node.op)
            if kind in binary:
                return binary[kind](left, right)
            if kind in (ast.Div, ast.Mod):
                quotient = (abs(left) // abs(right)) * (-1 if (left < 0) != (right < 0) else 1)
                return quotient if kind is ast.Div else left - quotient * right
            if kind in (ast.LShift, ast.RShift) and left >= 0 and 0 <= right <= 31:
                return left << right if kind is ast.LShift else left >> right
        raise ValueError(f"unsupported integer expression: {expr}")

    return visit(ast.parse(expr.strip(), mode="eval").body)
