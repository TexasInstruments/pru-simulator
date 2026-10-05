"""GENERATED FILE -- source: schema/ssi_config_abi.json."""
import struct

ABI_VERSION = 0x2
SHARED_BASE = 0x10000
CONFIG_OFFSET = 0x0
CONFIG_ADDRESS = 0x10000
CONFIG_ABI_VERSION_OFFSET = 0x0
CONFIG_ABI_VERSION_OFFSET_FROM_SHARED = 0x0
CONFIG_FRAME_BITS_OFFSET = 0x4
CONFIG_FRAME_BITS_OFFSET_FROM_SHARED = 0x4
CONFIG_CLOCK_DELAY_LOOPS_OFFSET = 0x8
CONFIG_CLOCK_DELAY_LOOPS_OFFSET_FROM_SHARED = 0x8
CONFIG_IDLE_DELAY_LOOPS_OFFSET = 0xC
CONFIG_IDLE_DELAY_LOOPS_OFFSET_FROM_SHARED = 0xC
MAILBOX_OFFSET = 0x20
MAILBOX_ADDRESS = 0x10020
MAILBOX_SEQUENCE_OFFSET = 0x0
MAILBOX_SEQUENCE_OFFSET_FROM_SHARED = 0x20
MAILBOX_RAW_FRAME_LO_OFFSET = 0x4
MAILBOX_RAW_FRAME_LO_OFFSET_FROM_SHARED = 0x24
MAILBOX_RAW_FRAME_HI_OFFSET = 0x8
MAILBOX_RAW_FRAME_HI_OFFSET_FROM_SHARED = 0x28
MAILBOX_FRAME_COUNT_OFFSET = 0xC
MAILBOX_FRAME_COUNT_OFFSET_FROM_SHARED = 0x2C
MAILBOX_STATUS_OFFSET = 0x10
MAILBOX_STATUS_OFFSET_FROM_SHARED = 0x30
EMULATOR_OFFSET = 0x40
EMULATOR_ADDRESS = 0x10040
EMULATOR_ABI_VERSION_OFFSET = 0x0
EMULATOR_ABI_VERSION_OFFSET_FROM_SHARED = 0x40
EMULATOR_FRAME_BITS_OFFSET = 0x4
EMULATOR_FRAME_BITS_OFFSET_FROM_SHARED = 0x44
EMULATOR_FRAME_LO_OFFSET = 0x8
EMULATOR_FRAME_LO_OFFSET_FROM_SHARED = 0x48
EMULATOR_FRAME_HI_OFFSET = 0xC
EMULATOR_FRAME_HI_OFFSET_FROM_SHARED = 0x4C
EMULATOR_STATUS_OFFSET = 0x10
EMULATOR_STATUS_OFFSET_FROM_SHARED = 0x50
CONFIG_SIZE = 16
MAILBOX_SIZE = 20
EMULATOR_SIZE = 20
_CONFIG_STRUCT = struct.Struct('<IIII')
_MAILBOX_STRUCT = struct.Struct('<IIIII')
_EMULATOR_STRUCT = struct.Struct('<IIIII')
_U32_MAX = 0xFFFFFFFF

def _u32(name, value):
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 0 <= value <= _U32_MAX):
        raise ValueError(f'{name} must be an unsigned 32-bit integer')
    return value

def pack_config(frame_bits, clock_delay_loops, idle_delay_loops):
    if (isinstance(frame_bits, bool) or not isinstance(frame_bits, int)
            or not 1 <= frame_bits <= 64):
        raise ValueError('frame_bits must be an integer from 1 to 64')
    return _CONFIG_STRUCT.pack(
        ABI_VERSION, _u32('frame_bits', frame_bits),
        _u32('clock_delay_loops', clock_delay_loops),
        _u32('idle_delay_loops', idle_delay_loops),
    )

def pack_emulator_config(frame_bits, frame_lo, frame_hi):
    if (isinstance(frame_bits, bool) or not isinstance(frame_bits, int)
            or not 1 <= frame_bits <= 64):
        raise ValueError('frame_bits must be an integer from 1 to 64')
    return _EMULATOR_STRUCT.pack(
        ABI_VERSION, frame_bits, _u32('frame_lo', frame_lo),
        _u32('frame_hi', frame_hi), 0,
    )

def _unpack(structure, buffer, size, names):
    data = bytes(buffer)
    if len(data) != size:
        raise ValueError(f'expected {size} bytes, got {len(data)}')
    return dict(zip(names, structure.unpack(data)))

def unpack_config(buffer):
    return _unpack(_CONFIG_STRUCT, buffer, CONFIG_SIZE, (
        'abi_version', 'frame_bits', 'clock_delay_loops',
        'idle_delay_loops',
    ))

def unpack_mailbox(buffer):
    return _unpack(_MAILBOX_STRUCT, buffer, MAILBOX_SIZE, (
        'sequence', 'raw_frame_lo', 'raw_frame_hi', 'frame_count', 'status',
    ))

def unpack_emulator(buffer):
    return _unpack(_EMULATOR_STRUCT, buffer, EMULATOR_SIZE, (
        'abi_version', 'frame_bits', 'frame_lo', 'frame_hi', 'status',
    ))
