"""Generate the config-only FOC firmware ABI from its JSON schema."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "foc_control_abi.json"
OUTPUTS = {
    "python": "pru_io/foc_control_abi.py",
    "assembly": "source/foc_control_abi.inc",
}


def _integer(value, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        result = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if result < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return result


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    with Path(path).open(encoding="utf-8") as source:
        raw = json.load(source)
    version = _integer(raw.get("version"), "version")
    shared_base = _integer(raw.get("shared_base"), "shared_base")
    group = raw.get("control")
    if not isinstance(group, dict) or not isinstance(group.get("fields"), list):
        raise ValueError("schema must contain a control field group")
    group_offset = _integer(group.get("offset"), "control.offset")
    fields = []
    names = set()
    expected_offset = 0
    for field in group["fields"]:
        if not isinstance(field, dict):
            raise ValueError("control fields must be objects")
        name = field.get("name")
        kind = field.get("type")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("control field names must be unique non-empty strings")
        if kind not in ("u32", "s32"):
            raise ValueError(f"unsupported FOC ABI field type {kind!r}")
        offset = _integer(field.get("offset"), f"control.{name}.offset")
        if offset != expected_offset or offset % 4:
            raise ValueError("control fields must be contiguous and 4-byte aligned")
        expected_offset += 4
        names.add(name)
        item = {"name": name, "type": kind, "offset": offset}
        if name == "abi_version":
            if kind != "u32" or offset != 0:
                raise ValueError("abi_version must be the first u32 control field")
        else:
            if "default" not in field:
                raise ValueError(f"control.{name} requires a schema default")
            item["default"] = field["default"]
        if "minimum" in field:
            item["minimum"] = field["minimum"]
        if "maximum" in field:
            item["maximum"] = field["maximum"]
        fields.append(item)
    if not fields or fields[0]["name"] != "abi_version":
        raise ValueError("abi_version must be the first control field")
    for field in fields:
        if field["name"] == "abi_version":
            continue
        low, high = ((0, 0xFFFFFFFF) if field["type"] == "u32"
                     else (-0x80000000, 0x7FFFFFFF))
        default = field["default"]
        if isinstance(default, bool) or not isinstance(default, int) or not low <= default <= high:
            raise ValueError(f"control.{field['name']}.default is outside {field['type']}")
        if not low <= field.get("minimum", low) <= high:
            raise ValueError(f"control.{field['name']}.minimum is outside {field['type']}")
        if not low <= field.get("maximum", high) <= high:
            raise ValueError(f"control.{field['name']}.maximum is outside {field['type']}")
    return {"version": version, "shared_base": shared_base,
            "control_offset": group_offset, "fields": fields,
            "control_size": expected_offset}


def _constants(schema: dict) -> list[tuple[str, int]]:
    values = [
        ("FOC_ABI_VERSION", schema["version"]),
        ("FOC_SHARED_BASE", schema["shared_base"]),
        ("FOC_CONTROL_OFFSET", schema["control_offset"]),
        ("FOC_CONTROL_ADDRESS", schema["shared_base"] + schema["control_offset"]),
    ]
    for field in schema["fields"]:
        name = field["name"].upper()
        values.append((f"FOC_{name}_OFFSET", field["offset"]))
        values.append((f"FOC_{name}_OFFSET_FROM_SHARED",
                       schema["control_offset"] + field["offset"]))
    return values


def _generate_assembly(schema: dict) -> str:
    lines = ["; GENERATED FILE -- edit schema/foc_control_abi.json, then run python -m tools.gen_foc_abi."]
    lines.extend(f"{name} .set 0x{value:08X}" for name, value in _constants(schema))
    return "\n".join(lines) + "\n"


def _generate_python(schema: dict) -> str:
    fields = schema["fields"]
    configurable = fields[1:]
    signature = ", ".join(
        f"{field['name']}={field['default']!r}" for field in configurable)
    format_string = "".join("I" if field["type"] == "u32" else "i"
                            for field in fields)
    lines = [
        '"""GENERATED FILE -- source: schema/foc_control_abi.json."""',
        "import struct", "", "_U32_MAX = 0xFFFFFFFF", "_I32_MIN = -0x80000000",
        "_I32_MAX = 0x7FFFFFFF", "",
    ]
    lines.extend(f"{name.removeprefix('FOC_')} = 0x{value:X}"
                 for name, value in _constants(schema))
    lines.extend([
        f"CONFIG_SIZE = {schema['control_size']}",
        f"_CONFIG_STRUCT = struct.Struct('<{format_string}')", "",
        "def _u32(name, value):",
        "    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _U32_MAX:",
        "        raise ValueError(f'{name} must be an unsigned 32-bit integer')",
        "    return value", "",
        "def _s32(name, value):",
        "    if isinstance(value, bool) or not isinstance(value, int) or not _I32_MIN <= value <= _I32_MAX:",
        "        raise ValueError(f'{name} must be a signed 32-bit integer')",
        "    return value", "",
        f"def pack_config({signature}):",
    ])
    for field in configurable:
        name = field["name"]
        kind = "u32" if field["type"] == "u32" else "s32"
        lines.append(f"    _{kind}('{name}', {name})")
        if "minimum" in field:
            lines.append(f"    if {name} < {field['minimum']!r}:")
            lines.append(f"        raise ValueError('{name} must be at least {field['minimum']}')")
        if "maximum" in field:
            lines.append(f"    if {name} > {field['maximum']!r}:")
            lines.append(f"        raise ValueError('{name} must be at most {field['maximum']}')")
    values = ["ABI_VERSION"]
    values.extend(f"_{'u32' if f['type'] == 'u32' else 's32'}('{f['name']}', {f['name']})"
                  for f in configurable)
    lines.append("    return _CONFIG_STRUCT.pack(" + ", ".join(values) + ")")
    names = ", ".join(repr(field["name"]) for field in fields)
    lines.extend([
        "", "def unpack_config(buffer):", "    data = bytes(buffer)",
        "    if len(data) != CONFIG_SIZE:",
        "        raise ValueError(f'expected {CONFIG_SIZE} bytes, got {len(data)}')",
        f"    return dict(zip(({names}), _CONFIG_STRUCT.unpack(data)))",
    ])
    return "\n".join(lines) + "\n"


def generate_files(schema: dict) -> dict[str, str]:
    return {OUTPUTS["python"]: _generate_python(schema),
            OUTPUTS["assembly"]: _generate_assembly(schema)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    mismatch = []
    for relative, content in generate_files(load_schema()).items():
        path = ROOT / relative
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                mismatch.append(relative)
        else:
            path.write_text(content, encoding="utf-8")
    if mismatch:
        parser.error("generated FOC ABI files differ: " + ", ".join(mismatch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
