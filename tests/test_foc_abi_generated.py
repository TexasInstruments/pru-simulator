import json

import pytest

from pru_io import foc_control_abi as abi
from tools.gen_foc_abi import ROOT, generate_files, load_schema


def test_generated_foc_abi_matches_schema_and_has_no_feedback_block():
    for relative, expected in generate_files(load_schema()).items():
        assert (ROOT / relative).read_text(encoding="utf-8") == expected

    fields = load_schema()["fields"]
    assert all("feedback" not in field["name"] for field in fields)
    assert all("mailbox" not in field["name"] for field in fields)
    assert [f["name"] for f in fields if f.get("owner") == "pru"] == [
        "ack_generation", "status"]


def test_foc_config_round_trip_uses_schema_defaults_and_signed_values():
    packed = abi.pack_config(enable=1, requested_generation=7,
                             speed_ref_q28=-2**28, ramp_rate_q28=1000,
                             vd_ref_q15=-8192, vq_ref_q15=14189,
                             initial_phase_q32=0x00CCCCCD)

    assert len(packed) == abi.CONFIG_SIZE == 48
    assert abi.unpack_config(packed) == {
        "abi_version": 2,
        "control_period_ticks": 12500,
        "pwm_period_ticks": 12500,
        "enable": 1,
        "requested_generation": 7,
        "speed_ref_q28": -2**28,
        "ramp_rate_q28": 1000,
        "vd_ref_q15": -8192,
        "vq_ref_q15": 14189,
        "initial_phase_q32": 0x00CCCCCD,
        "ack_generation": 0,
        "status": 0,
    }
    assert abi.unpack_config(abi.pack_config())["ramp_rate_q28"] == abi.SPEED_Q28_ONE


def test_pru_owned_words_are_not_host_arguments_and_constants_are_generated():
    with pytest.raises(TypeError):
        abi.pack_config(ack_generation=1)
    assert abi.ACK_GENERATION_OFFSET == 0x28 and abi.STATUS_OFFSET == 0x2C
    assert (abi.STATUS_RUNNING, abi.STATUS_INVALID_CONFIG, abi.STATUS_SATURATED) == (1, 2, 4)
    inc = (ROOT / "source" / "foc_control_abi.inc").read_text()
    assert "FOC_STATUS_SATURATED .set 0x00000004" in inc
    assert "FOC_ACK_GENERATION_OFFSET .set 0x00000028" in inc


@pytest.mark.parametrize(
    "kwargs",
    [
        {"control_period_ticks": 0},
        {"pwm_period_ticks": -1},
        {"enable": 2},
        {"requested_generation": -1},
        {"speed_ref_q28": 2**28 + 1},
        {"speed_ref_q28": -2**28 - 1},
        {"ramp_rate_q28": 2**28 + 1},
        {"vd_ref_q15": 32768},
        {"vq_ref_q15": -32769},
        {"vq_ref_q15": True},
    ],
)
def test_foc_config_rejects_values_outside_schema_ranges(kwargs):
    with pytest.raises(ValueError):
        abi.pack_config(**kwargs)


def test_generator_takes_abi_order_type_and_default_from_schema(tmp_path):
    schema = json.loads((ROOT / "schema" / "foc_control_abi.json").read_text())
    schema["control"]["fields"][1]["default"] = 1000
    altered_schema = tmp_path / "foc_control_abi.json"
    altered_schema.write_text(json.dumps(schema))
    generated = generate_files(load_schema(altered_schema))

    assert "control_period_ticks=1000" in generated["pru_io/foc_control_abi.py"]
