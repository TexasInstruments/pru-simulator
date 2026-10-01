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


def test_foc_config_round_trip_uses_schema_defaults_and_signed_q15():
    packed = abi.pack_config(alpha_q15=-8192, beta_q15=14189,
                             phase_increment_q32=0x00CCCCCD)

    assert len(packed) == abi.CONFIG_SIZE == 32
    assert abi.unpack_config(packed) == {
        "abi_version": 1,
        "control_period_ticks": 12500,
        "pwm_period_ticks": 12500,
        "phase_increment_q32": 0x00CCCCCD,
        "modulation_q15": 16384,
        "alpha_q15": -8192,
        "beta_q15": 14189,
        "initial_phase_q32": 0,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"control_period_ticks": 0},
        {"pwm_period_ticks": -1},
        {"phase_increment_q32": -1},
        {"modulation_q15": 32769},
        {"alpha_q15": 0x80000000},
        {"beta_q15": True},
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
