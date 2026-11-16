#!/usr/bin/env python3
"""Lint PRU assembly (clpru syntax) for mistakes the assembler misses or reports badly.

Usage:
    python3 scripts/lint_pru.py [--openpru] [--c-abi] [-I <dir>]... <file.asm> [<file.asm> ...]

Exit code 0 = no errors (warnings allowed), 1 = errors found, 2 = usage error.
Standard library only; no install needed.

Checks:
    E column1      Instruction mnemonic in column 1 (the TI assembler reads it as a label)
    E immediate    Literal immediate outside the operand's range (e.g. ADD with 300)
    E register     Invalid register or field (r32, r1.b4, r1.w3, r1.t32, C32)
    E label        Quick branch or LOOP to a label not defined in the file
    E reserved     Label named like an instruction (LOOP, HALT, NOP ...); clpru rejects it
    E number       Underscore inside a numeric literal (0xC3A5_9E17); clpru rejects it
    W extern       JMP/CALL/JAL to a symbol not defined here and not declared .global/.ref
    W r31event     Literal write to R31 without bit 5, which raises no event
    W nohalt       No HALT, no return (JMP r3.w2 / RET) and no final unconditional jump
                   (task-manager tasks that end in a yield XIN also trigger this; check them)

Register use, per .asmfunc function (checks adapted from TI's internal PRULint rules):
    W PRU-C001     Register read but never written in the function. Registers named in the
                   function header (for example "Parameters: r14 = ...") count as inputs.
    W PRU-C003     Register written twice with no read between. Skipped for r25-r29 (the
                   multiplier) and across branches, labels and .if/.else.
    W PRU-C004     (--c-abi) Function writes r4-r13 without storing the incoming value before
                   the first write and loading it back from the same place on every path to a
                   return, writes r3.w2 (a nested call) without saving the return address first,
                   or returns through r3.w2 after a nested call without writing it back
                   (references/c-interop.md). Not seen: writes inside macros, a register kept
                   in another register, a save overwritten through a different register.
    W PRU-X001     r28 or r29 written on the instruction right before a MAC read (xin 0): the
                   MAC samples them one instruction late, so it uses the old value

Macros and includes:
    W PRU-M001     Name in the instruction slot is neither an instruction nor a defined macro
    W PRU-M003     Macro invokes itself
    W PRU-M004     Macro redefined with a different body
    W PRU-M005     .include not found. Searched: the file's folder and its ancestors, -I folders,
                   the current folder, and source/firmware/common of an OpenPRU checkout.
                   M001 is skipped while an include is missing, since the macro may be in it.

With --openpru, also apply OpenPRU best_practices.md rules (warnings):
    W tab          Tab character (only spaces are allowed)
    W width        Line longer than 79 columns (soft limit) or 129 (hard limit)
    W todo         TODO marker (OpenPRU uses FIXME only)
    W include      .include path with forward slashes (use backslashes)
    W PRU-D001..3  Function or macro without a header, with a mismatched "Function:" name, or
                   missing Description, PEAK cycles, Parameters, Registers modified, Returns
                   or Pseudo code

Labels from files named in .include directives are resolved relative to the
including file, so calls into included macros/functions are not reported.
"""
import os
import re
import sys

MNEMONICS = {
    "ADD", "ADC", "SUB", "SUC", "RSB", "RSC", "AND", "OR", "XOR", "NOT", "LSL", "LSR",
    "MIN", "MAX", "CLR", "SET", "LMBD", "MOV", "LDI", "LDI32", "ZERO", "FILL",
    "LBBO", "SBBO", "LBCO", "SBCO", "XIN", "XOUT", "XCHG", "SXIN", "SXOUT", "SXCHG",
    "MVIB", "MVIW", "MVID", "JMP", "JAL", "CALL", "RET", "LOOP", "ILOOP", "HALT", "SLP",
    "QBGT", "QBGE", "QBLT", "QBLE", "QBEQ", "QBNE", "QBA", "QBBS", "QBBC", "WBS", "WBC", "NOP",
}

# mnemonic -> list of (operand index, min, max, description)
RANGES = {
    **{m: [(2, 0, 255, "OP(255)")] for m in
       ("ADD", "ADC", "SUB", "SUC", "RSB", "RSC", "AND", "OR", "XOR", "MIN", "MAX", "LMBD",
        "QBGT", "QBGE", "QBLT", "QBLE", "QBEQ", "QBNE")},
    **{m: [(2, 0, 31, "OP(31)")] for m in ("LSL", "LSR", "CLR", "SET", "QBBS", "QBBC")},
    "WBS": [(1, 0, 31, "OP(31)")],
    "WBC": [(1, 0, 31, "OP(31)")],
    "LDI": [(1, 0, 0xFFFF, "IM(65535); use LDI32 for larger values")],
    "MOV": [(1, 0, 0xFFFF, "16-bit immediate; use LDI32 for larger values")],
    "LDI32": [(1, 0, 0xFFFFFFFF, "32-bit immediate")],
    **{m: [(2, 0, 255, "offset OP(255)"), (3, 1, 124, "byte count 1-124")]
       for m in ("LBBO", "SBBO", "LBCO", "SBCO")},
    **{m: [(0, 0, 253, "device ID"), (2, 1, 124, "byte count 1-124")]
       for m in ("XIN", "XOUT", "XCHG")},
    "ZERO": [(1, 1, 124, "byte count 1-124")],
    "FILL": [(1, 1, 124, "byte count 1-124")],
    "LOOP": [(1, 1, 256, "loop count 1-256")],
}

QUICK_BRANCHES = {"QBGT", "QBGE", "QBLT", "QBLE", "QBEQ", "QBNE", "QBA", "QBBS", "QBBC"}
REG_RE = re.compile(r"&?\br(\d+)(?:\.([bwt])(\d+))?\b", re.IGNORECASE)
CONST_RE = re.compile(r"^C(\d+)$", re.IGNORECASE)
IDENT_RE = re.compile(r"^[A-Za-z_$][\w$]*\??$")
SAFE_EXPR_RE = re.compile(r"^[\d\sxXa-fA-F+\-*/()<>|&~^]+$")
SET_RE = re.compile(r"^\.(set|equ)\b", re.IGNORECASE)
STRING_RE = re.compile(r'"[^"]*"')
UNDERSCORE_NUM_RE = re.compile(r"\b(?:0[xX][0-9A-Fa-f]*_[0-9A-Fa-f_]*|\d+_[\d_]*)")   # clpru rejects 0xC3A5_9E17
# Every instruction mnemonic is a reserved identifier in clpru 2.3.3 (checked with -v4), as a label
# definition or a branch target. ILOOP, CALL and RET are not; TSEN is, though it is not in MNEMONICS.
RESERVED_LABELS = (MNEMONICS - {"ILOOP", "CALL", "RET"}) | {"TSEN"}
MAC_ID = 0
MAC_OPERAND_REGS = {28, 29}


def strip_comment(line):
    if line.startswith(("*", "!")):
        return ""
    return line.split(";", 1)[0].rstrip()


def split_operands(text):
    return [o.strip() for o in text.split(",")] if text.strip() else []


def evaluate(expr, symbols):
    """Return an int for a literal expression, or None if it can't be resolved."""
    expr = expr.strip().lstrip("&")
    if REG_RE.fullmatch(expr) or CONST_RE.match(expr):
        return None

    def sub(m):
        name = m.group(0)
        if re.fullmatch(r"0[xX][0-9a-fA-F]+|\d+", name):
            return name
        return str(symbols[name]) if name in symbols else "?"

    resolved = re.sub(r"0[xX][0-9a-fA-F]+|[A-Za-z_$][\w$]*|\d+", sub, expr)
    if "?" in resolved or not SAFE_EXPR_RE.match(resolved):
        return None
    try:
        value = eval(resolved, {"__builtins__": {}}, {})  # restricted to numeric literals
    except Exception:
        return None
    return value if isinstance(value, int) else None


def parse(path):
    """Return a list of (lineno, label, mnemonic, operands, raw) and the symbol tables."""
    rows, symbols, aliases, labels, externs, col1 = [], {}, {}, set(), set(), set()
    includes, timeline, underscored = [], [], []
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    def define(n, name, value):
        """Record a constant. The timeline lets a check see the value in force at its own line."""
        symbols[name] = value
        timeline.append((n, dict(symbols)))

    for n, raw in enumerate(lines, 1):
        line = strip_comment(raw)
        if not line.strip():
            continue
        if UNDERSCORE_NUM_RE.search(STRING_RE.sub('""', line)):
            underscored.append(n)
        label, rest = None, line
        if not line[0].isspace():
            head, _, tail = line.partition(" ")
            head_tab, _, tail_tab = line.partition("\t")
            if len(head_tab) < len(head):
                head, tail = head_tab, tail_tab
            if head.startswith("."):
                head, tail = "", line      # a directive such as .asmfunc, not a label
            elif head.upper() in MNEMONICS and not SET_RE.match(tail.strip()):
                col1.add(n)                # a mnemonic here is a label; "MAX .set 255" is a constant, not one
                head, tail = "", line
            label, rest = (head.rstrip(":") or None), tail
            if label and SET_RE.match(rest.strip()):
                value = evaluate(rest.strip()[4:], symbols)
                if value is not None:
                    define(n, label, value)
                rows.append((n, None, None, [], raw))
                continue
            if label:
                labels.add(label)
        rest = rest.strip()
        if not rest:
            rows.append((n, label, None, [], raw))
            continue
        mnem, _, ops = rest.partition(" ")
        if "\t" in mnem:
            mnem, _, ops2 = mnem.partition("\t")
            ops = ops2 + " " + ops
        upper = mnem.upper()
        operands = split_operands(ops)
        if upper in (".ASG", ".EVAL") and len(operands) == 2:
            value = evaluate(operands[0].strip('"'), symbols)
            if value is not None:
                define(n, operands[1], value)
            else:
                aliases[operands[1]] = operands[0].strip('"')
        elif upper in (".GLOBAL", ".REF", ".DEF"):
            externs.update(operands)
        elif upper in (".INCLUDE", ".COPY") and operands:
            includes.append(operands[0].strip('"'))
        rows.append((n, label, upper, operands, raw))
    return rows, symbols, aliases, labels, externs, col1, includes, lines, timeline, underscored


def find_include(base, inc, search):
    """Resolve an .include name against the including file's folder, its ancestors (so an
    OpenPRU path such as source\\firmware\\common\\x.inc resolves from the repo root), the
    -I folders and the current folder."""
    rel = inc.replace("\\", "/")
    dirs, d = [base], base
    for _ in range(8):
        parent = os.path.dirname(d)
        if parent == d:
            break
        dirs.append(parent)
        d = parent
    dirs += [os.path.abspath(s) for s in search] + [os.getcwd()]
    for d in dirs:
        candidate = os.path.abspath(os.path.join(d, rel))
        if os.path.isfile(candidate):
            return candidate
    return None


def style_checks(lines, includes_raw):
    warnings = []
    for n, raw in enumerate(lines, 1):
        if "\t" in raw:
            warnings.append((n, "tab", "tab character; OpenPRU allows spaces only"))
        width = len(raw.rstrip("\r\n"))
        if width > 129:
            warnings.append((n, "width", f"{width} columns exceeds the 129-column hard limit"))
        elif width > 79:
            warnings.append((n, "width", f"{width} columns exceeds the 79-column soft limit"))
        if re.search(r"\bTODO\b", raw):
            warnings.append((n, "todo", "use FIXME instead of TODO (OpenPRU convention)"))
    for n, inc in includes_raw:
        if "/" in inc:
            warnings.append((n, "include", f".include \"{inc}\" uses '/'; OpenPRU requires '\\'"))
    # One summary line per check keeps output readable on legacy files.
    grouped = {}
    for n, code, msg in warnings:
        grouped.setdefault((code, msg.split(" exceeds")[-1] if code == "width" else msg), []).append(n)
    summary = []
    for (code, msg), line_numbers in grouped.items():
        if len(line_numbers) == 1:
            text = [m for n, c, m in warnings if n == line_numbers[0] and c == code][0]
            summary.append((line_numbers[0], code, text))
        else:
            limit = "exceeds" + msg if code == "width" else msg
            summary.append((line_numbers[0], code,
                            f"{len(line_numbers)} lines: {limit} (first shown)"))
    return summary


# ---- Checks ported from TI's PRULint: register use, macro graph, headers -----------------

KNOWN_NON_MACRO = {"SCAN", "TSEN"}
BRANCH_LIKE = QUICK_BRANCHES | {"JMP", "JAL", "CALL", "RET", "LOOP", "ILOOP", "WBS", "WBC", "HALT", "SLP"}
WRITE_FIRST = {"ADD", "ADC", "SUB", "SUC", "RSB", "RSC", "AND", "OR", "XOR", "NOT", "MIN", "MAX",
               "LSL", "LSR", "MOV", "LDI", "LDI32", "SET", "CLR", "LMBD", "MVIB", "MVIW", "MVID", "JAL"}
IO_REGS = {30, 31}
MULTIPLIER_REGS = set(range(25, 30))   # the multiplier reads r25-r29 itself, so back-to-back writes are normal


def reg_name(m):
    return f"r{int(m.group(1))}" + (f".{m.group(2).lower()}{m.group(3)}" if m.group(2) else "")


def reg_num(name):
    return int(name[1:].split(".")[0])


def reg_tokens(op):
    return [reg_name(m) for m in REG_RE.finditer(op) if int(m.group(1)) <= 31]


def reg_span(op, size):
    """Register numbers covered by a block transfer of `size` bytes starting at operand `op`."""
    m = REG_RE.search(op)
    if not m or int(m.group(1)) > 31:
        return set()
    n, kind, idx = int(m.group(1)), (m.group(2) or "").lower(), int(m.group(3) or 0)
    start = n * 4 + (idx if kind in ("b", "w") else 0)   # .bN and .wN both start at byte N (w1 = bytes 1-2)
    if size is None or size < 1:
        return {n}
    return set(range(start // 4, min((start + size - 1) // 4, 31) + 1))


def reg_access(mnem, ops, symbols):
    """Return (register reads, register writes, register numbers written) for one instruction."""
    reads, writes, written = [], [], set()

    def size(i):
        return evaluate(ops[i], symbols) if i < len(ops) else None

    if mnem in ("LBBO", "LBCO") and len(ops) >= 4:
        reads += [t for op in ops[1:3] for t in reg_tokens(op)]
        writes += reg_tokens(ops[0])[:1]
        written = reg_span(ops[0], size(3))
    elif mnem in ("SBBO", "SBCO") and len(ops) >= 4:
        reads += [t for op in ops[1:3] for t in reg_tokens(op)]
        reads += [f"r{i}" for i in reg_span(ops[0], size(3))]
    elif mnem in XFR and len(ops) >= 3:
        reads += reg_tokens(ops[0])
        if not mnem.endswith("IN"):
            reads += [f"r{i}" for i in reg_span(ops[1], size(2))]
        if not mnem.endswith("OUT"):
            writes += reg_tokens(ops[1])[:1]
            written = reg_span(ops[1], size(2))
    elif mnem in ("ZERO", "FILL") and len(ops) >= 2:
        writes += reg_tokens(ops[0])[:1]
        written = reg_span(ops[0], size(1))
    elif mnem in WRITE_FIRST and ops:
        dest = reg_tokens(ops[0])
        writes += dest[:1]
        reads += [t for op in ops[1:] for t in reg_tokens(op)]
        if mnem in ("SET", "CLR") and len(ops) == 2:
            reads += dest[:1]          # the two-operand form updates the register in place
    else:
        reads += [t for op in ops for t in reg_tokens(op)]
    return reads, writes, written | {reg_num(w) for w in writes}


def walk_includes(path, search):
    """Parse a file and everything it .includes. Returns (parsed files, unresolved includes).

    OpenPRU makefiles pass source/firmware/common as an include path, so that folder is
    searched too when an ancestor of the file has it."""
    search, d = list(search), os.path.dirname(os.path.abspath(path))
    while True:
        common = os.path.join(d, "source", "firmware", "common")
        if os.path.isdir(common):
            search.append(common)
            break
        if os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)
    seen, files, unresolved = set(), [], []

    def visit(p):
        if p in seen:
            return
        seen.add(p)
        parsed = parse(p)
        files.append((p, parsed))
        for n, _, mnem, ops, _ in parsed[0]:
            if mnem in (".INCLUDE", ".COPY") and ops:
                inc = ops[0].strip('"')
                found = find_include(os.path.dirname(p), inc, search)
                if found:
                    visit(found)
                else:
                    unresolved.append((p, n, inc))

    visit(os.path.abspath(path))
    return files, unresolved


def find_blocks(rows):
    """Macro bodies (.macro/.endm) and functions (.asmfunc/.endasmfunc)."""
    blocks, macro, func = [], None, None
    for i, (n, label, mnem, ops, raw) in enumerate(rows):
        if mnem == ".MACRO" and label:
            macro = (i, n, label)
        elif mnem == ".ENDM" and macro:
            blocks.append({"kind": "Macro", "name": macro[2], "line": macro[1], "rows": rows[macro[0] + 1:i]})
            macro = None
        elif mnem == ".ASMFUNC":
            func = (i, n, label)
        elif mnem == ".ENDASMFUNC" and func:
            body = rows[func[0] + 1:i]
            name = func[2] or next((r[1] for r in body if r[1]), None)
            if name:
                blocks.append({"kind": "Function", "name": name.strip("|"), "line": func[1], "rows": body})
            func = None
    return blocks


def block_header(lines, block):
    """Comment lines directly above a definition and directly under its label."""
    def is_comment(s):
        return s.lstrip().startswith(";")

    text, j = [], block["line"] - 2
    while j >= 0 and (is_comment(lines[j]) or not lines[j].strip()):
        if lines[j].strip():
            text.insert(0, lines[j])
        j -= 1
    first_label = next((r[0] for r in block["rows"] if r[1]), block["line"])
    j = first_label if block["kind"] == "Function" else block["line"]
    while j < len(lines) and (is_comment(lines[j]) or not lines[j].strip()):
        if lines[j].strip():
            text.append(lines[j])
        j += 1
    return text


FIELD_RE = re.compile(r"^\s*;\s*([A-Za-z][A-Za-z _-]*?)\s*:\s*(.*?)\s*$")
HEADER_FIELDS = [("Description", ("description",)), ("PEAK cycles", ("peakcycles",)),
                 ("Parameters", ("parameters",)),
                 ("Registers modified", ("registers", "registersmodified", "sideeffects")),
                 ("Returns", ("returns",)), ("Pseudo code", ("pseudocode",))]


def header_checks(lines, blocks):
    """PRU-D: the header fields best_practices.md asks for on every function and macro."""
    warnings = []
    for b in blocks:
        fields = {}
        for raw in block_header(lines, b):
            m = FIELD_RE.match(raw)
            if m:
                fields[re.sub(r"[ _-]", "", m.group(1)).lower()] = m.group(2)
        if not fields:
            warnings.append((b["line"], "PRU-D001", f"{b['name']}: no header comment block"))
            continue
        named = fields.get(b["kind"].lower())
        if named and named.split() and named.split()[0].strip("|").upper() != b["name"].upper():
            warnings.append((b["line"], "PRU-D002", f"{b['name']}: header says '{b['kind']}: {named.split()[0]}'"))
        missing = [label for label, keys in HEADER_FIELDS if not any(k in fields for k in keys)]
        if missing:
            warnings.append((b["line"], "PRU-D003", f"{b['name']}: header lacks {', '.join(missing)}"))
    return warnings


CALLEE_BYTES = set(range(16, 56))   # r4-r13, as register number * 4 + byte
RETURN_BYTES = {14, 15}             # r3.w2
XFR = {"XIN", "XOUT", "XCHG", "SXIN", "SXOUT", "SXCHG"}


def reg_bytes(op, size=False):
    """Bytes (register number * 4 + byte) that a register operand covers. Pass `size` for a block
    transfer starting there; None (count not resolvable) means up to the end of that register."""
    m = REG_RE.search(op)
    if not m or int(m.group(1)) > 31:
        return set()
    n, kind, idx = int(m.group(1)), (m.group(2) or "").lower(), int(m.group(3) or 0)
    start = n * 4 + {"b": idx, "w": idx, "t": idx // 8}.get(kind, 0)
    if size is False:
        width = {"b": 1, "w": 2, "t": 1}.get(kind, 4)
    else:
        width = size if size and size > 0 else n * 4 + 4 - start
    return set(range(start, min(start + width, 128)))


def block_transfer(mnem, ops, symbols):
    """(kind, where, bytes) for a block store, load or swap of registers, else None. `where` names
    the far end (memory base and offset, or broadside device) together with the first register byte,
    so a load only counts as a restore when it reads back what the matching store wrote."""
    def norm(op):
        value = evaluate(op, symbols)
        return str(value) if value is not None else op.lstrip("&").lower()

    if mnem in ("SBBO", "SBCO", "LBBO", "LBCO") and len(ops) >= 4:
        kind, far = ("store" if mnem[0] == "S" else "load"), (mnem[1:], norm(ops[1]), norm(ops[2]))
        covered = reg_bytes(ops[0], evaluate(ops[3], symbols))
    elif mnem in XFR and len(ops) >= 3:
        kind = "store" if mnem.endswith("OUT") else "load" if mnem.endswith("IN") else "swap"
        far, covered = ("XFR", norm(ops[0])), reg_bytes(ops[1], evaluate(ops[2], symbols))
    else:
        return None
    return (kind, far + (min(covered),), covered) if covered else None


def callee_saved_violations(rows, aliases, symbols):
    """C ABI preservation in one function.
    Returns (register numbers, return address clobbered, return address not reloaded).

    r4-r13, per byte and per path through the function's branches: the incoming value has to be
    stored before the first write, and a load from the same place has to bring it back before every
    return. Reading the new value, loading from somewhere else, restoring one byte of a register
    that was overwritten whole, saving or restoring on only one branch, or storing the changed value
    over the saved one all leave the caller's value changed.
    r3.w2: a write (a nested call) needs the return address saved somewhere first, and a return
    through r3.w2 after a nested call needs it written back.

    Not tracked: a saved value overwritten through a different register or an overlapping address,
    a register kept in another register instead of memory, and anything a macro does."""
    nodes, label_at, waiting = [], {}, []
    ret_saved = ret_clobbered = False
    for _, label, mnem, operands, _ in rows:
        if label:
            waiting.append(label)
        if not mnem or mnem.startswith("."):
            continue
        for name in waiting:
            label_at[name] = len(nodes)
        waiting = []
        ops = [aliases.get(o.lstrip("&"), o) for o in operands]
        reads, writes, _ = reg_access(mnem, ops, symbols)
        xfer = block_transfer(mnem, ops, symbols)
        if xfer and xfer[0] != "store":
            wrote = xfer[2]
        elif mnem in ("ZERO", "FILL") and len(ops) >= 2:
            wrote = reg_bytes(ops[0], evaluate(ops[1], symbols))
        elif mnem == "CALL":
            wrote = set(RETURN_BYTES)          # CALL is JAL r3.w2
        else:
            wrote = set().union(*(reg_bytes(w) for w in writes))
        if mnem not in BRANCH_LIKE and any(reg_bytes(r) & RETURN_BYTES for r in reads):
            ret_saved = True
        if wrote & RETURN_BYTES and not ret_saved:
            ret_clobbered = True
        nodes.append((mnem, ops[0] if ops else None, xfer, wrote))

    def step(i, dirty, saved):
        """State after instruction i: bytes that differ from the caller's, and where each incoming
        byte is currently saved."""
        mnem, _, xfer, wrote = nodes[i]
        dirty, saved, restored = set(dirty), dict(saved), set()
        if xfer:
            kind, where, covered = xfer
            if kind != "store":
                restored = {b for b in covered if saved.get(b) == where}
            if kind != "load":
                for b in covered & CALLEE_BYTES:
                    if b in restored or (b in dirty and saved.get(b) == where):
                        del saved[b]           # that place now holds the changed value
                    elif b not in dirty:
                        saved[b] = where
        dirty = (dirty - restored) | (wrote & CALLEE_BYTES - restored)
        if wrote & RETURN_BYTES:
            if mnem in ("JAL", "CALL"):
                dirty |= RETURN_BYTES
            else:
                dirty -= RETURN_BYTES          # written back from wherever it was kept
        return dirty, saved

    def successors(i):
        """Next instructions, or None when this one returns to the caller."""
        mnem, target = nodes[i][:2]
        if mnem == "RET" or (mnem == "JMP" and target and REG_RE.fullmatch(target)):
            return None
        if mnem == "HALT":
            return []
        if mnem in ("JMP", "QBA"):
            return [label_at[target]] if target in label_at else []   # a jump out is not a return
        if mnem in QUICK_BRANCHES and target in label_at:
            return [i + 1, label_at[target]]
        return [i + 1]

    # Forward pass over the branches. Where paths meet, a byte is dirty if it is on either path
    # and saved only if it is saved in the same place on both.
    end, leaked, ret_lost = len(nodes), set(), False
    state, work = {0: (set(), {})}, [0]
    while work:
        i = work.pop()
        if i == end:                           # running off the end of the function
            leaked |= state[i][0]
            continue
        dirty, saved = step(i, *state[i])
        following = successors(i)
        if following is None:
            leaked |= dirty
            mnem, target = nodes[i][:2]
            if dirty & RETURN_BYTES and (mnem == "RET" or reg_bytes(target) & RETURN_BYTES):
                ret_lost = True
            continue
        for nxt in following:
            if nxt not in state:
                state[nxt] = (set(dirty), dict(saved))
                work.append(nxt)
                continue
            old_dirty, old_saved = state[nxt]
            merged = {b: w for b, w in old_saved.items() if saved.get(b) == w}
            if not dirty <= old_dirty or len(merged) != len(old_saved):
                state[nxt] = (old_dirty | dirty, merged)
                work.append(nxt)
    return sorted({b // 4 for b in leaked & CALLEE_BYTES}), ret_clobbered, ret_lost and not ret_clobbered


def register_checks(lines, blocks, aliases, symbols, opaque, c_abi):
    """PRU-C001 uninitialized read, PRU-C003 discarded write, PRU-C004 (--c-abi) save-on-entry write."""
    warnings = []
    for b in blocks:
        written, first_read, read_all, pending, dead = set(), {}, set(), {}, []
        for n, label, mnem, operands, raw in b["rows"]:
            if label:
                pending.clear()
            if mnem in (".IF", ".ELSE", ".ELSEIF", ".ENDIF", ".SWITCH", ".CASE", ".BREAK", ".ENDSWITCH",
                        ".WHILE", ".ENDLOOP"):
                pending.clear()        # only one arm of conditional assembly runs
            if not mnem or mnem.startswith("."):
                continue
            ops = [aliases.get(o.lstrip("&"), o) for o in operands]
            reads, writes, span = reg_access(mnem, ops, symbols)
            for r in reads:
                first_read.setdefault(reg_num(r), (n, r))
                read_all.add(reg_num(r))
                for k in [k for k in pending if reg_num(k) == reg_num(r)]:
                    del pending[k]
            for w in writes:
                if w in pending and reg_num(w) not in IO_REGS | MULTIPLIER_REGS:
                    dead.append((pending[w], n, w))
                pending[w] = n
            written |= span
            if mnem in BRANCH_LIKE or mnem not in MNEMONICS:
                pending.clear()
        for first, second, reg in dead[:3]:
            warnings.append((first, "PRU-C003", f"{b['name']}: {reg} is written here and again at line {second} "
                             "with no read between, so the first value is discarded"))
        if len(dead) > 3:
            warnings.append((dead[3][0], "PRU-C003", f"{b['name']}: {len(dead) - 3} more discarded writes"))
        if b["kind"] == "Function":
            documented = " ".join(block_header(lines, b)).lower()
            inverse = {v: k for k, v in aliases.items()}
            unset = []
            for num, (n, r) in sorted(first_read.items()):
                alias = inverse.get(r, "")
                if num in written or num in IO_REGS or num in opaque:
                    continue
                if re.search(rf"\br{num}\b", documented) or (alias and alias.lower() in documented):
                    continue           # named in the header, so a documented input
                unset.append((n, f"r{num}"))
            if unset:
                warnings.append((unset[0][0], "PRU-C001", f"{b['name']}: reads {', '.join(r for _, r in unset)} without "
                                 "writing them; if the caller supplies them, name them in the header"))
            if c_abi:
                unsaved, ret_clobbered, ret_lost = callee_saved_violations(b["rows"], aliases, symbols)
                if unsaved:
                    warnings.append((b["line"], "PRU-C004", f"{b['name']}: writes "
                                     f"{', '.join(f'r{r}' for r in unsaved)} without saving the incoming value "
                                     "first and loading it back before every return; the C ABI needs r4-r13 "
                                     "preserved"))
                if ret_clobbered:
                    warnings.append((b["line"], "PRU-C004", f"{b['name']}: writes r3.w2 (a nested call) without "
                                     "saving the return address first"))
                if ret_lost:
                    warnings.append((b["line"], "PRU-C004", f"{b['name']}: returns through r3.w2 after a nested "
                                     "call without writing the saved return address back"))
    return warnings


def macro_checks(files, unresolved):
    """PRU-M001 undefined macro, PRU-M003 recursion, PRU-M004 conflicting redefinition, PRU-M005 include."""
    warnings, defs, uses = [], {}, []
    root = files[0][0]
    for p, parsed in files:
        for b in find_blocks(parsed[0]):
            if b["kind"] == "Macro":
                body = tuple(re.sub(r"\s+", " ", strip_comment(r[4]).strip()) for r in b["rows"])
                called = {r[2] for r in b["rows"] if r[2] and not r[2].startswith(".") and r[2] not in MNEMONICS}
                defs.setdefault(b["name"].upper(), []).append((p, b["line"], body, called))
        if p == root:
            uses = [(r[0], r[2]) for r in parsed[0] if r[2] and IDENT_RE.match(r[2])
                    and r[2] not in MNEMONICS and r[2] not in KNOWN_NON_MACRO]
    for p, n, inc in unresolved:
        where = "" if p == root else f" (included from {os.path.basename(p)})"
        warnings.append((n if p == root else 0, "PRU-M005", f"include '{inc}'{where} was not found; macro and "
                         "register checks may be incomplete (add its folder with -I)"))
    if not unresolved:
        for n, name in uses:
            if name not in defs:
                warnings.append((n, "PRU-M001", f"'{name.lower()}' is not an instruction and no macro of that "
                                 "name is defined in this file or its includes"))
    for name, occ in defs.items():
        if len({o[2] for o in occ}) > 1:
            for p, line, _, _ in occ[1:]:
                if p == root:
                    warnings.append((line, "PRU-M004", f"macro '{name.lower()}' redefined with a different body "
                                     f"(first at {os.path.basename(occ[0][0])}:{occ[0][1]})"))

    def reaches(start, target, seen):
        for callee in defs[start][0][3] if start in defs else ():
            if callee == target:
                return True
            if callee not in seen:
                seen.add(callee)
                if reaches(callee, target, seen):
                    return True
        return False

    for name, occ in defs.items():
        if occ[0][0] == root and reaches(name, name, {name}):
            warnings.append((occ[0][1], "PRU-M003", f"macro '{name.lower()}' invokes itself; macro expansion "
                             "has no base case"))
    return warnings


def lint(path, openpru=False, search=(), c_abi=False):
    files, unresolved = walk_includes(path, search)
    rows, symbols, aliases, labels, externs, col1, includes, lines, timeline, underscored = files[0][1]
    inherited = {}
    for _, (_r, inc_symbols, inc_aliases, inc_labels, inc_externs, *_rest) in files[1:]:
        labels = labels | inc_labels | inc_externs
        for name, value in inc_symbols.items():
            symbols.setdefault(name, value)
            inherited.setdefault(name, value)
        for name, value in inc_aliases.items():
            aliases.setdefault(name, value)
    errors, warnings = [], []
    opaque = {reg_num(reg_tokens(r[3][0])[0]) for _, parsed in files for r in parsed[0]
              if r[2] == ".SASSIGN" and len(r[3]) > 0 and reg_tokens(r[3][0])}
    blocks = find_blocks(rows)
    warnings.extend(register_checks(lines, blocks, aliases, symbols, opaque, c_abi))
    warnings.extend(macro_checks(files, unresolved))
    if openpru:
        includes_raw = [(n, ops[0].strip('"')) for n, _, m, ops, _ in rows
                        if m in (".INCLUDE", ".COPY") and ops]
        warnings.extend(style_checks(lines, includes_raw))
        warnings.extend(header_checks(lines, blocks))
    has_end = False

    def resolve_alias(op):
        return aliases.get(op.lstrip("&"), op)

    def symbols_at(n):
        """Constants as defined at line n, so a name reassigned later in the file doesn't change earlier checks."""
        current = {}
        for line_no, snapshot in timeline:
            if line_no > n:
                break
            current = snapshot
        return {**inherited, **current}

    for n in underscored:
        errors.append((n, "number", "underscore in a numeric literal; clpru rejects it (write 0xC3A59E17)"))

    prev = None
    for n, label, mnem, operands, raw in rows:
        if n in col1:
            errors.append((n, "column1", f"'{mnem}' is in column 1 and will be read as a label; indent it"))
        if label and mnem != ".MACRO" and label.upper() in RESERVED_LABELS:
            errors.append((n, "reserved", f"label '{label}' is a reserved identifier in clpru; rename it (for example "
                           f"{label.lower()}_top)"))
        if not mnem or mnem.startswith("."):
            continue
        symbols = symbols_at(n)
        ops = [resolve_alias(o) for o in operands]
        if mnem == "XIN" and len(ops) >= 3 and evaluate(ops[0], symbols) == MAC_ID and prev is not None:
            hit = reg_access(prev[0], [resolve_alias(o) for o in prev[1]], symbols_at(prev[2]))[2] & MAC_OPERAND_REGS
            if hit:
                warnings.append((prev[2], "PRU-X001", f"r{min(hit)} is written on the instruction right before the MAC "
                                 f"read at line {n}; the MAC samples r28/r29 one instruction late, so it multiplies "
                                 "the old value. Put another instruction between them"))
        prev = (mnem, operands, n)

        if mnem == "HALT" or (mnem == "JMP" and ops and ops[0].lower() == "r3.w2") or mnem == "RET":
            has_end = True

        for op in ops:
            for m in REG_RE.finditer(op):
                reg, kind, idx = int(m.group(1)), m.group(2), m.group(3)
                limit = {"b": 3, "w": 2, "t": 31}
                if reg > 31:
                    errors.append((n, "register", f"r{reg} does not exist (r0-r31)"))
                elif kind and int(idx) > limit[kind.lower()]:
                    errors.append((n, "register", f"{m.group(0).lstrip('&')}: .{kind}{idx} is out of range"))
            c = CONST_RE.match(op)
            if c and int(c.group(1)) > 31:
                errors.append((n, "register", f"{op} does not exist (C0-C31)"))

        rule_ops = ops
        if mnem in ("CLR", "SET", "QBBS", "QBBC") and len(ops) == 2:
            rule_ops = [ops[0], ops[0], ops[1]] if mnem in ("CLR", "SET") else [ops[0], ops[1], None]
        for idx, lo, hi, desc in RANGES.get(mnem, []):
            if idx < len(rule_ops) and rule_ops[idx] is not None:
                value = evaluate(rule_ops[idx], symbols)
                if value is not None and not lo <= value <= hi:
                    errors.append((n, "immediate", f"{mnem} operand {idx + 1} = {value} is outside {desc}"))

        if mnem in QUICK_BRANCHES or mnem == "LOOP":
            target = ops[0] if ops else ""
            if target and IDENT_RE.match(target) and target not in labels:
                errors.append((n, "label", f"{mnem} target '{target}' is not defined in this file"))
        elif mnem in ("JMP", "CALL", "JAL"):
            target = ops[1] if mnem == "JAL" and len(ops) > 1 else (ops[0] if ops else "")
            if target and IDENT_RE.match(target) and not REG_RE.fullmatch(target) \
                    and target not in labels and target not in externs:
                warnings.append((n, "extern", f"{mnem} target '{target}' is not defined here or declared .global/.ref"))

        if mnem in ("LDI", "MOV") and len(ops) == 2 and ops[0].lower().startswith("r31"):
            value = evaluate(ops[1], symbols)
            if value is not None and value < 64 and not value & 0x20:
                warnings.append((n, "r31event", f"write of {value:#x} to R31 lacks bit 5 (0x20) and raises no event"))

    instructions = [r for r in rows if r[2] and not r[2].startswith(".")]
    if instructions and instructions[-1][2] in ("QBA", "JMP"):
        has_end = True  # ends in an unconditional jump, e.g. an intentional forever loop
    if not has_end:
        warnings.append((0, "nohalt", "no HALT and no return (JMP r3.w2 / RET) found; check the program can't run off the end"))
    return errors, warnings


def main(argv):
    args, search, c_abi, paths = argv[1:], [], False, []
    if "-h" in args or "--help" in args:
        print(__doc__.strip())
        return 0
    openpru = "--openpru" in args
    usage = "\n".join(__doc__.strip().splitlines()[2:4])
    i = 0
    while i < len(args):
        if args[i] == "-I":
            if i + 1 >= len(args):
                print(f"-I needs a folder\n{usage}", file=sys.stderr)
                return 2
            search.append(args[i + 1])
            i += 1
        elif args[i] in ("--c-abi", "--openpru"):
            c_abi = c_abi or args[i] == "--c-abi"
        elif args[i].startswith("-"):
            print(f"unknown option {args[i]}\n{usage}", file=sys.stderr)
            return 2
        else:
            paths.append(args[i])
        i += 1
    if not paths:
        print(usage, file=sys.stderr)
        return 2
    total_errors = 0
    for path in paths:
        try:
            errors, warnings = lint(path, openpru, search, c_abi)
        except OSError as exc:
            print(f"{path}: cannot read ({exc.strerror})")
            total_errors += 1
            continue
        for n, code, msg in sorted(errors):
            print(f"{path}:{n}: error [{code}] {msg}")
        for n, code, msg in sorted(warnings):
            print(f"{path}:{n}: warning [{code}] {msg}")
        total_errors += len(errors)
        print(f"{path}: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
