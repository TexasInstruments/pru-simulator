;******************************************************************************
; File: pdm_clk.asm
;
; Description:
;   PDM microphone clock generator (step 1 of the PRU PDM interface).
;   Generates a 3.072 MHz square wave on PRU1 direct GPO pin
;   R30.t[PDM_CLK_PIN] from a 200 MHz PRU core clock, using a fractional
;   adder (scheme from open-pru/examples/custom_frequency_generator, with an
;   exact integer accumulator instead of Q30 nanoseconds).
;
;   Device agnostic: only instructions common to PRU-ICSS (V3, AM261x) and
;   PRU-ICSSG (V4, AM243x) are used. Pinmux of the GPO pin is done by the host.
;
; Timing: 200 MHz / 3.072 MHz = 65.1041666... = 65 + 5/48 PRU cycles
;   short period : 65 cycles = 32 high + 33 low
;   long period  : 66 cycles = 33 high + 33 low
;   every 48 periods: 43 short + 5 long = 3125 cycles -> exactly 3.072000 MHz
;   period jitter: 1 PRU cycle (5 ns)
;
; Recomputing for other clocks (N = f_pru / f_pdm):
;   CFG_T_SH_CYC               = floor(N)
;   CFG_FRAC_ACC/CFG_FRAC_MAX  = N - floor(N), reduced to lowest terms
;   CFG_HIGH_CYC               = floor(CFG_T_SH_CYC / 2)
;   CFG_LOW_CYC                = CFG_T_SH_CYC - CFG_HIGH_CYC
;   Keep every value a plain literal: pru-simulator does not expand a .set
;   whose value refers to another .set symbol, and mis-times silently.
;
; Registers modified: r7, r8, r9, R30.t[PDM_CLK_PIN] (other R30 bits untouched)
;******************************************************************************

    .global PDM_CLK_GEN

;------------------------------------------------------------------------------
; Build options
;------------------------------------------------------------------------------
PDM_CLK_PIN     .set    0       ; R30 bit driving the PDM clock (0..19)

CFG_T_SH_CYC    .set    65      ; short period in PRU cycles
CFG_HIGH_CYC    .set    32      ; high time of a short period
CFG_LOW_CYC     .set    33      ; low time = CFG_T_SH_CYC - CFG_HIGH_CYC (literal!)
CFG_FRAC_ACC    .set    5       ; fraction added every period
CFG_FRAC_MAX    .set    48      ; accumulator wrap value

    .if (CFG_HIGH_CYC < 5) | (CFG_LOW_CYC < 5)
    .emsg "CFG_HIGH_CYC and CFG_LOW_CYC must be >= 5"
    .endif
    .if (CFG_HIGH_CYC + CFG_LOW_CYC) != CFG_T_SH_CYC
    .emsg "CFG_HIGH_CYC + CFG_LOW_CYC must equal CFG_T_SH_CYC"
    .endif
    .if PDM_CLK_PIN > 19
    .emsg "PDM_CLK_PIN must be 0..19"
    .endif

;------------------------------------------------------------------------------
; PDM_CLK_GEN: endless clock loop, never returns.
;   Delays: "loop L, n" + one nop body = n + 1 cycles.
;   High (set -> clr): qba + delay(HIGH-3) + qble + clr = HIGH
;                      (+1 after a long-period decision: extra nop)
;   Low, short path  : add + delay(LOW-2) + set       = LOW
;   Low, long path   : sub + add + delay(LOW-3) + set = LOW
;------------------------------------------------------------------------------
PDM_CLK_GEN:
    ldi     r7, CFG_FRAC_ACC
    ldi     r8, CFG_FRAC_MAX
    ldi     r9, 0                           ; fractional accumulator
    set     r30, r30, PDM_CLK_PIN           ; first rising edge
    nop                                     ; stands in for qba: first high = HIGH
PDM_HIGH:
    loop    PDM_HIGH_DLY, CFG_HIGH_CYC - 4  ; delay(HIGH - 3)
    nop
PDM_HIGH_DLY:
    qble    PDM_LONG, r9, r8                ; acc >= FRAC_MAX -> long period
    clr     r30, r30, PDM_CLK_PIN           ; falling edge
    add     r9, r9, r7
    loop    PDM_LOW_S_DLY, CFG_LOW_CYC - 3  ; delay(LOW - 2)
    nop
PDM_LOW_S_DLY:
    set     r30, r30, PDM_CLK_PIN           ; rising edge
    qba     PDM_HIGH
PDM_LONG:
    clr     r30, r30, PDM_CLK_PIN           ; falling edge
    sub     r9, r9, r8
    add     r9, r9, r7
    loop    PDM_LOW_L_DLY, CFG_LOW_CYC - 4  ; delay(LOW - 3)
    nop
PDM_LOW_L_DLY:
    set     r30, r30, PDM_CLK_PIN           ; rising edge
    nop                                     ; +1 cycle high -> 66-cycle period
    qba     PDM_HIGH
