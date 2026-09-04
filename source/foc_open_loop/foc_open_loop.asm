; foc_open_loop.asm - open-loop FOC (Level 1) on a single PRU core (pru0)
; ---------------------------------------------------------------------------
; Reads SpeedRef/IdRef/IqRef from the `control` shared-memory block, runs
; RC (ramp control) -> RG (ramp generator) -> sin/cos LUT -> inverse Park ->
; SVGEN (SVPWM), and writes Ta/Tb/Tc duty cycles + angle to the `pwm_out`
; block every iteration. No current feedback in this phase (Plan A / Level 1
; Incremental System Build) -- see docs/superpowers/specs/
; 2026-09-04-foc-open-loop-design.md.
;
; Q24 fixed point (1.7.24; 0x01000000 = 1.0) for all per-unit values. Angle
; is a 32-bit phase accumulator; natural 32-bit wrap = one electrical
; revolution. Q24 x Q24 multiplies use the broadside MAC (device 0):
; operands in R28/R29, 64-bit product in R26/R27 (see source/mac_example.asm
; and the MUL_Q24 macro below) -- NOT R25/R26 as an earlier, incorrect
; reference doc claimed.
;
; Built and verified incrementally, one macro at a time (see
; docs/superpowers/plans/2026-09-04-foc-pru-firmware.md, tasks A0-A5).
;
; ---------------------------------------------------------------------------
; Register map (persistent across loop iterations unless noted; r30/r31 are
; reserved GPIO and unused by this firmware):
;
;   Boot-once constants, never written again after init:
;     r1  BASE_CTRL   shared-mem base of the `control` block (FOC_CONTROL_BASE)
;     r2  BASE_PWM    shared-mem base of the `pwm_out` block (FOC_PWM_OUT_BASE)
;     r3  BASE_LUT    shared-mem base of the `sine_lut` block (FOC_SINE_LUT_BASE)
;
;   Config cache, refreshed only when control.requested_generation changes:
;     r5  APPLIED_GEN     cached requested_generation we have applied
;     r6  SPEED_REF        cached speed_ref_q24
;     r7  ID_REF           cached id_ref_q24
;     r8  IQ_REF            cached iq_ref_q24
;     r9  RAMP_RATE        cached ramp_rate_q24
;     r10 ENABLE            cached enable
;
;   Pipeline state, persistent across loop iterations:
;     r11 SETPOINT      RC's ramped output, slewed toward SPEED_REF
;     r12 THETA_ACC     RG's 32-bit phase accumulator (natural wrap = 1 rev)
;     r13 LOOP_COUNTER  bumped once per completed core-loop pass
;     r14 SEQ           pwm_out seqlock parity (odd = write in progress)
;
;   Core-loop scratch, recomputed every iteration, no state carried past it:
;     r0  VC          SVGEN: Vc, then Tc (Ta/Tb/Tc = Vx - Vcom + 0.5)
;     r15 TMP
;     r16 TMP2
;     r17 SIN_Q24     sin(theta_acc), nearest-neighbor LUT lookup; dead by
;                      SVGEN, reused there as bias/min/max/sum scratch
;     r18 COS_Q24     cos(theta_acc), quarter-table-offset LUT lookup; dead
;                      by SVGEN, reused there as bias/min/max/sum scratch
;     r19 VALPHA      IPARK: id_ref*cos - iq_ref*sin
;     r22 VBETA       IPARK: id_ref*sin + iq_ref*cos
;     r23 VA          SVGEN: Va = Vbeta, then Ta
;     r24 VB          SVGEN: Vb, then Tb
;
;   MUL_Q24 macro window -- transient only, never holds state across a call:
;     r20/r21           sign-mask scratch
;     r25/r26/r27/r28/r29  MAC hardware window (ctrl/status, product, operands)
;
; SVGEN's Vmax/Vmin use the hardware MIN/MAX instruction, which -- like
; QBGT/QBLT -- is UNSIGNED (core/alu.py's min_/max_ compare the raw 32-bit
; ints, and this sim's registers hold no sign of their own). Va/Vb/Vc are
; signed Q24, so each is XORed with 0x80000000 (flips the sign bit) before
; MIN/MAX and XORed back after: two's-complement order is preserved under
; that transform, so unsigned MIN/MAX on the biased values reproduces
; signed MIN/MAX exactly. The -0.5*Vbeta term and Vcom's (max+min)/2 both
; need an arithmetic (sign-extending) right shift by 1; LSR is logical
; only, so it is synthesized as LSR-by-1 OR'd with the operand's own sign
; bit (equivalent to sign-extending the vacated top bit).
; ---------------------------------------------------------------------------

    .include "foc_abi.inc"

    .asg r1,  BASE_CTRL
    .asg r2,  BASE_PWM
    .asg r3,  BASE_LUT
    .asg r5,  APPLIED_GEN
    .asg r6,  SPEED_REF
    .asg r7,  ID_REF
    .asg r8,  IQ_REF
    .asg r9,  RAMP_RATE
    .asg r10, ENABLE
    .asg r11, SETPOINT
    .asg r12, THETA_ACC
    .asg r13, LOOP_COUNTER
    .asg r14, SEQ
    .asg r0,  VC
    .asg r15, TMP
    .asg r16, TMP2
    .asg r17, SIN_Q24
    .asg r18, COS_Q24
    .asg r19, VALPHA
    .asg r22, VBETA
    .asg r23, VA
    .asg r24, VB

; =============================================================================
; MUL_Q24 out, a, b -- signed Q24 x Q24 -> Q24 multiply via the broadside MAC
; (device 0, multiply-only mode).
;
; The MAC's R28*R29 is an UNSIGNED multiply (sim registers are plain
; unsigned 32-bit ints, core/registers.py), so a negative Q24 operand (top
; bit set) fed straight in would multiply as a huge positive magnitude
; instead of a negative product. This macro decomposes each operand into
; (sign, |value|) first, multiplies the magnitudes on the real hardware
; path, then reapplies the combined sign -- entirely branchless (mask = 0 -
; signbit is 0x00000000 or 0xFFFFFFFF; XOR/SUB with that mask is the
; standard two's-complement abs()/negate trick). Branchless also sidesteps
; this assembler's lack of macro-local ("?") labels (core/preprocessor.py's
; .macro expansion does plain textual substitution, no per-invocation label
; renaming) which would otherwise collide across MUL_Q24's several call
; sites in this file.
;
; Parameters:
;   out - destination register for the signed Q24 product
;   a   - first signed Q24 operand register (unmodified)
;   b   - second signed Q24 operand register (unmodified)
;
; Registers modified: r20, r21, r25, r26, r27, r28, r29 (all transient).
; =============================================================================
    .macro MUL_Q24 out, a, b
    lsr   r20, a, 31
    rsb   r20, r20, 0            ; r20 = sign mask of a (0 or 0xFFFFFFFF)
    xor   r28, a, r20
    sub   r28, r28, r20          ; r28 = |a|

    lsr   r21, b, 31
    rsb   r21, r21, 0            ; r21 = sign mask of b
    xor   r29, b, r21
    sub   r29, r29, r21          ; r29 = |b|

    xor   r20, r20, r21          ; r20 = combined sign mask of the result

    ldi   r25, 0                 ; MAC_MODE=0: multiply-only, clears accumulator
    xout  0, &r25, 1
    nop
    xin   0, &r26, 4             ; r26 = |a|*|b| low word
    xin   0, &r27, 4             ; r27 = |a|*|b| high word (Q48, 64-bit)

    lsr   r26, r26, 24
    lsl   r27, r27, 8
    or    r26, r26, r27          ; r26 = |a*b| renormalized to Q24

    xor   r27, r26, r20
    sub   out, r27, r20          ; out = signed Q24 result
    .endm

    .retain
    .retainrefs
    .global main
    .sect ".text"

main:
    ldi32 BASE_CTRL, FOC_CONTROL_BASE
    ldi32 BASE_PWM, FOC_PWM_OUT_BASE
    ldi32 BASE_LUT, FOC_SINE_LUT_BASE
    ldi   APPLIED_GEN, 0      ; force the first pass to always latch + ack
    ldi   SETPOINT, 0
    ldi   THETA_ACC, 0
    ldi   LOOP_COUNTER, 0
    ldi   SEQ, 0

l_check_config:
    lbbo  &TMP, BASE_CTRL, FOC_CONTROL_REQUESTED_GENERATION_OFF, 4
    qbeq  l_core_loop, TMP, APPLIED_GEN   ; unchanged -> run the pipeline

    lbbo  &ENABLE, BASE_CTRL, FOC_CONTROL_ENABLE_OFF, 4
    ; speed_ref_q24/id_ref_q24/iq_ref_q24/ramp_rate_q24 are 4 consecutive
    ; words (0x14..0x23) -> one 16-byte burst into r6..r9.
    lbbo  &SPEED_REF, BASE_CTRL, FOC_CONTROL_SPEED_REF_Q24_OFF, 16
    mov   APPLIED_GEN, TMP
    sbbo  &APPLIED_GEN, BASE_CTRL, FOC_CONTROL_PRU_ACK_GENERATION_OFF, 4

l_core_loop:
    ; ---- RC (ramp control): slew SETPOINT toward SPEED_REF by up to
    ; +-RAMP_RATE per iteration, clamping at the target. The sim's
    ; QBGT/QBLT/etc. are UNSIGNED (core/branch.py), so the compare is done
    ; on |diff| (a true non-negative magnitude, valid for an unsigned
    ; compare against RAMP_RATE) rather than on the raw signed difference,
    ; using the same branchless sign-mask trick as MUL_Q24. ----
    sub   TMP, SPEED_REF, SETPOINT      ; TMP = diff = speed_ref - setpoint
    lsr   TMP2, TMP, 31
    rsb   TMP2, TMP2, 0                 ; TMP2 = sign mask of diff
    xor   TMP, TMP, TMP2
    sub   TMP, TMP, TMP2                 ; TMP = |diff|
    qbgt  l_rc_clamp, TMP, RAMP_RATE     ; RAMP_RATE > |diff| -> one step overshoots: clamp
    xor   TMP, RAMP_RATE, TMP2
    sub   TMP, TMP, TMP2                 ; TMP = +-RAMP_RATE, sign of diff
    add   SETPOINT, SETPOINT, TMP
    qba   l_rc_done
l_rc_clamp:
    mov   SETPOINT, SPEED_REF
l_rc_done:

    ; ---- RG (ramp generator): theta_acc += setpoint * SPEED_SCALE. The
    ; 32-bit accumulator wraps naturally = one electrical revolution. ----
    ldi32 TMP2, FOC_SPEED_SCALE
    MUL_Q24 TMP, SETPOINT, TMP2
    add   THETA_ACC, THETA_ACC, TMP

    ; ---- sin/cos lookup: nearest-neighbor from the 2048-entry Q24 sine LUT
    ; (host-seeded at load). index = top 11 bits of theta_acc, already in
    ; 0..2047 after the LSR (no mask needed there); cosine is a
    ; quarter-table (512-entry = 2048/4 = pi/2) offset, wrapped mod 2048. ----
    lsr   TMP, THETA_ACC, 21            ; TMP = LUT index (0..2047)
    lsl   TMP2, TMP, 2                  ; TMP2 = byte offset
    lbbo  &SIN_Q24, BASE_LUT, TMP2, 4

    ldi   TMP2, 512
    add   TMP, TMP, TMP2                ; TMP = index + quarter-table offset
    ldi   TMP2, 0x7FF                   ; wrap mask, FOC_SINE_LUT_COUNT-1 (2048 entries)
    and   TMP, TMP, TMP2
    lsl   TMP, TMP, 2
    lbbo  &COS_Q24, BASE_LUT, TMP, 4

    ; ---- IPARK (inverse Park): Vα = Id*cos - Iq*sin, Vβ = Id*sin + Iq*cos ----
    MUL_Q24 TMP,  ID_REF, COS_Q24
    MUL_Q24 TMP2, IQ_REF, SIN_Q24
    sub   VALPHA, TMP, TMP2

    MUL_Q24 TMP,  ID_REF, SIN_Q24
    MUL_Q24 TMP2, IQ_REF, COS_Q24
    add   VBETA, TMP, TMP2

    ; ---- SVGEN (SVPWM): Va=Vbeta; Vb=-0.5*Vbeta+(sqrt3/2)*Valpha;
    ; Vc=-0.5*Vbeta-(sqrt3/2)*Valpha. See the register-map comment above for
    ; the unsigned-MIN/MAX bias trick and the synthesized arithmetic
    ; right-shift used throughout this section. ----
    mov   VA, VBETA

    lsr   TMP, VBETA, 1
    ldi32 TMP2, 0x80000000
    and   TMP2, VBETA, TMP2
    or    TMP, TMP, TMP2                ; TMP = half_beta = ASR1(Vbeta)
    rsb   TMP, TMP, 0                   ; TMP = -half_beta

    ldi32 TMP2, 0x00DDB3D7               ; CONST_SQRT3_HALF = round(sqrt(3)/2 * 2^24)
    MUL_Q24 TMP2, TMP2, VALPHA           ; TMP2 = (sqrt3/2)*Valpha
    add   VB, TMP, TMP2
    sub   VC, TMP, TMP2

    ; Vmax/Vmin via the unsigned-MIN/MAX bias trick, then
    ; Vcom = (Vmax+Vmin) >> 1 (arithmetic).
    ldi32 TMP, 0x80000000                ; TMP = SIGNBIT
    xor   TMP2, VA, TMP                  ; TMP2 = bias_a
    xor   SIN_Q24, VB, TMP               ; SIN_Q24 (dead) = bias_b
    min   COS_Q24, TMP2, SIN_Q24         ; COS_Q24 (dead) = running_min(a,b)
    max   TMP2, TMP2, SIN_Q24            ; TMP2 = running_max(a,b)
    xor   SIN_Q24, VC, TMP               ; SIN_Q24 = bias_c
    min   COS_Q24, COS_Q24, SIN_Q24      ; COS_Q24 = min(a,b,c), biased
    max   TMP2, TMP2, SIN_Q24            ; TMP2 = max(a,b,c), biased
    xor   COS_Q24, COS_Q24, TMP          ; COS_Q24 = Vmin
    xor   TMP2, TMP2, TMP                ; TMP2 = Vmax

    add   SIN_Q24, TMP2, COS_Q24         ; SIN_Q24 = Vmax + Vmin
    lsr   TMP2, SIN_Q24, 1
    and   SIN_Q24, SIN_Q24, TMP          ; SIN_Q24 = sign bit of the sum
    or    TMP2, TMP2, SIN_Q24            ; TMP2 = Vcom = ASR1(Vmax+Vmin)

    ldi32 TMP, 0x00800000                ; CONST_ONE_HALF (0.5 in Q24)
    sub   VA, VA, TMP2
    add   VA, VA, TMP                    ; VA now holds Ta
    sub   VB, VB, TMP2
    add   VB, VB, TMP                    ; VB now holds Tb
    sub   VC, VC, TMP2
    add   VC, VC, TMP                    ; VC now holds Tc

    ; ---- Publish under seqlock (seq odd while writing, even when stable).
    ; theta_cmd/valpha/vbeta were computed earlier this pass and are still
    ; live in THETA_ACC/VALPHA/VBETA -- published here, together with
    ; Ta/Tb/Tc, loop_counter and timestamp_cycles, as one atomic update. ----
    add   SEQ, SEQ, 1
    sbbo  &SEQ, BASE_PWM, FOC_PWM_OUT_SEQ_OFF, 4
    sbbo  &VA, BASE_PWM, FOC_PWM_OUT_TA_Q24_OFF, 4
    sbbo  &VB, BASE_PWM, FOC_PWM_OUT_TB_Q24_OFF, 4
    sbbo  &VC, BASE_PWM, FOC_PWM_OUT_TC_Q24_OFF, 4
    sbbo  &VALPHA, BASE_PWM, FOC_PWM_OUT_VALPHA_Q24_OFF, 4
    sbbo  &VBETA, BASE_PWM, FOC_PWM_OUT_VBETA_Q24_OFF, 4
    sbbo  &THETA_ACC, BASE_PWM, FOC_PWM_OUT_THETA_CMD_U32_OFF, 4
    add   LOOP_COUNTER, LOOP_COUNTER, 1
    sbbo  &LOOP_COUNTER, BASE_PWM, FOC_PWM_OUT_LOOP_COUNTER_OFF, 4
    ; timestamp_cycles (u64): no hardware IEP cycle counter is running on
    ; this single-PRU (pru0-only) build, so this is a monotonic placeholder
    ; (low word = loop_counter, high word = 0), not a real cycle count.
    sbbo  &LOOP_COUNTER, BASE_PWM, FOC_PWM_OUT_TIMESTAMP_CYCLES_OFF, 4
    ldi   TMP, 0
    sbbo  &TMP, BASE_PWM, 0x24, 4
    add   SEQ, SEQ, 1
    sbbo  &SEQ, BASE_PWM, FOC_PWM_OUT_SEQ_OFF, 4

    qba   l_check_config
