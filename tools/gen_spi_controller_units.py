"""Generate the mechanical blocks of source/spi/spi_controller.asm.

Two blocks, between ``; >>> generated: <name>`` and ``; <<< generated: <name>``:

* ``inline units``: one ``m_unit`` variant per (HALF_K, sample offset) for
  HALF_K 3..5. Slot 0 = L, slot HALF_K = C, slot S_OFF = S; P, A, B take the
  first free slots in that order, nops fill the rest. MP = 1 when B runs
  before S (B then merges the previous unit's sample).
* ``unit list``: the 152 data units plus tail units 152..159 (154+ only when
  Q is deep enough). Unit m's P prepares frame bit m + 1; units 151, 152,
  153+ load the tail control words V151/V152/V153 into r1.w1 instead.

``--write`` rewrites the blocks in place; ``--check`` (default) exits 1 if the
file differs. tests/test_spi_controller.py runs the check.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASM = ROOT / "source" / "spi" / "spi_controller.asm"

_L = "    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)"
_C = "    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge"
_S = "    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)"
_P = "    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5"
_A = "    lsl  acc, acc, 1                  ; A"
_B = "    or   acc, acc, r3                 ; B  merge latest sample"
_N = "    nop"

#: Tail units after bit 151: 152 and 153 always, 154 + j when Q >= j + 1.
TAIL_MAX = 8


def inline_variants() -> str:
    out = []
    for k in (3, 4, 5):
        for s in range(1, 2 * k):
            if s == k:
                continue
            free = [_P, _A, _B] + [_N] * (2 * k - 6)
            slots, bslot = [], None
            for i in range(2 * k):
                if i == 0:
                    slots.append(_L)
                elif i == k:
                    slots.append(_C)
                elif i == s:
                    slots.append(_S)
                else:
                    item = free.pop(0)
                    if item is _B:
                        bslot = i
                    slots.append(item)
            out.append(f".if BODY_SEL == {16 * k + s}               ; k={k}, s={s}")
            out.append(f"MP .set {1 if s > bslot else 0}")
            out.append("m_unit .macro xop, xdst, xsrc, xsh, acc")
            out += slots
            out += [".endm", ".endif"]
    return "\n".join(out)


def units() -> str:
    out = []
    for m in range(152 + TAIL_MAX):
        if m < 151:
            n = m + 1
            byte, b = divmod(n, 8)
            b = 7 - b
            src = f"r{5 + byte // 4}.b{byte % 4}"
            op, sh = ("lsr", b - 5) if b >= 5 else ("lsl", 5 - b)
            p = f"{op}, r1.b0, {src}, {sh}"
        elif m == 151:
            p = "or, r1.w1, r24.w0, 0"
        elif m == 152:
            p = "or, r1.w1, r24.w2, 0"
        else:
            p = "or, r1.w1, r25.w0, 0"
        line = f"    m_unit {p}, r{10 + m // 24}"
        if m >= 154:
            out += [f".if Q >= {m - 153}", line, ".endif"]
        elif m <= 1:
            out += [f"spi_unit{m}:", line]
        else:
            out.append(line)
    return "\n".join(out)


BLOCKS = {"inline units": inline_variants, "unit list": units}


def render(text: str) -> str:
    for name, fn in BLOCKS.items():
        pat = re.compile(rf"(; >>> generated: {name}[^\n]*\n).*?(\n; <<< generated: {name})",
                         re.S)
        if not pat.search(text):
            raise ValueError(f"markers for block {name!r} not found")
        text = pat.sub(lambda m: m.group(1) + fn() + m.group(2), text)
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="rewrite the blocks in place")
    args = ap.parse_args(argv)
    text = ASM.read_text(encoding="utf-8")
    new = render(text)
    if args.write:
        ASM.write_text(new, encoding="utf-8")
        return 0
    if new != text:
        print(f"{ASM} differs from the generator; run with --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
