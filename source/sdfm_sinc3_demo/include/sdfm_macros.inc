; sdfm_macros.inc — SINC3 decimation macro
; Adapted from TI SDK for PRU Simulator (guard and .if(1) wrapper stripped).
;
; Original source:
;   workspace_ccstheia/empty_am261x-lp_icss_m0_pru1_fw_ti-pru-cgt/include/sdfm_macros.inc

; ── M_ACC3_PROCESS ────────────────────────────────────────────────────────────
; 3-stage comb filter: computes y[n] = x[n] - 3x[n-1] + 3x[n-2] - x[n-3]
; using a chain of first-difference stages (each stage = one RSB + MOV pair).
;
; Expects:
;   DN0 (R2)  — current ACC3 shadow value (raw from R31, already masked)
;   MASK_REG (R20) — 0x0FFFFFFF, applied to final result
;
; State registers (persist across calls):
;   DN1 (R3)  — previous DN0 value (oldest)
;   DN3 (R4)  — previous first-difference CN3
;   DN5 (R5)  — previous second-difference CN4
;
; Output:
;   CN5 (R8)  — SINC3 filter output (28-bit masked)
;
; Scratch (overwritten each call):
;   CN3 (R6), CN4 (R7)
;
; Expanded RTL (7 cycles):
;   CN3 = DN0 - DN1        ; first  difference
;   DN1 = DN0              ; update state 1
;   CN4 = CN3 - DN3        ; second difference
;   DN3 = CN3              ; update state 2
;   CN5 = CN4 - DN5        ; third  difference
;   DN5 = CN4              ; update state 3
;   CN5 = CN5 & MASK_REG   ; 28-bit output limit

.macro  M_ACC3_PROCESS  DN1, DN3, DN5
        RSB     CN3, DN1, DN0
        MOV     DN1, DN0
        RSB     CN4, DN3, CN3
        MOV     DN3, CN3
        RSB     CN5, DN5, CN4
        MOV     DN5, CN4
        AND     CN5, CN5, MASK_REG
.endm
