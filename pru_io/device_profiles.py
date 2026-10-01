"""Validated factories for device profiles exposed to host tools."""
from __future__ import annotations

from copy import deepcopy

from pru_io.device_model import OPEN_DRAIN, PUSH_PULL
from pru_io.ssi_encoder_model import SSIEncoderModel
from pru_io.tca9538_device_model import TCA9538Model


_PROFILE_DEFAULTS = {
    "ssi_encoder": {
        "clock_pin": 0,
        "data_pin": 8,
        "position": 0,
        "resolution": 12,
        "encoding": "binary",
        "f_max_hz": 4_000_000,
        "monoflop_us": 20.5,
        "core_clock_hz": 250_000_000,
        "idle_value": 1,
        "name": "ssi_encoder",
    },
    "tca9538": {
        "address": 0x23,
        "scl_pin": 0,
        "sda_pin": 1,
        "name": "tca9538",
    },
}

_PROFILE_OUTPUTS = {
    "ssi_encoder": {"data_pin": PUSH_PULL},
    "tca9538": {"scl_pin": OPEN_DRAIN, "sda_pin": OPEN_DRAIN},
}


def discover_device_profiles() -> dict:
    """Return profile names, defaults, and the pins each profile drives."""
    return {
        name: {
            "defaults": deepcopy(defaults),
            "outputs": dict(_PROFILE_OUTPUTS[name]),
        }
        for name, defaults in _PROFILE_DEFAULTS.items()
    }


def create_device(profile: str, config: dict | None = None):
    """Create an SSI encoder or TCA9538 from a closed, validated config."""
    if not isinstance(profile, str) or profile not in _PROFILE_DEFAULTS:
        raise ValueError(f"unknown device profile {profile!r}")
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("device profile config must be an object")
    if any(not isinstance(key, str) for key in config):
        raise ValueError("device profile config keys must be strings")

    defaults = _PROFILE_DEFAULTS[profile]
    unknown = set(config) - defaults.keys()
    if unknown:
        raise ValueError(f"unknown {profile} config fields: {sorted(unknown)}")
    values = {**defaults, **config}
    _validate_name(values["name"])

    if profile == "ssi_encoder":
        return SSIEncoderModel(**values)

    _validate_pin(values["scl_pin"], "scl_pin")
    _validate_pin(values["sda_pin"], "sda_pin")
    if values["scl_pin"] == values["sda_pin"]:
        raise ValueError("scl_pin and sda_pin must differ")
    address = values["address"]
    if (isinstance(address, bool) or not isinstance(address, int)
            or not 0 <= address <= 0x7F):
        raise ValueError("address must be an integer from 0 to 127")
    return TCA9538Model(**values)


def _validate_name(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("name must be a non-empty string")


def _validate_pin(value: object, name: str) -> None:
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 0 <= value < 20):
        raise ValueError(f"{name} must be an integer from 0 to 19")
