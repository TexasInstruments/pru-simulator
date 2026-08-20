"""GENERATED FILE -- do not edit by hand.

Source: schema/ssi_config_abi.json
Regenerate with: python tools/gen_ssi_abi.py
"""
import struct


CONFIG_BASE = 0x00010000
FRAMES_BASE = 0x00010100
MAILBOX_BASE = 0x00010200
CAPTURE_BASE = 0x00010240
TRACE_BASE = 0x00010400


FRAME_SLOT_SIZE = 16
TRACE_RECORD_SIZE = 24
PAUSE_INNER_ITERS = 250


CONFIG_ABI_VERSION_OFF = 0x0
CONFIG_STRUCT_SIZE_OFF = 0x4
CONFIG_REQUESTED_GENERATION_OFF = 0x8
CONFIG_PRU0_ACK_GENERATION_OFF = 0xC
CONFIG_PRU1_ACK_GENERATION_OFF = 0x10
CONFIG_TOPOLOGY_OFF = 0x14
CONFIG_ENCODING_TYPE_OFF = 0x15
CONFIG_ALIGNMENT_OFF = 0x16
CONFIG_FORMATION_MODE_OFF = 0x17
CONFIG_FRAME_WIDTH_BITS_OFF = 0x18
CONFIG_POSITION_OFFSET_BITS_OFF = 0x1A
CONFIG_POSITION_WIDTH_BITS_OFF = 0x1C
CONFIG_SINGLETURN_WIDTH_BITS_OFF = 0x1E
CONFIG_MULTITURN_WIDTH_BITS_OFF = 0x20
CONFIG_ERROR_OFFSET_BITS_OFF = 0x22
CONFIG_ERROR_WIDTH_BITS_OFF = 0x24
CONFIG_PADDING_WIDTH_BITS_OFF = 0x26
CONFIG_CLOCK_HIGH_CYCLES_OFF = 0x28
CONFIG_CLOCK_LOW_CYCLES_OFF = 0x2C
CONFIG_SAMPLE_DELAY_CYCLES_OFF = 0x30
CONFIG_TV_CYCLES_OFF = 0x34
CONFIG_TM_PAUSE_OUTER_ITERS_OFF = 0x38
CONFIG_TP_PAUSE_OUTER_ITERS_OFF = 0x3C
CONFIG_FORMATION_PAUSE_OUTER_ITERS_OFF = 0x40
CONFIG_SEQUENCE_HOLD_MODE_OFF = 0x44
CONFIG_FAULT_MODE_OFF = 0x45
CONFIG_CAPTURE_MODE_OFF = 0x46
CONFIG_SEQUENCE_HOLD_COUNT_OFF = 0x48
CONFIG_FAULT_ARGUMENT_OFF = 0x4C
CONFIG_FAULT_REPEAT_COUNT_OFF = 0x50


FRAME_SLOT_FRAME_BITS_OFF = 0x0
FRAME_SLOT_HOLD_OVERRIDE_CYCLES_OR_FRAMES_OFF = 0x8


MAILBOX_SEQ_OFF = 0x0
MAILBOX_RAW_FRAME_OFF = 0x4
MAILBOX_POSITION_VALUE_OFF = 0xC
MAILBOX_STATUS_BITS_OFF = 0x10
MAILBOX_FRAME_COUNTER_OFF = 0x14
MAILBOX_TIMESTAMP_CYCLES_OFF = 0x18


CAPTURE_TRACE_WRITE_INDEX_OFF = 0x0
CAPTURE_TRACE_OVERRUN_COUNT_OFF = 0x4


TRACE_RECORD_TIMESTAMP_CYCLES_OFF = 0x0
TRACE_RECORD_RAW_FRAME_OFF = 0x8
TRACE_RECORD_POSITION_VALUE_OFF = 0x10
TRACE_RECORD_STATUS_BITS_OFF = 0x14
TRACE_RECORD_FLAGS_OFF = 0x16


FRAME_SLOT_UNUSED_SENTINEL = 0xFFFFFFFFFFFFFFFF


_CONFIG_SIZE = 256


_CONFIG_FIELDS = {
    "abi_version": (0x0, "I"),
    "struct_size": (0x4, "I"),
    "requested_generation": (0x8, "I"),
    "pru0_ack_generation": (0xC, "I"),
    "pru1_ack_generation": (0x10, "I"),
    "topology": (0x14, "B"),
    "encoding_type": (0x15, "B"),
    "alignment": (0x16, "B"),
    "formation_mode": (0x17, "B"),
    "frame_width_bits": (0x18, "H"),
    "position_offset_bits": (0x1A, "H"),
    "position_width_bits": (0x1C, "H"),
    "singleturn_width_bits": (0x1E, "H"),
    "multiturn_width_bits": (0x20, "H"),
    "error_offset_bits": (0x22, "H"),
    "error_width_bits": (0x24, "H"),
    "padding_width_bits": (0x26, "H"),
    "clock_high_cycles": (0x28, "I"),
    "clock_low_cycles": (0x2C, "I"),
    "sample_delay_cycles": (0x30, "I"),
    "tv_cycles": (0x34, "I"),
    "tm_pause_outer_iters": (0x38, "I"),
    "tp_pause_outer_iters": (0x3C, "I"),
    "formation_pause_outer_iters": (0x40, "I"),
    "sequence_hold_mode": (0x44, "B"),
    "fault_mode": (0x45, "B"),
    "capture_mode": (0x46, "B"),
    "sequence_hold_count": (0x48, "I"),
    "fault_argument": (0x4C, "I"),
    "fault_repeat_count": (0x50, "I"),
}


_MAILBOX_FIELDS = {
    "seq": (0x0, "I"),
    "raw_frame": (0x4, "Q"),
    "position_value": (0xC, "I"),
    "status_bits": (0x10, "I"),
    "frame_counter": (0x14, "I"),
    "timestamp_cycles": (0x18, "Q"),
}


_TRACE_RECORD_FIELDS = {
    "timestamp_cycles": (0x0, "Q"),
    "raw_frame": (0x8, "Q"),
    "position_value": (0x10, "I"),
    "status_bits": (0x14, "H"),
    "flags": (0x16, "B"),
}


def pack_config(**fields):
    """Pack config fields into a 256-byte little-endian buffer.

    Fields not passed default to 0. Raises ValueError on an unknown
    field name.
    """
    buf = bytearray(_CONFIG_SIZE)
    for name, value in fields.items():
        if name not in _CONFIG_FIELDS:
            raise ValueError(f"unknown config field: {name!r}")
        offset, fmt = _CONFIG_FIELDS[name]
        struct.pack_into("<" + fmt, buf, offset, value)
    return bytes(buf)


def unpack_config(data):
    """Inverse of pack_config: 256 bytes -> dict of named field -> int."""
    return {
        name: struct.unpack_from("<" + fmt, data, offset)[0]
        for name, (offset, fmt) in _CONFIG_FIELDS.items()
    }


def unpack_mailbox(data):
    """Mailbox section's 64 bytes -> dict of named field -> int."""
    return {
        name: struct.unpack_from("<" + fmt, data, offset)[0]
        for name, (offset, fmt) in _MAILBOX_FIELDS.items()
    }


def unpack_trace_record(data):
    """One 24-byte trace record -> dict of named field -> int."""
    return {
        name: struct.unpack_from("<" + fmt, data, offset)[0]
        for name, (offset, fmt) in _TRACE_RECORD_FIELDS.items()
    }


def pack_frame_slot(frame_bits, hold_override=0):
    """Pack one 16-byte prepacked emulator frame slot."""
    return struct.pack("<QI4x", frame_bits, hold_override)
