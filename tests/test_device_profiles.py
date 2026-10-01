import pytest

from pru_io.device_profiles import create_device, discover_device_profiles
from pru_io.device_model import OPEN_DRAIN, PUSH_PULL
from pru_io.ssi_encoder_model import SSIEncoderModel
from pru_io.tca9538_device_model import TCA9538Model


def test_device_discovery_describes_validated_profiles():
    profiles = discover_device_profiles()

    assert set(profiles) == {"ssi_encoder", "tca9538"}
    assert profiles["ssi_encoder"]["outputs"] == {"data_pin": PUSH_PULL}
    assert profiles["tca9538"]["outputs"] == {
        "scl_pin": OPEN_DRAIN,
        "sda_pin": OPEN_DRAIN,
    }


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
        ("tca9538", {"address": 0x80}),
        ("tca9538", {"scl_pin": 20}),
        ("tca9538", {"scl_pin": True}),
        ("tca9538", {"scl_pin": 1, "sda_pin": 1}),
    ],
)
def test_invalid_device_profile_is_rejected(profile, config):
    with pytest.raises(ValueError):
        create_device(profile, config)
