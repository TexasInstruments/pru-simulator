"""GENERATED FILE -- source: schema/foc_control_abi.json."""
import struct

_U32_MAX = 0xFFFFFFFF
_I32_MIN = -0x80000000
_I32_MAX = 0x7FFFFFFF

ABI_VERSION = 0x1
SHARED_BASE = 0x10000
CONTROL_OFFSET = 0x100
CONTROL_ADDRESS = 0x10100
ABI_VERSION_OFFSET = 0x0
ABI_VERSION_OFFSET_FROM_SHARED = 0x100
CONTROL_PERIOD_TICKS_OFFSET = 0x4
CONTROL_PERIOD_TICKS_OFFSET_FROM_SHARED = 0x104
PWM_PERIOD_TICKS_OFFSET = 0x8
PWM_PERIOD_TICKS_OFFSET_FROM_SHARED = 0x108
PHASE_INCREMENT_Q32_OFFSET = 0xC
PHASE_INCREMENT_Q32_OFFSET_FROM_SHARED = 0x10C
MODULATION_Q15_OFFSET = 0x10
MODULATION_Q15_OFFSET_FROM_SHARED = 0x110
ALPHA_Q15_OFFSET = 0x14
ALPHA_Q15_OFFSET_FROM_SHARED = 0x114
BETA_Q15_OFFSET = 0x18
BETA_Q15_OFFSET_FROM_SHARED = 0x118
INITIAL_PHASE_Q32_OFFSET = 0x1C
INITIAL_PHASE_Q32_OFFSET_FROM_SHARED = 0x11C
CONFIG_SIZE = 32
_CONFIG_STRUCT = struct.Struct('<IIIIIiiI')

def _u32(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _U32_MAX:
        raise ValueError(f'{name} must be an unsigned 32-bit integer')
    return value

def _s32(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or not _I32_MIN <= value <= _I32_MAX:
        raise ValueError(f'{name} must be a signed 32-bit integer')
    return value

def pack_config(control_period_ticks=12500, pwm_period_ticks=12500, phase_increment_q32=0, modulation_q15=16384, alpha_q15=16384, beta_q15=0, initial_phase_q32=0):
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
    _u32('phase_increment_q32', phase_increment_q32)
    _u32('modulation_q15', modulation_q15)
    if modulation_q15 > 32768:
        raise ValueError('modulation_q15 must be at most 32768')
    _s32('alpha_q15', alpha_q15)
    if alpha_q15 < -32768:
        raise ValueError('alpha_q15 must be at least -32768')
    if alpha_q15 > 32767:
        raise ValueError('alpha_q15 must be at most 32767')
    _s32('beta_q15', beta_q15)
    if beta_q15 < -32768:
        raise ValueError('beta_q15 must be at least -32768')
    if beta_q15 > 32767:
        raise ValueError('beta_q15 must be at most 32767')
    _u32('initial_phase_q32', initial_phase_q32)
    return _CONFIG_STRUCT.pack(ABI_VERSION, _u32('control_period_ticks', control_period_ticks), _u32('pwm_period_ticks', pwm_period_ticks), _u32('phase_increment_q32', phase_increment_q32), _u32('modulation_q15', modulation_q15), _s32('alpha_q15', alpha_q15), _s32('beta_q15', beta_q15), _u32('initial_phase_q32', initial_phase_q32))

def unpack_config(buffer):
    data = bytes(buffer)
    if len(data) != CONFIG_SIZE:
        raise ValueError(f'expected {CONFIG_SIZE} bytes, got {len(data)}')
    return dict(zip(('abi_version', 'control_period_ticks', 'pwm_period_ticks', 'phase_increment_q32', 'modulation_q15', 'alpha_q15', 'beta_q15', 'initial_phase_q32'), _CONFIG_STRUCT.unpack(data)))
