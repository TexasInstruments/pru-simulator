"""PRU assembler preprocessor.

Handles:
  - ; comment stripping
  - empty-line removal
  - NAME .set VALUE    (text substitution; symbol in label field, TI syntax)
  - .macro / .endm     (with optional parameters)
  - .if EXPR / .else / .endif
  - .include "file"
  - .struct / .u8 / .u16 / .u32 / .ends
"""

from __future__ import annotations

import os
import re
from typing import Optional


class Preprocessor:
    def __init__(self) -> None:
        self.defines: dict[str, str] = {}
        self.macros: dict[str, tuple[list[str], list[str]]] = {}  # name → (params, body_lines)
        self.structs: dict[str, int] = {}   # flat map: "StructName.field" → byte_offset
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
        struct_offset: int = 0

        # Conditional-assembly stack: list of (emit: bool, seen_else: bool)
        cond_stack: list[tuple[bool, bool]] = []

        while i < len(lines):
            line = lines[i]
            i += 1

            # Strip inline ; comments
            line = _strip_comment(line).strip()

            if not line:
                continue

            # ------------------------------------------------------------------
            # .struct / .ends / .u8 / .u16 / .u32  (processed unconditionally
            # because they only define symbols, not output lines)
            # ------------------------------------------------------------------
            if _match_directive(line, ".struct"):
                name = line.split(None, 1)[1].strip()
                current_struct = name
                struct_offset = 0
                continue

            if _match_directive(line, ".ends"):
                current_struct = None
                struct_offset = 0
                continue

            if current_struct is not None:
                m = re.match(r"\.(u8|u16|u32)\s+(\w+)", line, re.IGNORECASE)
                if m:
                    type_name = m.group(1).lower()
                    field_name = m.group(2)
                    key = f"{current_struct}.{field_name}"
                    self.structs[key] = struct_offset
                    self.defines[key] = str(struct_offset)
                    sizes = {"u8": 1, "u16": 2, "u32": 4}
                    struct_offset += sizes[type_name]
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

            # ------------------------------------------------------------------
            # NAME .set VALUE / NAME .equ VALUE
            # (symbol is a label; per TI PRU assembler syntax the symbol must
            #  appear in the label field, not after the directive)
            # ------------------------------------------------------------------
            m = re.match(r"^(\w+)\s+\.(?:set|equ)\b\s*(.+)$", line, re.IGNORECASE)
            if m:
                self.defines[m.group(1)] = m.group(2).strip()
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
            if _match_directive(line, ".macro"):
                # Split on whitespace only for the first two tokens
                # (.macro  NAME  param1, param2, ...)
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
                self.macros[macro_name] = (params, body)
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
            if first_token in self.macros:
                expanded = self._expand_macro(first_token, line)
                output.extend(expanded)
                continue

            # ------------------------------------------------------------------
            # Regular instruction / label line
            # ------------------------------------------------------------------
            output.append(line)

        return output

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _apply_defines(self, line: str) -> str:
        """Replace defined symbols in *line* using whole-word substitution."""
        for name, value in self.defines.items():
            # Use word-boundary replacement so we don't mangle sub-strings
            line = re.sub(r"\b" + re.escape(name) + r"\b", value, line)
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
        params, body = self.macros[name]
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
    return line.lower().startswith(directive.lower())
