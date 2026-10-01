"""Plot and verify a Signal Graph CSV exported by the PRU simulator.

Usage:
    python tools/plot_ssi_trace.py path/to/pru-trace.csv
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


REQUIRED_COLUMNS = {"step", "core", "gpo0", "gpi8"}


def load_trace(path: str | Path) -> list[dict[str, str]]:
    """Load a simulator CSV and validate the signals needed for SSI decoding."""
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError("trace is missing required columns: " + ", ".join(sorted(missing)))
        rows = list(reader)

    for row in rows:
        row["_step"] = int(row["step"])
        for name in ("gpo0", "gpi8"):
            row[name] = int(row[name])
        for name in ("gpi16",):
            if name in row:
                row[name] = int(row[name])
    return rows


def _clock_groups(rows: list[dict[str, str]], idle_gap: int = 500) -> list[list[dict[str, int]]]:
    rows = sorted(rows, key=lambda row: int(row.get("_step", row["step"])))
    if not rows:
        return []

    transitions: list[dict[str, int]] = []
    previous = int(rows[0]["gpo0"])
    for row in rows[1:]:
        clock = int(row["gpo0"])
        if clock != previous:
            transitions.append({
                "step": int(row.get("_step", row["step"])),
                "from": previous,
                "to": clock,
                "data": int(row["gpi8"]),
            })
        previous = clock

    groups: list[list[dict[str, int]]] = []
    for transition in transitions:
        if not groups or transition["step"] - groups[-1][-1]["step"] > idle_gap:
            groups.append([])
        groups[-1].append(transition)
    return groups


def decode_ssi_frames(
    rows: list[dict[str, str]], idle_gap: int = 500
) -> list[dict[str, int | str]]:
    """Decode 12-bit MSB-first SSI frames from PRU1 GPO0/GPI8 samples."""
    frames: list[dict[str, int | str]] = []
    for group in _clock_groups(rows, idle_gap):
        rising = [transition for transition in group if transition["from"] == 0 and transition["to"] == 1]
        if len(rising) < 12:
            continue
        bits = "".join(str(transition["data"]) for transition in rising[:12])
        frames.append({
            "start": group[0]["step"],
            "end": group[-1]["step"],
            "bits": bits,
            "value": int(bits, 2),
            "rising_edges": len(rising),
        })
    return frames


def _signal_values(rows: list[dict[str, str]], column: str) -> tuple[list[int], list[int]]:
    ordered = sorted(rows, key=lambda row: row["_step"])
    return [row["_step"] for row in ordered], [int(row[column]) for row in ordered]


def _mirror_mismatches(rows_by_core: dict[str, list[dict[str, str]]], left_core: str, left_signal: str,
                       right_core: str, right_signal: str) -> tuple[int, int]:
    left = {row["_step"]: int(row[left_signal]) for row in rows_by_core.get(left_core, [])}
    right = {row["_step"]: int(row[right_signal]) for row in rows_by_core.get(right_core, [])}
    common = left.keys() & right.keys()
    return sum(left[step] != right[step] for step in common), len(common)


def plot_trace(rows: list[dict[str, str]], frames: list[dict[str, int | str]], output: str | Path) -> None:
    rows_by_core = {}
    for row in rows:
        rows_by_core.setdefault(row["core"], []).append(row)

    frame_count = min(len(frames), 4)
    fig, axes = plt.subplots(1 + frame_count, 1, figsize=(15, 3 + frame_count * 2.4), squeeze=False)
    axes = axes[:, 0]

    colors = {"pru0": ("tab:blue", "tab:orange"), "pru1": ("tab:green", "tab:red")}
    for core, core_rows in sorted(rows_by_core.items()):
        clock_column = "gpo0" if core == "pru1" else "gpi16"
        data_column = "gpi8" if core == "pru1" else "gpo0"
        if clock_column not in core_rows[0]:
            continue
        steps, clock = _signal_values(core_rows, clock_column)
        axes[0].step(steps, [v + (0 if core == "pru1" else 2) for v in clock], where="post",
                     label=f"{core} {clock_column} clock", color=colors.get(core, ("black", "gray"))[0])
        _, data = _signal_values(core_rows, data_column)
        axes[0].step(steps, [v + (1 if core == "pru1" else 3) for v in data], where="post",
                     label=f"{core} {data_column} data", color=colors.get(core, ("black", "gray"))[1])
    axes[0].set_title("Full exported trace (offset lanes: PRU1 clock/data, PRU0 clock/data)")
    axes[0].set_ylabel("logic level + lane")
    axes[0].legend(loc="upper right", ncol=2)
    axes[0].grid(alpha=0.2)

    for index, frame in enumerate(frames[:frame_count], start=1):
        start = int(frame["start"])
        end = int(frame["end"])
        for core, core_rows in sorted(rows_by_core.items()):
            window = [row for row in core_rows if start - 100 <= row["_step"] <= end + 100]
            if not window:
                continue
            clock_column = "gpo0" if core == "pru1" else "gpi16"
            data_column = "gpi8" if core == "pru1" else "gpo0"
            if clock_column not in window[0]:
                continue
            steps, clock = _signal_values(window, clock_column)
            axes[index].step(steps, clock, where="post", label=f"{core} {clock_column}", color=colors.get(core, ("black",))[0])
            _, data = _signal_values(window, data_column)
            axes[index].step(steps, [value + 1 for value in data], where="post",
                             label=f"{core} {data_column}", color=colors.get(core, ("black", "gray"))[1])
        axes[index].set_xlim(start - 100, end + 100)
        axes[index].set_title(f"Frame {index}: 0x{int(frame['value']):03X} ({frame['bits']})")
        axes[index].set_ylabel("logic")
        axes[index].legend(loc="upper right", ncol=2)
        axes[index].grid(alpha=0.2)

    axes[-1].set_xlabel("simulator step")
    fig.tight_layout()
    fig.savefig(output, dpi=160)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    rows = load_trace(args.csv)
    rows_by_core = {}
    for row in rows:
        rows_by_core.setdefault(row["core"], []).append(row)
    pru1_rows = rows_by_core.get("pru1", [])
    frames = decode_ssi_frames(pru1_rows)
    output = args.output or args.csv.with_name(args.csv.stem + "-ssi.png")
    plot_trace(rows, frames, output)

    print(f"rows: {len(rows)}; cores: {', '.join(sorted(rows_by_core))}")
    print(f"plot: {output}")
    for index, frame in enumerate(frames, start=1):
        print(f"frame {index}: steps {frame['start']}..{frame['end']}, bits {frame['bits']}, value 0x{int(frame['value']):03X}")
    if "pru0" in rows_by_core:
        for left, right in (("gpo0", "gpi16"), ("gpi8", "gpo0")):
            if right in rows_by_core["pru0"][0]:
                mismatches, common = _mirror_mismatches(rows_by_core, "pru1", left, "pru0", right)
                print(f"mirror PRU1 {left} / PRU0 {right}: {mismatches} mismatches over {common} aligned samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
