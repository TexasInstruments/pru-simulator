"""Tests for the bundled simple SSI profile presets."""

import importlib.util
import json
from pathlib import Path


BUNDLE_ROOT = Path(__file__).parents[1] / "firmware" / "ssi_test"
GENERATOR_PATH = BUNDLE_ROOT / "tools" / "generate_config.py"
CONFIG_PATH = BUNDLE_ROOT / "ssi_test" / "ssi_hardware_config.h"

SICK_PRESETS = (
    "CUSTOM_LEGACY_12BIT_4MHZ",
    "AHS_AHM36_SINGLETURN",
    "AHS_AHM36_MULTITURN",
    "AFS_AFM60_SINGLETURN",
    "AFS_AFM60_MULTITURN_30BIT",
    "AFS_AFM60_MULTITURN_27BIT",
    "AFS_AFM60S_PRO_SINGLETURN",
    "AFS_AFM60S_PRO_MULTITURN",
    "ARS60_SHORT",
    "ARS60_LONG",
    "TTK70",
    "KH53",
)

PRESET_LAYOUTS = {
    "CUSTOM_LEGACY_12BIT_4MHZ": (12, 12, 0, 0, 4_000_000),
    "AHS_AHM36_SINGLETURN": (15, 14, 1, 14, 1_500_000),
    "AHS_AHM36_MULTITURN": (27, 26, 1, 26, 1_500_000),
    "AFS_AFM60_SINGLETURN": (21, 18, 3, 18, 1_500_000),
    "AFS_AFM60_MULTITURN_30BIT": (33, 30, 3, 30, 1_500_000),
    "AFS_AFM60_MULTITURN_27BIT": (30, 27, 3, 27, 1_500_000),
    "AFS_AFM60S_PRO_SINGLETURN": (21, 18, 3, 18, 1_500_000),
    "AFS_AFM60S_PRO_MULTITURN": (28, 25, 3, 25, 1_500_000),
    "ARS60_SHORT": (13, 13, 0, 0, 1_500_000),
    "ARS60_LONG": (17, 15, 2, 15, 1_500_000),
    "TTK70": (26, 24, 2, 24, 1_500_000),
    "KH53": (24, 24, 0, 0, 1_500_000),
}


def _load_generator():
    spec = importlib.util.spec_from_file_location("bundled_ssi_generator", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _select_preset(source, preset):
    selector = next(
        line
        for line in source.splitlines()
        if line.startswith("#define SSI_PRESET ")
    )
    return source.replace(selector, f"#define SSI_PRESET SSI_PRESET_{preset}")


def test_bundled_header_declares_the_supported_sick_presets():
    source = CONFIG_PATH.read_text(encoding="utf-8")

    for preset in SICK_PRESETS:
        assert f"SSI_PRESET_{preset}" in source
    assert any(line.startswith("#define SSI_PRESET ") for line in source.splitlines())


def test_generated_profile_identifies_the_selected_preset():
    generator = _load_generator()
    profile = json.loads(
        (BUNDLE_ROOT / "include" / "ssi_build_config.json").read_text(
            encoding="utf-8"
        )
    )
    selected = generator.read_config(CONFIG_PATH)

    assert profile["SSI_PRESET"] == selected["SSI_PRESET"]
    assert profile["SSI_PRESET_NAME"] == generator.PRESET_NAMES[selected["SSI_PRESET"]]


def test_generator_selects_an_ahs_singleturn_preset_from_the_header(tmp_path):
    generator = _load_generator()
    project_root = tmp_path / "ssi_project"
    config_dir = project_root / "ssi_test"
    include_dir = project_root / "include"
    config_dir.mkdir(parents=True)
    include_dir.mkdir()
    source = CONFIG_PATH.read_text(encoding="utf-8")
    source = _select_preset(source, "AHS_AHM36_SINGLETURN")
    (config_dir / "ssi_hardware_config.h").write_text(source, encoding="utf-8")
    (include_dir / "ssi_test_abi.h").write_text(
        (BUNDLE_ROOT / "include" / "ssi_test_abi.h").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    output_dir = project_root / "generated"
    generator.generate(config_dir / "ssi_hardware_config.h", output_dir)
    profile = json.loads(
        (output_dir / "ssi_build_config.json").read_text(encoding="utf-8")
    )

    assert profile["SSI_PRESET_NAME"] == "AHS_AHM36_SINGLETURN"
    assert profile["SSI_FRAME_BITS"] == 15
    assert profile["SSI_POSITION_BITS"] == 14
    assert profile["SSI_ERROR_BITS"] == 1
    assert profile["SSI_CLOCK_HZ"] == 1_500_000


def test_generator_accepts_every_declared_preset(tmp_path):
    generator = _load_generator()
    source = CONFIG_PATH.read_text(encoding="utf-8")
    abi_source = (BUNDLE_ROOT / "include" / "ssi_test_abi.h").read_text(encoding="utf-8")

    for preset_number, (preset, layout) in enumerate(PRESET_LAYOUTS.items()):
        project_root = tmp_path / preset
        config_dir = project_root / "ssi_test"
        include_dir = project_root / "include"
        config_dir.mkdir(parents=True)
        include_dir.mkdir()
        selected_source = _select_preset(source, preset)
        (config_dir / "ssi_hardware_config.h").write_text(
            selected_source, encoding="utf-8"
        )
        (include_dir / "ssi_test_abi.h").write_text(abi_source, encoding="utf-8")

        output_dir = project_root / "generated"
        generator.generate(config_dir / "ssi_hardware_config.h", output_dir)
        profile = json.loads(
            (output_dir / "ssi_build_config.json").read_text(encoding="utf-8")
        )

        frame_bits, position_bits, error_bits, error_offset, clock_hz = layout
        assert profile["SSI_PRESET"] == preset_number
        assert profile["SSI_PRESET_NAME"] == preset
        assert profile["SSI_FRAME_BITS"] == frame_bits
        assert profile["SSI_POSITION_BITS"] == position_bits
        assert profile["SSI_ERROR_BITS"] == error_bits
        assert profile["SSI_ERROR_OFFSET"] == error_offset
        assert profile["SSI_CLOCK_HZ"] == clock_hz
