"""SSI encoder frame presets: the standard 12-bit encoder and SICK families.

Frame widths, position/error field widths and error-bit placement come from
SICK AG, "Technical information - SSI Interface Description - Synchronous
Serial Interface for Absolute Encoders" (IM0100079, part no. 8027422,
2022-02-08), section 3 (pp. 8-24). The document is not redistributed here.

Every SICK frame sends the position MSB first, then the error bits last, so
the error field occupies the low bits of the frame word (offsets are left to
``SSIEncoderModel``'s trailing-error default). Timing fields used by the model:

* ``f_max_hz``: 2 MHz, the highest SSI baud rate the document allows (p. 4).
* ``monoflop_us``: 20 us, the middle of the document's tm range of 15-25 us
  (p. 5).

The document's tv (< T/2) and Tp (> tm) are master-side constraints and the
model has no equivalent, so presets do not carry them. Configurable AHS/AHM
and AFS/AFM families use an explicit binary example, not a vendor-wide default.
ARS60 uses standard Gray for the illustrated 8192/32768-step SSI variants;
TTK70 uses Gray position plus two binary error bits (TTK70-AXA0-K02's 24-bit
position, extended by IM0100079 p. 23's error fields). KH53 uses 24-bit Gray.
Other ARS60 variants with trimmed Gray are not modeled by these presets.
Encoding sources are linked in PRESET_SOURCES below.

``RM08_12BIT_4MHZ`` is the standard simple encoder, not a SICK frame:
12 bits, natural binary, no error bits, clock <= 4 MHz and tm = 12.5 us. It
follows the RM08 magnetic encoder data sheet (RM08D01_18, issue 18), p. 10: SSI
output with up to 4096 cpr (12 bits), clock <= 4 MHz and 12.5 us <= tm <= 20.5
us. The preset uses the lower tm bound; the model default (20.5 us) is the
upper bound. The data sheet is not redistributed here.
"""
from __future__ import annotations

_SICK = {"f_max_hz": 2_000_000, "monoflop_us": 20, "encoding": "binary"}


def _frame(resolution: int, error_bits: int, encoding: str = "binary") -> dict:
    return {**_SICK, "resolution": resolution,
            "position_bits": resolution - error_bits, "error_bits": error_bits,
            "encoding": encoding}


PRESETS = {
    "RM08_12BIT_4MHZ": {
        "resolution": 12, "position_bits": 12, "error_bits": 0,
        "encoding": "binary", "f_max_hz": 4_000_000, "monoflop_us": 12.5,
    },
    "AHS_AHM36_SINGLETURN": _frame(15, 1),
    "AHS_AHM36_MULTITURN": _frame(27, 1),
    "AFS_AFM60_SINGLETURN": _frame(21, 3),
    "AFS_AFM60_MULTITURN_30BIT": _frame(33, 3),
    "AFS_AFM60_MULTITURN_27BIT": _frame(30, 3),
    "AFS_AFM60S_PRO_SINGLETURN": _frame(21, 3),
    "AFS_AFM60S_PRO_MULTITURN_EXAMPLE": _frame(28, 3),
    "ARS60_SHORT": _frame(13, 0, "gray"),
    "ARS60_LONG": _frame(17, 2, "gray"),
    "TTK70": _frame(26, 2, "gray"),
    "KH53": _frame(24, 0, "gray"),
}

# IM0100079 page of each preset's frame diagram.
PRESET_SOURCES = {
    "RM08_12BIT_4MHZ": "RM08D01_18 p. 10",
    "AHS_AHM36_SINGLETURN": "IM0100079 p. 8",
    "AHS_AHM36_MULTITURN": "IM0100079 p. 11",
    "AFS_AFM60_SINGLETURN": "IM0100079 p. 14",
    "AFS_AFM60_MULTITURN_30BIT": "IM0100079 p. 14",
    "AFS_AFM60_MULTITURN_27BIT": "IM0100079 pp. 14-15",
    "AFS_AFM60S_PRO_SINGLETURN": "IM0100079 p. 17",
    "AFS_AFM60S_PRO_MULTITURN_EXAMPLE": "IM0100079 p. 18 (25-bit example)",
    "ARS60_SHORT": "IM0100079 p. 22; Gray: https://www.sick.com/media/pdf/8/28/728/dataSheet_ARS60-AAA08192_1031458_en.pdf p. 2",
    "ARS60_LONG": "IM0100079 p. 22; Gray: https://www.sick.com/media/pdf/6/06/406/dataSheet_ARS60-A4B32768_1031497_zf.pdf p. 2",
    "TTK70": "IM0100079 p. 23; Gray: https://www.sick.com/media/pdf/0/40/840/dataSheet_TTK70-AXA0-K02_1038033_en.pdf p. 2",
    "KH53": "IM0100079 p. 24; Gray: https://www.sick.com/media/docs/0/00/600/product_information_kh53_linear_encoders_en_im0011600.pdf p. 3",
}


def preset_fields(name: str) -> dict:
    """Return a copy of one preset's ``SSIEncoderModel`` keyword arguments."""
    if not isinstance(name, str) or name not in PRESETS:
        raise ValueError(f"unknown SSI preset {name!r}; choose from {sorted(PRESETS)}")
    return dict(PRESETS[name])
