"""Generate the frame bodies of source/spi/spi_peripheral.asm (PRU1 SPI peripheral).

Two blocks, between ``; >>> generated: <name>`` and ``; <<< generated: <name>``:
``frame mode 1`` and ``frame mode 0``. Each is the fully unrolled 152-bit frame
for one SPI mode: per bit one launch-edge poll and one capture-edge poll (see
the asm header for the poll forms), the MISO drive, and "slot" work (MOSI
gather, command decode, TX fetch) scheduled into the gaps between polls.

Scheduling model (design target K = core cycles per CLK half period).
The code does not depend on K; K only decides where the slot work goes, so
that the code also keeps up at the fastest rate. A poll whose loop has period
g detects an edge 0..g-1 cycles after it becomes visible when PRU1 waits. If
PRU1 arrives late, it detects the edge as late as it arrives. The level after
the edge lasts K cycles, so the lateness bound G must stay <= K - 1:

    G_next = max(g_next - 1, G + w - K)

where w is the time from one detection read to the next poll's first read
(poll exit + MISO drive + slot work). Every op is placed in the earliest slot
where this holds, earliest deadline first. The generator fails if an op
misses its deadline. Cycle counts are checked by single-stepping in
tests/test_spi_peripheral.py.

``--write`` rewrites the blocks in place; the default mode is a check, which
exits 1 if the file differs. tests/test_spi_peripheral.py runs the check.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASM = ROOT / "source" / "spi" / "spi_peripheral.asm"

K_TARGET = 3            # design target: CLK half period in core cycles
FRAME_BITS = 152
FRAME_BYTES = 19
FETCH_COST = 3          # lbbo 4 B from PRU1 DMEM: 1 + 2 stall (DRAM read_latency 2)

# Snapshot slot of bit i (0 = MSB first) of a byte: (register a/b, byte field).
# Chosen so that GATHER below yields the byte MSB first (verified by
# gather_check()).
SNAP_SLOT = [("b", 3), ("a", 3), ("b", 1), ("a", 1),
             ("b", 2), ("a", 2), ("b", 0), ("a", 0)]
SNAP_REGS = {0: ("r2", "r3"), 1: ("r4", "r5")}      # byte parity -> (a, b)


def _gather_value(a: int, b: int) -> int:
    m = 0xFFFFFFFF
    t = ((b << 1) & m) ^ a
    t ^= t >> 14
    t ^= t >> 4
    return (t >> 2) & 0xFF


def gather_check() -> dict[int, int]:
    """Return {CLK-bit garbage: xor correction} after checking all 256 bytes.

    Each snapshot field holds MOSI at bit 2. In mode 1 the capture snapshot
    has CLK = 0 (field = MOSI << 2); in mode 0 it has CLK = 1 (field gets bit
    1 set). The gather is linear (xor/shift), so the garbage only xors a
    constant into the result.
    """
    out = {}
    for garbage in (0, 2):
        def run(byte):
            a = b = 0
            for i, (reg, f) in enumerate(SNAP_SLOT):
                v = (((byte >> (7 - i)) & 1) << 2) | garbage
                if reg == "a":
                    a |= v << (8 * f)
                else:
                    b |= v << (8 * f)
            return _gather_value(a, b)
        corr = run(0)
        if any(run(x) ^ corr != x for x in range(256)):
            raise AssertionError("gather does not reproduce every byte")
        out[garbage] = corr
    return out


def _snap(m: int, i: int) -> str:
    a, b = SNAP_REGS[m % 2]
    reg, f = SNAP_SLOT[i]
    return f"{a if reg == 'a' else b}.b{f}"


def _rx_dest(m: int) -> str:
    if m == 0:
        return "r18.b0"
    if m == 1:
        return "r18.b1"
    j = m - 3
    return f"r{14 + j // 4}.b{j % 4}"


def _tx_field(n: int) -> str:
    j = n // 8 - 3
    return f"r{10 + j // 4}.b{j % 4}"


def _drive(n: int) -> str:
    return (f"    lsl  r30.w0, {_tx_field(n)}, {4 + n % 8:<2}"
            f"        ; MISO bit {n}: data bit {7 - n % 8} -> GPO11 (one R30 write)")


@dataclass
class Op:
    name: str
    lines: list[str]
    cost: int
    release: int = 0                 # first half index it may go in
    deadline: int = 10 ** 9          # last half index it may go in
    after: list[int] = field(default_factory=list)
    half: int | None = None


@dataclass
class Half:
    bit: int
    kind: str                        # "L" launch or "C" capture
    poll: list[str]
    exit: int                        # cycles from detection read to next instr
    g: int                           # poll loop period
    drive: str | None = None
    ops: list[Op] = field(default_factory=list)


def _halves(mode: int) -> list[Half]:
    hs: list[Half] = []
    for n in range(FRAME_BITS):
        m, i = divmod(n, 8)
        idle = m == 2
        if mode == 1:
            hs.append(Half(n, "L", [
                f"m1_l{n}:",
                "    and  r1.b0, r31.b0, 0x42          ; CLK|/CS",
                f"    qbeq m1_g{n}, r1.b0, 2            ; CLK high, /CS low: launch edge",
                f"    qbeq m1_l{n}, r1.b0, 0            ; CLK low, /CS low: wait",
                "    jmp  frame_abort                   ; /CS high",
                f"m1_g{n}:"], exit=2, g=3,
                drive=_drive(n) if m >= 3 else None))
            if idle:
                hs.append(Half(n, "C", [
                    f"m1_c{n}:",
                    f"    qbbs m1_c{n}, r31, 1              ; idle byte: wait CLK low"],
                    exit=1, g=1))
            else:
                s = _snap(m, i)
                hs.append(Half(n, "C", [
                    f"m1_c{n}:",
                    f"    and  {s}, r31.b0, 0x46{'':<{11 - len(s)}}; snapshot CLK|MOSI|/CS",
                    f"    qbbs m1_c{n}, {s}, 1{'':<{12 - len(s)}}; CLK high: wait (capture = falling)"],
                    exit=2, g=2))
        else:
            if idle:
                hs.append(Half(n, "C", [
                    f"m0_c{n}:",
                    f"    qbbs m0_g{n}, r31, 1              ; idle byte: CLK high = capture edge",
                    f"    qbbc m0_c{n}, r31, 6              ; /CS low: wait",
                    "    jmp  frame_abort                   ; /CS high",
                    f"m0_g{n}:"], exit=1, g=2))
            else:
                s = _snap(m, i)
                hs.append(Half(n, "C", [
                    f"m0_c{n}:",
                    f"    and  {s}, r31.b0, 0x46{'':<{11 - len(s)}}; snapshot CLK|MOSI|/CS",
                    f"    qbbs m0_g{n}, {s}, 1{'':<{12 - len(s)}}; CLK high: capture edge",
                    f"    qbbc m0_c{n}, {s}, 6{'':<{12 - len(s)}}; /CS low: wait",
                    "    jmp  frame_abort                   ; /CS high",
                    f"m0_g{n}:"], exit=2, g=3))
            if n < FRAME_BITS - 1:
                hs.append(Half(n, "L", [
                    f"m0_l{n}:",
                    f"    qbbs m0_l{n}, r31, 1              ; wait CLK low (launch = falling)"],
                    exit=1, g=1,
                    drive=_drive(n + 1) if (n + 1) // 8 >= 3 else None))
    return hs


def _ops(mode: int, hs: list[Half]) -> list[Op]:
    idx_c = {h.bit: j for j, h in enumerate(hs) if h.kind == "C"}
    drive_at = {}
    for j, h in enumerate(hs):
        if h.drive:
            n = h.bit if mode == 1 else h.bit + 1
            drive_at[n] = j
    corr = gather_check()[0 if mode == 1 else 2]
    ops: list[Op] = []

    def add(op: Op, chain: bool = True) -> int:
        if chain and ops:
            op.after.append(len(ops) - 1)
        ops.append(op)
        return len(ops) - 1

    def gather(m: int) -> int:
        a, b = SNAP_REGS[m % 2]
        dest = _rx_dest(m)
        rel = idx_c[8 * m + 7]
        dl = idx_c[8 * (m + 2)] - 1 if 8 * (m + 2) < FRAME_BITS else 10 ** 9
        lines = [
            f"    lsl  r6, {b}, 1                    ; gather byte {m} (1/7)",
            f"    xor  r6, r6, {a}",
            "    lsr  r7, r6, 14",
            "    xor  r6, r6, r7",
            "    lsr  r7, r6, 4",
            "    xor  r6, r6, r7",
            f"    lsr  {dest}, r6, 2{'':<{17 - len(dest)}}; byte {m} done"]
        if m < 2 and corr:
            lines.append(f"    xor  {dest}, {dest}, 0x{corr:02X}{'':<{9 - len(dest)}}"
                         f"; mode 0: CLK=1 in the snapshot inverts the byte")
        last = None
        for j, ln in enumerate(lines):
            last = add(Op(f"g{m}.{j}", [ln], 1, release=rel, deadline=dl))
        return last

    gather(0)
    for ln in ["    lsr  r25, r18.b0, 1                ; cmd >> 1: 1 for READ, R/W",
               "    xor  r25, r25, 1                   ; 0 iff the command reads",
               "    min  r25, r25, 1",
               "    sub  r24, r25, 1                   ; M = 0xFFFFFFFF (read) or 0"]:
        add(Op("dec", [ln], 1))
    g1_last = gather(1)
    for ln in ["    lsl  r19, r18.b1, 4                ; addr * 16",
               "    xor  r19, r19, r26                 ; ^0x800: addr<0x80 -> P_TX block, else <0x800",
               "    and  r19, r19, r24                 ; no read command -> 0",
               "    max  r19, r19, r27                 ; below ZERO_BLK -> ZERO_BLK"]:
        add(Op("ptr", [ln], 1))
    fetch_last = None
    for j in range(4):
        first_bit = 24 + 32 * j
        dl = drive_at[first_bit] - 1
        fetch_last = add(Op(f"f{j}", [
            f"    lbbo &r{10 + j}, r19, {4 * j}, 4{'':<{14 - len(str(4 * j))}}"
            f"; TX bytes {4 * j}..{4 * j + 3} (fetch {j + 1}/4)"],
            FETCH_COST, deadline=dl), chain=False)
        ops[fetch_last].after.append(fetch_last - 1)
    # gathers of data bytes: chained to each other, not to the fetches
    prev_gather = None
    for m in range(3, FRAME_BYTES):
        first = len(ops)
        last = gather(m)
        ops[first].after = [prev_gather if prev_gather is not None else g1_last]
        prev_gather = last
    return ops



def schedule(mode: int, k: int = K_TARGET):
    hs = _halves(mode)
    ops = _ops(mode, hs)
    G = hs[0].g - 1
    for j, h in enumerate(hs):
        nxt_g = hs[j + 1].g if j + 1 < len(hs) else 1
        used = h.exit + (1 if h.drive else 0)
        while True:
            ready = [o for o in ops if o.half is None and o.release <= j
                     and all(ops[a].half is not None for a in o.after)]
            ready.sort(key=lambda o: o.deadline)
            placed = False
            for o in ready:
                if max(nxt_g - 1, G + used + o.cost - k) <= k - 1:
                    o.half = j
                    h.ops.append(o)
                    used += o.cost
                    placed = True
                    break
            if not placed:
                break
        G = max(nxt_g - 1, G + used - k)
        if G > k - 1:
            raise ValueError(f"mode {mode}: lateness {G} after half {j} exceeds {k - 1}")
        for o in ops:
            if o.half is None and o.deadline <= j:
                raise ValueError(f"mode {mode}: op {o.name} missed its deadline (half {j})")
    tail = [o for o in ops if o.half is None]
    return hs, tail


def frame_block(mode: int) -> str:
    hs, tail = schedule(mode)
    out = []
    for h in hs:
        out.extend(h.poll)
        if h.drive:
            out.append(h.drive)
        for o in h.ops:
            out.extend(o.lines)
    out.append("; after the last capture edge: remaining gathers")
    for o in tail:
        out.extend(o.lines)
    out.append("    jmp  frame_end")
    return "\n".join(out)


def stats(mode: int) -> dict:
    """Static per-bit costs of the generated code (polls, drive, slot ops)."""
    hs, tail = schedule(mode)
    per_bit: dict[int, int] = {}
    for h in hs:
        per_bit[h.bit] = per_bit.get(h.bit, 0) + h.exit + (1 if h.drive else 0)
    slot = {}
    for h in hs:
        slot[h.bit] = slot.get(h.bit, 0) + sum(o.cost for o in h.ops)
    return {"fixed": per_bit, "slot": slot, "tail_ops": len(tail)}


BLOCKS = {"frame mode 1": lambda: frame_block(1), "frame mode 0": lambda: frame_block(0)}


def render(text: str) -> str:
    for name, fn in BLOCKS.items():
        pat = re.compile(rf"(; >>> generated: {name}[^\n]*\n).*?(; <<< generated: {name})",
                         re.S)
        if not pat.search(text):
            raise ValueError(f"markers for block {name!r} not found")
        text = pat.sub(lambda m: m.group(1) + fn() + "\n" + m.group(2), text)
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="rewrite the blocks in place")
    ap.add_argument("--stats", action="store_true", help="print per-bit static costs")
    args = ap.parse_args(argv)
    if args.stats:
        for mode in (1, 0):
            s = stats(mode)
            print(f"mode {mode}: fixed/bit {sorted(set(s['fixed'].values()))}, "
                  f"slot cycles per byte "
                  f"{[sum(s['slot'][8 * m + i] for i in range(8)) for m in range(19)]}, "
                  f"tail ops {s['tail_ops']}")
        return 0
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
