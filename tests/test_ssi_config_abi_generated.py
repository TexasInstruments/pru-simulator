# tests/test_ssi_config_abi_generated.py
"""Drift + round-trip tests for the SSI config ABI schema/generator.

These guard the invariant that `schema/ssi_config_abi.json` is the single
source of truth for `source/ssi_config_abi.inc` and `pru_io/ssi_config_abi.py`
— a schema edit that forgets to re-run `tools/gen_ssi_abi.py` must fail here.
"""
import json
import struct
from pathlib import Path

import pytest

from tools.gen_ssi_abi import (
    C_PATH,
    SCHEMA_PATH,
    INC_PATH,
    PY_PATH,
    generate_c,
    load_schema,
    generate_inc,
    generate_python,
)
from pru_io import ssi_config_abi as abi


def test_generated_files_match_schema():
    """The checked-in .inc/.py files must equal what the generator produces
    right now, in memory, from the checked-in schema."""
    schema = load_schema(SCHEMA_PATH)

    expected_inc = generate_inc(schema)
    expected_py = generate_python(schema)
    expected_c = generate_c(schema)

    actual_inc = Path(INC_PATH).read_text(encoding="utf-8")
    actual_py = Path(PY_PATH).read_text(encoding="utf-8")
    actual_c = Path(C_PATH).read_text(encoding="utf-8")

    assert actual_inc == expected_inc
    assert actual_py == expected_py
    assert actual_c == expected_c


def test_timestamped_sample_ring_layout_is_generated_from_schema():
    schema = load_schema(SCHEMA_PATH)
    sample = schema["producer_sample"]

    assert sample["base"] == 0x6400
    assert sample["count"] == 256
    assert sample["stride"] == 32
    assert [field["name"] for field in sample["fields"]] == [
        "write_seq",
        "timestamp_iep",
        "position_q31_32",
        "generation",
        "flags",
    ]


def test_pru_assembly_snapshot_contains_generated_mode_and_validity_definitions():
    generated = Path(INC_PATH).read_text(encoding="utf-8")
    assert "SSI_PRODUCER_MODE_STATIC_SEQUENCE .set 0" in generated
    assert "SSI_PRODUCER_MODE_TIMESTAMPED .set 1" in generated
    assert "SSI_PRODUCER_SAMPLE_FLAG_VALID .set 1" in generated


def test_all_shared_memory_sections_are_non_overlapping_and_in_64kib():
    schema = load_schema(SCHEMA_PATH)
    intervals = []
    for name, section in schema.items():
        if name.startswith("_") or "base" not in section:
            continue
        coverage = section.get("stride", max(
            field["offset"] + field["size"] for field in section["fields"]
        ))
        coverage *= section.get("count", 1)
        intervals.append((section["base"], section["base"] + coverage, name))

    intervals.sort()
    assert intervals[-1][1] <= 0x10000
    for previous, current in zip(intervals, intervals[1:]):
        assert previous[1] <= current[0]


def test_generator_rejects_global_section_overlap(tmp_path):
    raw = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
    raw["producer_sample"]["base"] = "0x6300"
    path = tmp_path / "overlap.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="overlap"):
        load_schema(path)


def test_generator_rejects_section_past_shared_memory_end(tmp_path):
    raw = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
    raw["producer_sample_diagnostics"]["base"] = "0xFFF0"
    path = tmp_path / "out-of-bounds.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="0x10000"):
        load_schema(path)


def test_config_round_trip():
    """pack_config -> unpack_config must return every real field unchanged.

    Reserved fields are excluded from both the input dict and the assertion,
    since unpack_config never surfaces them.
    """
    schema = load_schema(SCHEMA_PATH)
    config_fields = schema["config"]["fields"]

    max_by_type = {"u8": 0xAB, "u16": 0xABCD, "u32": 0xABCDEF01, "u64": 0x0123456789ABCDEF}

    values = {}
    for field in config_fields:
        if field["name"].startswith("_"):
            continue
        values[field["name"]] = max_by_type[field["type"]]

    packed = abi.pack_config(**values)
    assert len(packed) == 256

    unpacked = abi.unpack_config(packed)
    assert unpacked == values


def test_pack_config_unknown_field_raises():
    import pytest

    with pytest.raises(ValueError):
        abi.pack_config(not_a_real_field=1)


def test_unpack_mailbox():
    """Hand-build a 64-byte mailbox buffer and confirm unpack_mailbox
    extracts every field at its documented offset."""
    seq = 4
    raw_frame = 0x0123456789ABCDEF
    position_value = 0xDEADBEEF
    status_bits = 0x00000002
    frame_counter = 42
    timestamp_cycles = 0x1122334455667788

    buf = bytearray(64)
    struct.pack_into("<I", buf, abi.MAILBOX_SEQ_OFF, seq)
    struct.pack_into("<Q", buf, abi.MAILBOX_RAW_FRAME_OFF, raw_frame)
    struct.pack_into("<I", buf, abi.MAILBOX_POSITION_VALUE_OFF, position_value)
    struct.pack_into("<I", buf, abi.MAILBOX_STATUS_BITS_OFF, status_bits)
    struct.pack_into("<I", buf, abi.MAILBOX_FRAME_COUNTER_OFF, frame_counter)
    struct.pack_into("<Q", buf, abi.MAILBOX_TIMESTAMP_CYCLES_OFF, timestamp_cycles)

    result = abi.unpack_mailbox(bytes(buf))

    assert result == {
        "seq": seq,
        "raw_frame": raw_frame,
        "position_value": position_value,
        "status_bits": status_bits,
        "frame_counter": frame_counter,
        "timestamp_cycles": timestamp_cycles,
    }
