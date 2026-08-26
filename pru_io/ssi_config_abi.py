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
PRODUCER_SAMPLES_BASE = 0x00016400
PRODUCER_SAMPLE_DIAGNOSTICS_BASE = 0x00018400


PAUSE_INNER_ITERS = 250
FRAME_SLOT_COUNT = 16
FRAME_SLOT_SIZE = 16
TRACE_RECORD_COUNT = 1024
TRACE_RECORD_SIZE = 24
PRODUCER_SAMPLE_COUNT = 256
PRODUCER_SAMPLE_SIZE = 32
PRODUCER_SAMPLE_DIAGNOSTICS_SIZE = 64


CONFIG_ABI_VERSION = 1
NO_ERROR_FIELD = 65535
IEPCLK_OCP_EN = 1
IEP_TICK_HZ = 300000000
PRODUCER_SAMPLE_Q_FRACTION_BITS = 32
DEFAULT_PRODUCER_PERIOD_IEP_TICKS = 288
DEFAULT_PRODUCER_SAMPLE_AGE_LIMIT_IEP_TICKS = 576
DEFAULT_PRODUCER_PREDICTION_HORIZON_LIMIT_IEP_TICKS = 6000
SSI_TOPOLOGY_LOOPBACK = 0
SSI_TOPOLOGY_READER_ONLY = 1
SSI_ENCODING_BINARY = 0
SSI_ENCODING_GRAY = 1
SSI_ENCODING_GRAY_EXCESS = 2
SSI_ENCODING_TANNENBAUM = 3
SSI_FORMATION_ASYNCHRONOUS = 0
SSI_FORMATION_SYNCHRONOUS = 1
SSI_HOLD_FRAMES = 0
SSI_HOLD_TIME = 1
SSI_PRODUCER_MODE_STATIC_SEQUENCE = 0
SSI_PRODUCER_MODE_TIMESTAMPED = 1
SSI_PRODUCER_SAMPLE_FLAG_VALID = 1
SSI_FAULT_NONE = 0
SSI_FAULT_STATUS_BITS = 1
SSI_FAULT_SENTINEL = 2
SSI_FAULT_ALL_ONES = 3
SSI_FAULT_MISSING_RESPONSE = 4
SSI_FAULT_SHORT_FRAME = 5
SSI_FAULT_EXCESSIVE_TV = 6
SSI_FAULT_STUCK_LOW = 7
SSI_FAULT_STUCK_HIGH = 8


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
CONFIG_PRODUCER_MODE_OFF = 0x47
CONFIG_SEQUENCE_HOLD_COUNT_OFF = 0x48
CONFIG_FAULT_ARGUMENT_OFF = 0x4C
CONFIG_FAULT_REPEAT_COUNT_OFF = 0x50
CONFIG_PRODUCER_PERIOD_IEP_TICKS_OFF = 0x54
CONFIG_PRODUCER_SAMPLE_AGE_LIMIT_IEP_TICKS_OFF = 0x58
CONFIG_PRODUCER_PREDICTION_HORIZON_LIMIT_IEP_TICKS_OFF = 0x5C


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


PRODUCER_SAMPLE_WRITE_SEQ_OFF = 0x0
PRODUCER_SAMPLE_TIMESTAMP_IEP_OFF = 0x8
PRODUCER_SAMPLE_POSITION_Q31_32_OFF = 0x10
PRODUCER_SAMPLE_GENERATION_OFF = 0x18
PRODUCER_SAMPLE_FLAGS_OFF = 0x1C


PRODUCER_SAMPLE_DIAGNOSTICS_LATEST_WRITE_SEQ_OFF = 0x0
PRODUCER_SAMPLE_DIAGNOSTICS_ACCEPTED_COUNT_OFF = 0x8
PRODUCER_SAMPLE_DIAGNOSTICS_COHERENCE_RETRY_COUNT_OFF = 0xC
PRODUCER_SAMPLE_DIAGNOSTICS_STALE_SAMPLE_COUNT_OFF = 0x10
PRODUCER_SAMPLE_DIAGNOSTICS_RING_OVERRUN_COUNT_OFF = 0x14
PRODUCER_SAMPLE_DIAGNOSTICS_LAST_REQUEST_TIMESTAMP_IEP_OFF = 0x18
PRODUCER_SAMPLE_DIAGNOSTICS_LAST_ESTIMATE_POSITION_Q31_32_OFF = 0x20
PRODUCER_SAMPLE_DIAGNOSTICS_STATUS_OFF = 0x28
PRODUCER_SAMPLE_DIAGNOSTICS_GENERATION_OFF = 0x2C
PRODUCER_SAMPLE_DIAGNOSTICS_HEAD_SEQ_OFF = 0x30
PRODUCER_SAMPLE_DIAGNOSTICS_LATEST_SLOT_INDEX_OFF = 0x34
PRODUCER_SAMPLE_DIAGNOSTICS_LATEST_STABLE_SAMPLE_SEQ_OFF = 0x38


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
    "producer_mode": (0x47, "B"),
    "sequence_hold_count": (0x48, "I"),
    "fault_argument": (0x4C, "I"),
    "fault_repeat_count": (0x50, "I"),
    "producer_period_iep_ticks": (0x54, "I"),
    "producer_sample_age_limit_iep_ticks": (0x58, "I"),
    "producer_prediction_horizon_limit_iep_ticks": (0x5C, "I"),
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


_PRODUCER_SAMPLE_FIELDS = {
    "write_seq": (0x0, "Q"),
    "timestamp_iep": (0x8, "Q"),
    "position_q31_32": (0x10, "q"),
    "generation": (0x18, "I"),
    "flags": (0x1C, "I"),
}


_PRODUCER_SAMPLE_DIAGNOSTICS_FIELDS = {
    "latest_write_seq": (0x0, "Q"),
    "accepted_count": (0x8, "I"),
    "coherence_retry_count": (0xC, "I"),
    "stale_sample_count": (0x10, "I"),
    "ring_overrun_count": (0x14, "I"),
    "last_request_timestamp_iep": (0x18, "Q"),
    "last_estimate_position_q31_32": (0x20, "q"),
    "status": (0x28, "I"),
    "generation": (0x2C, "I"),
    "head_seq": (0x30, "I"),
    "latest_slot_index": (0x34, "I"),
    "latest_stable_sample_seq": (0x38, "Q"),
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


def pack_producer_sample(write_seq, timestamp_iep, position_q31_32, generation=0, flags=0):
    """Pack one 32-byte timestamped producer sample entry.

    The producer writes an odd write_seq before the payload and the
    matching even write_seq after the payload is complete.
    """
    return struct.pack("<QQqII", write_seq, timestamp_iep,
                       position_q31_32, generation, flags)


def pack_producer_sample_payload(timestamp_iep, position_q31_32, generation=0, flags=0):
    """Pack the 24-byte payload read between write_seq checks."""
    return struct.pack("<QqII", timestamp_iep, position_q31_32,
                       generation, flags)

def unpack_producer_sample(data):
    """Unpack one complete 32-byte producer sample entry."""
    return {
        name: struct.unpack_from("<" + fmt, data, offset)[0]
        for name, (offset, fmt) in _PRODUCER_SAMPLE_FIELDS.items()
    }

def unpack_producer_sample_payload(data, write_seq):
    """Unpack a payload captured while *write_seq* was stable."""
    values = {
        name: struct.unpack_from("<" + fmt, data, offset - 8)[0]
        for name, (offset, fmt) in _PRODUCER_SAMPLE_FIELDS.items()
        if name != "write_seq"
    }
    values["write_seq"] = write_seq
    return values


def pack_producer_head(head_seq, latest_slot_index, latest_stable_sample_seq):
    """Pack the 16-byte constant-time producer head."""
    return struct.pack("<IIQ", head_seq, latest_slot_index,
                       latest_stable_sample_seq)

def pack_producer_head_payload(latest_slot_index, latest_stable_sample_seq):
    """Pack the 12-byte payload read between head_seq checks."""
    return struct.pack("<IQ", latest_slot_index,
                       latest_stable_sample_seq)

def unpack_producer_head(data):
    """Unpack the 16-byte producer head."""
    head_seq, latest_slot_index, latest_stable_sample_seq = struct.unpack("<IIQ", data)
    return {
        "head_seq": head_seq,
        "latest_slot_index": latest_slot_index,
        "latest_stable_sample_seq": latest_stable_sample_seq,
    }

def unpack_producer_head_payload(data, head_seq):
    """Unpack a head payload captured while *head_seq* was stable."""
    latest_slot_index, latest_stable_sample_seq = struct.unpack("<IQ", data)
    return {
        "head_seq": head_seq,
        "latest_slot_index": latest_slot_index,
        "latest_stable_sample_seq": latest_stable_sample_seq,
    }


def pack_frame_slot(frame_bits, hold_override=0):
    """Pack one 16-byte prepacked emulator frame slot."""
    return struct.pack("<QI4x", frame_bits, hold_override)
