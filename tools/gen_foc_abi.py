#!/usr/bin/env python3
"""Generate the open-loop FOC shared-memory ABI snapshots.

The JSON schema is the source of truth.  The generated assembly include,
Python helpers, and C header are checked in so the PRU, simulator, and host
side all use the same offsets.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = _ROOT / "schema" / "foc_abi.json"
INC_PATH = _ROOT / "source" / "foc_abi.inc"
PY_PATH = _ROOT / "pru_io" / "foc_abi.py"
C_PATH = _ROOT / "include" / "foc_abi.h"

ICSS_SHARED_BASE = 0x00010000
ICSS_SHARED_SIZE = 0x00010000

SECTION_ORDER = ("control", "pwm_out", "motor_fb", "sine_lut")
SECTION_BASE_NAME = {
    "control": "CONTROL_BASE",
    "pwm_out": "PWM_OUT_BASE",
    "motor_fb": "MOTOR_FB_BASE",
    "sine_lut": "SINE_LUT_BASE",
}

TYPE_INFO = {
    "u32": (4, "I"),
    "i32": (4, "i"),
    "u64": (8, "Q"),
    "i64": (8, "q"),
}


def _is_reserved(field: dict) -> bool:
    return field["name"].startswith("_")


def _as_int(value, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            try:
                return int(value, 16)
            except ValueError:
                pass
    raise ValueError(f"{label} must be an integer")


def load_schema(path=SCHEMA_PATH) -> dict:
    """Load and validate the FOC ABI schema.

    Fields must tile their section without gaps or overlaps.  Complete
    sections must also remain disjoint and inside the 64 KiB shared window.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    missing = [name for name in SECTION_ORDER if name not in raw]
    if missing:
        raise ValueError(f"schema missing sections: {', '.join(missing)}")

    schema = {"_constants": dict(raw.get("constants", {}))}
    for section_name in SECTION_ORDER:
        section = raw[section_name]
        fields = []
        for field in section.get("fields", []):
            item = dict(field)
            item["offset"] = _as_int(
                item.get("offset"),
                f"section '{section_name}' field '{item.get('name')}' offset",
            )
            type_name = item.get("type")
            if type_name not in TYPE_INFO:
                raise ValueError(
                    f"section '{section_name}' field '{item.get('name')}' "
                    f"has unknown type {type_name!r}"
                )
            expected_size, _ = TYPE_INFO[type_name]
            if item.get("size") != expected_size:
                raise ValueError(
                    f"section '{section_name}' field '{item.get('name')}': "
                    f"size {item.get('size')} doesn't match type {type_name} "
                    f"(expected {expected_size})"
                )
            if item["offset"] < 0:
                raise ValueError(
                    f"section '{section_name}' field '{item.get('name')}' "
                    "has a negative offset"
                )
            fields.append(item)

        if not fields:
            raise ValueError(f"section '{section_name}' has no fields")

        section_copy = {"fields": fields}
        if "base" in section:
            section_copy["base"] = _as_int(
                section["base"], f"section '{section_name}' base"
            )
        else:
            raise ValueError(f"section '{section_name}' has no base")
        if "count" in section:
            section_copy["count"] = _as_int(
                section["count"], f"section '{section_name}' count"
            )
            if section_copy["count"] < 1:
                raise ValueError(f"section '{section_name}' count must be positive")
        if "stride" in section:
            section_copy["stride"] = _as_int(
                section["stride"], f"section '{section_name}' stride"
            )
            if section_copy["stride"] < 1:
                raise ValueError(f"section '{section_name}' stride must be positive")
        schema[section_name] = section_copy

        expected_offset = 0
        previous = "<section start>"
        for field in sorted(fields, key=lambda candidate: candidate["offset"]):
            if field["offset"] != expected_offset:
                relation = "gap or overlap"
                raise ValueError(
                    f"section '{section_name}': {relation} between "
                    f"'{previous}' (ends at 0x{expected_offset:X}) and "
                    f"'{field['name']}' (starts at 0x{field['offset']:X})"
                )
            expected_offset += field["size"]
            previous = field["name"]

        if "stride" in section_copy and expected_offset != section_copy["stride"]:
            raise ValueError(
                f"section '{section_name}': fields cover {expected_offset} "
                f"bytes but declared stride is {section_copy['stride']}"
            )

    intervals = []
    for section_name in SECTION_ORDER:
        section = schema[section_name]
        field_coverage = max(
            field["offset"] + field["size"] for field in section["fields"]
        )
        item_size = section.get("stride", field_coverage)
        count = section.get("count", 1)
        start = section["base"]
        end = start + item_size * count
        if start < 0 or end > ICSS_SHARED_SIZE:
            raise ValueError(
                f"section '{section_name}' spans 0x{start:X}..0x{end:X}, "
                f"outside shared-memory bounds 0x0000..0x{ICSS_SHARED_SIZE:X}"
            )
        intervals.append((start, end, section_name))

    intervals.sort()
    for previous, current in zip(intervals, intervals[1:]):
        if previous[1] > current[0]:
            raise ValueError(
                f"shared-memory overlap: section '{previous[2]}' ends at "
                f"0x{previous[1]:X}, section '{current[2]}' starts at "
                f"0x{current[0]:X}"
            )
    return schema


def _section_prefix(section_name: str) -> str:
    return section_name.upper()


def _section_size(section: dict) -> int:
    if "stride" in section:
        return section["stride"]
    return max(field["offset"] + field["size"] for field in section["fields"])


def _constant(schema: dict, name: str, default=0):
    return schema.get("_constants", {}).get(name, default)


def _status_flag(schema: dict, name: str, default=0) -> int:
    flags = schema.get("_constants", {}).get("status_flags", {})
    return int(flags.get(name, default))


def generate_inc(schema: dict) -> str:
    """Return the PRU assembly include text."""
    lines = [
        "; GENERATED FILE -- do not edit by hand. Source: schema/foc_abi.json.",
        "; Regenerate with: python tools/gen_foc_abi.py",
        "",
        f"FOC_ICSS_SHARED_BASE .set 0x{ICSS_SHARED_BASE:08X}",
        f"FOC_ABI_VERSION .set {_constant(schema, 'abi_version')}",
        f"FOC_Q_FRACTION_BITS .set {_constant(schema, 'q_fraction_bits')}",
        f"FOC_Q_ONE .set {1 << int(_constant(schema, 'q_fraction_bits', 24))}",
        f"FOC_IEP_TICK_HZ .set {_constant(schema, 'iep_tick_hz')}",
        f"FOC_SINE_LUT_ENTRIES .set {_constant(schema, 'sine_lut_entries')}",
        f"FOC_SPEED_SCALE .set {_constant(schema, 'speed_scale')}",
        f"SPEED_SCALE .set {_constant(schema, 'speed_scale')}",
        f"FOC_SPEED_BASE_RPM .set {_constant(schema, 'speed_base_rpm')}",
        f"FOC_CONTROL_LOOP_HZ .set {_constant(schema, 'control_loop_hz')}",
        f"FOC_CURRENT_BASE_A .set {_constant(schema, 'current_base_a')}",
        f"FOC_DEFAULT_CONTROL_PERIOD_IEP_TICKS .set "
        f"{_constant(schema, 'default_control_period_iep_ticks')}",
        f"FOC_MAX_VOLTAGE_MAGNITUDE_Q24 .set "
        f"{_constant(schema, 'max_voltage_magnitude_q24')}",
        f"FOC_MAX_VOLTAGE_MAGNITUDE_SQUARED_Q24 .set "
        f"{_constant(schema, 'max_voltage_magnitude_squared_q24')}",
        f"FOC_STATUS_DISABLED .set {_status_flag(schema, 'disabled')}",
        f"FOC_STATUS_SATURATED .set {_status_flag(schema, 'saturated')}",
        f"FOC_STATUS_DEADLINE_MISS .set "
        f"{_status_flag(schema, 'deadline_miss')}",
        f"FOC_STATUS_INVALID_CONFIG .set "
        f"{_status_flag(schema, 'invalid_config')}",
        "",
    ]

    for section_name in SECTION_ORDER:
        section = schema[section_name]
        prefix = _section_prefix(section_name)
        base = section["base"]
        lines.append(
            f"FOC_{prefix}_BASE .set 0x{ICSS_SHARED_BASE + base:08X}"
        )
        lines.append(f"FOC_{prefix}_OFFSET .set 0x{base:X}")
        for field in section["fields"]:
            if not _is_reserved(field):
                lines.append(
                    f"FOC_{prefix}_{field['name'].upper()}_OFF .set "
                    f"0x{field['offset']:X}"
                )
        if section_name == "control":
            control_period_offset = next(
                field["offset"]
                for field in section["fields"]
                if field["name"] == "control_period_iep_ticks"
            )
            lines.append(
                f"FOC_CONTROL_PERIOD_IEP_TICKS_OFF .set 0x{control_period_offset:X}"
            )
        if section_name == "pwm_out":
            timestamp_offset = next(
                field["offset"]
                for field in section["fields"]
                if field["name"] == "timestamp_cycles"
            )
            lines.append(
                f"FOC_PWM_OUT_TIMESTAMP_IEP_OFF .set 0x{timestamp_offset:X}"
            )
        lines.append(f"FOC_{prefix}_SIZE .set {_section_size(section)}")
        if "count" in section:
            lines.append(f"FOC_{prefix}_COUNT .set {section['count']}")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _field_dict_text(var_name: str, fields: list[dict]) -> str:
    entries = []
    for field in fields:
        if _is_reserved(field):
            continue
        fmt = TYPE_INFO[field["type"]][1]
        entries.append(
            f'    "{field["name"]}": '
            f'(0x{field["offset"]:X}, "{fmt}"),'
        )
    if var_name == "_PWM_OUT_FIELDS":
        timestamp = next(
            field for field in fields if field["name"] == "timestamp_cycles"
        )
        entries.append(
            f'    "timestamp_iep": (0x{timestamp["offset"]:X}, "Q"),'
        )
    return var_name + " = {\n" + "\n".join(entries) + "\n}"


def _unpack_function(name: str, fields_name: str, doc: str) -> str:
    return (
        f"def {name}(data):\n"
        f"    \"\"\"{doc}\"\"\"\n"
        "    return {\n"
        f'        name: struct.unpack_from("<" + fmt, data, offset)[0]\n'
        f"        for name, (offset, fmt) in {fields_name}.items()\n"
        "    }"
    )


def generate_python(schema: dict) -> str:
    """Return the generated Python ABI helper module."""
    q_bits = int(_constant(schema, "q_fraction_bits", 24))
    lines = [
        '"""GENERATED FILE -- do not edit by hand.',
        "",
        "Source: schema/foc_abi.json",
        "Regenerate with: python tools/gen_foc_abi.py",
        '"""',
        "import struct",
        "",
        f"ICSS_SHARED_BASE = 0x{ICSS_SHARED_BASE:08X}",
        f"ICSS_SHARED_SIZE = 0x{ICSS_SHARED_SIZE:X}",
        f"ABI_VERSION = {_constant(schema, 'abi_version')}",
        f"Q_FRACTION_BITS = {q_bits}",
        "Q_ONE = 1 << Q_FRACTION_BITS",
        f"IEP_TICK_HZ = {_constant(schema, 'iep_tick_hz')}",
        f"SINE_LUT_ENTRIES = {_constant(schema, 'sine_lut_entries')}",
        f"SPEED_SCALE = {_constant(schema, 'speed_scale')}",
        f"SPEED_BASE_RPM = {_constant(schema, 'speed_base_rpm')}",
        f"CONTROL_LOOP_HZ = {_constant(schema, 'control_loop_hz')}",
        f"CURRENT_BASE_A = {_constant(schema, 'current_base_a')}",
        f"DEFAULT_CONTROL_PERIOD_IEP_TICKS = "
        f"{_constant(schema, 'default_control_period_iep_ticks')}",
        f"MAX_VOLTAGE_MAGNITUDE_Q24 = "
        f"{_constant(schema, 'max_voltage_magnitude_q24')}",
        f"MAX_VOLTAGE_MAGNITUDE_SQUARED_Q24 = "
        f"{_constant(schema, 'max_voltage_magnitude_squared_q24')}",
        f"MAX_VOLTAGE_MAGNITUDE_PU = "
        f"{_constant(schema, 'max_voltage_magnitude_pu')!r}",
        f"STATUS_DISABLED = {_status_flag(schema, 'disabled')}",
        f"STATUS_SATURATED = {_status_flag(schema, 'saturated')}",
        f"STATUS_DEADLINE_MISS = {_status_flag(schema, 'deadline_miss')}",
        f"STATUS_INVALID_CONFIG = {_status_flag(schema, 'invalid_config')}",
        "U32_MASK = 0xFFFF_FFFF",
        "",
    ]

    for section_name in SECTION_ORDER:
        section = schema[section_name]
        prefix = _section_prefix(section_name)
        base = section["base"]
        lines.extend([
            f"{SECTION_BASE_NAME[section_name]} = "
            f"0x{ICSS_SHARED_BASE + base:08X}",
            f"{prefix}_OFFSET = 0x{base:X}",
            f"{prefix}_SIZE = {_section_size(section)}",
        ])
        if "count" in section:
            lines.append(f"{prefix}_COUNT = {section['count']}")
        lines.append("")
        for field in section["fields"]:
            if not _is_reserved(field):
                lines.append(
                    f"{prefix}_{field['name'].upper()}_OFF = "
                    f"0x{field['offset']:X}"
                )
        if section_name == "control":
            lines.append(
                "CONTROL_PERIOD_IEP_TICKS_OFF = "
                "CONTROL_CONTROL_PERIOD_IEP_TICKS_OFF"
            )
        if section_name == "pwm_out":
            lines.append(
                "PWM_OUT_TIMESTAMP_IEP_OFF = PWM_OUT_TIMESTAMP_CYCLES_OFF"
            )
        lines.append("")

    for section_name in SECTION_ORDER:
        prefix = _section_prefix(section_name)
        lines.append(
            _field_dict_text(
                f"_{prefix}_FIELDS", schema[section_name]["fields"]
            )
        )
        lines.append("")

    lines.extend([
        "def _pack_fields(field_map, size, values):",
        "    buf = bytearray(size)",
        "    for name, value in values.items():",
        "        if name not in field_map:",
        "            raise ValueError(f\"unknown FOC field: {name!r}\")",
        "        offset, fmt = field_map[name]",
        "        struct.pack_into(\"<\" + fmt, buf, offset, value)",
        "    return bytes(buf)",
        "",
        "def _unpack_fields(field_map, data):",
        "    return {",
        '        name: struct.unpack_from("<" + fmt, data, offset)[0]',
        "        for name, (offset, fmt) in field_map.items()",
        "    }",
        "",
        "def pack_control(**fields):",
        '    """Pack a control block, defaulting ABI metadata to current values."""',
        "    values = {\"abi_version\": ABI_VERSION, \"struct_size\": CONTROL_SIZE,",
        "              \"control_period_iep_ticks\": DEFAULT_CONTROL_PERIOD_IEP_TICKS}",
        "    values.update(fields)",
        "    return _pack_fields(_CONTROL_FIELDS, CONTROL_SIZE, values)",
        "",
        "def unpack_control(data):",
        '    """Unpack a control block into named integer fields."""',
        "    return _unpack_fields(_CONTROL_FIELDS, data)",
        "",
        "def pack_pwm_out(**fields):",
        '    """Pack one complete PWM output block."""',
        "    return _pack_fields(_PWM_OUT_FIELDS, PWM_OUT_SIZE, fields)",
        "",
        "def unpack_pwm_out(data):",
        '    """Unpack one complete PWM output block."""',
        "    return _unpack_fields(_PWM_OUT_FIELDS, data)",
        "",
        "def pack_motor_fb(**fields):",
        '    """Pack one complete motor feedback block."""',
        "    return _pack_fields(_MOTOR_FB_FIELDS, MOTOR_FB_SIZE, fields)",
        "",
        "def unpack_motor_fb(data):",
        '    """Unpack one complete motor feedback block."""',
        "    return _unpack_fields(_MOTOR_FB_FIELDS, data)",
        "",
        "def pack_seqlock(seq, payload):",
        '    """Combine a sequence word and a payload for a seqlock block."""',
        "    return struct.pack(\"<I\", seq & U32_MASK) + bytes(payload)",
        "",
        "def read_coherent(read_seq, read_payload, unpacker, retries=8):",
        '    """Read a seqlock payload, returning None after bounded retries."""',
        "    for _ in range(max(1, int(retries))):",
        "        first = int(read_seq()) & U32_MASK",
        "        if first & 1:",
        "            continue",
        "        payload = bytes(read_payload())",
        "        second = int(read_seq()) & U32_MASK",
        "        if first == second and not (second & 1):",
        "            return unpacker(struct.pack(\"<I\", first) + payload)",
        "    return None",
        "",
        "def read_coherent_pwm_out(read_seq, read_payload, retries=8):",
        "    return read_coherent(read_seq, read_payload, unpack_pwm_out, retries)",
        "",
        "def read_coherent_motor_fb(read_seq, read_payload, retries=8):",
        "    return read_coherent(read_seq, read_payload, unpack_motor_fb, retries)",
        "",
        "read_seqlock = read_coherent",
        "",
    ])
    return "\n".join(lines)


def _c_field_macro(section_name: str, field_name: str) -> str:
    return f"FOC_{section_name.upper()}_OFF_{field_name.upper()}"


def generate_c(schema: dict) -> str:
    """Return the generated C header used by host-side snapshots."""
    q_bits = int(_constant(schema, "q_fraction_bits", 24))
    lines = [
        "/*",
        " * GENERATED FILE -- source: schema/foc_abi.json",
        " *",
        " * Regenerate with: python tools/gen_foc_abi.py",
        " */",
        "#ifndef FOC_ABI_H_",
        "#define FOC_ABI_H_",
        "",
        "#include <stdint.h>",
        "",
        f"#define FOC_ICSS_SHARED_BASE                 (0x{ICSS_SHARED_BASE:08X}U)",
        f"#define FOC_ABI_VERSION                      ({_constant(schema, 'abi_version')}U)",
        f"#define FOC_Q_FRACTION_BITS                  ({q_bits}U)",
        f"#define FOC_Q_ONE                             ({1 << q_bits}U)",
        f"#define FOC_IEP_TICK_HZ                      ({_constant(schema, 'iep_tick_hz')}U)",
        f"#define FOC_SINE_LUT_ENTRIES                 ({_constant(schema, 'sine_lut_entries')}U)",
        f"#define FOC_SPEED_SCALE                      ({_constant(schema, 'speed_scale')}U)",
        f"#define FOC_SPEED_BASE_RPM                   ({_constant(schema, 'speed_base_rpm')}U)",
        f"#define FOC_CONTROL_LOOP_HZ                  ({_constant(schema, 'control_loop_hz')}U)",
        f"#define FOC_CURRENT_BASE_A                   ({_constant(schema, 'current_base_a')}U)",
        f"#define FOC_DEFAULT_CONTROL_PERIOD_IEP_TICKS "
        f"({_constant(schema, 'default_control_period_iep_ticks')}U)",
        f"#define FOC_MAX_VOLTAGE_MAGNITUDE_Q24       "
        f"({_constant(schema, 'max_voltage_magnitude_q24')}U)",
        f"#define FOC_MAX_VOLTAGE_MAGNITUDE_SQUARED_Q24 "
        f"({_constant(schema, 'max_voltage_magnitude_squared_q24')}U)",
        f"#define FOC_STATUS_DISABLED                 "
        f"({_status_flag(schema, 'disabled')}U)",
        f"#define FOC_STATUS_SATURATED                "
        f"({_status_flag(schema, 'saturated')}U)",
        f"#define FOC_STATUS_DEADLINE_MISS            "
        f"({_status_flag(schema, 'deadline_miss')}U)",
        f"#define FOC_STATUS_INVALID_CONFIG           "
        f"({_status_flag(schema, 'invalid_config')}U)",
        "",
    ]

    for section_name in SECTION_ORDER:
        section = schema[section_name]
        prefix = section_name.upper()
        lines.extend([
            f"#define FOC_{prefix}_BASE{' ' * max(1, 36 - len(prefix))}"
            f"(0x{ICSS_SHARED_BASE + section['base']:08X}U)",
            f"#define FOC_{prefix}_OFFSET{' ' * max(1, 32 - len(prefix))}"
            f"(0x{section['base']:04X}U)",
            f"#define FOC_{prefix}_SIZE{' ' * max(1, 35 - len(prefix))}"
            f"({_section_size(section)}U)",
        ])
        if "count" in section:
            lines.append(
                f"#define FOC_{prefix}_COUNT{' ' * max(1, 35 - len(prefix))}"
                f"({section['count']}U)"
            )
        for field in section["fields"]:
            if not _is_reserved(field):
                macro = _c_field_macro(section_name, field["name"])
                lines.append(
                    f"#define {macro:<44} (0x{field['offset']:02X}U)"
                )
        lines.append("")

    lines.extend([
        "typedef struct {",
        "    uint32_t abi_version;",
        "    uint32_t struct_size;",
        "    uint32_t enable;",
        "    uint32_t requested_generation;",
        "    uint32_t pru_ack_generation;",
        "    int32_t speed_ref_q24;",
        "    int32_t id_ref_q24;",
        "    int32_t iq_ref_q24;",
        "    int32_t ramp_rate_q24;",
        "    uint32_t control_period_iep_ticks;",
        "} foc_control_t;",
        "",
        "typedef struct {",
        "    uint32_t seq;",
        "    int32_t ta_q24;",
        "    int32_t tb_q24;",
        "    int32_t tc_q24;",
        "    int32_t valpha_q24;",
        "    int32_t vbeta_q24;",
        "    uint32_t theta_cmd_u32;",
        "    uint32_t loop_counter;",
        "    uint64_t timestamp_cycles;",
        "    uint32_t status;",
        "    int32_t speed_cmd_q24;",
        "} foc_pwm_out_t;",
        "",
        "typedef struct {",
        "    uint32_t seq;",
        "    int32_t ia_q24;",
        "    int32_t ib_q24;",
        "    int32_t ic_q24;",
        "    int32_t id_meas_q24;",
        "    int32_t iq_meas_q24;",
        "    uint32_t rotor_theta_u32;",
        "    int32_t speed_rpm_q24;",
        "    uint64_t timestamp;",
        "} foc_motor_fb_t;",
        "",
        "#endif /* FOC_ABI_H_ */",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate FOC ABI snapshots")
    parser.add_argument(
        "--parent-include-dir",
        type=Path,
        help="also write foc_abi.h/.inc snapshots in this directory",
    )
    args = parser.parse_args()
    schema = load_schema(SCHEMA_PATH)

    INC_PATH.parent.mkdir(parents=True, exist_ok=True)
    INC_PATH.write_text(generate_inc(schema), encoding="utf-8", newline="\n")
    PY_PATH.parent.mkdir(parents=True, exist_ok=True)
    PY_PATH.write_text(generate_python(schema), encoding="utf-8", newline="\n")
    C_PATH.parent.mkdir(parents=True, exist_ok=True)
    C_PATH.write_text(generate_c(schema), encoding="utf-8", newline="\n")

    if args.parent_include_dir is not None:
        args.parent_include_dir.mkdir(parents=True, exist_ok=True)
        (args.parent_include_dir / "foc_abi.h").write_text(
            generate_c(schema), encoding="utf-8", newline="\n"
        )
        (args.parent_include_dir / "foc_abi.inc").write_text(
            generate_inc(schema), encoding="utf-8", newline="\n"
        )

    print(f"wrote {INC_PATH}")
    print(f"wrote {PY_PATH}")
    print(f"wrote {C_PATH}")


if __name__ == "__main__":
    main()
