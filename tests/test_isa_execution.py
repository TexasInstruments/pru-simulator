"""ISA execution validation — compare simulator DRAM output against expected values.

Loads references/isa_execution_test.out (compiled with TI CGT), runs to HALT in
the simulator, and verifies each 4-byte DRAM result slot against computed expected
values. Optionally compares byte-for-byte against a hardware dump if available.
"""

import sys
import os
import struct
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from simulator import Simulator

REFS_DIR = os.path.join(os.path.dirname(__file__), '..', 'references')
ELF_PATH = os.path.join(REFS_DIR, 'isa_execution_test.out')
HW_BIN_PATH = os.path.join(REFS_DIR, 'isa_execution_hw.bin')

NUM_TESTS = 97  # 0..96 inclusive (slot 96 is sentinel)
DRAM_SIZE = NUM_TESTS * 4  # bytes to compare


def u32(val):
    """Truncate to unsigned 32-bit."""
    return val & 0xFFFFFFFF


# Expected results computed from the assembly source.
# Index = test number, value = expected 32-bit DRAM word.
EXPECTED = {
    # Section 1: ADD
    0: u32(100 + 200),                # 300
    1: u32(0x1234 + 0x55),            # 0x1289
    2: u32(0xFFFFFFFF + 1),           # 0x00000000
    3: u32(0x7FFFFFFF + 1),           # 0x80000000
    4: 0x12345678,                    # + 0
    5: u32(0xA0 + 0x30),              # 0xD0 (byte field, upper zeroed)
    6: u32((0x8000 + 0x8000) & 0xFFFF),  # 0x0000 (16-bit wrap)
    7: u32(7 + 3),                    # 10

    # Section 2: SUB
    8: u32(500 - 200),                # 300
    9: u32(0xFF - 0x0F),              # 0xF0
    10: u32(0 - 1),                   # 0xFFFFFFFF
    11: 0,                            # equal
    12: u32(100 - 5),                 # 95 (RSB r4, r2, r1 = r1 - r2)
    13: u32(0xFF - 0x0F),             # 0xF0 (RSB imm)

    # Section 3: Logical
    14: 0xFF00FF00 & 0x0F0F0F0F,      # 0x0F000F00
    15: 0x0000ABCD & 0xFF,            # 0x000000CD
    16: 0xF0F0F0F0 | 0x0F0F0F0F,     # 0xFFFFFFFF
    17: 0x00001200 | 0x34,            # 0x00001234
    18: 0xAAAAAAAA ^ 0x55555555,      # 0xFFFFFFFF
    19: 0,                            # XOR self
    20: u32(~0x00FF00FF),             # 0xFF00FF00
    21: u32(~0),                      # 0xFFFFFFFF

    # Section 4: Shifts
    22: 1 << 1,                       # 2
    23: 0xABCD << 16,                 # 0xABCD0000
    24: 1 << 31,                      # 0x80000000
    25: 0xDEADBEEF,                   # << 0
    26: 0x80000000 >> 1,              # 0x40000000
    27: 0xABCD0000 >> 16,             # 0x0000ABCD
    28: 0x80000000 >> 31,             # 0x00000001
    29: 0x56781234,                   # >> 0

    # Section 5: Bit operations
    30: 0 | (1 << 0),                 # 0x00000001
    31: 0 | (1 << 31),               # 0x80000000
    32: 0 | (1 << 15),               # 0x00008000
    33: u32(0xFFFFFFFF & ~(1 << 0)),  # 0xFFFFFFFE
    34: u32(0xFFFFFFFF & ~(1 << 31)), # 0x7FFFFFFF
    35: u32(0xFF & ~(1 << 7)),        # 0x0000007F
    36: 31,                           # LMBD 1 in 0x80000000
    37: 0,                            # LMBD 1 in 0x00000001

    # Section 6: MIN/MAX
    38: 10,                           # MIN(10, 20)
    39: 10,                           # MIN(20, 10)
    40: 5,                            # MIN(5, 5)
    41: 20,                           # MAX(10, 20)
    42: 20,                           # MAX(20, 10)
    43: 5,                            # MAX(5, 5)

    # Section 7: Data movement
    44: 0x0000ABCD,                   # LDI full reg
    45: 0x56781234,                   # LDI w0 + w2
    46: 0x44332211,                   # LDI byte fields
    47: 0xCAFEBABE,                   # MOV reg
    48: 0x000000DE,                   # MOV r4.b0 = r1.b3
    49: 0x0000FACE,                   # MOV r4.w0 = r1.w2
    50: 0x0000AA00,                   # LDI r4.b1 = 0xAA
    51: 0x0000FFFF,                   # w0=FFFF, w2=0000

    # Section 8: Branch semantics (1=taken, 0=not taken)
    # TI spec: QBGT label, op1, op2 → branch if op1 > op2
    # In the assembly: QBGT TAKEN, r1, imm → op1=r1, op2=imm
    # BUT the actual PRU encoding puts: rs1=r1 (2nd operand), src2=imm (3rd operand)
    # The comparison is: src2 > rs1? or rs1 > src2?
    # We store the hardware result to determine the correct convention.
    52: 1,   # QBEQ: 5 == 5 → taken
    53: 0,   # QBEQ: 5 == 6 → not taken
    54: 1,   # QBNE: 5 != 6 → taken
    55: 0,   # QBNE: 5 != 5 → not taken
    # QBGT/QBGE/QBLT/QBLE: these depend on comparison order.
    # TI assembly syntax: QBGT label, Rs1, Op2
    # Hardware semantics: branch if Op2 > Rs1
    # (i.e., the immediate/src2 is compared against register)
    56: 1,   # QBGT TAKEN, r1=5, 10 → "10 > 5" → taken
    57: 0,   # QBGT TAKEN, r1=10, 5 → "5 > 10" → not taken
    58: 0,   # QBGT TAKEN, r1=5, 5 → "5 > 5" → not taken
    59: 1,   # QBGE TAKEN, r1=5, 10 → "10 >= 5" → taken
    60: 1,   # QBGE TAKEN, r1=5, 5 → "5 >= 5" → taken
    61: 0,   # QBGE TAKEN, r1=5, 3 → "3 >= 5" → not taken
    62: 1,   # QBLT TAKEN, r1=5, 3 → "3 < 5" → taken
    63: 0,   # QBLT TAKEN, r1=5, 10 → "10 < 5" → not taken
    64: 0,   # QBLT TAKEN, r1=5, 5 → "5 < 5" → not taken
    65: 1,   # QBLE TAKEN, r1=5, 3 → "3 <= 5" → taken
    66: 1,   # QBLE TAKEN, r1=5, 5 → "5 <= 5" → taken
    67: 0,   # QBLE TAKEN, r1=5, 10 → "10 <= 5" → not taken

    # Section 9: Bit test branches
    68: 1,   # QBBS: bit 0 of 1 → set → taken
    69: 0,   # QBBS: bit 0 of 2 → clear → not taken
    70: 1,   # QBBC: bit 0 of 2 → clear → taken
    71: 0,   # QBBC: bit 31 of 0x80000000 → set → not taken

    # Section 10: Memory round-trip
    72: 0xDEADBEEF,    # SBBO/LBBO round-trip
    73: 0x0000CAFE,    # 2-byte write, 4-byte read (upper zeroed)
    74: 0x56781234,    # SBCO/LBCO round-trip via C24
    75: 0xFEEDFACE,    # LBBO with offset

    # Section 11: LOOP
    76: 30,            # 10 iterations × 3
    77: 99,            # 1 iteration × 99

    # Section 12: LMBD edge cases
    78: 31,            # LMBD 0 in 0x7FFFFFFF → bit 31 is the leftmost 0
    79: 32,            # LMBD 1 in 0x00000000 → not found = 32

    # Section 13: Carry-chain (ADC/SUC)
    # PRU carry convention: carry=1 when no borrow, carry=0 when borrow (ARM-like).
    # ADC: Rd = Rs1 + Op2 + C
    # SUC: Rd = Rs1 - Op2 - ~C  (subtracts borrow = NOT carry)
    80: 151,           # 10-5=5 (no borrow, C=1); ADC: 100+50+1=151
    81: 150,           # 5-10 (borrow, C=0); ADC: 100+50+0=150
    # SUC: Rd = Rs1 - Op2 - ~C
    82: 50,            # no borrow (C=1); SUC: 100-50-~1 = 100-50-0=50
    83: 49,            # borrow (C=0); SUC: 100-50-~0 = 100-50-1=49

    # Section 14: Register field isolation
    84: 0x000000AA,    # b0 only
    85: 0xBB000000,    # b3 only
    86: 0x22221111,    # w0=1111, w2=2222
    87: 0x00000030,    # ADD b0: 0x10+0x20=0x30

    # Section 15: Unsigned comparison edge cases
    88: 1,   # QBGT: 0xFFFFFFFF > 1? → taken
    89: 0,   # QBGT: 1 > 0xFFFFFFFF? → not taken
    90: 1,   # QBGE: 0x80000000 >= 0x7FFFFFFF? → taken
    91: 1,   # QBLT: 0x7FFFFFFF < 0x80000000? → taken
    92: 1,   # QBGE: 0 >= 0? → taken
    93: 0,   # QBGT: 0 > 0? → not taken
    94: 1,   # QBLE: 0 <= 0? → taken
    95: 0,   # QBLT: 0 < 0? → not taken

    # Sentinel
    96: 96,
}


@pytest.fixture(scope="module")
def sim_dram():
    """Run firmware in simulator, return DRAM bytes."""
    if not os.path.exists(ELF_PATH):
        pytest.skip("references/isa_execution_test.out not found")

    sim = Simulator()
    with open(ELF_PATH, 'rb') as f:
        elf_data = f.read()
    errors = sim.load_elf("pru0", elf_data)
    assert not errors, f"ELF load errors: {errors}"

    # Run to halt (safety limit)
    max_steps = 20000
    for _ in range(max_steps):
        result = sim.step("pru0")
        if result["halted"]:
            break
    else:
        pytest.fail(f"Firmware did not halt within {max_steps} steps")

    return sim.memory_read(0, DRAM_SIZE)


@pytest.fixture(scope="module")
def hw_dram():
    """Load hardware DRAM dump if available."""
    if not os.path.exists(HW_BIN_PATH):
        return None
    with open(HW_BIN_PATH, 'rb') as f:
        data = f.read()
    return data[:DRAM_SIZE]


def read_slot(dram_bytes, index):
    """Read 4-byte LE word from DRAM at slot index."""
    offset = index * 4
    return struct.unpack_from('<I', dram_bytes, offset)[0]


class TestISAExecution:
    """Validate instruction execution results against computed expectations."""

    def test_firmware_halts(self, sim_dram):
        """Firmware runs and halts successfully."""
        assert len(sim_dram) == DRAM_SIZE

    def test_sentinel(self, sim_dram):
        """Final slot contains test count marker."""
        assert read_slot(sim_dram, 96) == 96

    @pytest.mark.parametrize("slot", sorted(EXPECTED.keys()),
                             ids=[f"slot_{i}" for i in sorted(EXPECTED.keys())])
    def test_slot(self, sim_dram, slot):
        """Verify individual test result."""
        actual = read_slot(sim_dram, slot)
        expected = EXPECTED[slot]
        assert actual == expected, (
            f"Slot {slot}: got 0x{actual:08X}, expected 0x{expected:08X}")


class TestHardwareComparison:
    """Compare simulator output against real hardware DRAM dump."""

    def test_hw_blob_matches(self, sim_dram, hw_dram):
        """Byte-for-byte comparison against hardware dump."""
        if hw_dram is None:
            pytest.skip("references/isa_execution_hw.bin not available")

        mismatches = []
        for i in range(NUM_TESTS):
            sim_val = read_slot(sim_dram, i)
            hw_val = read_slot(hw_dram, i)
            if sim_val != hw_val:
                mismatches.append(
                    f"  Slot {i:3d}: sim=0x{sim_val:08X}  hw=0x{hw_val:08X}")

        if mismatches:
            report = "\n".join(mismatches)
            pytest.fail(
                f"{len(mismatches)} slot(s) differ between simulator and hardware:\n{report}")

    @pytest.mark.parametrize("slot", sorted(EXPECTED.keys()),
                             ids=[f"hw_slot_{i}" for i in sorted(EXPECTED.keys())])
    def test_hw_slot(self, hw_dram, slot):
        """Verify hardware matches expected value per slot."""
        if hw_dram is None:
            pytest.skip("references/isa_execution_hw.bin not available")
        actual = read_slot(hw_dram, slot)
        expected = EXPECTED[slot]
        assert actual == expected, (
            f"HW Slot {slot}: got 0x{actual:08X}, expected 0x{expected:08X}")
