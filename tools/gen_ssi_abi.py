"""Generate the Python and PRU assembly views of the SSI shared-memory ABI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "ssi_config_abi.json"
OUTPUTS = {
    "python": "pru_io/ssi_config_abi.py",
    "assembly": "source/ssi_config_abi.inc",
}
_EXPECTED_FIELDS = {
    "config": ("abi_version", "frame_bits", "clock_delay_loops", "idle_delay_loops"),
    "mailbox": ("sequence", "raw_frame_lo", "raw_frame_hi", "frame_count", "status"),
    "emulator": ("abi_version", "frame_bits", "frame_lo", "frame_hi", "status", "monoflop_ticks"),
}


def _integer(value, name: str) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, str):
            parsed = int(value, 0)
        elif isinstance(value, int):
            parsed = value
        else:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer or base-prefixed string") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return parsed


def _validate_schema(schema: dict) -> dict:
    version = _integer(schema.get("version"), "version")
    shared_base = _integer(schema.get("shared_base"), "shared_base")
    groups = {}
    for group_name, expected_names in _EXPECTED_FIELDS.items():
        group = schema.get(group_name)
        if not isinstance(group, dict):
            raise ValueError(f"schema is missing {group_name!r}")
        group_offset = _integer(group.get("offset"), f"{group_name}.offset")
        fields = group.get("fields")
        if not isinstance(fields, list):
            raise ValueError(f"{group_name}.fields must be an array")
        by_name = {}
        for field in fields:
            if not isinstance(field, dict) or field.get("type") != "u32":
                raise ValueError(f"{group_name} fields must have type 'u32'")
            name = field.get("name")
            if not isinstance(name, str) or name in by_name:
                raise ValueError(f"invalid or duplicate {group_name} field name")
            offset = _integer(field.get("offset"), f"{group_name}.{name}.offset")
            if offset % 4:
                raise ValueError(f"{group_name}.{name}.offset must be 4-byte aligned")
            by_name[name] = offset
        if tuple(by_name) != expected_names:
            raise ValueError(f"{group_name} fields must be ordered as {expected_names}")
        groups[group_name] = {"offset": group_offset, "fields": by_name}
    return {"version": version, "shared_base": shared_base, **groups}


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    """Load and validate the versioned SSI ABI JSON schema."""
    with Path(path).open(encoding="utf-8") as source:
        schema = json.load(source)
    _validate_schema(schema)
    return schema


def _constants(schema: dict) -> list[tuple[str, int]]:
    values = [("SSI_ABI_VERSION", schema["version"]),
              ("SSI_SHARED_BASE", schema["shared_base"])]
    for group_name in _EXPECTED_FIELDS:
        group = schema[group_name]
        prefix = f"SSI_{group_name.upper()}"
        values.append((f"{prefix}_SIZE", max(group["fields"].values()) + 4))
        values.append((f"{prefix}_OFFSET", group["offset"]))
        values.append((f"{prefix}_ADDRESS",
                       schema["shared_base"] + group["offset"]))
        for name, offset in group["fields"].items():
            values.append((f"{prefix}_{name.upper()}_OFFSET", offset))
            values.append((f"{prefix}_{name.upper()}_OFFSET_FROM_SHARED",
                           group["offset"] + offset))
    return values


def _generate_assembly(schema: dict) -> str:
    lines = ["; GENERATED FILE -- edit schema/ssi_config_abi.json, then run python -m tools.gen_ssi_abi."]
    lines.extend(f"{name} .set 0x{value:08X}" for name, value in _constants(schema))
    return "\n".join(lines) + "\n"


def _generate_python(schema: dict) -> str:
    config = schema["config"]
    mailbox = schema["mailbox"]
    emulator = schema["emulator"]
    lines = [
        '"""GENERATED FILE -- source: schema/ssi_config_abi.json."""',
        "import struct",
        "",
    ]
    for name, value in _constants(schema):
        python_name = name.removeprefix("SSI_")
        lines.append(f"{python_name} = 0x{value:X}")
    lines.extend([
        f"_CONFIG_STRUCT = struct.Struct('<{'I' * len(config['fields'])}')",
        f"_MAILBOX_STRUCT = struct.Struct('<{'I' * len(mailbox['fields'])}')",
        f"_EMULATOR_STRUCT = struct.Struct('<{'I' * len(emulator['fields'])}')",
        "_U32_MAX = 0xFFFFFFFF",
        "",
        "def _u32(name, value):",
        "    if (isinstance(value, bool) or not isinstance(value, int)",
        "            or not 0 <= value <= _U32_MAX):",
        "        raise ValueError(f'{name} must be an unsigned 32-bit integer')",
        "    return value",
        "",
        "def pack_config(frame_bits, clock_delay_loops, idle_delay_loops):",
        "    if (isinstance(frame_bits, bool) or not isinstance(frame_bits, int)",
        "            or not 1 <= frame_bits <= 64):",
        "        raise ValueError('frame_bits must be an integer from 1 to 64')",
        "    return _CONFIG_STRUCT.pack(",
        "        ABI_VERSION, _u32('frame_bits', frame_bits),",
        "        _u32('clock_delay_loops', clock_delay_loops),",
        "        _u32('idle_delay_loops', idle_delay_loops),",
        "    )",
        "",
        "def pack_emulator_config(frame_bits, frame_lo, frame_hi, monoflop_ticks):",
        "    if (isinstance(frame_bits, bool) or not isinstance(frame_bits, int)",
        "            or not 1 <= frame_bits <= 64):",
        "        raise ValueError('frame_bits must be an integer from 1 to 64')",
        "    return _EMULATOR_STRUCT.pack(",
        "        ABI_VERSION, frame_bits, _u32('frame_lo', frame_lo),",
        "        _u32('frame_hi', frame_hi), 0, _u32('monoflop_ticks', monoflop_ticks),",
        "    )",
        "",
        "def _unpack(structure, buffer, size, names):",
        "    data = bytes(buffer)",
        "    if len(data) != size:",
        "        raise ValueError(f'expected {size} bytes, got {len(data)}')",
        "    return dict(zip(names, structure.unpack(data)))",
        "",
        "def unpack_config(buffer):",
        "    return _unpack(_CONFIG_STRUCT, buffer, CONFIG_SIZE, (",
        "        'abi_version', 'frame_bits', 'clock_delay_loops',",
        "        'idle_delay_loops',",
        "    ))",
        "",
        "def unpack_mailbox(buffer):",
        "    return _unpack(_MAILBOX_STRUCT, buffer, MAILBOX_SIZE, (",
        "        'sequence', 'raw_frame_lo', 'raw_frame_hi', 'frame_count', 'status',",
        "    ))",
        "",
        "def unpack_emulator(buffer):",
        "    return _unpack(_EMULATOR_STRUCT, buffer, EMULATOR_SIZE, (",
        "        'abi_version', 'frame_bits', 'frame_lo', 'frame_hi', 'status', 'monoflop_ticks',",
        "    ))",
        "",
    ])
    return "\n".join(lines)


def generate_files(schema: dict) -> dict[str, str]:
    """Return generated file contents keyed by repository-relative path."""
    normalized = _validate_schema(schema)
    return {
        OUTPUTS["python"]: _generate_python(normalized),
        OUTPUTS["assembly"]: _generate_assembly(normalized),
    }


def write_files(schema: dict | None = None) -> list[Path]:
    """Write the checked-in generated files and return their paths."""
    contents = generate_files(load_schema() if schema is None else schema)
    written = []
    for relative, content in contents.items():
        path = ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail if checked-in generated files are stale")
    args = parser.parse_args()
    contents = generate_files(load_schema())
    if args.check:
        stale = [relative for relative, expected in contents.items()
                 if not (ROOT / relative).exists()
                 or (ROOT / relative).read_text(encoding="utf-8") != expected]
        if stale:
            parser.error("generated SSI ABI files are stale: " + ", ".join(stale))
        return
    for path in write_files(load_schema()):
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
