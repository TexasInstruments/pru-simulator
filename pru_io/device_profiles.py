"""Validated factories for device profiles exposed to host tools."""
from __future__ import annotations

from copy import deepcopy

from pru_io.device_model import OPEN_DRAIN, PUSH_PULL
from pru_io.foc_motor_model import FocMotorModel
from pru_io.ssi_encoder_model import SSIEncoderModel
from pru_io.ssi_presets import PRESETS, PRESET_SOURCES
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
        "preset": None,
        "position_bits": None,
        "position_offset": None,
        "error_bits": 0,
        "error_offset": None,
        "error_value": 0,
    },
    "tca9538": {
        "address": 0x23,
        "scl_pin": 0,
        "sda_pin": 1,
        "name": "tca9538",
    },
    "foc_motor": {
        "current_a_pin": 3,
        "current_b_pin": 4,
        "resistance_ohm": 0.5,
        "inductance_h": 0.001,
        "dc_bus_v": 48.0,
        "current_scale_a": 20.0,
        "current_limit_a": 40.0,
        "core_clock_hz": 250_000_000,
        "current_a_clock_hz": 20_000_000,
        "current_b_clock_hz": 20_000_000,
        "flux_linkage_vs": 0.05,
        "pole_pairs": 4,
        "inertia_kg_m2": 0.0005,
        "damping_nm_s": 0.003,
        "load_torque_nm": 0.0,
        "name": "foc_motor",
    },
}

_PRESET_FIELDS = set().union(*PRESETS.values())

_PROFILE_OUTPUTS = {
    "ssi_encoder": {"data_pin": PUSH_PULL},
    "tca9538": {"scl_pin": OPEN_DRAIN, "sda_pin": OPEN_DRAIN},
    "foc_motor": {"current_a_pin": PUSH_PULL, "current_b_pin": PUSH_PULL},
}


def discover_device_profiles() -> dict:
    """Return profile names, defaults, and the pins each profile drives."""
    profiles = {
        name: {
            "defaults": deepcopy({
                field: value for field, value in defaults.items()
                if not (name in ("ssi_encoder", "foc_motor")
                        and field == "core_clock_hz")
            }),
            "outputs": dict(_PROFILE_OUTPUTS[name]),
            **({"pru_output_mask": 0x7, "supported_cores": ["pru0"],
                "sim_clock_field": "core_clock_hz"}
               if name == "foc_motor" else {}),
        }
        for name, defaults in _PROFILE_DEFAULTS.items()
    }
    profiles["ssi_encoder"]["presets"] = {
        name: {**fields, "source": PRESET_SOURCES[name]}
        for name, fields in PRESETS.items()
    }
    return profiles


def create_device(profile: str, config: dict | None = None, *,
                  default_core_clock_hz=None):
    """Create a supported device from a closed, validated profile config."""
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
        if "core_clock_hz" not in config and default_core_clock_hz is not None:
            values["core_clock_hz"] = default_core_clock_hz
        preset = values.pop("preset")
        if preset is None:
            return SSIEncoderModel(**values)
        # The preset supplies its frame fields; only explicit config overrides.
        overrides = {key: value for key, value in values.items()
                     if key in config or key not in _PRESET_FIELDS}
        return SSIEncoderModel.from_preset(preset, **overrides)

    if profile == "foc_motor":
        return FocMotorModel(**values)

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
