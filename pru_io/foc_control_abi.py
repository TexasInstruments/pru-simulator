"""GENERATED FILE -- source: schema/foc_control_abi.json."""
import struct

_U32_MAX = 0xFFFFFFFF
_I32_MIN = -0x80000000
_I32_MAX = 0x7FFFFFFF

ABI_VERSION = 0x2
SHARED_BASE = 0x10000
CONTROL_OFFSET = 0x100
CONTROL_ADDRESS = 0x10100
ABI_VERSION_OFFSET = 0x0
ABI_VERSION_OFFSET_FROM_SHARED = 0x100
CONTROL_PERIOD_TICKS_OFFSET = 0x4
CONTROL_PERIOD_TICKS_OFFSET_FROM_SHARED = 0x104
PWM_PERIOD_TICKS_OFFSET = 0x8
PWM_PERIOD_TICKS_OFFSET_FROM_SHARED = 0x108
ENABLE_OFFSET = 0xC
ENABLE_OFFSET_FROM_SHARED = 0x10C
REQUESTED_GENERATION_OFFSET = 0x10
REQUESTED_GENERATION_OFFSET_FROM_SHARED = 0x110
SPEED_REF_Q28_OFFSET = 0x14
SPEED_REF_Q28_OFFSET_FROM_SHARED = 0x114
RAMP_RATE_Q28_OFFSET = 0x18
RAMP_RATE_Q28_OFFSET_FROM_SHARED = 0x118
VD_REF_Q15_OFFSET = 0x1C
VD_REF_Q15_OFFSET_FROM_SHARED = 0x11C
VQ_REF_Q15_OFFSET = 0x20
VQ_REF_Q15_OFFSET_FROM_SHARED = 0x120
INITIAL_PHASE_Q32_OFFSET = 0x24
INITIAL_PHASE_Q32_OFFSET_FROM_SHARED = 0x124
ACK_GENERATION_OFFSET = 0x28
ACK_GENERATION_OFFSET_FROM_SHARED = 0x128
STATUS_OFFSET = 0x2C
STATUS_OFFSET_FROM_SHARED = 0x12C
SPEED_Q28_ONE = 0x10000000
SPEED_BASE_ELECTRICAL_HZ = 0x3E8
VOLTAGE_Q15_ONE = 0x8000
STATUS_RUNNING = 0x1
STATUS_INVALID_CONFIG = 0x2
STATUS_SATURATED = 0x4
CONFIG_SIZE = 48
_CONFIG_STRUCT = struct.Struct('<IIIIIiIiiIII')

def _u32(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _U32_MAX:
        raise ValueError(f'{name} must be an unsigned 32-bit integer')
    return value

def _s32(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or not _I32_MIN <= value <= _I32_MAX:
        raise ValueError(f'{name} must be a signed 32-bit integer')
    return value

def pack_config(control_period_ticks=12500, pwm_period_ticks=12500, enable=0, requested_generation=0, speed_ref_q28=0, ramp_rate_q28=268435456, vd_ref_q15=0, vq_ref_q15=0, initial_phase_q32=0):
    _u32('control_period_ticks', control_period_ticks)
    if control_period_ticks < 12500:
        raise ValueError('control_period_ticks must be at least 12500')
    if control_period_ticks > 12500:
        raise ValueError('control_period_ticks must be at most 12500')
    _u32('pwm_period_ticks', pwm_period_ticks)
    if pwm_period_ticks < 12500:
        raise ValueError('pwm_period_ticks must be at least 12500')
    if pwm_period_ticks > 12500:
        raise ValueError('pwm_period_ticks must be at most 12500')
    _u32('enable', enable)
    if enable > 1:
        raise ValueError('enable must be at most 1')
    _u32('requested_generation', requested_generation)
    _s32('speed_ref_q28', speed_ref_q28)
    if speed_ref_q28 < -268435456:
        raise ValueError('speed_ref_q28 must be at least -268435456')
    if speed_ref_q28 > 268435456:
        raise ValueError('speed_ref_q28 must be at most 268435456')
    _u32('ramp_rate_q28', ramp_rate_q28)
    if ramp_rate_q28 > 268435456:
        raise ValueError('ramp_rate_q28 must be at most 268435456')
    _s32('vd_ref_q15', vd_ref_q15)
    if vd_ref_q15 < -32768:
        raise ValueError('vd_ref_q15 must be at least -32768')
    if vd_ref_q15 > 32767:
        raise ValueError('vd_ref_q15 must be at most 32767')
    _s32('vq_ref_q15', vq_ref_q15)
    if vq_ref_q15 < -32768:
        raise ValueError('vq_ref_q15 must be at least -32768')
    if vq_ref_q15 > 32767:
        raise ValueError('vq_ref_q15 must be at most 32767')
    _u32('initial_phase_q32', initial_phase_q32)
    return _CONFIG_STRUCT.pack(ABI_VERSION, _u32('control_period_ticks', control_period_ticks), _u32('pwm_period_ticks', pwm_period_ticks), _u32('enable', enable), _u32('requested_generation', requested_generation), _s32('speed_ref_q28', speed_ref_q28), _u32('ramp_rate_q28', ramp_rate_q28), _s32('vd_ref_q15', vd_ref_q15), _s32('vq_ref_q15', vq_ref_q15), _u32('initial_phase_q32', initial_phase_q32), 0, 0)

def unpack_config(buffer):
    data = bytes(buffer)
    if len(data) != CONFIG_SIZE:
        raise ValueError(f'expected {CONFIG_SIZE} bytes, got {len(data)}')
    return dict(zip(('abi_version', 'control_period_ticks', 'pwm_period_ticks', 'enable', 'requested_generation', 'speed_ref_q28', 'ramp_rate_q28', 'vd_ref_q15', 'vq_ref_q15', 'initial_phase_q32', 'ack_generation', 'status'), _CONFIG_STRUCT.unpack(data)))
