;******************************************************************************
; File: pdm_gen.asm
;
; Description:
;   PRU1 stereo PDM microphone generator of the PDM audio demo.
;   Plays the 16 kHz stereo PCM clip in ICSS shared RAM (pdm_shared.inc)
;   through two 2nd-order sigma-delta modulators and drives
;     R30.t[PDM_CLK_PIN]  3.072 MHz PDM clock
;     R30.t[PDM_DAT_PIN]  one PDM data line carrying both microphones:
;                         left  bit sampled at the rising edge  (PRU0 SD ch0)
;                         right bit sampled at the falling edge (PRU0 SD ch1)
;   Python references: source/pdm/reference.py (sigma_delta2,
;   pdm_clock_edges); tests: tests/test_pdm_gen.py.
;
; Clock: integer fractional adder of audio_pipe pdm_clk.asm (copy in
;   tests/asm/pdm_clk_ref.asm). 200 MHz / 3.072 MHz = 65 + 5/48 cycles:
;   high 32 (33 in a long period), low 33; 43 short + 5 long periods per 48
;   = 3125 cycles, zero drift, 1 cycle (5 ns) jitter. Edge times equal
;   reference.pdm_clock_edges exactly, whatever the data: every path
;   through a half period has a fixed cycle count.
;   pdm_clk.asm tests "acc >= FRAC_MAX" at the end of high time k and adds
;   the extra cycle to high time k+1. Here the test runs inside high time
;   k+1 on b = acc + FRAC_ACC (threshold FRAC_MAX + FRAC_ACC, b starts at
;   0): same decisions, same edges, extra cycle in the half that decides.
;
; Modulator (per channel, per PDM period; FS = 32768, bit 1 = +FS):
;     i1 += x - fb;  i2 += i1 - fb;  bit = (i2 >= 0);  fb = bit ? +FS : -FS
;   x = int16 clip sample (loader peak 16384 = 0.5 FS), held for
;   PCM_PERIODS = 192 periods (3.072 MHz / 16 kHz, zero-order hold); 0 after
;   the clip. 32-bit integrators, add/sub/branch only.
;
; Timing of one PDM period (t = cycles after the edge instruction):
;   low half  (fall at t=0, rise at t=33):
;     t1      qba PDM_LOW
;     t2..t5  left modulator arithmetic
;     t6      qbbs on i2L sign
;     t7      set/clr DATA = left bit of the next rising edge -> setup 26
;     t8..t9  fb, branch balance
;     t10..27 fetch slot, 18 cycles on all three paths:
;               no fetch | fetch frame (LBBO 3 + SBBO 2) | clip end
;     t28..32 padding: loop 4 + nop = 5
;   high half (rise at t=0, fall at t=32, t=33 in a long period):
;     t1..t4  right modulator arithmetic
;     t5      qbbs on i2R sign
;     t6      set/clr DATA = right bit of the next falling edge -> setup 26
;     t7..t8  fb, branch balance
;     t9      xr = xr_next (commit the staged right sample)
;     t10..12 fractional adder (t13 too in a long period)
;     t13..31 padding: loop 18 + nop = 19
;
;   Cycle budget per half period (edge instruction included):
;     half          length  busy  padding
;     low, fetch       33     28        5   (fetch slot 18, of it LBBO 3, SBBO 2)
;     low, no fetch    33     13       20   (slot: 3 busy + 15 nop loop)
;     low, clip end    33     18       15   (slot: 8 busy + 10 nop loop)
;     high, short      32     13       19
;     high, long       33     14       19
;   busy = edge + modulator step + slot/adder work; padding = LOOP + NOP
;   delay cycles (loop instruction included), collapsed by the idle skip.
;   Spare low-half cycles on the fetch path: 4 (the LOW_DLY loop could
;   shrink to 1 nop), i.e. LBBO + SBBO may grow by 4 cycles in total.
;   Memory: ICSS shared RAM read_latency 2, write_latency 1, jitter 0 in
;   the simulator: LBBO 4 B = 1 + 2 stalls = 3 cycles, SBBO 4 B = 1 + 1 =
;   2 cycles (measured in pru-simulator, tests/test_pdm_gen.py holds the
;   edges to the schedule on fetch periods). On silicon the shared-RAM
;   latency depends on arbitration with PRU0's ring writes: re-measure and
;   re-balance CFG_LBBO_CYC / CFG_SBBO_CYC before trusting the clock there.
;
;   Setup: each data bit changes 7 (left) / 6 (right) cycles after the
;   opposite clock edge and is stable for >= SETUP_MIN_CYC = 26 cycles
;   (130 ns) before its sampling edge (27 for the right bit of a long
;   period). Hold after the previous sampling edge: 6 cycles (left bit,
;   30 ns), 7 cycles (right bit, 35 ns).
;
; PCM pacing: frame k (LBBO 32 bit from CLIP_BASE + 4k: left .w0, right
;   .w2) is fetched in the low half after left bit 192k-1 is driven, i.e.
;   two halves before the first bit that uses it. xl takes the left sample
;   at once (next left step = bit 192k); the right sample is staged in
;   xr_next and committed in every high half after its right step, so
;   right bit 192k is the first to use it. Frame 0 is fetched by the init.
;   HDR_GEN_POS = frames fetched, stored after each fetch. A fetch slot
;   with frame >= CLIP_LEN sets x = 0 and stores HDR_GEN_DONE = 1 (again
;   every 192 periods); the first such slot runs in low half 192*LEN-2,
;   so GEN_DONE rises 1.5 PDM periods (< 0.5 us) before the last frame's
;   last bit is sampled. CLIP_LEN is clamped to CLIP_MAX_FRAMES.
;
; Registers:
;   r0  shared RAM base            r10 xl      left input (sign-extended)
;   r1  next clip frame address    r11 xr      right input in use
;   r2  frames fetched (GEN_POS)   r12 xr_next staged right input
;   r3  CLIP_LEN (clamped)         r13 i1L     r16 i1R
;   r4  periods to next fetch      r14 i2L     r17 i2R
;   r5  fractional accumulator b   r15 fbL     r18 fbR
;   r6  FRAC_MAX + FRAC_ACC        r19 constant 1 (GEN_DONE)
;   r7  +FS = 0x8000 (also the int16 sign-extension constant)
;   r8  -FS = 0xFFFF8000           r20 FRAC_MAX - FRAC_ACC
;   r9  fetched frame word         r30 GPO (only the two pins are touched)
;
; Keep every build option a plain literal: pru-simulator does not expand a
; .set whose value refers to another .set symbol. Derived values are
; checked by the .if/.emsg guards (pru-simulator reports a failing guard as
; an unknown-directive load error).
;******************************************************************************

    .include "pdm_shared.inc"

    .global PDM_GEN

;------------------------------------------------------------------------------
; Build options
;------------------------------------------------------------------------------
PDM_CLK_PIN     .set    0       ; R30 bit driving the PDM clock (0..19)
PDM_DAT_PIN     .set    1       ; R30 bit driving the PDM data line (0..19)

CFG_T_SH_CYC    .set    65      ; short period in PRU cycles
CFG_HIGH_CYC    .set    32      ; high time of a short period
CFG_LOW_CYC     .set    33      ; low time = CFG_T_SH_CYC - CFG_HIGH_CYC (literal!)
CFG_FRAC_ACC    .set    5       ; fraction added every period
CFG_FRAC_MAX    .set    48      ; accumulator wrap value
PCM_PERIODS     .set    192     ; PDM periods per PCM frame (3.072 MHz / 16 kHz)
SETUP_MIN_CYC   .set    26      ; data stable this many cycles before its edge

CFG_LBBO_CYC    .set    3       ; LBBO 4 B from shared RAM, stalls included
CFG_SBBO_CYC    .set    2       ; SBBO 4 B to shared RAM, stalls included

    .if (CFG_HIGH_CYC + CFG_LOW_CYC) != CFG_T_SH_CYC
    .emsg "CFG_HIGH_CYC + CFG_LOW_CYC must equal CFG_T_SH_CYC"
    .endif
    .if (CFG_HIGH_CYC < 15) | (CFG_LOW_CYC < (CFG_LBBO_CYC + CFG_SBBO_CYC + 25))
    .emsg "half periods too short for the modulator step and fetch slot"
    .endif
    .if (PDM_CLK_PIN > 19) | (PDM_DAT_PIN > 19) | (PDM_CLK_PIN == PDM_DAT_PIN)
    .emsg "PDM_CLK_PIN and PDM_DAT_PIN must be distinct, 0..19"
    .endif
    .if (PCM_PERIODS < 2) | (PCM_PERIODS > 255)
    .emsg "PCM_PERIODS must be 2..255 (ldi immediate)"
    .endif
    .if SETUP_MIN_CYC < 8
    .emsg "SETUP_MIN_CYC must be >= 8"
    .endif
    .if (SETUP_MIN_CYC > (CFG_HIGH_CYC - 6)) | (SETUP_MIN_CYC > (CFG_LOW_CYC - 7))
    .emsg "SETUP_MIN_CYC exceeds the setup this layout gives"
    .endif

;------------------------------------------------------------------------------
; PDM_GEN: init, then the endless period loop. Never returns.
;   Delays: "loop L, n" + one nop body = n + 1 cycles (idle skip collapses it).
;------------------------------------------------------------------------------
PDM_GEN:
    clr     r30, r30, PDM_CLK_PIN           ; clock and data start low
    clr     r30, r30, PDM_DAT_PIN
    ldi32   r0, PDM_SHARED_BASE
    ldi32   r1, PDM_CLIP_BASE
    add     r1, r1, r0                      ; frame 0 address
    ldi     r2, 0                           ; frames fetched
    lbbo    &r3, r0, PDM_HDR_CLIP_LEN, 4
    ldi32   r9, PDM_CLIP_MAX_FRAMES
    qbge    PDM_LEN_OK, r3, r9              ; len <= max
    mov     r3, r9
PDM_LEN_OK:
    ldi     r4, PCM_PERIODS                 ; first fetch slot: low half 190
    ldi     r5, 0                           ; b = previous acc + FRAC_ACC: period 0 short
    ldi     r6, CFG_FRAC_MAX
    add     r6, r6, CFG_FRAC_ACC
    ldi32   r7, 0x8000                      ; +FS
    ldi32   r8, 0xFFFF8000                  ; -FS
    ldi     r19, 1
    ldi     r20, CFG_FRAC_MAX
    sub     r20, r20, CFG_FRAC_ACC
    ldi     r13, 0                          ; integrators 0, previous bits 0
    ldi     r14, 0
    mov     r15, r8
    ldi     r16, 0
    ldi     r17, 0
    mov     r18, r8
    sbbo    &r2, r0, PDM_HDR_GEN_POS, 4     ; GEN_POS = 0
    sbbo    &r2, r0, PDM_HDR_GEN_DONE, 4    ; GEN_DONE = 0
    ; frame 0 (same as PDM_FETCH, untimed)
    ldi     r10, 0
    ldi     r12, 0
    qble    PDM_INIT_END, r2, r3            ; len <= 0: silence from the start
    lbbo    &r9, r1, 0, 4
    add     r1, r1, 4
    add     r2, r2, 1
    sbbo    &r2, r0, PDM_HDR_GEN_POS, 4
    mov     r10, r9.w0                      ; int16 -> int32: (w ^ 0x8000) - 0x8000
    xor     r10, r10, r7
    sub     r10, r10, r7
    mov     r12, r9.w2
    xor     r12, r12, r7
    sub     r12, r12, r7
    qba     PDM_INIT_GO
PDM_INIT_END:
    sbbo    &r19, r0, PDM_HDR_GEN_DONE, 4
PDM_INIT_GO:
    mov     r11, r12

;------------------------------------------------------------------------------
; Low half: left bit for the next rising edge, then the fetch slot.
;------------------------------------------------------------------------------
PDM_LOW:
    add     r13, r13, r10                   ; t2  i1L += xl - fbL
    sub     r13, r13, r15                   ; t3
    add     r14, r14, r13                   ; t4  i2L += i1L - fbL
    sub     r14, r14, r15                   ; t5
    qbbs    PDM_L_NEG, r14, 31              ; t6
    set     r30, r30, PDM_DAT_PIN           ; t7  left bit 1
    mov     r15, r7                         ; t8  fbL = +FS
    qba     PDM_L_JOIN                      ; t9
PDM_L_NEG:
    clr     r30, r30, PDM_DAT_PIN           ; t7  left bit 0
    mov     r15, r8                         ; t8  fbL = -FS
    nop                                     ; t9  = qba
PDM_L_JOIN:
    ; fetch slot, t10..t27 on every path
    sub     r4, r4, 1                       ; t10
    qbeq    PDM_FETCH, r4, 0                ; t11
    loop    PDM_NF_DLY, CFG_LBBO_CYC + CFG_SBBO_CYC + 9    ; t12..t26
    nop
PDM_NF_DLY:
    qba     PDM_SLOT_END                    ; t27
PDM_FETCH:
    ldi     r4, PCM_PERIODS                 ; t12
    qble    PDM_CLIP_END, r2, r3            ; t13 frames fetched >= len
    lbbo    &r9, r1, 0, 4                   ; t14..t16  L in .w0, R in .w2
    add     r1, r1, 4                       ; t17
    add     r2, r2, 1                       ; t18
    sbbo    &r2, r0, PDM_HDR_GEN_POS, 4     ; t19..t20
    mov     r10, r9.w0                      ; t21  xl = sign-extended left
    xor     r10, r10, r7                    ; t22
    sub     r10, r10, r7                    ; t23
    mov     r12, r9.w2                      ; t24  xr_next = sign-extended right
    xor     r12, r12, r7                    ; t25
    sub     r12, r12, r7                    ; t26
    qba     PDM_SLOT_END                    ; t27
PDM_CLIP_END:
    ldi     r10, 0                          ; t14  silence
    ldi     r12, 0                          ; t15
    sbbo    &r19, r0, PDM_HDR_GEN_DONE, 4   ; t16..t17
    loop    PDM_END_DLY, CFG_LBBO_CYC + 6   ; t18..t27
    nop
PDM_END_DLY:
PDM_SLOT_END:
    loop    PDM_LOW_DLY, CFG_LOW_CYC - CFG_LBBO_CYC - CFG_SBBO_CYC - 24  ; t28..t32
    nop
PDM_LOW_DLY:
    set     r30, r30, PDM_CLK_PIN           ; t33 = t0 of the high half: rising edge

;------------------------------------------------------------------------------
; High half: right bit for the next falling edge, then the fractional adder.
;------------------------------------------------------------------------------
    add     r16, r16, r11                   ; t1  i1R += xr - fbR
    sub     r16, r16, r18                   ; t2
    add     r17, r17, r16                   ; t3  i2R += i1R - fbR
    sub     r17, r17, r18                   ; t4
    qbbs    PDM_R_NEG, r17, 31              ; t5
    set     r30, r30, PDM_DAT_PIN           ; t6  right bit 1
    mov     r18, r7                         ; t7  fbR = +FS
    qba     PDM_R_JOIN                      ; t8
PDM_R_NEG:
    clr     r30, r30, PDM_DAT_PIN           ; t6  right bit 0
    mov     r18, r8                         ; t7  fbR = -FS
    nop                                     ; t8  = qba
PDM_R_JOIN:
    mov     r11, r12                        ; t9  commit staged right sample
    qble    PDM_LONG, r5, r6                ; t10 b >= FRAC_MAX + FRAC_ACC
    add     r5, r5, CFG_FRAC_ACC            ; t11
    qba     PDM_F_JOIN                      ; t12
PDM_LONG:
    sub     r5, r5, r20                     ; t11 b -= FRAC_MAX - FRAC_ACC
    nop                                     ; t12 = qba
    nop                                     ; t13 long period: +1 high cycle
PDM_F_JOIN:
    loop    PDM_HIGH_DLY, CFG_HIGH_CYC - 14 ; t13..t31 (t14..t32 long)
    nop
PDM_HIGH_DLY:
    clr     r30, r30, PDM_CLK_PIN           ; t32 (t33): falling edge
    qba     PDM_LOW                         ; t1 of the low half
