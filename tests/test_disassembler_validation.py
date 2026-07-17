"""Disassembler validation against TI PRU CGT compiled binary.

Loads references/pru_encoding_test.out (compiled with clpru v2.3.3)
and verifies every instruction disassembles correctly.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.elf_loader import load_elf
from core.disassembler import disassemble, disassemble_word


@pytest.fixture(scope="module")
def elf_image():
    """Load the reference ELF binary."""
    elf_path = os.path.join(os.path.dirname(__file__), '..', 'references', 'pru_encoding_test.out')
    if not os.path.exists(elf_path):
        pytest.skip("references/pru_encoding_test.out not found (compile with TI CGT first)")
    with open(elf_path, 'rb') as f:
        data = f.read()
    return load_elf(data)


@pytest.fixture(scope="module")
def disassembled(elf_image):
    """Disassemble all instruction words."""
    return disassemble(elf_image.text_words, elf_image.symbols)


# Expected: (index, opcode, source_text)
# These are verified against the reference assembly (pru_encoding_test.asm)
# compiled with TI clpru v2.3.3 --silicon_version=3.

EXPECTED_INSTRUCTIONS = [
    # Section 1: Arithmetic
    (0, 'ADD', 'ADD r1, r2, r3'),
    (1, 'ADD', 'ADD r1, r2, 55'),
    (2, 'ADD', 'ADD r1.b0, r2.b0, r3.b0'),
    (3, 'ADD', 'ADD r1.b1, r2.b1, r3.b1'),
    (4, 'ADD', 'ADD r1.b2, r2.b2, r3.b2'),
    (5, 'ADD', 'ADD r1.b3, r2.b3, r3.b3'),
    (6, 'ADD', 'ADD r1.w0, r2.w0, r3.w0'),
    (7, 'ADD', 'ADD r1.w2, r2.w2, r3.w2'),
    (8, 'ADD', 'ADD r31, r30, r29'),
    (9, 'ADC', 'ADC r1, r2, r3'),
    (10, 'ADC', 'ADC r1, r2, 85'),
    (11, 'SUB', 'SUB r1, r2, r3'),
    (12, 'SUB', 'SUB r1, r2, 171'),
    (13, 'SUC', 'SUC r1, r2, r3'),
    (14, 'SUC', 'SUC r1, r2, 18'),
    (15, 'RSB', 'RSB r1, r2, r3'),
    (16, 'RSB', 'RSB r1, r2, 153'),
    (17, 'RSC', 'RSC r1, r2, r3'),
    (18, 'RSC', 'RSC r1, r2, 68'),
    # Section 2: Logical
    (19, 'AND', 'AND r1, r2, r3'),
    (20, 'AND', 'AND r1, r2, 255'),
    (21, 'OR', 'OR r1, r2, r3'),
    (22, 'OR', 'OR r1, r2, 128'),
    (23, 'XOR', 'XOR r1, r2, r3'),
    (24, 'XOR', 'XOR r1, r2, 1'),
    (25, 'NOT', 'NOT r1, r2'),
    # Section 3: Shift
    (26, 'LSL', 'LSL r1, r2, r3'),
    (27, 'LSL', 'LSL r1, r2, 7'),
    (28, 'LSL', 'LSL r1, r2, 31'),
    (29, 'LSR', 'LSR r1, r2, r3'),
    (30, 'LSR', 'LSR r1, r2, 16'),
    # Section 4: Bit operations
    (31, 'SET', 'SET r1, r2, r3'),
    (32, 'SET', 'SET r1, r2, 15'),
    (33, 'SET', 'SET r1, r2, 0'),
    (34, 'SET', 'SET r1, r2, 31'),
    (35, 'CLR', 'CLR r1, r2, r3'),
    (36, 'CLR', 'CLR r1, r2, 7'),
    (37, 'LMBD', 'LMBD r1, r2, r3'),
    (38, 'LMBD', 'LMBD r1, r2, 1'),
    # Section 5: Min/Max
    (39, 'MIN', 'MIN r1, r2, r3'),
    (40, 'MIN', 'MIN r1, r2, 66'),
    (41, 'MAX', 'MAX r1, r2, r3'),
    (42, 'MAX', 'MAX r1, r2, 254'),
    # Section 6: LDI / MOV
    (43, 'LDI', 'LDI r1.w0, 0x1234'),
    (44, 'LDI', 'LDI r1.w2, 0x5678'),
    (45, 'LDI', 'LDI r1.b0, 0x00AA'),
    (46, 'LDI', 'LDI r1.b1, 0x00BB'),
    (47, 'LDI', 'LDI r1.b2, 0x00CC'),
    (48, 'LDI', 'LDI r1.b3, 0x00DD'),
    (49, 'LDI', 'LDI r1, 0x9999'),
    (50, 'LDI', 'LDI r0, 0x0000'),
    (51, 'LDI', 'LDI r31, 0xFFFF'),
    (52, 'MOV', 'MOV r1, r2'),
    (53, 'MOV', 'MOV r1.b0, r2.b3'),
    # Section 7: Unconditional branches
    (54, 'JMP', 'JMP r5'),
    (55, 'JMP', 'JMP L_BRANCH_TARGET'),
    (56, 'QBA', 'QBA L_BRANCH_TARGET'),
    # Section 8: Conditional branches
    (57, 'QBEQ', 'QBEQ L_BRANCH_TARGET, r1, r2'),
    (58, 'QBEQ', 'QBEQ L_BRANCH_TARGET, r1, 0'),
    (59, 'QBNE', 'QBNE L_BRANCH_TARGET, r1, r2'),
    (60, 'QBNE', 'QBNE L_BRANCH_TARGET, r1, 255'),
    (61, 'QBGT', 'QBGT L_BRANCH_TARGET, r1, r2'),
    (62, 'QBGT', 'QBGT L_BRANCH_TARGET, r1, 16'),
    (63, 'QBGE', 'QBGE L_BRANCH_TARGET, r1, r2'),
    (64, 'QBGE', 'QBGE L_BRANCH_TARGET, r1, 32'),
    (65, 'QBLT', 'QBLT L_BRANCH_TARGET, r1, r2'),
    (66, 'QBLT', 'QBLT L_BRANCH_TARGET, r1, 48'),
    (67, 'QBLE', 'QBLE L_BRANCH_TARGET, r1, r2'),
    (68, 'QBLE', 'QBLE L_BRANCH_TARGET, r1, 64'),
    # Section 9: Bit test and branch
    (69, 'QBBS', 'QBBS L_BRANCH_TARGET, r1, r2'),
    (70, 'QBBS', 'QBBS L_BRANCH_TARGET, r1, 5'),
    (71, 'QBBC', 'QBBC L_BRANCH_TARGET, r1, r2'),
    (72, 'QBBC', 'QBBC L_BRANCH_TARGET, r1, 31'),
    # Branch target (NOP pseudo-instruction)
    (73, 'NOP', 'NOP'),
    # Section 10: LOOP
    (74, 'LOOP', 'LOOP L_LOOP_END1, 10'),
    (76, 'LOOP', 'LOOP L_LOOP_END2, r5'),
    # Section 11: LBBO/SBBO
    (78, 'LBBO', 'LBBO &r1, r2, 0, 4'),
    (79, 'LBBO', 'LBBO &r1, r2, 8, 4'),
    (80, 'LBBO', 'LBBO &r1, r2, r3, 4'),
    (81, 'LBBO', 'LBBO &r1, r2, 0, 1'),
    (82, 'LBBO', 'LBBO &r1, r2, 0, 2'),
    (83, 'LBBO', 'LBBO &r1, r2, 0, 8'),
    (84, 'LBBO', 'LBBO &r1, r2, 0, 16'),
    (85, 'LBBO', 'LBBO &r1, r2, 0, r0.b0'),
    (86, 'SBBO', 'SBBO &r1, r2, 0, 4'),
    (87, 'SBBO', 'SBBO &r1, r2, 12, 4'),
    (88, 'SBBO', 'SBBO &r1, r2, r3, 4'),
    (89, 'SBBO', 'SBBO &r1, r2, 0, 1'),
    (90, 'SBBO', 'SBBO &r1, r2, 0, r0.b0'),
    # Section 12: LBCO/SBCO
    (91, 'LBCO', 'LBCO &r1, c24, 0, 4'),
    (92, 'LBCO', 'LBCO &r1, c28, 16, 4'),
    (93, 'LBCO', 'LBCO &r1, c24, 0, 8'),
    (94, 'LBCO', 'LBCO &r1, c24, r3, 4'),
    (95, 'SBCO', 'SBCO &r1, c24, 0, 4'),
    (96, 'SBCO', 'SBCO &r1, c28, 32, 4'),
    (97, 'SBCO', 'SBCO &r1, c24, 0, r0.b0'),
    # Section 13: XFR
    (98, 'XIN', 'XIN 10, &r0, 4'),
    (99, 'XIN', 'XIN 11, &r0, 4'),
    (100, 'XIN', 'XIN 12, &r0, 4'),
    (101, 'XIN', 'XIN 14, &r2, 32'),
    (102, 'XIN', 'XIN 0, &r25, 8'),
    (103, 'XOUT', 'XOUT 10, &r0, 4'),
    (104, 'XOUT', 'XOUT 11, &r5, 8'),
    (105, 'XOUT', 'XOUT 0, &r25, 8'),
    (106, 'XCHG', 'XCHG 10, &r0, 4'),
    # Section 14: Utility
    (107, 'NOP', 'NOP'),
    (108, 'HALT', 'HALT'),
    (109, 'SLP', 'SLP 0'),
    (110, 'SLP', 'SLP 1'),
    (111, 'ZERO', 'ZERO &r1, 4'),
    (112, 'ZERO', 'ZERO &r0, 36'),
    (113, 'FILL', 'FILL &r1, 4'),
    (114, 'FILL', 'FILL &r1, 8'),
    # Section 15: Register encoding
    (115, 'ADD', 'ADD r0, r0, 0'),
    (116, 'ADD', 'ADD r1, r1, 0'),
    (117, 'ADD', 'ADD r2, r2, 0'),
    (118, 'ADD', 'ADD r3, r3, 0'),
    (119, 'ADD', 'ADD r4, r4, 0'),
    (120, 'ADD', 'ADD r5, r5, 0'),
    (121, 'ADD', 'ADD r8, r8, 0'),
    (122, 'ADD', 'ADD r10, r10, 0'),
    (123, 'ADD', 'ADD r15, r15, 0'),
    (124, 'ADD', 'ADD r16, r16, 0'),
    (125, 'ADD', 'ADD r20, r20, 0'),
    (126, 'ADD', 'ADD r24, r24, 0'),
    (127, 'ADD', 'ADD r28, r28, 0'),
    (128, 'ADD', 'ADD r30, r30, 0'),
    (129, 'ADD', 'ADD r31, r31, 0'),
    # Section 16: Field encoding
    (130, 'LDI', 'LDI r5.b0, 0x0011'),
    (131, 'LDI', 'LDI r5.b1, 0x0022'),
    (132, 'LDI', 'LDI r5.b2, 0x0033'),
    (133, 'LDI', 'LDI r5.b3, 0x0044'),
    (134, 'LDI', 'LDI r5.w0, 0x5555'),
    (135, 'LDI', 'LDI r5.w2, 0x6666'),
    # Section 17: Wait instructions
    (136, 'WBS', 'WBS r31, 5'),
    (137, 'WBC', 'WBC r31, 3'),
    # Section 18: JAL
    (138, 'JAL', 'JAL r30.w0, r5'),
    (139, 'JAL', 'JAL r30.w0, L_BRANCH_TARGET'),
    # Section 19: Backward branches
    (140, 'NOP', 'NOP'),
    (141, 'QBA', 'QBA L_BACK_TARGET'),
    (142, 'QBEQ', 'QBEQ L_BACK_TARGET, r1, 0'),
    # Final HALT
    (143, 'HALT', 'HALT'),
]


class TestDisassemblerValidation:
    """Validate disassembler output against TI CGT compiled binary."""

    def test_elf_loads_correctly(self, elf_image):
        """ELF loader extracts expected number of instruction words."""
        assert len(elf_image.text_words) >= 144

    def test_symbols_extracted(self, elf_image):
        """ELF loader extracts symbols/labels."""
        assert len(elf_image.symbols) > 100
        # Check a few known symbols
        assert 'L_ADD_imm' in elf_image.symbols.values()
        assert 'L_BRANCH_TARGET' in elf_image.symbols.values()
        assert 'L_END' in elf_image.symbols.values()

    @pytest.mark.parametrize("idx,expected_opcode,expected_text", EXPECTED_INSTRUCTIONS,
                             ids=[f"{i}_{t.split()[0]}" for i, _, t in EXPECTED_INSTRUCTIONS])
    def test_instruction(self, disassembled, idx, expected_opcode, expected_text):
        """Verify individual instruction decoding."""
        instr = disassembled[idx]
        assert instr.opcode == expected_opcode, (
            f"[{idx}] opcode mismatch: got '{instr.opcode}', expected '{expected_opcode}'")
        assert instr.source_text == expected_text, (
            f"[{idx}] text mismatch: got '{instr.source_text}', expected '{expected_text}'")

    def test_no_unknown_instructions(self, disassembled):
        """No instruction in the user code section should decode as unknown (.dw)."""
        for i in range(144):
            assert disassembled[i].opcode != 'DW', (
                f"[{i}] decoded as unknown: {disassembled[i].source_text}")

    def test_branch_targets_resolve_to_labels(self, disassembled):
        """All branch instructions targeting L_BRANCH_TARGET use the label name."""
        for i in range(57, 73):
            text = disassembled[i].source_text
            assert 'L_BRANCH_TARGET' in text, (
                f"[{i}] branch target not resolved to label: {text}")

    def test_backward_branch_resolves(self, disassembled):
        """Backward branches resolve to correct label."""
        assert 'L_BACK_TARGET' in disassembled[141].source_text
        assert 'L_BACK_TARGET' in disassembled[142].source_text

    def test_loop_targets_resolve(self, disassembled):
        """LOOP instructions resolve end-label."""
        assert 'L_LOOP_END1' in disassembled[74].source_text
        assert 'L_LOOP_END2' in disassembled[76].source_text


class TestDisassemblerRoundtrip:
    """Verify disassemble_word is deterministic and address-aware."""

    def test_address_tracking(self, elf_image):
        """Each disassembled instruction has correct address."""
        instrs = disassemble(elf_image.text_words, elf_image.symbols)
        for i, instr in enumerate(instrs):
            assert instr.address == i

    def test_deterministic(self, elf_image):
        """Disassembling the same words twice gives the same result."""
        instrs1 = disassemble(elf_image.text_words, elf_image.symbols)
        instrs2 = disassemble(elf_image.text_words, elf_image.symbols)
        for i, (a, b) in enumerate(zip(instrs1, instrs2)):
            assert a.source_text == b.source_text
            assert a.opcode == b.opcode
