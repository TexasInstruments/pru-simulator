; SPDX-License-Identifier: BSD-3-Clause
; Copyright (C) 2024-2025 Texas Instruments Incorporated - http://www.ti.com/

;*******************************************************************************
;   File:     sdfm_sinc3_demo.asm
;
;   Brief:    SINC3 sigma-delta filter demo for the PRU Simulator.
;             Implements a free-running SINC3 filter on SD channel 2 with
;             OSR=64, stores 256 filtered samples to DRAM0.
;
;   Adapted from:
;     workspace_ccstheia/empty_am261x-lp_icss_m0_pru1_fw_ti-pru-cgt/main.asm
;
;   Hardware differences from the original AM261x ICSS-M firmware:
;     - ICSS-G (simulator) SD registers are at C4+0x44..0x5F
;       (AM261x ICSS-M used C4+0x90..0xA9)
;     - R30/R31 encoding is identical on ICSS-M and ICSS-G: reinit=bit[23],
;       clr_ovf+val=bit[24], valid=bit[28], read is non-destructive
;     - Data buffer uses DRAM0 (C24 = 0x00000000); original used C16 (external)
;     - GPCFG pin-mux init is omitted (handled by simulator configuration)
;
;   How to run in the simulator:
;     See source/sdfm_sinc3_demo/README.md
;
;*******************************************************************************

    .include "include/sdfm_defines.inc"
    .include "include/sdfm_macros.inc"

; ── Constant table entries used in this demo ──────────────────────────────────
; C4  = 0x00026000  ICSS_CFG base (SD registers at C4+0x44..0x5F)
; C24 = 0x00000000  DRAM0 base    (256-sample result buffer)
;
; These values are pre-loaded by config/constants_am243x.cfg; no code needed.
;
; SD register offsets from C4 (ICSS-G layout):
;   SD_CFG_REG0         C4 + 0x44   global SD config
;   SD_CLK_SEL_REG0     C4 + 0x48   channel 0 clock / acc-select
;   SD_SAMPLE_SIZE_REG0 C4 + 0x4C   channel 0 OSR
;   SD_CLK_SEL_REG1     C4 + 0x50
;   SD_SAMPLE_SIZE_REG1 C4 + 0x54
;   SD_CLK_SEL_REG2     C4 + 0x58   ← channel 2 used in this demo
;   SD_SAMPLE_SIZE_REG2 C4 + 0x5C   ← channel 2 OSR = 64

; ── SD_CLK_SEL_REG field encoding ─────────────────────────────────────────────
;   bits [1:0]  CLK_SEL  — 0=own SD clock, 1=SD clock from ch0, etc.
;   bit  [2]    CLK_INV  — 0=normal, 1=inverted clock
;   bits [5:4]  ACC_SEL  — 0=acc3 (SINC3), 1=acc2 (SINC2), 2=acc1 (SINC1)
CLK_SEL_SINC3_OWN .set 0x01    ; CLK_SEL=1 own SD clock, ACC_SEL=0 SINC3

; ── Data buffer ───────────────────────────────────────────────────────────────
;   r21.w0  running write offset (bytes) from C24
;   r21.w2  wrap boundary (bytes): 256 samples × 4 bytes = 1024 = 0x400
BUF_BOUNDARY .set 0x0400      ; 256 samples × 4 bytes

;*******************************************************************************
;* MAIN
;*******************************************************************************

main:

; ── SD hardware initialisation ────────────────────────────────────────────────

; SD_CFG_REG0 (C4+0x44): disable Manchester, no load-sharing
    ldi     r0.w0, 0
    sbco    &r0, c4, 0x44, 2

; CH2 SD_CLK_SEL_REG (C4+0x58): SINC3, own SD clock (CLK_SEL=1, ACC_SEL=0)
    ldi     r0.b0, CLK_SEL_SINC3_OWN
    sbco    &r0, c4, 0x58, 2

; CH2 SD_SAMPLE_SIZE_REG (C4+0x5C): OSR=64  (register value = OSR-1 = 63)
    ldi     r0.b0, 63
    sbco    &r0, c4, 0x5C, 1

; ── Initialise fixed working registers ────────────────────────────────────────

; 28-bit mask for ACC3 output (MASK_REG = R20)
    ldi32   r20, 0x0FFFFFFF

; Buffer pointer: r21.w0 = current byte offset (starts at 0), r21.w2 = boundary
    ldi32   r21, 0x04000000         ; r21.w0=0x0000, r21.w2=BUF_BOUNDARY(0x0400)

; ── Start SD channel 2 ────────────────────────────────────────────────────────

; Clear sd_en first, then set it (toggles the SD enable — same as hardware reset)
    clr     r30, r30, 25            ; R30[25]=0: sd_en off
    nop
    nop
    set     r30, r30, 25            ; R30[25]=1: sd_en on (but ch_sel still 0)
    nop
    nop

; Select channel 2: R30.b3 = ch2_sel | ch_enable = 8 | 2 = 0x0A
;   Decodes as: R30[29:26] = ch_sel = 2,  R30[25] = sd_en = 1
    ldi     r30.b3, ch2_sel + ch_enable
    nop

; Re-initialise channel 2 accumulators (R31 write, bit 23 = reinit per Verilog)
; set r31, r31, 23  reads current R31 status, sets bit 23, writes back as command
    set     r31, r31, 23            ; bit 23 = reinit command (mx_pru_r3031_5[4])
    nop

; ── Free-running SINC3 filter loop ────────────────────────────────────────────
; Firmware flow each OSR period (64 SD clocks):
;   1. Poll R31 bit 28 (valid/shadow_update_flag)
;   2. Read R31: bits[27:0] = ACC3 shadow value → R2 (DN0) [non-destructive]
;   2b. Write R31 bit[24] to clear valid+ovf flags
;   3. Apply 3-stage comb (M_ACC3_PROCESS) → SINC3 result in R8 (CN5)
;   4. Store R8 to DRAM0 buffer; advance pointer with wrap-around at 256 samples

wait_channel2:
    qbbc    wait_channel2, r31, 28  ; wait for shadow_update_flag (R31[28])

; Read ACC3 shadow value from R31[27:0], mask to 28 bits, store in DN0 (R2).
; NOTE: reading R31 is NON-DESTRUCTIVE (combinatorial per Verilog). Valid must be
; cleared explicitly by writing R31 bit[24] (clr_ovf and clr_valid command).
    and     r2, r31, r20            ; DN0 (R2) = R31 & 0x0FFFFFFF (read shadow)

; Clear valid and overflow flags: R31 write bit[24] = clr_ovf+val (mx_pru_r3031_5[2])
; NOTE: SET reads R31 status (including data[27:0]), sets bit 24, writes back.
; Data bit 23 may be set (if shadow >= 8M), but the simulator uses rising-edge
; detection for bit 23 (reinit), so the echoed data bit does not spuriously reinit.
    set     r31, r31, 24            ; bit 24 = clr_ovf and clr_valid command

; Apply 3-stage comb filter — result in CN5 (R8), 7 cycles
    M_ACC3_PROCESS  DN1, DN3, DN5

; Store 32-bit SINC3 result to DRAM0 buffer at current offset
    sbco    &r8, c24, r21.w0, 4

; Advance write pointer; wrap back to 0 after BUF_BOUNDARY bytes
    add     r21.w0, r21.w0, 4
    qbne    skip_wrap, r21.w0, r21.w2
    ldi     r21.w0, 0
skip_wrap:

    qba     wait_channel2           ; loop forever

; end of sdfm_sinc3_demo.asm
