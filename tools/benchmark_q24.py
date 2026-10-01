"""Benchmark Q24 product grouping on the real PRU simulator model.

The benchmark compares the existing multiply-only, per-product truncation
shape with a two-product MAC accumulation.  It deliberately uses positive
magnitudes so the MAC experiment is not confused with the separate signed
inverse-Park problem.
"""

from __future__ import annotations

import argparse
import random

from core.pru_core import PRUCore
from mem.constant_table import ConstantTable
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from pru_io.io_port import IOPort
from xfr.xfr_bus import XFRBus


Q24_SHIFT = 24
# This pair has a discarded-fraction carry: floor(p1)+floor(p2) differs from
# floor(p1+p2) by one Q24 LSB.
OPERANDS = (5_433_013, 15_902_542, 2_530_830, 6_624_040)


def _make_core(source: str) -> PRUCore:
    memory = MemoryBus()
    memory.add_region(MemoryRegion("DRAM0", 0, 0x2000, 2, 1, 0))
    core = PRUCore("q24-benchmark", memory, XFRBus(), IOPort(), ConstantTable())
    errors = core.load_asm(source)
    if errors:
        raise RuntimeError("benchmark assembly failed: " + "; ".join(errors))
    return core


def _source(*, mac: bool, prescaled: bool) -> str:
    a, b, c, d = OPERANDS
    scale = "" if not prescaled else """
    lsl r2, r2, 8
    lsl r4, r4, 8
"""
    if mac:
        setup = """
    zero &r28, 8
    ldi r25, 1
    xout 0, &r25, 1
"""
        body = """
bench_start:
    mov r28, r2
    mov r29, r3
    xout 0, &r25, 1
    mov r28, r4
    mov r29, r5
    xout 0, &r25, 1
"""
        if prescaled:
            body += """
    xin 0, &r27, 4
    mov r10, r27
"""
        else:
            body += """
    xin 0, &r26, 8
    lsr r26, r26, 24
    lsl r27, r27, 8
    or r26, r26, r27
    mov r10, r26
"""
    else:
        setup = """
    ldi r25, 0
    xout 0, &r25, 1
"""
        def read_result(register: str) -> str:
            if prescaled:
                return f"""
    xin 0, &r27, 4
    mov {register}, r27
"""
            return f"""
    xin 0, &r26, 8
    lsr r26, r26, 24
    lsl r27, r27, 8
    or r26, r26, r27
    mov {register}, r26
"""
        body = """
bench_start:
    mov r28, r2
    mov r29, r3
    nop
""" + read_result("r10") + """
    mov r28, r4
    mov r29, r5
    nop
""" + read_result("r11") + """
    add r10, r10, r11
"""
    return f"""
    ldi32 r2, 0x{a:08X}
    ldi32 r3, 0x{b:08X}
    ldi32 r4, 0x{c:08X}
    ldi32 r5, 0x{d:08X}
{scale}{setup}{body}
bench_end:
    halt
"""


def _measure(*, mac: bool, prescaled: bool) -> dict[str, int]:
    core = _make_core(_source(mac=mac, prescaled=prescaled))
    labels = core._parser.labels
    while core.pc != labels["bench_start"]:
        core.step()
    start = (
        core.counters.cycles,
        core.counters.instruction_count,
        core.counters.stall_cycles,
    )
    while core.pc != labels["bench_end"]:
        core.step()
    end = (
        core.counters.cycles,
        core.counters.instruction_count,
        core.counters.stall_cycles,
    )
    return {
        "cycles": end[0] - start[0],
        "instructions": end[1] - start[1],
        "stalls": end[2] - start[2],
        "result": core.registers.read_full(10),
        "first_product": core.registers.read_full(11),
    }


def _trunc_product(a: int, b: int) -> int:
    sign = -1 if (a < 0) != (b < 0) else 1
    return sign * ((abs(a) * abs(b)) >> Q24_SHIFT)


def _trunc_sum(products: tuple[int, int]) -> int:
    value = sum(products)
    sign = -1 if value < 0 else 1
    return sign * (abs(value) >> Q24_SHIFT)


def _truncation_stats(samples: int) -> dict[str, int]:
    rng = random.Random(0xF0C)
    mismatches = 0
    max_abs_lsb = 0
    for _ in range(samples):
        a, b, c, d = (
            rng.randrange(-(1 << Q24_SHIFT), (1 << Q24_SHIFT) + 1)
            for _ in range(4)
        )
        per_product = _trunc_product(a, b) + _trunc_product(c, d)
        final_accumulation = _trunc_sum((a * b, c * d))
        delta = final_accumulation - per_product
        if delta:
            mismatches += 1
            max_abs_lsb = max(max_abs_lsb, abs(delta))
    return {"samples": samples, "mismatches": mismatches,
            "max_abs_lsb": max_abs_lsb}


def benchmark(*, samples: int = 100_000) -> dict[str, dict[str, int]]:
    """Return measured simulator counters and the truncation comparison."""
    return {
        "raw_multiply_only": _measure(mac=False, prescaled=False),
        "raw_mac_accumulate": _measure(mac=True, prescaled=False),
        "prescaled_multiply_only": _measure(mac=False, prescaled=True),
        "prescaled_mac_accumulate": _measure(mac=True, prescaled=True),
        "truncation": _truncation_stats(samples),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=100_000)
    args = parser.parse_args()
    result = benchmark(samples=args.samples)
    print("Q24 operands:", OPERANDS)
    for name in (
        "raw_multiply_only",
        "raw_mac_accumulate",
        "prescaled_multiply_only",
        "prescaled_mac_accumulate",
    ):
        values = result[name]
        print(
            f"{name:28} cycles={values['cycles']:2} "
            f"instructions={values['instructions']:2} "
            f"stalls={values['stalls']:2} result=0x{values['result']:08X}"
        )
    values = result["truncation"]
    print(
        f"truncation mismatches={values['mismatches']}/{values['samples']} "
        f"max_abs_lsb={values['max_abs_lsb']}"
    )


if __name__ == "__main__":
    main()
