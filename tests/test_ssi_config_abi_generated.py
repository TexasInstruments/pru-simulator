# tests/test_ssi_config_abi_generated.py
"""Drift + round-trip tests for the SSI config ABI schema/generator.

These guard the invariant that `schema/ssi_config_abi.json` is the single
source of truth for `source/ssi_config_abi.inc` and `pru_io/ssi_config_abi.py`
— a schema edit that forgets to re-run `tools/gen_ssi_abi.py` must fail here.
"""
import struct
from pathlib import Path

from tools.gen_ssi_abi import (
    SCHEMA_PATH,
    INC_PATH,
    PY_PATH,
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

    actual_inc = Path(INC_PATH).read_text(encoding="utf-8")
    actual_py = Path(PY_PATH).read_text(encoding="utf-8")

    assert actual_inc == expected_inc
    assert actual_py == expected_py


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
