; sdfm_defines.inc — register aliases and SD channel constants
; Adapted from TI SDK for PRU Simulator (simulator uses .asg directly).
;
; Original source:
;   workspace_ccstheia/empty_am261x-lp_icss_m0_pru1_fw_ti-pru-cgt/include/sdfm_defines.inc

; ── Register assignment ───────────────────────────────────────────────────────
; These match the live register map used by M_ACC3_PROCESS.  Names come from
; the TI SDFM firmware reference.
;
;   DN0  = R2   SD integrator output (ACC3 shadow register value from R31)
;   DN1  = R3   Sinc3 differentiator state 1 (oldest)
;   DN3  = R4   Sinc3 differentiator state 2
;   DN5  = R5   Sinc3 differentiator state 3 (newest)
;   CN3  = R6   Comb stage 1 output (scratch)
;   CN4  = R7   Comb stage 2 output (scratch)
;   CN5  = R8   Comb stage 3 output = final SINC3 result
;   MASK_REG = R20  28-bit mask (0x0FFFFFFF)

.asg    R2,  DN0
.asg    R3,  DN1
.asg    R4,  DN3
.asg    R5,  DN5
.asg    R6,  CN3
.asg    R7,  CN4
.asg    R8,  CN5
.asg    R20, MASK_REG

; ── SD channel-select constants for R30.b3 ────────────────────────────────────
; Channel field occupies R30[29:26] (4 bits).  ch_enable = SD_EN = R30[25].
;
;   r30.b3 = ch_sel_value | ch_enable
;
; ch_sel decodes:  ch_sel_field = r30.b3 >> 2 & 0xF
;   ch0_sel = 0  -> ch_sel_field = 0 -> channel 0
;   ch1_sel = 4  -> ch_sel_field = 1 -> channel 1
;   ch2_sel = 8  -> ch_sel_field = 2 -> channel 2

ch0_sel .set 0
ch1_sel .set 4
ch2_sel .set 8
ch3_sel .set 12

ch_enable .set 2       ; SD_EN bit in r30.b3 (= R30 bit 25)
