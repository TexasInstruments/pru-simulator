#!/usr/bin/env python3
"""Generate the SSI ABI's C, PRU-assembly `.inc`, and Python snapshots from
`schema/ssi_config_abi.json`.

Usage: python tools/gen_ssi_abi.py

All three generated files are checked in; `tests/test_ssi_config_abi_generated.py`
regenerates them in memory and diffs against the checked-in copies so a schema
edit that forgets to re-run this script fails immediately. Use
`--parent-include-dir` to refresh both CCS snapshots outside the simulator
submodule.
"""
import json
import os
import struct
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

SCHEMA_PATH = _ROOT / "schema" / "ssi_config_abi.json"
INC_PATH = _ROOT / "source" / "ssi_config_abi.inc"
PY_PATH = _ROOT / "pru_io" / "ssi_config_abi.py"
C_PATH = _ROOT / "include" / "ssi_config_abi.h"

#: ICSS_SHARED base address (matches `c28` in config/constants_am243x.cfg).
ICSS_SHARED_BASE = 0x00010000
ICSS_SHARED_SIZE = 0x00010000

#: Fixed inner-loop count the design doc's `*_pause_outer_iters` fields are
#: multiplied against. Not configurable, not in the schema.
PAUSE_INNER_ITERS = 250

#: Section processing order (also the order fields appear in generated output).
SECTION_ORDER = (
    "config",
    "frame_slot",
    "mailbox",
    "capture",
    "trace_record",
    "producer_sample",
    "producer_sample_diagnostics",
)

#: Section key -> generated base-address constant name (without the SSI_/no
#: prefix; callers add SSI_ for the .inc and leave it bare for Python).
SECTION_BASE_NAME = {
    "config": "CONFIG_BASE",
    "frame_slot": "FRAMES_BASE",
    "mailbox": "MAILBOX_BASE",
    "capture": "CAPTURE_BASE",
    "trace_record": "TRACE_BASE",
    "producer_sample": "PRODUCER_SAMPLES_BASE",
    "producer_sample_diagnostics": "PRODUCER_SAMPLE_DIAGNOSTICS_BASE",
}

#: Scalar type -> (byte size, struct format char).
TYPE_INFO = {
    "u8": (1, "B"),
    "u16": (2, "H"),
    "u32": (4, "I"),
    "u64": (8, "Q"),
    "i64": (8, "q"),
}


def _is_reserved(field: dict) -> bool:
    return field["name"].startswith("_")


def load_schema(path) -> dict:
    """Load and validate the ABI schema.

    Converts hex-string offsets/bases to `int`. Validates that within each
    section, fields sorted by offset tile the section exactly: no overlaps,
    no undocumented gaps. It also validates that complete sections neither
    overlap each other nor extend beyond the 64 KiB ICSSG shared RAM window.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    schema = {"_constants": raw.get("constants", {})}
    for section_name, section in raw.items():
        if section_name == "constants":
            continue
        fields = []
        for field in section["fields"]:
            f = dict(field)
            f["offset"] = int(f["offset"], 16)
            if f["type"] in TYPE_INFO:
                expected_size, _ = TYPE_INFO[f["type"]]
                if f["size"] != expected_size:
                    raise ValueError(
                        f"section '{section_name}' field '{f['name']}': "
                        f"size {f['size']} doesn't match type {f['type']} "
                        f"(expected {expected_size})"
                    )
            fields.append(f)

        new_section = {"fields": fields}
        if "base" in section:
            new_section["base"] = int(section["base"], 16)
        if "count" in section:
            new_section["count"] = section["count"]
        if "stride" in section:
            new_section["stride"] = section["stride"]
        schema[section_name] = new_section

        ordered = sorted(fields, key=lambda x: x["offset"])
        expected_offset = 0
        prev_name = "<section start>"
        for f in ordered:
            if f["offset"] != expected_offset:
                raise ValueError(
                    f"section '{section_name}': gap or overlap between "
                    f"'{prev_name}' (ends at 0x{expected_offset:X}) and "
                    f"'{f['name']}' (starts at 0x{f['offset']:X})"
                )
            expected_offset = f["offset"] + f["size"]
            prev_name = f["name"]

        if "stride" in new_section and expected_offset != new_section["stride"]:
            raise ValueError(
                f"section '{section_name}': fields cover {expected_offset} "
                f"bytes but declared stride is {new_section['stride']}"
            )

    intervals = []
    for section_name, section in schema.items():
        if section_name.startswith("_") or "base" not in section:
            continue
        field_coverage = max(
            field["offset"] + field["size"] for field in section["fields"]
        )
        item_size = section.get("stride", field_coverage)
        coverage = item_size * section.get("count", 1)
        start = section["base"]
        end = start + coverage
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


def _frame_slot_sentinel(schema: dict) -> int:
    for field in schema["frame_slot"]["fields"]:
        if field["name"] == "frame_bits":
            return (1 << (field["size"] * 8)) - 1
    raise ValueError("frame_slot section has no 'frame_bits' field")


def generate_inc(schema: dict) -> str:
    """Return the full text of the PRU-assembly `.inc` file."""
    lines = [
        "; GENERATED FILE -- do not edit by hand. Source: schema/ssi_config_abi.json.",
        "; Regenerate with: python tools/gen_ssi_abi.py",
        "",
        f"SSI_PAUSE_INNER_ITERS .set {PAUSE_INNER_ITERS}",
        "",
    ]

    constants = schema.get("_constants", {})
    for name, value in (
        ("SSI_CONFIG_ABI_VERSION", constants.get("config_abi_version")),
        ("SSI_NO_ERROR_FIELD", constants.get("no_error_field")),
        ("SSI_IEPCLK_OCP_EN", constants.get("iepclk_ocp_en")),
        ("SSI_IEP_TICK_HZ", constants.get("iep_tick_hz")),
        (
            "SSI_PRODUCER_SAMPLE_Q_FRACTION_BITS",
            constants.get("producer_sample_q_fraction_bits"),
        ),
        (
            "SSI_DEFAULT_PRODUCER_PERIOD_IEP_TICKS",
            constants.get("default_producer_period_iep_ticks"),
        ),
        (
            "SSI_DEFAULT_PRODUCER_SAMPLE_AGE_LIMIT_IEP_TICKS",
            constants.get("default_producer_sample_age_limit_iep_ticks"),
        ),
        (
            "SSI_DEFAULT_PRODUCER_PREDICTION_HORIZON_LIMIT_IEP_TICKS",
            constants.get("default_producer_prediction_horizon_limit_iep_ticks"),
        ),
    ):
        if value is not None:
            lines.append(f"{name} .set {value}")
    for enum_values in constants.get("enums", {}).values():
        for name, value in enum_values.items():
            lines.append(f"{name} .set {value}")
    lines.append("")

    for section_name in SECTION_ORDER:
        section = schema[section_name]
        base_const = f"SSI_{SECTION_BASE_NAME[section_name]}"
        lines.append(f"{base_const} .set 0x{ICSS_SHARED_BASE + section['base']:08X}")

        prefix = section_name.upper()
        for field in section["fields"]:
            if _is_reserved(field):
                continue
            off_name = f"SSI_{prefix}_{field['name'].upper()}_OFF"
            lines.append(f"{off_name} .set 0x{field['offset']:X}")

        if "stride" in section:
            stride_name = (
                "SSI_FRAME_SLOT_SIZE"
                if section_name == "frame_slot"
                else "SSI_TRACE_RECORD_SIZE"
                if section_name == "trace_record"
                else f"SSI_{prefix}_SIZE"
            )
            lines.append(f"{stride_name} .set {section['stride']}")

        if "count" in section:
            lines.append(f"SSI_{prefix}_COUNT .set {section['count']}")

        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def generate_python(schema: dict) -> str:
    """Return the full text of `pru_io/ssi_config_abi.py`.

    Built as a list of self-contained blocks joined by blank lines, so each
    block's own formatting stays simple regardless of neighbors.
    """
    blocks = []

    blocks.append(
        '"""GENERATED FILE -- do not edit by hand.\n\n'
        "Source: schema/ssi_config_abi.json\n"
        "Regenerate with: python tools/gen_ssi_abi.py\n"
        '"""\n'
        "import struct"
    )

    base_lines = [
        f"{SECTION_BASE_NAME[name]} = 0x{ICSS_SHARED_BASE + schema[name]['base']:08X}"
        for name in SECTION_ORDER
    ]
    blocks.append("\n".join(base_lines))

    size_lines = [f"PAUSE_INNER_ITERS = {PAUSE_INNER_ITERS}"]
    for section_name in SECTION_ORDER:
        section = schema[section_name]
        prefix = section_name.upper()
        if "count" in section:
            size_lines.append(f"{prefix}_COUNT = {section['count']}")
        if "stride" in section and section_name != "config":
            size_name = (
                "FRAME_SLOT_SIZE"
                if section_name == "frame_slot"
                else "TRACE_RECORD_SIZE"
                if section_name == "trace_record"
                else f"{prefix}_SIZE"
            )
            size_lines.append(f"{size_name} = {section['stride']}")
    blocks.append("\n".join(size_lines))

    constants = schema.get("_constants", {})
    constant_lines = []
    for name, value in (
        ("CONFIG_ABI_VERSION", constants.get("config_abi_version")),
        ("NO_ERROR_FIELD", constants.get("no_error_field")),
        ("IEPCLK_OCP_EN", constants.get("iepclk_ocp_en")),
        ("IEP_TICK_HZ", constants.get("iep_tick_hz")),
        (
            "PRODUCER_SAMPLE_Q_FRACTION_BITS",
            constants.get("producer_sample_q_fraction_bits"),
        ),
        (
            "DEFAULT_PRODUCER_PERIOD_IEP_TICKS",
            constants.get("default_producer_period_iep_ticks"),
        ),
        (
            "DEFAULT_PRODUCER_SAMPLE_AGE_LIMIT_IEP_TICKS",
            constants.get("default_producer_sample_age_limit_iep_ticks"),
        ),
        (
            "DEFAULT_PRODUCER_PREDICTION_HORIZON_LIMIT_IEP_TICKS",
            constants.get("default_producer_prediction_horizon_limit_iep_ticks"),
        ),
    ):
        if value is not None:
            constant_lines.append(f"{name} = {value}")
    for enum_values in constants.get("enums", {}).values():
        for name, value in enum_values.items():
            constant_lines.append(f"{name} = {value}")
    if constant_lines:
        blocks.append("\n".join(constant_lines))

    for section_name in SECTION_ORDER:
        section = schema[section_name]
        prefix = section_name.upper()
        off_lines = [
            f"{prefix}_{field['name'].upper()}_OFF = 0x{field['offset']:X}"
            for field in section["fields"]
            if not _is_reserved(field)
        ]
        if off_lines:
            blocks.append("\n".join(off_lines))

    blocks.append(f"FRAME_SLOT_UNUSED_SENTINEL = 0x{_frame_slot_sentinel(schema):016X}")

    config_fields = schema["config"]["fields"]
    config_size = max(field["offset"] + field["size"] for field in config_fields)
    blocks.append(f"_CONFIG_SIZE = {config_size}")

    def field_dict_text(var_name, fields):
        entries = [
            f'    "{field["name"]}": (0x{field["offset"]:X}, "{TYPE_INFO[field["type"]][1]}"),'
            for field in fields
            if not _is_reserved(field)
        ]
        return var_name + " = {\n" + "\n".join(entries) + "\n}"

    blocks.append(field_dict_text("_CONFIG_FIELDS", schema["config"]["fields"]))
    blocks.append(field_dict_text("_MAILBOX_FIELDS", schema["mailbox"]["fields"]))
    blocks.append(field_dict_text("_TRACE_RECORD_FIELDS", schema["trace_record"]["fields"]))
    blocks.append(
        field_dict_text("_PRODUCER_SAMPLE_FIELDS", schema["producer_sample"]["fields"])
    )
    blocks.append(
        field_dict_text(
            "_PRODUCER_SAMPLE_DIAGNOSTICS_FIELDS",
            schema["producer_sample_diagnostics"]["fields"],
        )
    )

    blocks.append(
        "def pack_config(**fields):\n"
        '    """Pack config fields into a 256-byte little-endian buffer.\n\n'
        "    Fields not passed default to 0. Raises ValueError on an unknown\n"
        "    field name.\n"
        '    """\n'
        "    buf = bytearray(_CONFIG_SIZE)\n"
        "    for name, value in fields.items():\n"
        "        if name not in _CONFIG_FIELDS:\n"
        '            raise ValueError(f"unknown config field: {name!r}")\n'
        "        offset, fmt = _CONFIG_FIELDS[name]\n"
        '        struct.pack_into("<" + fmt, buf, offset, value)\n'
        "    return bytes(buf)"
    )

    blocks.append(
        "def unpack_config(data):\n"
        '    """Inverse of pack_config: 256 bytes -> dict of named field -> int."""\n'
        "    return {\n"
        '        name: struct.unpack_from("<" + fmt, data, offset)[0]\n'
        "        for name, (offset, fmt) in _CONFIG_FIELDS.items()\n"
        "    }"
    )

    blocks.append(
        "def unpack_mailbox(data):\n"
        '    """Mailbox section\'s 64 bytes -> dict of named field -> int."""\n'
        "    return {\n"
        '        name: struct.unpack_from("<" + fmt, data, offset)[0]\n'
        "        for name, (offset, fmt) in _MAILBOX_FIELDS.items()\n"
        "    }"
    )

    blocks.append(
        "def unpack_trace_record(data):\n"
        '    """One 24-byte trace record -> dict of named field -> int."""\n'
        "    return {\n"
        '        name: struct.unpack_from("<" + fmt, data, offset)[0]\n'
        "        for name, (offset, fmt) in _TRACE_RECORD_FIELDS.items()\n"
        "    }"
    )

    blocks.append(
        "def pack_producer_sample(write_seq, timestamp_iep, position_q31_32, "
        "generation=0, flags=0):\n"
        '    """Pack one 32-byte timestamped producer sample entry.\n\n'
        "    The producer writes an odd write_seq before the payload and the\n"
        "    matching even write_seq after the payload is complete.\n"
        '    """\n'
        "    return struct.pack(\"<QQqII\", write_seq, timestamp_iep,\n"
        "                       position_q31_32, generation, flags)"
    )

    blocks.append(
        "def pack_producer_sample_payload(timestamp_iep, position_q31_32, "
        "generation=0, flags=0):\n"
        '    """Pack the 24-byte payload read between write_seq checks."""\n'
        "    return struct.pack(\"<QqII\", timestamp_iep, position_q31_32,\n"
        "                       generation, flags)\n\n"
        "def unpack_producer_sample(data):\n"
        '    """Unpack one complete 32-byte producer sample entry."""\n'
        "    return {\n"
        '        name: struct.unpack_from("<" + fmt, data, offset)[0]\n'
        "        for name, (offset, fmt) in _PRODUCER_SAMPLE_FIELDS.items()\n"
        "    }\n\n"
        "def unpack_producer_sample_payload(data, write_seq):\n"
        '    """Unpack a payload captured while *write_seq* was stable."""\n'
        "    values = {\n"
        '        name: struct.unpack_from("<" + fmt, data, offset - 8)[0]\n'
        "        for name, (offset, fmt) in _PRODUCER_SAMPLE_FIELDS.items()\n"
        "        if name != \"write_seq\"\n"
        "    }\n"
        '    values["write_seq"] = write_seq\n'
        "    return values"
    )

    blocks.append(
        "def pack_producer_head(head_seq, latest_slot_index, "
        "latest_stable_sample_seq):\n"
        '    """Pack the 16-byte constant-time producer head."""\n'
        "    return struct.pack(\"<IIQ\", head_seq, latest_slot_index,\n"
        "                       latest_stable_sample_seq)\n\n"
        "def pack_producer_head_payload(latest_slot_index, "
        "latest_stable_sample_seq):\n"
        '    """Pack the 12-byte payload read between head_seq checks."""\n'
        "    return struct.pack(\"<IQ\", latest_slot_index,\n"
        "                       latest_stable_sample_seq)\n\n"
        "def unpack_producer_head(data):\n"
        '    """Unpack the 16-byte producer head."""\n'
        "    head_seq, latest_slot_index, latest_stable_sample_seq = "
        "struct.unpack(\"<IIQ\", data)\n"
        "    return {\n"
        '        "head_seq": head_seq,\n'
        '        "latest_slot_index": latest_slot_index,\n'
        '        "latest_stable_sample_seq": latest_stable_sample_seq,\n'
        "    }\n\n"
        "def unpack_producer_head_payload(data, head_seq):\n"
        '    """Unpack a head payload captured while *head_seq* was stable."""\n'
        "    latest_slot_index, latest_stable_sample_seq = "
        "struct.unpack(\"<IQ\", data)\n"
        "    return {\n"
        '        "head_seq": head_seq,\n'
        '        "latest_slot_index": latest_slot_index,\n'
        '        "latest_stable_sample_seq": latest_stable_sample_seq,\n'
        "    }"
    )

    blocks.append(
        "def pack_frame_slot(frame_bits, hold_override=0):\n"
        '    """Pack one 16-byte prepacked emulator frame slot."""\n'
        '    return struct.pack("<QI4x", frame_bits, hold_override)'
    )

    return "\n\n\n".join(blocks) + "\n"


def _c_field_macro(section_name: str, field_name: str) -> str:
    field = field_name.upper()
    prefixes = {
        "config": "SSI_CONFIG_OFF_",
        "frame_slot": "SSI_FRAME_SLOT_OFF_",
        "mailbox": "SSI_MAILBOX_OFF_",
        "capture": "SSI_CAPTURE_OFF_",
        "trace_record": "SSI_TRACE_RECORD_",
        "producer_sample": "SSI_PRODUCER_SAMPLE_OFF_",
        "producer_sample_diagnostics": "SSI_PRODUCER_SAMPLE_DIAGNOSTICS_OFF_",
    }
    prefix = prefixes[section_name]
    return f"{prefix}{field}_OFF" if section_name == "trace_record" else f"{prefix}{field}"


def generate_c(schema: dict) -> str:
    """Return the checked-in C snapshot used by the R5/CCS project."""
    constants = schema.get("_constants", {})
    config_size = max(
        field["offset"] + field["size"] for field in schema["config"]["fields"]
    )
    lines = [
        "/*",
        " * GENERATED FILE -- source: schema/ssi_config_abi.json",
        " *",
        " * Regenerate with: python tools/gen_ssi_abi.py",
        " */",
        "#ifndef SSI_CONFIG_ABI_H_",
        "#define SSI_CONFIG_ABI_H_",
        "",
        "#include <stdint.h>",
        "",
        f"#define SSI_CONFIG_ABI_VERSION                 ({constants.get('config_abi_version', 1)}U)",
        f"#define SSI_CONFIG_SIZE                         ({config_size}U)",
        f"#define SSI_PAUSE_INNER_ITERS                  ({PAUSE_INNER_ITERS}U)",
        f"#define SSI_NO_ERROR_FIELD                     (0x{constants.get('no_error_field', 0xFFFF):04X}U)",
        f"#define SSI_IEPCLK_OCP_EN                      ({constants.get('iepclk_ocp_en', 0)}U)",
        f"#define SSI_IEP_TICK_HZ                         ({constants.get('iep_tick_hz', 0)}U)",
        "",
    ]

    q_bits = constants.get("producer_sample_q_fraction_bits")
    period = constants.get("default_producer_period_iep_ticks")
    age_limit = constants.get("default_producer_sample_age_limit_iep_ticks")
    horizon_limit = constants.get(
        "default_producer_prediction_horizon_limit_iep_ticks"
    )
    if q_bits is not None:
        lines.append(f"#define SSI_PRODUCER_SAMPLE_Q_FRACTION_BITS ({q_bits}U)")
    if period is not None:
        lines.append(f"#define SSI_DEFAULT_PRODUCER_PERIOD_IEP_TICKS ({period}U)")
    if age_limit is not None:
        lines.append(
            f"#define SSI_DEFAULT_PRODUCER_SAMPLE_AGE_LIMIT_IEP_TICKS ({age_limit}U)"
        )
    if horizon_limit is not None:
        lines.append(
            f"#define SSI_DEFAULT_PRODUCER_PREDICTION_HORIZON_LIMIT_IEP_TICKS ({horizon_limit}U)"
        )
    lines.append("")

    section_offsets = {
        "frame_slot": "SSI_FRAMES_OFF",
        "mailbox": "SSI_MAILBOX_OFF",
        "capture": "SSI_CAPTURE_OFF",
        "trace_record": "SSI_TRACE_OFF",
        "producer_sample": "SSI_PRODUCER_SAMPLES_OFF",
        "producer_sample_diagnostics": "SSI_PRODUCER_SAMPLE_DIAGNOSTICS_OFF",
    }
    for section_name in SECTION_ORDER:
        section = schema[section_name]
        if section_name != "config":
            macro = section_offsets[section_name]
            lines.append(f"#define {macro:<45} (0x{section['base']:04X}U)")
        if "count" in section:
            name = (
                "SSI_FRAME_SLOT_COUNT"
                if section_name == "frame_slot"
                else "SSI_TRACE_RECORD_COUNT"
                if section_name == "trace_record"
                else f"SSI_{section_name.upper()}_COUNT"
            )
            lines.append(f"#define {name:<45} ({section['count']}U)")
        if "stride" in section and section_name != "config":
            name = (
                "SSI_FRAME_SLOT_SIZE"
                if section_name == "frame_slot"
                else "SSI_TRACE_RECORD_SIZE"
                if section_name == "trace_record"
                else f"SSI_{section_name.upper()}_SIZE"
            )
            lines.append(f"#define {name:<45} ({section['stride']}U)")
        for field in section["fields"]:
            if _is_reserved(field):
                continue
            macro = _c_field_macro(section_name, field["name"])
            lines.append(f"#define {macro:<45} (0x{field['offset']:02X}U)")
        lines.append("")

    lines.extend(
        [
            "#define SSI_CONFIG_OFF_FORMATION_PAUSE_ITERS SSI_CONFIG_OFF_FORMATION_PAUSE_OUTER_ITERS",
            "#define SSI_FRAME_SLOT_OFF_HOLD_OVERRIDE SSI_FRAME_SLOT_OFF_HOLD_OVERRIDE_CYCLES_OR_FRAMES",
            "",
        ]
    )

    for enum_values in constants.get("enums", {}).values():
        lines.append("enum {")
        for name, value in enum_values.items():
            lines.append(f"    {name} = {value},")
        lines.extend(["};", ""])

    lines.extend(["#endif /* SSI_CONFIG_ABI_H_ */", ""])
    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate SSI ABI snapshots")
    parser.add_argument(
        "--parent-include-dir",
        type=Path,
        help="also write CCS ssi_config_abi.h/.inc snapshots in this directory",
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
        (args.parent_include_dir / "ssi_config_abi.h").write_text(
            generate_c(schema), encoding="utf-8", newline="\n"
        )
        (args.parent_include_dir / "ssi_config_abi.inc").write_text(
            generate_inc(schema), encoding="utf-8", newline="\n"
        )

    print(f"wrote {INC_PATH}")
    print(f"wrote {PY_PATH}")
    print(f"wrote {C_PATH}")
    if args.parent_include_dir is not None:
        print(f"wrote {args.parent_include_dir / 'ssi_config_abi.h'}")
        print(f"wrote {args.parent_include_dir / 'ssi_config_abi.inc'}")


if __name__ == "__main__":
    main()
