"""Tests for the schema-generated open-loop FOC shared-memory ABI."""

import json
from pathlib import Path

import pytest

from pru_io import foc_abi as abi
from tools.gen_foc_abi import (
    C_PATH,
    INC_PATH,
    PY_PATH,
    SCHEMA_PATH,
    generate_c,
    generate_inc,
    generate_python,
    load_schema,
)


def test_foc_sections_use_the_documented_shared_memory_windows():
    schema = load_schema(SCHEMA_PATH)

    assert schema["control"]["base"] == 0x0000
    assert schema["pwm_out"]["base"] == 0x0100
    assert schema["motor_fb"]["base"] == 0x0200
    assert schema["sine_lut"]["base"] == 0x1000
    assert schema["sine_lut"]["count"] == 2048
    assert schema["sine_lut"]["stride"] == 4
    assert schema["_constants"]["q_fraction_bits"] == 24


def test_generated_foc_artifacts_match_the_schema():
    schema = load_schema(SCHEMA_PATH)

    assert INC_PATH.read_text(encoding="utf-8") == generate_inc(schema)
    assert PY_PATH.read_text(encoding="utf-8") == generate_python(schema)
    assert C_PATH.read_text(encoding="utf-8") == generate_c(schema)


def test_control_round_trip_preserves_signed_references_and_metadata():
    packed = abi.pack_control(
        enable=1,
        requested_generation=7,
        pru_ack_generation=6,
        speed_ref_q24=abi.Q_ONE // 2,
        id_ref_q24=-abi.Q_ONE // 4,
        iq_ref_q24=abi.Q_ONE,
        ramp_rate_q24=abi.Q_ONE // 100,
    )

    assert len(packed) == abi.CONTROL_SIZE
    assert abi.unpack_control(packed) == {
        "abi_version": abi.ABI_VERSION,
        "struct_size": abi.CONTROL_SIZE,
        "enable": 1,
        "requested_generation": 7,
        "pru_ack_generation": 6,
        "speed_ref_q24": abi.Q_ONE // 2,
        "id_ref_q24": -abi.Q_ONE // 4,
        "iq_ref_q24": abi.Q_ONE,
        "ramp_rate_q24": abi.Q_ONE // 100,
    }


def test_coherent_pwm_read_retries_odd_and_changed_sequences():
    payload = abi.pack_pwm_out(
        seq=4,
        ta_q24=abi.Q_ONE // 2,
        tb_q24=abi.Q_ONE // 3,
        tc_q24=abi.Q_ONE // 4,
        valpha_q24=-abi.Q_ONE // 5,
        vbeta_q24=abi.Q_ONE // 6,
        theta_cmd_u32=0x12345678,
        loop_counter=12,
        timestamp_cycles=99,
    )
    reads = iter([1, 4, 5, 6, 6])

    result = abi.read_coherent_pwm_out(
        lambda: next(reads),
        lambda: payload[4:],
    )

    assert result["seq"] == 6
    assert result["ta_q24"] == abi.Q_ONE // 2
    assert result["valpha_q24"] == -abi.Q_ONE // 5
    assert result["theta_cmd_u32"] == 0x12345678


def test_schema_rejects_shared_memory_overlap(tmp_path):
    raw = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    raw["pwm_out"]["base"] = "0x0020"
    path = Path(tmp_path) / "overlap.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="overlap"):
        load_schema(path)


def test_schema_rejects_section_past_shared_window(tmp_path):
    raw = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    raw["sine_lut"]["base"] = "0xF000"
    path = Path(tmp_path) / "past-window.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="outside shared-memory bounds"):
        load_schema(path)
