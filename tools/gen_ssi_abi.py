#!/usr/bin/env python3
"""Generate the SSI config ABI's PRU-assembly `.inc` and Python module from
`schema/ssi_config_abi.json`.

Usage: python tools/gen_ssi_abi.py

Both generated files are checked in; `tests/test_ssi_config_abi_generated.py`
regenerates them in memory and diffs against the checked-in copies so a
schema edit that forgets to re-run this script fails immediately.
"""
import json
import os
import struct
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

SCHEMA_PATH = _ROOT / "schema" / "ssi_config_abi.json"
INC_PATH = _ROOT / "source" / "ssi_config_abi.inc"
PY_PATH = _ROOT / "pru_io" / "ssi_config_abi.py"

#: ICSS_SHARED base address (matches `c28` in config/constants_am243x.cfg).
ICSS_SHARED_BASE = 0x00010000

#: Fixed inner-loop count the design doc's `*_pause_outer_iters` fields are
#: multiplied against. Not configurable, not in the schema.
PAUSE_INNER_ITERS = 250

#: Section processing order (also the order fields appear in generated output).
SECTION_ORDER = ("config", "frame_slot", "mailbox", "capture", "trace_record")

#: Section key -> generated base-address constant name (without the SSI_/no
#: prefix; callers add SSI_ for the .inc and leave it bare for Python).
SECTION_BASE_NAME = {
    "config": "CONFIG_BASE",
    "frame_slot": "FRAMES_BASE",
    "mailbox": "MAILBOX_BASE",
    "capture": "CAPTURE_BASE",
    "trace_record": "TRACE_BASE",
}

#: Scalar type -> (byte size, struct format char).
TYPE_INFO = {
    "u8": (1, "B"),
    "u16": (2, "H"),
    "u32": (4, "I"),
    "u64": (8, "Q"),
}


def _is_reserved(field: dict) -> bool:
    return field["name"].startswith("_")


def load_schema(path) -> dict:
    """Load and validate the ABI schema.

    Converts hex-string offsets/bases to `int`. Validates that within each
    section, fields sorted by offset tile the section exactly: no overlaps,
    no undocumented gaps.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    schema = {}
    for section_name, section in raw.items():
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
            stride_name = "SSI_FRAME_SLOT_SIZE" if section_name == "frame_slot" else "SSI_TRACE_RECORD_SIZE"
            lines.append(f"{stride_name} .set {section['stride']}")

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

    blocks.append(
        f"FRAME_SLOT_SIZE = {schema['frame_slot']['stride']}\n"
        f"TRACE_RECORD_SIZE = {schema['trace_record']['stride']}\n"
        f"PAUSE_INNER_ITERS = {PAUSE_INNER_ITERS}"
    )

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

    blocks.append("_CONFIG_SIZE = 256")

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
        "def pack_frame_slot(frame_bits, hold_override=0):\n"
        '    """Pack one 16-byte prepacked emulator frame slot."""\n'
        '    return struct.pack("<QI4x", frame_bits, hold_override)'
    )

    return "\n\n\n".join(blocks) + "\n"


def main():
    schema = load_schema(SCHEMA_PATH)

    INC_PATH.parent.mkdir(parents=True, exist_ok=True)
    INC_PATH.write_text(generate_inc(schema), encoding="utf-8", newline="\n")

    PY_PATH.parent.mkdir(parents=True, exist_ok=True)
    PY_PATH.write_text(generate_python(schema), encoding="utf-8", newline="\n")

    print(f"wrote {INC_PATH}")
    print(f"wrote {PY_PATH}")


if __name__ == "__main__":
    main()
