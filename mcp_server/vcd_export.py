"""Deterministic VCD export for PRU GPIO waveforms.

The exporter deliberately omits wall-clock metadata and uses timestamps relative
to the beginning of a capture.  This makes the same loaded program and simulator
configuration produce byte-identical output, suitable for CI comparisons.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def parse_pin_selection(selection: str) -> list[int]:
    """Parse comma-separated pins and inclusive ranges into sorted pin numbers."""
    pins: set[int] = set()
    if not selection.strip():
        raise ValueError("Pin selection must not be empty")
    for item in selection.split(","):
        item = item.strip()
        if not item:
            raise ValueError(f"Invalid pin selection: {selection!r}")
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start, end = int(start_text, 0), int(end_text, 0)
            if start > end:
                raise ValueError(f"Pin range must be ascending: {item!r}")
            pins.update(range(start, end + 1))
        else:
            pins.add(int(item, 0))
    if min(pins) < 0 or max(pins) > 19:
        raise ValueError("PRU GPIO pins must be in the range 0-19")
    return sorted(pins)


def _vcd_identifiers(count: int) -> list[str]:
    """Return compact printable VCD identifiers (more than enough for 40 pins)."""
    alphabet = [chr(code) for code in range(33, 127)]
    if count > len(alphabet):
        raise ValueError("Too many VCD signals")
    return alphabet[:count]


def export_pin_waveform(sim, core: str, path: str, max_steps: int = 10000,
                        pins: str = "0-19", include_gpi: bool = False) -> dict:
    """Run a loaded PRU core and export selected GPIO pins as deterministic VCD.

    Values are sampled before execution and after every retired instruction.
    Timestamps are relative to the capture start while ``start_cycle`` and
    ``end_cycle`` retain the corresponding absolute simulator counter values.

    ``complete`` is true only when the core halts or reaches the end of its
    loaded program.  ``success`` mirrors ``complete``: a valid partial VCD is
    still written when the step budget is exhausted, but that capture must not
    be reported as a successful complete run.
    """
    if max_steps < 0:
        raise ValueError("max_steps must be non-negative")

    selected_pins = parse_pin_selection(pins)
    pru = sim._get_core(core)
    clock_mhz = sim._pru1_clock_mhz if core == "pru1" else sim._pru_clock_mhz
    cycle_period_ps = round(1_000_000 / clock_mhz)
    if cycle_period_ps <= 0:
        raise ValueError(f"Invalid PRU clock frequency: {clock_mhz}")

    kinds = ["gpo"] + (["gpi"] if include_gpi else [])
    signals = [(kind, pin) for kind in kinds for pin in selected_pins]
    identifiers = _vcd_identifiers(len(signals))
    signal_ids = dict(zip(signals, identifiers))

    lines = [
        "$version TI PRU Simulator deterministic VCD exporter $end",
        "$timescale 1ps $end",
        f"$scope module {core} $end",
    ]
    metadata_signals = []
    for kind, pin in signals:
        identifier = signal_ids[(kind, pin)]
        name = f"{kind}_{pin}"
        lines.append(f"$var wire 1 {identifier} {name} $end")
        metadata_signals.append({"name": name, "kind": kind, "pin": pin})
    lines.extend(["$upscope $end", "$enddefinitions $end", "#0"])

    def snapshot() -> dict[tuple[str, int], int]:
        io_state = sim.io(core)
        return {
            (kind, pin): io_state[f"{kind}_pins"][pin]
            for kind, pin in signals
        }

    previous = snapshot()
    for signal in signals:
        lines.append(f"{previous[signal]}{signal_ids[signal]}")

    start_cycle = pru.counters.cycles
    steps_executed = 0
    transition_count = 0
    for _ in range(max_steps):
        if pru.halted or pru.pc >= len(pru.instructions):
            break
        before_cycle = pru.counters.cycles
        pru.step()
        if pru.counters.cycles == before_cycle:
            break
        steps_executed += 1
        current = snapshot()
        changes = [signal for signal in signals if current[signal] != previous[signal]]
        if changes:
            timestamp = (pru.counters.cycles - start_cycle) * cycle_period_ps
            lines.append(f"#{timestamp}")
            for signal in changes:
                lines.append(f"{current[signal]}{signal_ids[signal]}")
            transition_count += len(changes)
        previous = current

    output = ("\n".join(lines) + "\n").encode("ascii")
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output)

    complete = pru.halted or pru.pc >= len(pru.instructions)
    if pru.halted:
        stop_reason = "halted"
    elif pru.pc >= len(pru.instructions):
        stop_reason = "end_of_program"
    else:
        stop_reason = "max_steps"

    return {
        "success": complete,
        "complete": complete,
        "path": str(output_path),
        "core": core,
        "clock_mhz": clock_mhz,
        "timescale": "1ps",
        "cycle_period_ps": cycle_period_ps,
        "start_cycle": start_cycle,
        "end_cycle": pru.counters.cycles,
        "steps_executed": steps_executed,
        "halted": pru.halted,
        "stop_reason": stop_reason,
        "signals": metadata_signals,
        "transition_count": transition_count,
        "bytes": len(output),
        "sha256": hashlib.sha256(output).hexdigest(),
    }
