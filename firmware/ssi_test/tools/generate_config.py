#!/usr/bin/env python3
"""Validate the numeric SSI profile and generate deterministic build inputs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Mapping


_DEFINE_RE = re.compile(
    r"^\s*#define\s+(?P<name>[A-Za-z_]\w*)(?:\s+(?P<value>[^\s/]+))?"
)
_INTEGER_RE = re.compile(
    r"^[+-]?(?:0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*$"
)
_CONDITIONAL_RE = re.compile(
    r"^\s*#(?P<directive>if|ifdef|ifndef|elif|else|endif)\b(?P<body>.*)$"
)

_PROFILE_FIELDS = (
    "SSI_PRESET",
    "SSI_CORE_HZ",
    "SSI_IEP_HZ",
    "SSI_PERIOD_NS",
    "SSI_ITERATIONS",
    "SSI_FRAME_BITS",
    "SSI_POSITION_BITS",
    "SSI_POSITION_OFFSET",
    "SSI_ERROR_BITS",
    "SSI_ERROR_OFFSET",
    "SSI_ERROR_VALUE",
    "SSI_ENCODING_GRAY",
    "SSI_CLOCK_HZ",
    "SSI_SAMPLE_NS",
    "SSI_TV_NS",
    "SSI_TM_NS",
    "SSI_TP_NS",
    "SSI_INITIAL_POSITION",
    "SSI_POSITION_STEP",
)

PRESET_NAMES = (
    "CUSTOM_LEGACY_12BIT_4MHZ",
    "AHS_AHM36_SINGLETURN",
    "AHS_AHM36_MULTITURN",
    "AFS_AFM60_SINGLETURN",
    "AFS_AFM60_MULTITURN_30BIT",
    "AFS_AFM60_MULTITURN_27BIT",
    "AFS_AFM60S_PRO_SINGLETURN",
    "AFS_AFM60S_PRO_MULTITURN",
    "ARS60_SHORT",
    "ARS60_LONG",
    "TTK70",
    "KH53",
)


def _parse_integer(token: str, *, name: str, path: Path) -> int:
    """Parse one C integer literal without evaluating arbitrary expressions."""
    if not _INTEGER_RE.fullmatch(token):
        raise ValueError(
            f"{path}: {name} must be a decimal or hexadecimal integer literal, "
            f"got {token!r}"
        )
    core = token.rstrip("uUlL")
    base = 16 if core.lower().startswith(("+0x", "-0x", "0x")) else 10
    sign = -1 if core.startswith("-") else 1
    unsigned = core[1:] if core[:1] in "+-" else core
    if base == 16:
        return sign * int(unsigned[2:], 16)
    return sign * int(unsigned, 10)


def _resolve_integer_token(token: str, values: Mapping[str, int], path: Path) -> int:
    """Resolve a literal or a previously defined numeric macro."""
    try:
        return _parse_integer(token, name=token, path=path)
    except ValueError:
        if token in values:
            return int(values[token])
        raise


def _condition_value(expression: str, values: Mapping[str, int], defined: set[str], path: Path) -> bool:
    expression = expression.strip()
    while expression.startswith("(") and expression.endswith(")"):
        expression = expression[1:-1].strip()
    if expression.startswith("defined(") and expression.endswith(")"):
        return expression[8:-1].strip() in defined
    if expression.startswith("!"):
        return not _condition_value(expression[1:], values, defined, path)
    for operator in ("==", "!="):
        if operator in expression:
            left, right = expression.split(operator, 1)
            left_value = _resolve_integer_token(left.strip(), values, path)
            right_value = _resolve_integer_token(right.strip(), values, path)
            return (left_value == right_value) if operator == "==" else (left_value != right_value)
    return _resolve_integer_token(expression, values, path) != 0


def _read_numeric_defines(path: Path) -> dict[str, int]:
    """Read numeric macros from the active branches of a small C header."""
    values: dict[str, int] = {}
    defined: set[str] = set()
    # Each item is parent-active, branch-already-selected, current-active.
    conditionals: list[tuple[bool, bool, bool]] = []

    def is_active() -> bool:
        return conditionals[-1][2] if conditionals else True

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        conditional = _CONDITIONAL_RE.match(raw_line)
        if conditional:
            directive = conditional.group("directive")
            body = conditional.group("body").strip()
            if directive == "if":
                parent_active = is_active()
                result = _condition_value(body, values, defined, path) if parent_active else False
                conditionals.append((parent_active, result, parent_active and result))
            elif directive in ("ifdef", "ifndef"):
                parent_active = is_active()
                present = body in defined
                result = present if directive == "ifdef" else not present
                conditionals.append((parent_active, result, parent_active and result))
            elif directive == "elif":
                if not conditionals:
                    raise ValueError(f"{path}:{line_number}: #elif without #if")
                parent_active, branch_taken, _ = conditionals[-1]
                result = (
                    _condition_value(body, values, defined, path)
                    if parent_active and not branch_taken
                    else False
                )
                conditionals[-1] = (parent_active, branch_taken or result, parent_active and result)
            elif directive == "else":
                if not conditionals:
                    raise ValueError(f"{path}:{line_number}: #else without #if")
                parent_active, branch_taken, _ = conditionals[-1]
                conditionals[-1] = (parent_active, True, parent_active and not branch_taken)
            else:
                if not conditionals:
                    raise ValueError(f"{path}:{line_number}: #endif without #if")
                conditionals.pop()
            continue

        if not is_active():
            continue
        match = _DEFINE_RE.match(raw_line)
        if not match:
            continue
        name = match.group("name")
        token = match.group("value")
        if name in defined:
            raise ValueError(f"{path}:{line_number}: duplicate definition {name}")
        defined.add(name)
        if token is None:
            continue
        values[name] = _resolve_integer_token(token, values, path)

    if conditionals:
        raise ValueError(f"{path}: unterminated conditional block")
    return values


def read_config(path: Path) -> dict[str, int]:
    """Read the numeric macros from the user-edited SSI header."""
    path = Path(path)
    values = _read_numeric_defines(path)
    missing = [name for name in _PROFILE_FIELDS if name not in values]
    if missing:
        raise ValueError(f"{path}: missing numeric SSI definitions: {', '.join(missing)}")
    return values


def _duration_cycles(value_ns: int, frequency_hz: int, field: str) -> int:
    numerator = value_ns * frequency_hz
    cycles, remainder = divmod(numerator, 1_000_000_000)
    if remainder:
        raise ValueError(f"{field} must convert to an integral core-cycle count")
    if cycles <= 0:
        raise ValueError(f"{field} must be positive")
    return cycles


def _word_pair(value: int) -> tuple[int, int]:
    return value & 0xFFFF_FFFF, (value >> 32) & 0xFFFF_FFFF


def _crossing_part_masks(offset: int, width: int) -> tuple[int, int]:
    """Return low/high masks for a field crossing the 32-bit boundary."""
    low_width = 32 - offset
    high_width = width - low_width
    return (1 << low_width) - 1, (1 << high_width) - 1


def validate_config(config: Mapping[str, int]) -> dict[str, int]:
    """Validate a profile and return it with all derived numeric constants."""
    result = {name: int(config[name]) for name in _PROFILE_FIELDS}
    c = result

    if c["SSI_CORE_HZ"] <= 0 or c["SSI_IEP_HZ"] <= 0:
        raise ValueError("core and IEP frequencies must be positive")
    if not 0 <= c["SSI_PRESET"] < len(PRESET_NAMES):
        raise ValueError("SSI_PRESET is not a supported preset")
    if c["SSI_ITERATIONS"] != 100_000:
        raise ValueError("SSI_ITERATIONS must be exactly 100000")
    if not 1 <= c["SSI_FRAME_BITS"] <= 64:
        raise ValueError("SSI_FRAME_BITS must be in the range 1..64")
    if not 1 <= c["SSI_POSITION_BITS"] <= 32:
        raise ValueError("SSI_POSITION_BITS must be in the range 1..32")
    if c["SSI_ENCODING_GRAY"] not in (0, 1):
        raise ValueError("SSI_ENCODING_GRAY must be 0 or 1")
    if c["SSI_ERROR_BITS"] < 0 or c["SSI_ERROR_BITS"] > 32:
        raise ValueError("SSI_ERROR_BITS must be in the range 0..32")
    if c["SSI_POSITION_OFFSET"] < 0 or c["SSI_ERROR_OFFSET"] < 0:
        raise ValueError("field offsets must be non-negative")
    if c["SSI_POSITION_OFFSET"] + c["SSI_POSITION_BITS"] > c["SSI_FRAME_BITS"]:
        raise ValueError("position field is outside the SSI frame")
    if c["SSI_ERROR_OFFSET"] + c["SSI_ERROR_BITS"] > c["SSI_FRAME_BITS"]:
        raise ValueError("error field is outside the SSI frame")
    position_mask = (1 << c["SSI_POSITION_BITS"]) - 1
    error_mask = (1 << c["SSI_ERROR_BITS"]) - 1 if c["SSI_ERROR_BITS"] else 0
    position_bits = position_mask << c["SSI_POSITION_OFFSET"]
    error_bits = error_mask << c["SSI_ERROR_OFFSET"]
    if position_bits & error_bits:
        raise ValueError("position and error fields overlap")
    if not 0 <= c["SSI_ERROR_VALUE"] <= error_mask:
        raise ValueError("SSI_ERROR_VALUE does not fit in SSI_ERROR_BITS")
    if not 0 <= c["SSI_INITIAL_POSITION"] <= position_mask:
        raise ValueError("SSI_INITIAL_POSITION does not fit in SSI_POSITION_BITS")
    if abs(c["SSI_POSITION_STEP"]) > position_mask:
        raise ValueError("SSI_POSITION_STEP does not fit in SSI_POSITION_BITS")

    if c["SSI_CLOCK_HZ"] <= 0:
        raise ValueError("SSI clock frequency must be positive")
    bit_cycles, remainder = divmod(c["SSI_CORE_HZ"], c["SSI_CLOCK_HZ"])
    if remainder or bit_cycles < 2:
        raise ValueError("SSI clock must have an integral core-cycle period")
    low_cycles = bit_cycles // 2
    high_cycles = bit_cycles - low_cycles

    period_ticks, remainder = divmod(
        c["SSI_PERIOD_NS"] * c["SSI_IEP_HZ"], 1_000_000_000
    )
    if remainder or period_ticks != 288:
        raise ValueError("This test requires an exact 960 ns / 300 MHz grid")

    sample_cycles = _duration_cycles(c["SSI_SAMPLE_NS"], c["SSI_CORE_HZ"], "SSI_SAMPLE_NS")
    tv_cycles = _duration_cycles(c["SSI_TV_NS"], c["SSI_CORE_HZ"], "SSI_TV_NS")
    tm_cycles = _duration_cycles(c["SSI_TM_NS"], c["SSI_CORE_HZ"], "SSI_TM_NS")
    tp_cycles = _duration_cycles(c["SSI_TP_NS"], c["SSI_CORE_HZ"], "SSI_TP_NS")
    if not tv_cycles < sample_cycles < high_cycles:
        raise ValueError("SSI_TV_NS must be shorter than the sample offset inside the high phase")
    if tp_cycles < tm_cycles:
        raise ValueError("SSI_TP_NS must cover SSI_TM_NS")

    sample_pad_cycles = sample_cycles - 2
    high_remaining_pad_cycles = high_cycles - sample_cycles - 10
    frame_start_low_pad_cycles = low_cycles - 2
    low_pad_cycles = low_cycles - 3
    if sample_pad_cycles < 1:
        raise ValueError("SSI_SAMPLE_NS leaves no room for the sampled-data loop")
    if high_remaining_pad_cycles < 1:
        raise ValueError("SSI_SAMPLE_NS leaves no room for the high-phase tail")
    if frame_start_low_pad_cycles < 1 or low_pad_cycles < 1:
        raise ValueError("SSI clock low phase is too short for the wire loop")

    frame_mask = (1 << c["SSI_FRAME_BITS"]) - 1
    position_lo, position_hi = _word_pair(position_mask)
    frame_lo, frame_hi = _word_pair(frame_mask)
    error_lo, error_hi = _word_pair(error_mask)
    position_cross_low_mask = position_cross_high_mask = 0
    if c["SSI_POSITION_OFFSET"] < 32 and (
        c["SSI_POSITION_OFFSET"] + c["SSI_POSITION_BITS"] > 32
    ):
        position_cross_low_mask, position_cross_high_mask = _crossing_part_masks(
            c["SSI_POSITION_OFFSET"], c["SSI_POSITION_BITS"]
        )
    error_cross_low_mask = error_cross_high_mask = 0
    if c["SSI_ERROR_BITS"] and c["SSI_ERROR_OFFSET"] < 32 and (
        c["SSI_ERROR_OFFSET"] + c["SSI_ERROR_BITS"] > 32
    ):
        error_cross_low_mask, error_cross_high_mask = _crossing_part_masks(
            c["SSI_ERROR_OFFSET"], c["SSI_ERROR_BITS"]
        )
    edge_timeout_cycles = max(tm_cycles, bit_cycles) + bit_cycles
    # After the final idle-high edge the reader executes the fixed result
    # stores, branch, and loop-control instructions before the next request
    # falling edge.  The measured path is 29 cycles; keep it inside tp rather
    # than silently adding a second protocol delay.
    idle_bookkeeping_cycles = 29
    if tp_cycles <= idle_bookkeeping_cycles:
        raise ValueError("SSI_TP_NS leaves no room for idle-edge bookkeeping")
    tp_outer_count, tp_remainder_cycles = divmod(
        max(tp_cycles - idle_bookkeeping_cycles, 1), 253
    )
    if tp_outer_count == 0:
        tp_outer_count = 1
    position_low_word = int(
        c["SSI_POSITION_OFFSET"] < 32
        and c["SSI_POSITION_OFFSET"] + c["SSI_POSITION_BITS"] <= 32
    )
    position_crosses_32 = int(
        c["SSI_POSITION_OFFSET"] < 32
        and c["SSI_POSITION_OFFSET"] + c["SSI_POSITION_BITS"] > 32
    )
    position_high_word = int(c["SSI_POSITION_OFFSET"] >= 32)
    error_low_word = int(
        c["SSI_ERROR_BITS"] > 0
        and c["SSI_ERROR_OFFSET"] < 32
        and c["SSI_ERROR_OFFSET"] + c["SSI_ERROR_BITS"] <= 32
    )
    error_crosses_32 = int(
        c["SSI_ERROR_BITS"] > 0
        and c["SSI_ERROR_OFFSET"] < 32
        and c["SSI_ERROR_OFFSET"] + c["SSI_ERROR_BITS"] > 32
    )
    error_high_word = int(c["SSI_ERROR_BITS"] > 0 and c["SSI_ERROR_OFFSET"] >= 32)
    # Audited against the generated default image on the simulator: after the
    # aligned scalar load, the worst-case static pack path reaches the first
    # data drive in 19 cycles. These increments cover only compile-time paths;
    # the source has no runtime configuration work in the wire loop.
    request_pack_cycles = 19
    request_pack_cycles += int(c["SSI_ENCODING_GRAY"])
    request_pack_cycles += 2 * position_crosses_32
    request_pack_cycles += 4 * int(c["SSI_ERROR_BITS"] > 0)
    request_pack_cycles += 2 * int(32 < c["SSI_FRAME_BITS"] < 64)
    qualifier_max_gap_cycles = 8
    reader_sample_path_cycles = 2
    data_valid_margin_cycles = low_cycles - request_pack_cycles
    if data_valid_margin_cycles < tv_cycles:
        raise ValueError(
            "SSI_TV_NS exceeds the measured request-pack/data-valid margin"
        )
    if low_cycles <= qualifier_max_gap_cycles:
        raise ValueError("SSI clock low phase is shorter than the qualifier polling gap")
    if any(
        value > 256
        for value in (
            sample_pad_cycles,
            high_remaining_pad_cycles,
            frame_start_low_pad_cycles,
            low_pad_cycles,
        )
    ):
        raise ValueError("SSI wire delay exceeds the PRU LOOP instruction range")
    result.update(
        {
            "SSI_BIT_CYCLES": bit_cycles,
            "SSI_LOW_CYCLES": low_cycles,
            "SSI_HIGH_CYCLES": high_cycles,
            "SSI_PERIOD_TICKS": period_ticks,
            "SSI_FIRST_LEAD_TICKS": period_ticks * 16,
            "SSI_EDGE_TIMEOUT_CYCLES": edge_timeout_cycles,
            "SSI_REQUEST_PACK_CYCLES": request_pack_cycles,
            "SSI_QUALIFIER_MAX_GAP_CYCLES": qualifier_max_gap_cycles,
            "SSI_READER_SAMPLE_PATH_CYCLES": reader_sample_path_cycles,
            "SSI_DATA_VALID_MARGIN_CYCLES": data_valid_margin_cycles,
            "SSI_SAMPLE_PAD_CYCLES": sample_pad_cycles,
            "SSI_HIGH_REMAINING_PAD_CYCLES": high_remaining_pad_cycles,
            "SSI_FRAME_START_LOW_PAD_CYCLES": frame_start_low_pad_cycles,
            "SSI_LOW_PAD_CYCLES": low_pad_cycles,
            "SSI_TP_INNER_COUNT": 250,
            "SSI_TP_OUTER_COUNT": tp_outer_count,
            "SSI_TP_REMAINDER_CYCLES": tp_remainder_cycles,
            "SSI_IDLE_BOOKKEEPING_CYCLES": idle_bookkeeping_cycles,
            "SSI_POSITION_LOW_WORD": position_low_word,
            "SSI_POSITION_CROSSES_32": position_crosses_32,
            "SSI_POSITION_HIGH_WORD": position_high_word,
            "SSI_ERROR_LOW_WORD": error_low_word,
            "SSI_ERROR_CROSSES_32": error_crosses_32,
            "SSI_ERROR_HIGH_WORD": error_high_word,
            "SSI_FRAME_LOW_WORD": int(c["SSI_FRAME_BITS"] <= 32),
            "SSI_FRAME_CROSSES_32": int(32 < c["SSI_FRAME_BITS"] < 64),
            "SSI_FRAME_FULL_64": int(c["SSI_FRAME_BITS"] == 64),
            "SSI_SAMPLE_CYCLES": sample_cycles,
            "SSI_TV_CYCLES": tv_cycles,
            "SSI_TM_CYCLES": tm_cycles,
            "SSI_TP_CYCLES": tp_cycles,
            "SSI_POSITION_MASK": position_mask,
            "SSI_POSITION_MASK_LO": position_lo,
            "SSI_POSITION_MASK_HI": position_hi,
            "SSI_ERROR_MASK": error_mask,
            "SSI_ERROR_MASK_LO": error_lo,
            "SSI_ERROR_MASK_HI": error_hi,
            "SSI_FRAME_MASK": frame_mask,
            "SSI_FRAME_MASK_LO": frame_lo,
            "SSI_FRAME_MASK_HI": frame_hi,
            "SSI_POSITION_CROSS_LOW_MASK": position_cross_low_mask,
            "SSI_POSITION_CROSS_HIGH_MASK": position_cross_high_mask,
            "SSI_ERROR_CROSS_LOW_MASK": error_cross_low_mask,
            "SSI_ERROR_CROSS_HIGH_MASK": error_cross_high_mask,
        }
    )
    return result


def encode_position(position: int, config: Mapping[str, int]) -> int:
    """Return the binary or Gray wire value for one position field."""
    width = int(config["SSI_POSITION_BITS"])
    mask = (1 << width) - 1
    if not 0 <= position <= mask:
        raise ValueError(f"position {position} does not fit in {width} bits")
    if int(config["SSI_ENCODING_GRAY"]):
        return (position ^ (position >> 1)) & mask
    return position


def pack_position_frame(position: int, error: int, config: Mapping[str, int]) -> int:
    """Pack a natural position and constant error field into the SSI frame."""
    validated = validate_config(config) if "SSI_POSITION_MASK" not in config else dict(config)
    encoded = encode_position(position, validated)
    error_mask = int(validated["SSI_ERROR_MASK"])
    if not 0 <= error <= error_mask:
        raise ValueError(f"error {error} does not fit in {validated['SSI_ERROR_BITS']} bits")
    return (
        (encoded << int(validated["SSI_POSITION_OFFSET"]))
        | (error << int(validated["SSI_ERROR_OFFSET"]))
    ) & int(validated["SSI_FRAME_MASK"])


def _write_if_changed(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def _format_set(name: str, value: int) -> str:
    return f"{name} .set {value if value < 10 else hex(value)}"


def generate(config_path: Path, output_dir: Path) -> None:
    """Generate ABI assembly, build constants, and simulator JSON."""
    config_path = Path(config_path)
    output_dir = Path(output_dir)
    validated = validate_config(read_config(config_path))
    abi_path = config_path.parents[1] / "include" / "ssi_test_abi.h"
    if not abi_path.is_file():
        raise ValueError(f"missing ABI source header: {abi_path}")
    abi = _read_numeric_defines(abi_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    build_lines = [
        "; GENERATED FILE -- do not edit by hand.",
        "; Source: ssi_hardware_config.h; regenerate with tools/generate_config.py.",
        "",
    ]
    for name in sorted(validated):
        build_lines.append(_format_set(name, validated[name]))
    build_lines.append("")

    abi_lines = [
        "; GENERATED FILE -- do not edit by hand.",
        "; Source: include/ssi_test_abi.h; regenerate with tools/generate_config.py.",
        "",
    ]
    for name in sorted(abi):
        abi_lines.append(_format_set(name, abi[name]))
    abi_lines.append("")

    payload = dict(validated)
    payload["SSI_PRESET_NAME"] = PRESET_NAMES[validated["SSI_PRESET"]]
    payload["abi"] = dict(sorted(abi.items()))
    _write_if_changed(output_dir / "ssi_build_config.inc", "\n".join(build_lines))
    _write_if_changed(output_dir / "ssi_test_abi.inc", "\n".join(abi_lines))
    _write_if_changed(
        output_dir / "ssi_build_config.json",
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(args.config, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
