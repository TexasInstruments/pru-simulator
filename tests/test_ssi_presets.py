"""Preset values are the frame sizes in SICK IM0100079 section 3 (pp. 8-24)."""
import pytest

from pru_io.ssi_encoder_model import SSIEncoderModel
from pru_io.ssi_presets import PRESETS, PRESET_SOURCES, preset_fields

# name: (frame bits, position bits, error bits), written out from the document.
EXPECTED = {
    "RM08_12BIT_4MHZ": (12, 12, 0),
    "AHS_AHM36_SINGLETURN": (15, 14, 1),
    "AHS_AHM36_MULTITURN": (27, 26, 1),
    "AFS_AFM60_SINGLETURN": (21, 18, 3),
    "AFS_AFM60_MULTITURN_30BIT": (33, 30, 3),
    "AFS_AFM60_MULTITURN_27BIT": (30, 27, 3),
    "AFS_AFM60S_PRO_SINGLETURN": (21, 18, 3),
    "AFS_AFM60S_PRO_MULTITURN_EXAMPLE": (28, 25, 3),
    "ARS60_SHORT": (13, 13, 0),
    "ARS60_LONG": (17, 15, 2),
    "TTK70": (26, 24, 2),
    "KH53": (24, 24, 0),
}


def test_preset_table_lists_the_twelve_presets_with_sources():
    assert set(PRESETS) == set(EXPECTED) == set(PRESET_SOURCES)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_preset_validates_with_the_documented_frame(name):
    frame_bits, position_bits, error_bits = EXPECTED[name]

    model = SSIEncoderModel.from_preset(name)

    assert (model.resolution, model.position_bits, model.error_bits) == (
        frame_bits, position_bits, error_bits)
    assert model.preset == name
    # The SICK frames send error bits after the position, so they are the
    # low bits of the word and the position sits above them.
    assert (model.position_offset, model.error_offset) == (error_bits, 0)
    assert model.encoding == ("gray" if name in {"ARS60_SHORT", "ARS60_LONG", "TTK70", "KH53"} else "binary")


def test_sick_presets_use_the_documented_timing_limits():
    for name in EXPECTED.keys() - {"RM08_12BIT_4MHZ"}:
        assert preset_fields(name)["f_max_hz"] == 2_000_000
        assert preset_fields(name)["monoflop_us"] == 20


def test_standard_preset_is_the_rm08_12_bit_4_mhz_encoder():
    fields = preset_fields("RM08_12BIT_4MHZ")

    assert (fields["resolution"], fields["error_bits"]) == (12, 0)
    assert (fields["f_max_hz"], fields["monoflop_us"]) == (4_000_000, 12.5)
    assert fields["encoding"] == "binary"
    assert "RM08" in PRESET_SOURCES["RM08_12BIT_4MHZ"]
    assert SSIEncoderModel.from_preset("RM08_12BIT_4MHZ").monoflop_us == 12.5


def test_options_override_preset_fields_and_unknown_presets_are_rejected():
    model = SSIEncoderModel.from_preset(
        "TTK70", encoding="gray", error_bits=0, position_bits=24, resolution=24)

    assert (model.resolution, model.error_bits, model.encoding) == (24, 0, "gray")
    with pytest.raises(ValueError, match="unknown SSI preset"):
        preset_fields("NOT_A_PRESET")
    preset_fields("TTK70")["resolution"] = 1  # a copy: the table is unchanged
    assert PRESETS["TTK70"]["resolution"] == 26


@pytest.mark.parametrize("name, position, error, raw", [
    ("ARS60_SHORT", 0x15, 0, 0x1F),
    ("ARS60_LONG", 0x15, 2, 0x7E),
    ("TTK70", 0x15, 2, 0x7E),
    ("KH53", 0x15, 0, 0x1F),
])
def test_vendor_gray_position_keeps_error_field_binary(name, position, error, raw):
    model = SSIEncoderModel.from_preset(name)
    assert model.pack_frame(position, error) == raw
    assert model.decode_frame(raw) == (position, error)
