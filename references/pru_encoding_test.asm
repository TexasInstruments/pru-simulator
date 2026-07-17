; PRU Instruction Encoding Reference File
; =========================================
; Compile with TI PRU CGT and return the .out file.
; Each instruction uses distinct operand values so the binary encoding
; can be unambiguously mapped back to the assembly.
;
; Linker command: use standard AM243x_PRU0.cmd (or any PRU linker .cmd)
;
; Convention:
;   - Dest = r1 (unless testing dest variations)
;   - Src1 = r2 (unless testing src variations)
;   - Src2 = r3 or immediate (unless testing src2 variations)
;   - Labels named L_<mnemonic>_<variant> for easy identification

    .text
    .global main

main:

; ============================================================
; SECTION 1: ARITHMETIC (Format 1)
; ADD Rd, Rs1, OP255  (OP255 = register or 8-bit immediate)
; ============================================================
L_ADD_reg:
    ADD r1, r2, r3              ; dest=r1, src1=r2, src2=r3 (register)
L_ADD_imm:
    ADD r1, r2, 0x37            ; dest=r1, src1=r2, src2=0x37 (immediate)
L_ADD_field_b0:
    ADD r1.b0, r2.b0, r3.b0    ; byte0 fields
L_ADD_field_b1:
    ADD r1.b1, r2.b1, r3.b1    ; byte1 fields
L_ADD_field_b2:
    ADD r1.b2, r2.b2, r3.b2    ; byte2 fields
L_ADD_field_b3:
    ADD r1.b3, r2.b3, r3.b3    ; byte3 fields
L_ADD_field_w0:
    ADD r1.w0, r2.w0, r3.w0    ; word0 fields
L_ADD_field_w2:
    ADD r1.w2, r2.w2, r3.w2    ; word2 fields
L_ADD_highreg:
    ADD r31, r30, r29           ; high register numbers

L_ADC_reg:
    ADC r1, r2, r3              ; add with carry, register
L_ADC_imm:
    ADC r1, r2, 0x55            ; add with carry, immediate

L_SUB_reg:
    SUB r1, r2, r3              ; subtract, register
L_SUB_imm:
    SUB r1, r2, 0xAB            ; subtract, immediate

L_SUC_reg:
    SUC r1, r2, r3              ; subtract with carry, register
L_SUC_imm:
    SUC r1, r2, 0x12            ; subtract with carry, immediate

L_RSB_reg:
    RSB r1, r2, r3              ; reverse subtract, register
L_RSB_imm:
    RSB r1, r2, 0x99            ; reverse subtract, immediate

L_RSC_reg:
    RSC r1, r2, r3              ; reverse subtract with carry, register
L_RSC_imm:
    RSC r1, r2, 0x44            ; reverse subtract with carry, immediate

; ============================================================
; SECTION 2: LOGICAL (Format 1 — same structure as arithmetic)
; ============================================================
L_AND_reg:
    AND r1, r2, r3
L_AND_imm:
    AND r1, r2, 0xFF
L_OR_reg:
    OR r1, r2, r3
L_OR_imm:
    OR r1, r2, 0x80
L_XOR_reg:
    XOR r1, r2, r3
L_XOR_imm:
    XOR r1, r2, 0x01
L_NOT_reg:
    NOT r1, r2

; ============================================================
; SECTION 3: SHIFT (Format 1 — OP(31) for shift amount)
; ============================================================
L_LSL_reg:
    LSL r1, r2, r3              ; shift amount in register
L_LSL_imm:
    LSL r1, r2, 7               ; shift amount immediate (0-31)
L_LSL_imm_max:
    LSL r1, r2, 31              ; max shift

L_LSR_reg:
    LSR r1, r2, r3
L_LSR_imm:
    LSR r1, r2, 16              ; shift right by 16

; ============================================================
; SECTION 4: BIT OPERATIONS
; ============================================================
L_SET_reg:
    SET r1, r2, r3              ; set bit (bit pos in register)
L_SET_imm:
    SET r1, r2, 15              ; set bit 15
L_SET_imm0:
    SET r1, r2, 0               ; set bit 0
L_SET_imm31:
    SET r1, r2, 31              ; set bit 31

L_CLR_reg:
    CLR r1, r2, r3
L_CLR_imm:
    CLR r1, r2, 7               ; clear bit 7

L_LMBD_reg:
    LMBD r1, r2, r3             ; left-most bit detect (register)
L_LMBD_imm:
    LMBD r1, r2, 1              ; LMBD searching for 1

; ============================================================
; SECTION 5: MIN/MAX
; ============================================================
L_MIN_reg:
    MIN r1, r2, r3
L_MIN_imm:
    MIN r1, r2, 0x42
L_MAX_reg:
    MAX r1, r2, r3
L_MAX_imm:
    MAX r1, r2, 0xFE

; ============================================================
; SECTION 6: DATA MOVEMENT
; ============================================================
L_LDI_w0:
    LDI r1.w0, 0x1234           ; load immediate to w0
L_LDI_w2:
    LDI r1.w2, 0x5678           ; load immediate to w2
L_LDI_b0:
    LDI r1.b0, 0xAA             ; load immediate to b0
L_LDI_b1:
    LDI r1.b1, 0xBB             ; load immediate to b1
L_LDI_b2:
    LDI r1.b2, 0xCC             ; load immediate to b2
L_LDI_b3:
    LDI r1.b3, 0xDD             ; load immediate to b3
L_LDI_reg:
    LDI r1, 0x9999              ; load immediate to full register (16-bit)
L_LDI_zero:
    LDI r0, 0                   ; r0 = 0
L_LDI_r31:
    LDI r31, 0xFFFF             ; r31 = 0xFFFF

L_MOV_reg:
    MOV r1, r2                  ; register move
L_MOV_field:
    MOV r1.b0, r2.b3            ; field-to-field move

; ============================================================
; SECTION 7: BRANCHES — UNCONDITIONAL
; ============================================================
L_JMP_reg:
    JMP r5                      ; jump to address in register
L_JMP_label:
    JMP L_BRANCH_TARGET         ; jump to label (forward)
L_QBA_label:
    QBA L_BRANCH_TARGET         ; quick branch always

; ============================================================
; SECTION 8: BRANCHES — CONDITIONAL (compare two operands)
; ============================================================
L_QBEQ_reg:
    QBEQ L_BRANCH_TARGET, r1, r2    ; branch if r1 == r2
L_QBEQ_imm:
    QBEQ L_BRANCH_TARGET, r1, 0x00  ; branch if r1 == 0
L_QBNE_reg:
    QBNE L_BRANCH_TARGET, r1, r2    ; branch if r1 != r2
L_QBNE_imm:
    QBNE L_BRANCH_TARGET, r1, 0xFF  ; branch if r1 != 0xFF
L_QBGT_reg:
    QBGT L_BRANCH_TARGET, r1, r2    ; branch if r1 > r2 (unsigned)
L_QBGT_imm:
    QBGT L_BRANCH_TARGET, r1, 0x10
L_QBGE_reg:
    QBGE L_BRANCH_TARGET, r1, r2
L_QBGE_imm:
    QBGE L_BRANCH_TARGET, r1, 0x20
L_QBLT_reg:
    QBLT L_BRANCH_TARGET, r1, r2
L_QBLT_imm:
    QBLT L_BRANCH_TARGET, r1, 0x30
L_QBLE_reg:
    QBLE L_BRANCH_TARGET, r1, r2
L_QBLE_imm:
    QBLE L_BRANCH_TARGET, r1, 0x40

; ============================================================
; SECTION 9: BRANCHES — BIT TEST
; ============================================================
L_QBBS_reg:
    QBBS L_BRANCH_TARGET, r1, r2    ; branch if bit set (bit pos in reg)
L_QBBS_imm:
    QBBS L_BRANCH_TARGET, r1, 5     ; branch if bit 5 set
L_QBBC_reg:
    QBBC L_BRANCH_TARGET, r1, r2
L_QBBC_imm:
    QBBC L_BRANCH_TARGET, r1, 31    ; branch if bit 31 clear

L_BRANCH_TARGET:
    NOP                              ; target for all branches above

; ============================================================
; SECTION 10: LOOP
; ============================================================
L_LOOP_imm:
    LOOP L_LOOP_END1, 10             ; loop 10 times (immediate count)
    ADD r1, r1, 1
L_LOOP_END1:

L_LOOP_reg:
    LOOP L_LOOP_END2, r5             ; loop r5 times (register count)
    ADD r2, r2, 1
L_LOOP_END2:

; ============================================================
; SECTION 11: MEMORY — LBBO/SBBO (base + offset)
; ============================================================
L_LBBO_imm_off:
    LBBO &r1, r2, 0, 4              ; load 4 bytes from [r2+0] into r1
L_LBBO_imm_off2:
    LBBO &r1, r2, 8, 4              ; load 4 bytes from [r2+8]
L_LBBO_reg_off:
    LBBO &r1, r2, r3, 4             ; load 4 bytes from [r2+r3]
L_LBBO_len1:
    LBBO &r1, r2, 0, 1              ; load 1 byte
L_LBBO_len2:
    LBBO &r1, r2, 0, 2              ; load 2 bytes
L_LBBO_len8:
    LBBO &r1, r2, 0, 8              ; load 8 bytes (r1-r2)
L_LBBO_len16:
    LBBO &r1, r2, 0, 16             ; load 16 bytes
L_LBBO_len_reg:
    LBBO &r1, r2, 0, b0             ; load r0.b0 bytes (register length)

L_SBBO_imm_off:
    SBBO &r1, r2, 0, 4              ; store 4 bytes from r1 to [r2+0]
L_SBBO_imm_off2:
    SBBO &r1, r2, 12, 4             ; store 4 bytes to [r2+12]
L_SBBO_reg_off:
    SBBO &r1, r2, r3, 4             ; store 4 bytes to [r2+r3]
L_SBBO_len1:
    SBBO &r1, r2, 0, 1              ; store 1 byte
L_SBBO_len_reg:
    SBBO &r1, r2, 0, b0             ; store r0.b0 bytes (register length)

; ============================================================
; SECTION 12: MEMORY — LBCO/SBCO (constant table + offset)
; ============================================================
L_LBCO_c24:
    LBCO &r1, c24, 0, 4             ; load 4 bytes from C24+0
L_LBCO_c28:
    LBCO &r1, c28, 16, 4            ; load 4 bytes from C28+16
L_LBCO_len8:
    LBCO &r1, c24, 0, 8             ; load 8 bytes
L_LBCO_reg_off:
    LBCO &r1, c24, r3, 4            ; load with register offset

L_SBCO_c24:
    SBCO &r1, c24, 0, 4             ; store 4 bytes to C24+0
L_SBCO_c28:
    SBCO &r1, c28, 32, 4            ; store 4 bytes to C28+32
L_SBCO_len_reg:
    SBCO &r1, c24, 0, b0            ; store r0.b0 bytes (register length)

; ============================================================
; SECTION 13: XFR — XIN/XOUT/XCHG
; ============================================================
L_XIN_bank0:
    XIN 10, &r0, 4                   ; XIN from device 10 (SPAD bank0), 4 bytes
L_XIN_bank1:
    XIN 11, &r0, 4                   ; XIN from device 11 (SPAD bank1)
L_XIN_bank2:
    XIN 12, &r0, 4                   ; XIN from device 12 (SPAD bank2)
L_XIN_ipc:
    XIN 14, &r2, 32                  ; XIN from device 14 (broadside), 32 bytes
L_XIN_mac:
    XIN 0, &r25, 8                   ; XIN from MAC accelerator

L_XOUT_bank0:
    XOUT 10, &r0, 4                  ; XOUT to device 10
L_XOUT_bank1:
    XOUT 11, &r5, 8                  ; XOUT to device 11, 8 bytes from r5
L_XOUT_mac:
    XOUT 0, &r25, 8                  ; XOUT to MAC accelerator

L_XCHG_bank0:
    XCHG 10, &r0, 4                  ; XCHG with SPAD bank0

; ============================================================
; SECTION 14: UTILITY
; ============================================================
L_NOP:
    NOP
L_HALT:
    HALT
L_SLP_0:
    SLP 0                            ; sleep mode 0
L_SLP_1:
    SLP 1                            ; sleep mode 1
L_ZERO_4:
    ZERO &r1, 4                      ; zero 4 bytes starting at r1
L_ZERO_36:
    ZERO &r0, 36                     ; zero 36 bytes (r0-r8)
L_FILL_4:
    FILL &r1, 4                      ; fill 4 bytes with 0xFF
L_FILL_8:
    FILL &r1, 8                      ; fill 8 bytes with 0xFF

; ============================================================
; SECTION 15: REGISTER NUMBER ENCODING TEST
; Each uses ADD rN, rN, 0 to isolate register number encoding
; ============================================================
L_REG_r0:
    ADD r0, r0, 0
L_REG_r1:
    ADD r1, r1, 0
L_REG_r2:
    ADD r2, r2, 0
L_REG_r3:
    ADD r3, r3, 0
L_REG_r4:
    ADD r4, r4, 0
L_REG_r5:
    ADD r5, r5, 0
L_REG_r8:
    ADD r8, r8, 0
L_REG_r10:
    ADD r10, r10, 0
L_REG_r15:
    ADD r15, r15, 0
L_REG_r16:
    ADD r16, r16, 0
L_REG_r20:
    ADD r20, r20, 0
L_REG_r24:
    ADD r24, r24, 0
L_REG_r28:
    ADD r28, r28, 0
L_REG_r30:
    ADD r30, r30, 0
L_REG_r31:
    ADD r31, r31, 0

; ============================================================
; SECTION 16: FIELD ENCODING TEST
; Each uses LDI with different field selectors on r5
; ============================================================
L_FIELD_r5_b0:
    LDI r5.b0, 0x11
L_FIELD_r5_b1:
    LDI r5.b1, 0x22
L_FIELD_r5_b2:
    LDI r5.b2, 0x33
L_FIELD_r5_b3:
    LDI r5.b3, 0x44
L_FIELD_r5_w0:
    LDI r5.w0, 0x5555
L_FIELD_r5_w2:
    LDI r5.w2, 0x6666

; ============================================================
; SECTION 17: WAIT INSTRUCTIONS
; ============================================================
L_WBS:
    WBS r31, 5                       ; wait for bit 5 of r31 to be set
L_WBC:
    WBC r31, 3                       ; wait for bit 3 of r31 to be clear

; ============================================================
; SECTION 18: JAL (Jump and Link)
; ============================================================
L_JAL_reg:
    JAL r30.w0, r5                   ; jump to r5, save return addr in r30.w0
L_JAL_label:
    JAL r30.w0, L_BRANCH_TARGET      ; jump to label, save return addr

; ============================================================
; SECTION 19: BACKWARD BRANCH TEST
; ============================================================
L_BACK_TARGET:
    NOP
L_QBA_back:
    QBA L_BACK_TARGET                ; backward branch (negative offset)
L_QBEQ_back:
    QBEQ L_BACK_TARGET, r1, 0       ; backward conditional branch

; ============================================================
; END — HALT to stop execution
; ============================================================
L_END:
    HALT
