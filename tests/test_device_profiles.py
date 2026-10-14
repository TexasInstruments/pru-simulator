import pytest

from pru_io.device_profiles import create_device, discover_device_profiles
from pru_io.device_model import OPEN_DRAIN, PUSH_PULL
from pru_io.ssi_encoder_model import SSIEncoderModel
from pru_io.tca9538_device_model import TCA9538Model


def test_device_discovery_describes_validated_profiles():
    profiles = discover_device_profiles()

    assert set(profiles) == {"ssi_encoder", "tca9538", "foc_motor"}
    assert profiles["ssi_encoder"]["outputs"] == {"data_pin": PUSH_PULL}
    assert profiles["tca9538"]["outputs"] == {
        "scl_pin": OPEN_DRAIN,
        "sda_pin": OPEN_DRAIN,
    }
    assert profiles["foc_motor"]["outputs"] == {
        "current_a_pin": PUSH_PULL,
        "current_b_pin": PUSH_PULL,
    }
    assert profiles["foc_motor"]["pru_output_mask"] == 0x7
    assert profiles["foc_motor"]["supported_cores"] == ["pru0"]


def test_ssi_profile_applies_defaults_and_accepts_overrides():
    model = create_device("ssi_encoder", {
        "name": "axis",
        "position": 0x35,
        "resolution": 8,
        "encoding": "gray",
    })

    assert isinstance(model, SSIEncoderModel)
    assert model.get_state()["name"] == "axis"
    assert model.position == 0x35
    assert model.resolution == 8
    assert model.encoding == "gray"


def test_ssi_profile_discovery_lists_the_presets():
    profile = discover_device_profiles()["ssi_encoder"]

    assert len(profile["presets"]) == 12
    assert profile["presets"]["AFS_AFM60_MULTITURN_30BIT"]["resolution"] == 33
    assert "IM0100079" in profile["presets"]["TTK70"]["source"]
    assert profile["defaults"]["preset"] is None
    assert profile["defaults"]["error_bits"] == 0


def test_ssi_profile_preset_supplies_the_frame_and_explicit_fields_override():
    model = create_device("ssi_encoder", {
        "preset": "AFS_AFM60_SINGLETURN", "position": 0x2AAAA,
        "error_value": 0b011, "encoding": "gray",
    })

    assert (model.preset, model.resolution, model.position_bits,
            model.error_bits, model.error) == ("AFS_AFM60_SINGLETURN", 21, 18, 3, 0b011)
    assert model.encoding == "gray"
    assert model.f_max_hz == 2_000_000
    assert create_device("ssi_encoder", {
        "preset": "TTK70", "resolution": 16, "position_bits": 14,
    }).error_bits == 2


def test_ssi_profile_accepts_layout_fields_without_a_preset():
    model = create_device("ssi_encoder", {
        "resolution": 33, "position_bits": 30, "error_bits": 3,
        "error_value": 5, "position": 1 << 29,
    })

    assert model.pack_frame(model.position, model.error) == (1 << 32) | 5


def test_tca_profile_preserves_open_drain_master_owned_lines():
    model = create_device("tca9538", {"address": 0x24, "name": "io"})

    assert isinstance(model, TCA9538Model)
    assert model.nets == {0: OPEN_DRAIN, 1: OPEN_DRAIN}
    assert model.address == 0x24


@pytest.mark.parametrize(
    ("profile", "config"),
    [
        ("unknown", {}),
        ("ssi_encoder", {"position": 1, "unexpected": 2}),
        ("ssi_encoder", {"resolution": 0}),
        ("ssi_encoder", {"preset": "NOT_A_PRESET"}),
        ("ssi_encoder", {"preset": 7}),
        ("ssi_encoder", {"preset": "TTK70", "error_value": 4}),
        ("ssi_encoder", {"error_bits": 12}),
        ("ssi_encoder", {"error_bits": 2, "error_value": 4}),
        ("ssi_encoder", {"position_bits": 13}),
        ("ssi_encoder", {"error_bits": 2, "error_offset": 11}),
        ("tca9538", {"address": 0x80}),
        ("tca9538", {"scl_pin": 20}),
        ("tca9538", {"scl_pin": True}),
        ("tca9538", {"scl_pin": 1, "sda_pin": 1}),
    ],
)
def test_invalid_device_profile_is_rejected(profile, config):
    with pytest.raises(ValueError):
        create_device(profile, config)
