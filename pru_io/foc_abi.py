"""GENERATED FILE -- do not edit by hand.

Source: schema/foc_abi.json
Regenerate with: python tools/gen_foc_abi.py
"""
import struct

ICSS_SHARED_BASE = 0x00010000
ICSS_SHARED_SIZE = 0x10000
ABI_VERSION = 1
Q_FRACTION_BITS = 24
Q_ONE = 1 << Q_FRACTION_BITS
IEP_TICK_HZ = 300000000
SINE_LUT_ENTRIES = 2048
SPEED_SCALE = 2863312
SPEED_BASE_RPM = 1000
CONTROL_LOOP_HZ = 100000
CURRENT_BASE_A = 10
U32_MASK = 0xFFFF_FFFF

CONTROL_BASE = 0x00010000
CONTROL_OFFSET = 0x0
CONTROL_SIZE = 36

CONTROL_ABI_VERSION_OFF = 0x0
CONTROL_STRUCT_SIZE_OFF = 0x4
CONTROL_ENABLE_OFF = 0x8
CONTROL_REQUESTED_GENERATION_OFF = 0xC
CONTROL_PRU_ACK_GENERATION_OFF = 0x10
CONTROL_SPEED_REF_Q24_OFF = 0x14
CONTROL_ID_REF_Q24_OFF = 0x18
CONTROL_IQ_REF_Q24_OFF = 0x1C
CONTROL_RAMP_RATE_Q24_OFF = 0x20

PWM_OUT_BASE = 0x00010100
PWM_OUT_OFFSET = 0x100
PWM_OUT_SIZE = 40

PWM_OUT_SEQ_OFF = 0x0
PWM_OUT_TA_Q24_OFF = 0x4
PWM_OUT_TB_Q24_OFF = 0x8
PWM_OUT_TC_Q24_OFF = 0xC
PWM_OUT_VALPHA_Q24_OFF = 0x10
PWM_OUT_VBETA_Q24_OFF = 0x14
PWM_OUT_THETA_CMD_U32_OFF = 0x18
PWM_OUT_LOOP_COUNTER_OFF = 0x1C
PWM_OUT_TIMESTAMP_CYCLES_OFF = 0x20

MOTOR_FB_BASE = 0x00010200
MOTOR_FB_OFFSET = 0x200
MOTOR_FB_SIZE = 40

MOTOR_FB_SEQ_OFF = 0x0
MOTOR_FB_IA_Q24_OFF = 0x4
MOTOR_FB_IB_Q24_OFF = 0x8
MOTOR_FB_IC_Q24_OFF = 0xC
MOTOR_FB_ID_MEAS_Q24_OFF = 0x10
MOTOR_FB_IQ_MEAS_Q24_OFF = 0x14
MOTOR_FB_ROTOR_THETA_U32_OFF = 0x18
MOTOR_FB_SPEED_RPM_Q24_OFF = 0x1C
MOTOR_FB_TIMESTAMP_OFF = 0x20

SINE_LUT_BASE = 0x00011000
SINE_LUT_OFFSET = 0x1000
SINE_LUT_SIZE = 4
SINE_LUT_COUNT = 2048

SINE_LUT_VALUE_OFF = 0x0

_CONTROL_FIELDS = {
    "abi_version": (0x0, "I"),
    "struct_size": (0x4, "I"),
    "enable": (0x8, "I"),
    "requested_generation": (0xC, "I"),
    "pru_ack_generation": (0x10, "I"),
    "speed_ref_q24": (0x14, "i"),
    "id_ref_q24": (0x18, "i"),
    "iq_ref_q24": (0x1C, "i"),
    "ramp_rate_q24": (0x20, "i"),
}

_PWM_OUT_FIELDS = {
    "seq": (0x0, "I"),
    "ta_q24": (0x4, "i"),
    "tb_q24": (0x8, "i"),
    "tc_q24": (0xC, "i"),
    "valpha_q24": (0x10, "i"),
    "vbeta_q24": (0x14, "i"),
    "theta_cmd_u32": (0x18, "I"),
    "loop_counter": (0x1C, "I"),
    "timestamp_cycles": (0x20, "Q"),
}

_MOTOR_FB_FIELDS = {
    "seq": (0x0, "I"),
    "ia_q24": (0x4, "i"),
    "ib_q24": (0x8, "i"),
    "ic_q24": (0xC, "i"),
    "id_meas_q24": (0x10, "i"),
    "iq_meas_q24": (0x14, "i"),
    "rotor_theta_u32": (0x18, "I"),
    "speed_rpm_q24": (0x1C, "i"),
    "timestamp": (0x20, "Q"),
}

_SINE_LUT_FIELDS = {
    "value": (0x0, "I"),
}

def _pack_fields(field_map, size, values):
    buf = bytearray(size)
    for name, value in values.items():
        if name not in field_map:
            raise ValueError(f"unknown FOC field: {name!r}")
        offset, fmt = field_map[name]
        struct.pack_into("<" + fmt, buf, offset, value)
    return bytes(buf)

def _unpack_fields(field_map, data):
    return {
        name: struct.unpack_from("<" + fmt, data, offset)[0]
        for name, (offset, fmt) in field_map.items()
    }

def pack_control(**fields):
    """Pack a control block, defaulting ABI metadata to current values."""
    values = {"abi_version": ABI_VERSION, "struct_size": CONTROL_SIZE}
    values.update(fields)
    return _pack_fields(_CONTROL_FIELDS, CONTROL_SIZE, values)

def unpack_control(data):
    """Unpack a control block into named integer fields."""
    return _unpack_fields(_CONTROL_FIELDS, data)

def pack_pwm_out(**fields):
    """Pack one complete PWM output block."""
    return _pack_fields(_PWM_OUT_FIELDS, PWM_OUT_SIZE, fields)

def unpack_pwm_out(data):
    """Unpack one complete PWM output block."""
    return _unpack_fields(_PWM_OUT_FIELDS, data)

def pack_motor_fb(**fields):
    """Pack one complete motor feedback block."""
    return _pack_fields(_MOTOR_FB_FIELDS, MOTOR_FB_SIZE, fields)

def unpack_motor_fb(data):
    """Unpack one complete motor feedback block."""
    return _unpack_fields(_MOTOR_FB_FIELDS, data)

def pack_seqlock(seq, payload):
    """Combine a sequence word and a payload for a seqlock block."""
    return struct.pack("<I", seq & U32_MASK) + bytes(payload)

def read_coherent(read_seq, read_payload, unpacker, retries=8):
    """Read a seqlock payload, returning None after bounded retries."""
    for _ in range(max(1, int(retries))):
        first = int(read_seq()) & U32_MASK
        if first & 1:
            continue
        payload = bytes(read_payload())
        second = int(read_seq()) & U32_MASK
        if first == second and not (second & 1):
            return unpacker(struct.pack("<I", first) + payload)
    return None

def read_coherent_pwm_out(read_seq, read_payload, retries=8):
    return read_coherent(read_seq, read_payload, unpack_pwm_out, retries)

def read_coherent_motor_fb(read_seq, read_payload, retries=8):
    return read_coherent(read_seq, read_payload, unpack_motor_fb, retries)

read_seqlock = read_coherent
