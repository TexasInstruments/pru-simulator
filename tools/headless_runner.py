#!/usr/bin/env python3
"""Deterministic, CI-oriented command-line runner for the PRU simulator.

The runner deliberately emits exactly one JSON object on stdout.  Diagnostics
belong in that object so callers never have to scrape human-readable output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from simulator import Simulator  # noqa: E402


EXIT_OK = 0
EXIT_INPUT = 2
EXIT_UNMET = 3
EXIT_INTERNAL = 4
_CORES = ("pru0", "pru1", "rtu0")


class JSONArgumentParser(argparse.ArgumentParser):
    """Raise argument errors so ``main`` can preserve JSON-only stdout."""

    def error(self, message: str) -> None:
        raise ValueError(message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        raise ValueError(message or self.format_help())

    def _print_message(self, message: str | None, file=None) -> None:
        # argparse otherwise writes usage/help before main can emit its JSON.
        return None


def _parser() -> argparse.ArgumentParser:
    parser = JSONArgumentParser(
        description="Run PRU assembly or a TI PRU ELF and emit deterministic JSON."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--assembly", metavar="PATH", help="assembly source file")
    source.add_argument("--elf", metavar="PATH", help="TI PRU ELF32 .out file")
    parser.add_argument("--core", choices=_CORES, default="pru0")
    parser.add_argument(
        "--until",
        default="halt",
        metavar="CONDITION",
        help=("halt (default), cycles>=N, reg:rN==V, reg:rN!=V, "
              "mem32:ADDR==V, mem32:ADDR!=V, gpi:N==V, or gpo:N==V"),
    )
    parser.add_argument("--max-steps", type=int, default=100_000)
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument("--include", action="append", default=[], metavar="DIR")
    parser.add_argument("--config", default=str(_ROOT / "memory.cfg"), metavar="PATH")
    parser.add_argument(
        "--json", action="store_true",
        help="explicitly request JSON (stdout is JSON even when omitted)",
    )
    return parser


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _base_payload(args: argparse.Namespace | None = None) -> dict:
    payload = {
        "schema_version": 1,
        "success": False,
        "reason": "input_error",
        "errors": [],
    }
    if args is not None:
        path = args.assembly or args.elf
        payload.update({
            "core": args.core,
            "condition": args.until,
            "input": {
                "format": "assembly" if args.assembly else "elf",
                "path": str(path),
            },
            "limits": {
                "max_cycles": args.max_cycles,
                "max_steps": args.max_steps,
            },
        })
    return payload


def _parse_int(text: str) -> int:
    return int(text.strip(), 0)


def _comparison(actual: Callable[[], int], operator: str, expected: int) -> Callable[[], bool]:
    if operator == "==":
        return lambda: actual() == expected
    return lambda: actual() != expected


def _compile_condition(sim: Simulator, core: str, expression: str) -> Callable[[], bool]:
    expression = expression.strip()
    pru = sim.cores[core]
    if expression == "halt":
        return lambda: pru.halted

    match = re.fullmatch(r"cycles>=(0[xX][0-9a-fA-F]+|\d+)", expression)
    if match:
        target = _parse_int(match.group(1))
        return lambda: pru.counters.cycles >= target

    match = re.fullmatch(
        r"reg:r(\d+)(==|!=)([-+]?(?:0[xX][0-9a-fA-F]+|\d+))", expression,
        re.IGNORECASE,
    )
    if match:
        index = int(match.group(1))
        if not 0 <= index < 32:
            raise ValueError(f"register index out of range in condition: {expression}")
        return _comparison(
            lambda: pru.registers.read_full(index), match.group(2), _parse_int(match.group(3))
        )

    match = re.fullmatch(
        r"mem32:((?:0[xX][0-9a-fA-F]+|\d+))(==|!=)([-+]?(?:0[xX][0-9a-fA-F]+|\d+))",
        expression,
        re.IGNORECASE,
    )
    if match:
        address = _parse_int(match.group(1))
        expected = _parse_int(match.group(3)) & 0xFFFFFFFF
        return _comparison(
            lambda: int.from_bytes(sim.memory_read(address, 4), "little"),
            match.group(2), expected,
        )

    match = re.fullmatch(r"(gpi|gpo):(\d+)==([01])", expression, re.IGNORECASE)
    if match:
        bank = f"{match.group(1).lower()}_pins"
        pin = int(match.group(2))
        if not 0 <= pin < 20:
            raise ValueError(f"pin index out of range in condition: {expression}")
        expected = int(match.group(3))
        return lambda: sim.io(core)[bank][pin] == expected

    raise ValueError(f"unsupported condition: {expression}")


def _state(sim: Simulator, core: str, steps: int) -> dict:
    status = sim.status()[core]
    return {
        "cycles": status["cycles"],
        "halted": status["halted"],
        "instruction_count": status["instruction_count"],
        "pc": status["pc"],
        "registers": [f"0x{value:08x}" for value in sim.registers(core)],
        "stall_cycles": status["stall_cycles"],
        "steps": steps,
    }


def run(args: argparse.Namespace) -> tuple[int, dict]:
    payload = _base_payload(args)
    if args.max_steps <= 0:
        payload["errors"] = ["--max-steps must be greater than zero"]
        return EXIT_INPUT, payload
    if args.max_cycles is not None and args.max_cycles <= 0:
        payload["errors"] = ["--max-cycles must be greater than zero"]
        return EXIT_INPUT, payload

    input_path = Path(args.assembly or args.elf)
    if not input_path.is_file():
        payload["errors"] = [f"input file does not exist: {input_path}"]
        return EXIT_INPUT, payload

    try:
        sim = Simulator(args.config)
        condition_met = _compile_condition(sim, args.core, args.until)
        if args.assembly:
            include_paths = [str(input_path.resolve().parent)]
            include_paths.extend(str(Path(item).resolve()) for item in args.include)
            errors = sim.load(args.core, input_path.read_text(encoding="utf-8"), include_paths)
        else:
            errors = sim.load_elf(args.core, input_path.read_bytes())
    except (OSError, UnicodeError, ValueError, KeyError) as exc:
        payload["errors"] = [str(exc)]
        return EXIT_INPUT, payload

    if errors:
        payload["reason"] = "load_error"
        payload["errors"] = list(errors)
        return EXIT_INPUT, payload

    steps = 0
    reason = "condition_met" if condition_met() else None
    while reason is None:
        before = sim.status()[args.core]
        sim.step(args.core, 1)
        steps += 1
        after = sim.status()[args.core]

        if args.max_cycles is not None and after["cycles"] > args.max_cycles:
            reason = "cycle_budget_exceeded"
            break
        if condition_met():
            reason = "halted" if args.until == "halt" else "condition_met"
            break
        if after["halted"]:
            reason = "condition_unmet"
            break
        if (after["pc"], after["cycles"], after["instruction_count"]) == (
            before["pc"], before["cycles"], before["instruction_count"]
        ):
            reason = "condition_unmet"
            break
        if steps >= args.max_steps:
            reason = "step_budget_exceeded"
            break

    success = reason in ("halted", "condition_met")
    payload.update({
        "success": success,
        "reason": reason,
        "state": _state(sim, args.core, steps),
    })
    return (EXIT_OK if success else EXIT_UNMET), payload


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except (ValueError, SystemExit) as exc:
        payload = _base_payload()
        message = str(exc) if str(exc) else "invalid command line"
        payload["errors"] = [message]
        _emit(payload)
        return EXIT_INPUT

    try:
        exit_code, payload = run(args)
    except Exception as exc:  # noqa: BLE001 - convert unexpected failures to JSON for CI
        payload = _base_payload(args)
        payload["reason"] = "internal_error"
        payload["errors"] = [f"{type(exc).__name__}: {exc}"]
        exit_code = EXIT_INTERNAL
    _emit(payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
