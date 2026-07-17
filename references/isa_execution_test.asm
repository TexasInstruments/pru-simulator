; PRU ISA Execution Validation Test
; ==================================
; Runs on real PRU hardware AND in the pru_simulator.
; Each test stores a 4-byte result to DRAM0 via SBCO c24.
;
; Convention:
;   r0  = DRAM write pointer (auto-increments by 4 after each store)
;   r1-r3 = operands
;   r4  = result to store
;   r5  = scratch (branch tests, loop counter)
;
; Compile: clpru --silicon_version=3 -c isa_execution_test.asm
; Link:    clpru --silicon_version=3 -z AM64x_PRU0.cmd -o isa_execution_test.out isa_execution_test.obj -i $CGT/lib -l libc.a
;
; After running on target, save DRAM0 (0x0000, length 0x2000) as isa_execution_hw.bin

    .text
    .global main

; === MACRO: store r4 to [C24 + r0], advance pointer ===
STORE_RESULT .macro
    SBCO &r4, c24, r0, 4
    ADD  r0, r0, 4
    .endm

main:
    ; Initialize write pointer to 0
    LDI  r0, 0

; ============================================================
; SECTION 1: ADD (8 tests)
; ============================================================

; [0] ADD basic: 100 + 200 = 300
    LDI  r1, 100
    LDI  r2, 200
    ADD  r4, r1, r2
    STORE_RESULT

; [1] ADD immediate: 0x1234 + 0x55 = 0x1289
    LDI  r1.w0, 0x1234
    LDI  r1.w2, 0x0000
    ADD  r4, r1, 0x55
    STORE_RESULT

; [2] ADD overflow: 0xFFFFFFFF + 1 = 0x00000000 (wraps)
    LDI  r1.w0, 0xFFFF
    LDI  r1.w2, 0xFFFF
    ADD  r4, r1, 1
    STORE_RESULT

; [3] ADD large: 0x7FFFFFFF + 1 = 0x80000000
    LDI  r1.w0, 0xFFFF
    LDI  r1.w2, 0x7FFF
    ADD  r4, r1, 1
    STORE_RESULT

; [4] ADD zero: 0x12345678 + 0 = 0x12345678
    LDI  r1.w0, 0x5678
    LDI  r1.w2, 0x1234
    ADD  r4, r1, 0
    STORE_RESULT

; [5] ADD byte fields: r1.b0 + r2.b0 (0xA0 + 0x30 = 0xD0)
    LDI  r1, 0x00A0
    LDI  r2, 0x0030
    ADD  r4.b0, r1.b0, r2.b0
    LDI  r4.b1, 0x00
    LDI  r4.b2, 0x00
    LDI  r4.b3, 0x00
    STORE_RESULT

; [6] ADD word fields: r1.w0 + r2.w0 (0x8000 + 0x8000 = 0x0000 wraps in 16-bit)
    LDI  r1.w0, 0x8000
    LDI  r2.w0, 0x8000
    ADD  r4.w0, r1.w0, r2.w0
    LDI  r4.w2, 0x0000
    STORE_RESULT

; [7] ADD high registers: r29 + r30
    LDI  r29, 0x0007
    LDI  r30.w0, 0x0003
    LDI  r30.w2, 0x0000
    ADD  r4, r29, r30
    STORE_RESULT

; ============================================================
; SECTION 2: SUB (6 tests)
; ============================================================

; [8] SUB basic: 500 - 200 = 300
    LDI  r1.w0, 500
    LDI  r1.w2, 0
    LDI  r2, 200
    SUB  r4, r1, r2
    STORE_RESULT

; [9] SUB immediate: 0xFF - 0x0F = 0xF0
    LDI  r1, 0x00FF
    SUB  r4, r1, 0x0F
    STORE_RESULT

; [10] SUB underflow: 0 - 1 = 0xFFFFFFFF (wraps)
    LDI  r1, 0
    SUB  r4, r1, 1
    STORE_RESULT

; [11] SUB equal: 0x12345678 - 0x12345678 = 0
    LDI  r1.w0, 0x5678
    LDI  r1.w2, 0x1234
    MOV  r2, r1
    SUB  r4, r1, r2
    STORE_RESULT

; [12] RSB: 5 - 100 reversed = 100 - 5 = 95
    LDI  r1, 100
    LDI  r2, 5
    RSB  r4, r2, r1
    STORE_RESULT

; [13] RSB immediate: imm - r1 = 0xFF - 0x0F = 0xF0
    LDI  r1, 0x0F
    RSB  r4, r1, 0xFF
    STORE_RESULT

; ============================================================
; SECTION 3: LOGICAL (8 tests)
; ============================================================

; [14] AND: 0xFF00FF00 & 0x0F0F0F0F = 0x0F000F00
    LDI  r1.w0, 0xFF00
    LDI  r1.w2, 0xFF00
    LDI  r2.w0, 0x0F0F
    LDI  r2.w2, 0x0F0F
    AND  r4, r1, r2
    STORE_RESULT

; [15] AND immediate: 0xABCD & 0xFF = 0x000000CD
    LDI  r1.w0, 0xABCD
    LDI  r1.w2, 0x0000
    AND  r4, r1, 0xFF
    STORE_RESULT

; [16] OR: 0xF0F0F0F0 | 0x0F0F0F0F = 0xFFFFFFFF
    LDI  r1.w0, 0xF0F0
    LDI  r1.w2, 0xF0F0
    LDI  r2.w0, 0x0F0F
    LDI  r2.w2, 0x0F0F
    OR   r4, r1, r2
    STORE_RESULT

; [17] OR immediate: 0x1200 | 0x34 = 0x00001234
    LDI  r1.w0, 0x1200
    LDI  r1.w2, 0x0000
    OR   r4, r1, 0x34
    STORE_RESULT

; [18] XOR: 0xAAAAAAAA ^ 0x55555555 = 0xFFFFFFFF
    LDI  r1.w0, 0xAAAA
    LDI  r1.w2, 0xAAAA
    LDI  r2.w0, 0x5555
    LDI  r2.w2, 0x5555
    XOR  r4, r1, r2
    STORE_RESULT

; [19] XOR self = 0
    LDI  r1.w0, 0xDEAD
    LDI  r1.w2, 0xBEEF
    XOR  r4, r1, r1
    STORE_RESULT

; [20] NOT: ~0x00FF00FF = 0xFF00FF00
    LDI  r1.w0, 0x00FF
    LDI  r1.w2, 0x00FF
    NOT  r4, r1
    STORE_RESULT

; [21] NOT zero: ~0x00000000 = 0xFFFFFFFF
    LDI  r1, 0
    NOT  r4, r1
    STORE_RESULT

; ============================================================
; SECTION 4: SHIFTS (8 tests)
; ============================================================

; [22] LSL by 1: 0x00000001 << 1 = 0x00000002
    LDI  r1, 1
    LSL  r4, r1, 1
    STORE_RESULT

; [23] LSL by 16: 0x0000ABCD << 16 = 0xABCD0000
    LDI  r1.w0, 0xABCD
    LDI  r1.w2, 0x0000
    LSL  r4, r1, 16
    STORE_RESULT

; [24] LSL by 31: 0x00000001 << 31 = 0x80000000
    LDI  r1, 1
    LSL  r4, r1, 31
    STORE_RESULT

; [25] LSL by 0: 0xDEADBEEF << 0 = 0xDEADBEEF
    LDI  r1.w0, 0xBEEF
    LDI  r1.w2, 0xDEAD
    LSL  r4, r1, 0
    STORE_RESULT

; [26] LSR by 1: 0x80000000 >> 1 = 0x40000000
    LDI  r1.w0, 0x0000
    LDI  r1.w2, 0x8000
    LSR  r4, r1, 1
    STORE_RESULT

; [27] LSR by 16: 0xABCD0000 >> 16 = 0x0000ABCD
    LDI  r1.w0, 0x0000
    LDI  r1.w2, 0xABCD
    LSR  r4, r1, 16
    STORE_RESULT

; [28] LSR by 31: 0x80000000 >> 31 = 0x00000001
    LDI  r1.w0, 0x0000
    LDI  r1.w2, 0x8000
    LSR  r4, r1, 31
    STORE_RESULT

; [29] LSR by 0: no change
    LDI  r1.w0, 0x1234
    LDI  r1.w2, 0x5678
    LSR  r4, r1, 0
    STORE_RESULT

; ============================================================
; SECTION 5: BIT OPERATIONS (8 tests)
; ============================================================

; [30] SET bit 0: 0x00000000 → 0x00000001
    LDI  r1, 0
    SET  r4, r1, 0
    STORE_RESULT

; [31] SET bit 31: 0x00000000 → 0x80000000
    LDI  r1, 0
    SET  r4, r1, 31
    STORE_RESULT

; [32] SET bit 15: 0x00000000 → 0x00008000
    LDI  r1, 0
    SET  r4, r1, 15
    STORE_RESULT

; [33] CLR bit 0: 0xFFFFFFFF → 0xFFFFFFFE
    LDI  r1.w0, 0xFFFF
    LDI  r1.w2, 0xFFFF
    CLR  r4, r1, 0
    STORE_RESULT

; [34] CLR bit 31: 0xFFFFFFFF → 0x7FFFFFFF
    LDI  r1.w0, 0xFFFF
    LDI  r1.w2, 0xFFFF
    CLR  r4, r1, 31
    STORE_RESULT

; [35] CLR bit 7: 0x000000FF → 0x0000007F
    LDI  r1, 0x00FF
    CLR  r4, r1, 7
    STORE_RESULT

; [36] LMBD find leftmost 1 in 0x80000000 = bit 31
    LDI  r1.w0, 0x0000
    LDI  r1.w2, 0x8000
    LMBD r4, r1, 1
    STORE_RESULT

; [37] LMBD find leftmost 1 in 0x00000001 = bit 0
    LDI  r1, 1
    LMBD r4, r1, 1
    STORE_RESULT

; ============================================================
; SECTION 6: MIN / MAX (6 tests)
; ============================================================

; [38] MIN(10, 20) = 10
    LDI  r1, 10
    LDI  r2, 20
    MIN  r4, r1, r2
    STORE_RESULT

; [39] MIN(20, 10) = 10
    LDI  r1, 20
    LDI  r2, 10
    MIN  r4, r1, r2
    STORE_RESULT

; [40] MIN(5, 5) = 5
    LDI  r1, 5
    MIN  r4, r1, 5
    STORE_RESULT

; [41] MAX(10, 20) = 20
    LDI  r1, 10
    LDI  r2, 20
    MAX  r4, r1, r2
    STORE_RESULT

; [42] MAX(20, 10) = 20
    LDI  r1, 20
    LDI  r2, 10
    MAX  r4, r1, r2
    STORE_RESULT

; [43] MAX(5, 5) = 5
    LDI  r1, 5
    MAX  r4, r1, 5
    STORE_RESULT

; ============================================================
; SECTION 7: DATA MOVEMENT (8 tests)
; ============================================================

; [44] LDI full register: r4 = 0x0000ABCD
    LDI  r4, 0xABCD
    STORE_RESULT

; [45] LDI w0 + w2 combined: 0x56781234
    LDI  r4.w0, 0x1234
    LDI  r4.w2, 0x5678
    STORE_RESULT

; [46] LDI byte fields: construct 0x44332211
    LDI  r4.b0, 0x11
    LDI  r4.b1, 0x22
    LDI  r4.b2, 0x33
    LDI  r4.b3, 0x44
    STORE_RESULT

; [47] MOV reg-to-reg: r4 = r1 = 0xCAFEBABE
    LDI  r1.w0, 0xBABE
    LDI  r1.w2, 0xCAFE
    MOV  r4, r1
    STORE_RESULT

; [48] MOV field: r4.b0 = r1.b3 (0xDE → low byte)
    LDI  r4.w0, 0x0000
    LDI  r4.w2, 0x0000
    LDI  r1.w0, 0x0000
    LDI  r1.w2, 0xDE00
    MOV  r4.b0, r1.b3
    STORE_RESULT

; [49] MOV w2 to w0: r4.w0 = r1.w2
    LDI  r4.w0, 0x0000
    LDI  r4.w2, 0x0000
    LDI  r1.w0, 0x0000
    LDI  r1.w2, 0xFACE
    MOV  r4.w0, r1.w2
    STORE_RESULT

; [50] LDI then overwrite b1: 0x0000XX00 pattern
    LDI  r4, 0
    LDI  r4.b1, 0xAA
    STORE_RESULT

; [51] LDI 0xFFFF to w0, 0x0000 to w2: result = 0x0000FFFF
    LDI  r4.w0, 0xFFFF
    LDI  r4.w2, 0x0000
    STORE_RESULT

; ============================================================
; SECTION 8: BRANCHES — comparison semantics (16 tests)
; Tests store 1 if branch taken, 0 if not.
; This section will reveal if comparison order is correct.
; ============================================================

; --- QBEQ: branch if op1 == op2 ---
; [52] QBEQ: r1=5, op2=5 → should branch (equal)
    LDI  r4, 0
    LDI  r1, 5
    QBEQ TAKEN_52, r1, 5
    QBA  DONE_52
TAKEN_52:
    LDI  r4, 1
DONE_52:
    STORE_RESULT

; [53] QBEQ: r1=5, op2=6 → should NOT branch
    LDI  r4, 0
    LDI  r1, 5
    QBEQ TAKEN_53, r1, 6
    QBA  DONE_53
TAKEN_53:
    LDI  r4, 1
DONE_53:
    STORE_RESULT

; --- QBNE: branch if not equal ---
; [54] QBNE: r1=5, op2=6 → should branch (not equal)
    LDI  r4, 0
    LDI  r1, 5
    QBNE TAKEN_54, r1, 6
    QBA  DONE_54
TAKEN_54:
    LDI  r4, 1
DONE_54:
    STORE_RESULT

; [55] QBNE: r1=5, op2=5 → should NOT branch (equal)
    LDI  r4, 0
    LDI  r1, 5
    QBNE TAKEN_55, r1, 5
    QBA  DONE_55
TAKEN_55:
    LDI  r4, 1
DONE_55:
    STORE_RESULT

; --- QBGT: branch if op1 > op2 ---
; Per TI spec: QBGT label, op1, op2 branches if op1 > op2
; [56] QBGT: op1=10, op2=r1=5 → 10 > 5, should branch
    LDI  r4, 0
    LDI  r1, 5
    QBGT TAKEN_56, r1, 10
    QBA  DONE_56
TAKEN_56:
    LDI  r4, 1
DONE_56:
    STORE_RESULT

; [57] QBGT: op1=5, op2=r1=10 → 5 > 10? NO
    LDI  r4, 0
    LDI  r1, 10
    QBGT TAKEN_57, r1, 5
    QBA  DONE_57
TAKEN_57:
    LDI  r4, 1
DONE_57:
    STORE_RESULT

; [58] QBGT: op1=5, op2=r1=5 → 5 > 5? NO (equal)
    LDI  r4, 0
    LDI  r1, 5
    QBGT TAKEN_58, r1, 5
    QBA  DONE_58
TAKEN_58:
    LDI  r4, 1
DONE_58:
    STORE_RESULT

; --- QBGE: branch if op1 >= op2 ---
; [59] QBGE: op1=10, op2=r1=5 → 10 >= 5, should branch
    LDI  r4, 0
    LDI  r1, 5
    QBGE TAKEN_59, r1, 10
    QBA  DONE_59
TAKEN_59:
    LDI  r4, 1
DONE_59:
    STORE_RESULT

; [60] QBGE: op1=5, op2=r1=5 → 5 >= 5, should branch (equal)
    LDI  r4, 0
    LDI  r1, 5
    QBGE TAKEN_60, r1, 5
    QBA  DONE_60
TAKEN_60:
    LDI  r4, 1
DONE_60:
    STORE_RESULT

; [61] QBGE: op1=3, op2=r1=5 → 3 >= 5? NO
    LDI  r4, 0
    LDI  r1, 5
    QBGE TAKEN_61, r1, 3
    QBA  DONE_61
TAKEN_61:
    LDI  r4, 1
DONE_61:
    STORE_RESULT

; --- QBLT: branch if op1 < op2 ---
; [62] QBLT: op1=3, op2=r1=5 → 3 < 5, should branch
    LDI  r4, 0
    LDI  r1, 5
    QBLT TAKEN_62, r1, 3
    QBA  DONE_62
TAKEN_62:
    LDI  r4, 1
DONE_62:
    STORE_RESULT

; [63] QBLT: op1=10, op2=r1=5 → 10 < 5? NO
    LDI  r4, 0
    LDI  r1, 5
    QBLT TAKEN_63, r1, 10
    QBA  DONE_63
TAKEN_63:
    LDI  r4, 1
DONE_63:
    STORE_RESULT

; [64] QBLT: op1=5, op2=r1=5 → 5 < 5? NO (equal)
    LDI  r4, 0
    LDI  r1, 5
    QBLT TAKEN_64, r1, 5
    QBA  DONE_64
TAKEN_64:
    LDI  r4, 1
DONE_64:
    STORE_RESULT

; --- QBLE: branch if op1 <= op2 ---
; [65] QBLE: op1=3, op2=r1=5 → 3 <= 5, should branch
    LDI  r4, 0
    LDI  r1, 5
    QBLE TAKEN_65, r1, 3
    QBA  DONE_65
TAKEN_65:
    LDI  r4, 1
DONE_65:
    STORE_RESULT

; [66] QBLE: op1=5, op2=r1=5 → 5 <= 5, should branch (equal)
    LDI  r4, 0
    LDI  r1, 5
    QBLE TAKEN_66, r1, 5
    QBA  DONE_66
TAKEN_66:
    LDI  r4, 1
DONE_66:
    STORE_RESULT

; [67] QBLE: op1=10, op2=r1=5 → 10 <= 5? NO
    LDI  r4, 0
    LDI  r1, 5
    QBLE TAKEN_67, r1, 10
    QBA  DONE_67
TAKEN_67:
    LDI  r4, 1
DONE_67:
    STORE_RESULT

; ============================================================
; SECTION 9: BIT TEST BRANCHES (4 tests)
; ============================================================

; [68] QBBS: test bit 0 of 0x00000001 → set, should branch
    LDI  r4, 0
    LDI  r1, 1
    QBBS TAKEN_68, r1, 0
    QBA  DONE_68
TAKEN_68:
    LDI  r4, 1
DONE_68:
    STORE_RESULT

; [69] QBBS: test bit 0 of 0x00000002 → clear, should NOT branch
    LDI  r4, 0
    LDI  r1, 2
    QBBS TAKEN_69, r1, 0
    QBA  DONE_69
TAKEN_69:
    LDI  r4, 1
DONE_69:
    STORE_RESULT

; [70] QBBC: test bit 0 of 0x00000002 → clear, should branch
    LDI  r4, 0
    LDI  r1, 2
    QBBC TAKEN_70, r1, 0
    QBA  DONE_70
TAKEN_70:
    LDI  r4, 1
DONE_70:
    STORE_RESULT

; [71] QBBC: test bit 31 of 0x80000000 → set, should NOT branch
    LDI  r4, 0
    LDI  r1.w0, 0x0000
    LDI  r1.w2, 0x8000
    QBBC TAKEN_71, r1, 31
    QBA  DONE_71
TAKEN_71:
    LDI  r4, 1
DONE_71:
    STORE_RESULT

; ============================================================
; SECTION 10: MEMORY LOAD/STORE round-trip (4 tests)
; ============================================================

; [72] SBBO + LBBO round-trip: write 0xDEADBEEF, read back
    LDI  r1.w0, 0xBEEF
    LDI  r1.w2, 0xDEAD
    LDI  r2.w0, 0x1F00
    LDI  r2.w2, 0x0000
    SBBO &r1, r2, 0, 4       ; write to DRAM[0x1F00]
    LDI  r4, 0
    LBBO &r4, r2, 0, 4       ; read back
    STORE_RESULT

; [73] SBBO partial: write 2 bytes (0xCAFE), read back 4 bytes (upper 2 should be 0)
    LDI  r1.w0, 0xCAFE
    LDI  r2.w0, 0x1F10
    LDI  r2.w2, 0x0000
    ; Clear target location first
    LDI  r3, 0
    SBBO &r3, r2, 0, 4
    ; Write 2 bytes
    SBBO &r1, r2, 0, 2
    LBBO &r4, r2, 0, 4
    STORE_RESULT

; [74] SBCO/LBCO round-trip via C24 (use SBBO to reach beyond 255)
    LDI  r1.w0, 0x1234
    LDI  r1.w2, 0x5678
    LDI  r2.w0, 0x1F20
    LDI  r2.w2, 0x0000
    SBBO &r1, r2, 0, 4
    LDI  r4, 0
    LBBO &r4, r2, 0, 4
    STORE_RESULT

; [75] LBBO with offset: write at base+8, read from base+8
    LDI  r1.w0, 0xFACE
    LDI  r1.w2, 0xFEED
    LDI  r2.w0, 0x1F30
    LDI  r2.w2, 0x0000
    SBBO &r1, r2, 8, 4       ; write to 0x1F38
    LDI  r4, 0
    LBBO &r4, r2, 8, 4       ; read from 0x1F38
    STORE_RESULT

; ============================================================
; SECTION 11: LOOP (2 tests)
; ============================================================

; [76] LOOP 10 iterations: accumulate r4 += 3 each iter → 30
    LDI  r4, 0
    LOOP LOOP_END_76, 10
    ADD  r4, r4, 3
LOOP_END_76:
    STORE_RESULT

; [77] LOOP 1 iteration: body should execute once
    LDI  r4, 0
    LOOP LOOP_END_77, 1
    ADD  r4, r4, 99
LOOP_END_77:
    STORE_RESULT

; ============================================================
; SECTION 12: LMBD edge cases (2 tests)
; ============================================================

; [78] LMBD find leftmost 0 in 0x7FFFFFFF = bit 31
    LDI  r1.w0, 0xFFFF
    LDI  r1.w2, 0x7FFF
    LMBD r4, r1, 0
    STORE_RESULT

; [79] LMBD find leftmost 1 in 0x00000000 = 32 (not found)
    LDI  r1, 0
    LMBD r4, r1, 1
    STORE_RESULT

; ============================================================
; SECTION 13: Carry-chain tests with ADC/SUC (4 tests)
; ============================================================

; [80] ADC without prior carry: same as ADD (100 + 50 = 150)
;      (carry flag state depends on prior SUB; do a non-borrowing SUB first)
    LDI  r1, 10
    LDI  r2, 5
    SUB  r3, r1, r2          ; 10 - 5 = 5, no borrow → carry=1
    LDI  r1, 100
    ADC  r4, r1, 50          ; 100 + 50 + carry(1) = 151
    STORE_RESULT

; [81] SUB that causes borrow, then ADC
    LDI  r1, 5
    LDI  r2, 10
    SUB  r3, r1, r2          ; 5 - 10 = underflow, borrow → carry=0
    LDI  r1, 100
    ADC  r4, r1, 50          ; 100 + 50 + carry(0) = 150
    STORE_RESULT

; [82] SUC: subtract with carry (no borrow case)
    LDI  r1, 10
    LDI  r2, 5
    SUB  r3, r1, r2          ; no borrow → carry=1
    LDI  r1, 100
    SUC  r4, r1, 50          ; 100 - 50 - ~carry = 100 - 50 - 0 = 50
    STORE_RESULT

; [83] SUC: subtract with carry (borrow case)
    LDI  r1, 5
    LDI  r2, 10
    SUB  r3, r1, r2          ; borrow → carry=0
    LDI  r1, 100
    SUC  r4, r1, 50          ; 100 - 50 - ~carry = 100 - 50 - 1 = 49
    STORE_RESULT

; ============================================================
; SECTION 14: Register field isolation (4 tests)
; ============================================================

; [84] Write b0 only, verify rest unchanged
    LDI  r4.w0, 0x0000
    LDI  r4.w2, 0x0000
    LDI  r4.b0, 0xAA
    STORE_RESULT

; [85] Write b3 only
    LDI  r4.w0, 0x0000
    LDI  r4.w2, 0x0000
    LDI  r4.b3, 0xBB
    STORE_RESULT

; [86] Write w2 only, w0 should stay
    LDI  r4.w0, 0x1111
    LDI  r4.w2, 0x2222
    STORE_RESULT

; [87] ADD to b0 field only: 0x10 + 0x20 = 0x30, upper bytes zeroed
    LDI  r4, 0
    LDI  r1.b0, 0x10
    LDI  r2.b0, 0x20
    ADD  r4.b0, r1.b0, r2.b0
    STORE_RESULT

; ============================================================
; SECTION 15: Unsigned comparison edge cases (8 tests)
; ============================================================

; [88] QBGT: 0xFFFFFFFF > 0x00000001? YES (unsigned)
    LDI  r4, 0
    LDI  r1, 1
    LDI  r2.w0, 0xFFFF
    LDI  r2.w2, 0xFFFF
    QBGT TAKEN_88, r1, r2
    QBA  DONE_88
TAKEN_88:
    LDI  r4, 1
DONE_88:
    STORE_RESULT

; [89] QBGT: 0x00000001 > 0xFFFFFFFF? NO (unsigned)
    LDI  r4, 0
    LDI  r1.w0, 0xFFFF
    LDI  r1.w2, 0xFFFF
    QBGT TAKEN_89, r1, 1
    QBA  DONE_89
TAKEN_89:
    LDI  r4, 1
DONE_89:
    STORE_RESULT

; [90] QBGE: 0x80000000 >= 0x7FFFFFFF? YES (unsigned, 0x8... > 0x7...)
    LDI  r4, 0
    LDI  r1.w0, 0xFFFF
    LDI  r1.w2, 0x7FFF
    LDI  r2.w0, 0x0000
    LDI  r2.w2, 0x8000
    QBGE TAKEN_90, r1, r2
    QBA  DONE_90
TAKEN_90:
    LDI  r4, 1
DONE_90:
    STORE_RESULT

; [91] QBLT: 0x7FFFFFFF < 0x80000000? YES (unsigned)
    LDI  r4, 0
    LDI  r1.w0, 0x0000
    LDI  r1.w2, 0x8000
    LDI  r2.w0, 0xFFFF
    LDI  r2.w2, 0x7FFF
    QBLT TAKEN_91, r1, r2
    QBA  DONE_91
TAKEN_91:
    LDI  r4, 1
DONE_91:
    STORE_RESULT

; [92] QBGE: 0 >= 0? YES
    LDI  r4, 0
    LDI  r1, 0
    QBGE TAKEN_92, r1, 0
    QBA  DONE_92
TAKEN_92:
    LDI  r4, 1
DONE_92:
    STORE_RESULT

; [93] QBGT: 0 > 0? NO
    LDI  r4, 0
    LDI  r1, 0
    QBGT TAKEN_93, r1, 0
    QBA  DONE_93
TAKEN_93:
    LDI  r4, 1
DONE_93:
    STORE_RESULT

; [94] QBLE: 0 <= 0? YES
    LDI  r4, 0
    LDI  r1, 0
    QBLE TAKEN_94, r1, 0
    QBA  DONE_94
TAKEN_94:
    LDI  r4, 1
DONE_94:
    STORE_RESULT

; [95] QBLT: 0 < 0? NO
    LDI  r4, 0
    LDI  r1, 0
    QBLT TAKEN_95, r1, 0
    QBA  DONE_95
TAKEN_95:
    LDI  r4, 1
DONE_95:
    STORE_RESULT

; ============================================================
; FINAL: Store test count and halt
; ============================================================

; [96] Sentinel: store the total test count (96) as final marker
    LDI  r4, 96
    STORE_RESULT

    HALT
