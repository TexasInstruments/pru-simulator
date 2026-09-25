; foc_open_loop.asm - open-loop FOC (Level 1) on PRU0
; ---------------------------------------------------------------------------
; The controller consumes Q24 voltage references and a ramped per-unit speed
; reference, then publishes conventional inverse-Park/SVPWM output. The
; control period is an absolute IEP deadline supplied in the versioned ABI.
; The host seeds the sine table and owns the plant model.
;
; A publication timestamp is the IEP count read at the publication boundary.
; The loop never converts loop_counter to time. If a completed pass crosses
; its next absolute deadline, the output is neutral and a fault is published.
; ---------------------------------------------------------------------------

    .include "foc_abi.inc"

    .asg r1,  BASE_CTRL
    .asg r2,  BASE_PWM
    .asg r3,  BASE_LUT
    .asg r4,  DEADLINE_LO
    .asg r5,  DEADLINE_HI
    .asg r6,  SPEED_REF
    .asg r7,  VD_REF
    .asg r8,  VQ_REF
    .asg r9,  RAMP_RATE
    .asg r10, ENABLE
    .asg r11, SETPOINT
    .asg r12, THETA_ACC
    .asg r13, LOOP_COUNTER
    .asg r14, PERIOD_IEP
    .asg r15, CONFIG_VALID
    .asg r0,  VC
    .asg r16, TMP
    .asg r17, SIN_Q24
    .asg r29, COS_Q24
    .asg r19, VALPHA
    .asg r21, TMP2
    .asg r22, VBETA
    .asg r23, VA
    .asg r24, VB

; R18 is persistent configuration state: byte 0 is the Vd sign and byte 1 is
; the Vd/Vq sign difference.  Only the generation-adoption path writes it;
; the arithmetic stages preserve it.

; The MAC is configured for multiply-only operation once in main.  Validation
; uses the complete Q48 result.  The hot path prescales one unsigned magnitude
; by 8 bits and reads only the upper product word: for A < 2^24,
; ((A << 8) * B) >> 32 == (A * B) >> 24.  The prescaled operand is only valid
; for bounded per-unit magnitudes; it is not a general Q24 multiply format.
;
; MUL_Q24_UNSIGNED: positive Q24 magnitudes -> Q24.  Clobbers r26-r29.
    .macro MUL_Q24_UNSIGNED out, a, b
    mov   r28, a
    mov   r29, b
    nop
    xin   0, &r26, 8
    lsr   r26, r26, 24
    lsl   r27, r27, 8
    or    r26, r26, r27
    mov   out, r26
    .endm

; MUL_Q24_PRESCALED: signed Q24 product where a is an unsigned magnitude
; already shifted left by 8 bits, b is an unsigned magnitude, sign is the
; first product's sign mask, and delta is an optional sign difference.  Both
; magnitudes must be below 1 pu so the left shift does not lose information.
; Clobbers r25, r27-r29; reads only the upper MAC product word.
    .macro MUL_Q24_PRESCALED out, a, b, sign, delta
    mov   r28, a
    mov   r29, b
    xor   r25, sign, delta
    xin   0, &r27, 4
    xor   r27, r27, r25
    sub   out, r27, r25
    .endm

; Keep one MAC operand live across adjacent products.  The sign/delta XOR is
; deliberately between the final operand move and XIN; besides forming the
; sign mask it provides the required one-cycle product settling interval.
    .macro MUL_Q24_PRESCALED_KEEP_B out, a, sign, delta
    mov   r28, a
    xor   r25, sign, delta
    xin   0, &r27, 4
    xor   r27, r27, r25
    sub   out, r27, r25
    .endm

    .macro MUL_Q24_PRESCALED_KEEP_A out, b, sign, delta
    mov   r29, b
    xor   r25, sign, delta
    xin   0, &r27, 4
    xor   r27, r27, r25
    sub   out, r27, r25
    .endm

; MUL_Q24_SIGNED_CONST: signed Q24 a multiplied by a positive prescaled
; constant.  The final operand write is followed by one settling instruction
; before XIN, matching the documented MAC schedule.
; Clobbers r25, r27-r29.
    .macro MUL_Q24_SIGNED_CONST out, a, prescaled_b
    ldi32 r29, prescaled_b
    lsr   r25, a, 31
    rsb   r25, r25, 0
    xor   r28, a, r25
    sub   r28, r28, r25
    nop
    xin   0, &r27, 4
    xor   r27, r27, r25
    sub   out, r27, r25
    .endm

    .retain
    .retainrefs
    .global main
    .sect ".text"

main:
    ldi32 BASE_CTRL, FOC_CONTROL_BASE
    ldi32 BASE_PWM, FOC_PWM_OUT_BASE
    ldi32 BASE_LUT, FOC_SINE_LUT_BASE
    ldi   CONFIG_VALID, 1
    ldi   r17, 0
    ldi   SETPOINT, 0
    ldi   THETA_ACC, 0
    ldi   LOOP_COUNTER, 0
    ldi   PERIOD_IEP, FOC_DEFAULT_CONTROL_PERIOD_IEP_TICKS
    ldi32 VC, 0xFFFFFFFF
    sbbo  &VC, BASE_CTRL, FOC_CONTROL_PRU_ACK_GENERATION_OFF, 4

    ; The firmware owns this accelerator mode exclusively.  Keep it in
    ; multiply-only mode for all products in the control loop.
    ldi   r25, 0
    xout  0, &r25, 1

    ; Establish the first absolute deadline from the IEP snapshot.
    lbco  &DEADLINE_LO, c26, 0x10, 8
    add   DEADLINE_LO, DEADLINE_LO, PERIOD_IEP
    adc   DEADLINE_HI, DEADLINE_HI, 0

l_check_config:
    l_config_start:
    lbbo  &TMP, BASE_CTRL, FOC_CONTROL_REQUESTED_GENERATION_OFF, 4
    lbbo  &ENABLE, BASE_CTRL, FOC_CONTROL_ENABLE_OFF, 4
    lbbo  &r25, BASE_CTRL, FOC_CONTROL_PRU_ACK_GENERATION_OFF, 4
    qbeq  l_wait_deadline, TMP, r25

    ; Speed, Vd, Vq, and slew are consecutive. The period is the final
    ; versioned control word and is read separately for legacy writers.
    mov   VC, TMP
    lbbo  &SPEED_REF, BASE_CTRL, FOC_CONTROL_SPEED_REF_Q24_OFF, 16
    lbbo  &PERIOD_IEP, BASE_CTRL, FOC_CONTROL_PERIOD_IEP_TICKS_OFF, 4
    qbeq  l_config_default_period, PERIOD_IEP, 0
    qba   l_config_ack
l_config_default_period:
    ldi   PERIOD_IEP, FOC_DEFAULT_CONTROL_PERIOD_IEP_TICKS

l_config_ack:
    ; Cache the voltage sign bits before acknowledging this generation.  R18
    ; is persistent configuration state and is not used as a scratch register.
    lsr   r18, VD_REF, 31
    lsr   r25, VQ_REF, 31
    xor   r18.b1, r18.b0, r25.b0

    ; Cache positive voltage magnitudes and validate the newly adopted pair.
    ; The periodic kernel reuses these values and only this path revalidates.
    lsr   r25, VD_REF, 31
    rsb   r25, r25, 0
    xor   r20, VD_REF, r25
    sub   r20, r20, r25
    lsr   r25, VQ_REF, 31
    rsb   r25, r25, 0
    xor   r21, VQ_REF, r25
    sub   r21, r21, r25
    MUL_Q24_UNSIGNED TMP, r20, r20
    MUL_Q24_UNSIGNED VA, r21, r21
    add   TMP, TMP, VA
    ldi32 VA, FOC_MAX_VOLTAGE_MAGNITUDE_SQUARED_Q24
    qble  l_config_vector_valid, VA, TMP
    ldi   CONFIG_VALID, 0
    qba   l_config_generation_ack
l_config_vector_valid:
    ldi   CONFIG_VALID, 1
    ; Cache magnitudes in the upper-word MAC representation.  This is safe
    ; only after the original Q24 voltage-circle validation above.
    lsl   r20, r20, 8
    lsl   r21, r21, 8
l_config_generation_ack:
    ; 0x28 is private scratch in the unused gap after the 40-byte control
    ; block; it is outside the external ABI and before pwm_out at 0x100.
    sbbo  &r20, BASE_CTRL, 0x28, 8
    mov   r25, VC
    sbbo  &r25, BASE_CTRL, FOC_CONTROL_PRU_ACK_GENERATION_OFF, 4

l_config_end:

l_wait_deadline:
    ; Wait for (now - deadline) to be non-negative in modular signed 64-bit
    ; arithmetic. This is an absolute deadline and remains correct at rollover.
    lbco  &r27, c26, 0x10, 8
    sub   r27, r27, DEADLINE_LO
    suc   r28, r28, DEADLINE_HI
    qbbs  l_wait_deadline, r28, 31

    ; Enable is sampled at the boundary independently of generation changes.
    l_input_load_start:
    lbbo  &ENABLE, BASE_CTRL, FOC_CONTROL_ENABLE_OFF, 4
    qbbc  l_disabled_output, ENABLE, 0

    l_computation_start:
    qbbc  l_invalid_config, CONFIG_VALID, 0

l_vector_valid:

    ; Reload the generation-time magnitude cache.  The cache survives the
    ; output/deadline scratch-register reuse without repeating its arithmetic.
    lbbo  &r20, BASE_CTRL, 0x28, 8

    ; RC: slew the signed speed command by one per-loop Q24 increment.
    l_entry_validation_end:
    l_ramp_start:
    sub   TMP, SPEED_REF, SETPOINT
    lsr   r24, TMP, 31
    rsb   r24, r24, 0
    xor   TMP, TMP, r24
    sub   TMP, TMP, r24
    qbgt  l_rc_clamp, TMP, RAMP_RATE
    xor   TMP, RAMP_RATE, r24
    sub   TMP, TMP, r24
    add   SETPOINT, SETPOINT, TMP
    qba   l_rc_done
l_rc_clamp:
    mov   SETPOINT, SPEED_REF
l_ramp_end:
l_rc_done:

    ; RG: the 32-bit accumulator is one electrical revolution.
    l_phase_accumulator_start:
    ; FOC_SPEED_SCALE is positive and prescaled by 8 bits for an upper-word
    ; MAC read: 0x002BB0D0 << 8 = 0x2BB0D000.
    MUL_Q24_SIGNED_CONST TMP, SETPOINT, 0x2BB0D000
    add   THETA_ACC, THETA_ACC, TMP
l_phase_accumulator_end:

    ; Host-seeded sine/cosine lookup. The quarter-turn offset is 512 entries.
    l_sine_lookup_start:
    lsr   TMP, THETA_ACC, 21
    lsl   r24, TMP, 2
    lbbo  &SIN_Q24, BASE_LUT, r24, 4
    l_sine_lookup_end:
    l_cosine_lookup_start:
    ; TMP is a 0..2047 index.  Bits 8..10 hold the quarter-turn offset;
    ; byte arithmetic avoids the two immediate loads and full-word masking.
    add   TMP.b1, TMP.b1, 2
    and   TMP.b1, TMP.b1, 7
    lsl   TMP, TMP, 2
    lbbo  &COS_Q24, BASE_LUT, TMP, 4
    l_cosine_lookup_end:

    ; Conventional inverse Park.
    l_inverse_park_start:
    ; R0 holds the Vd sign mask and R10 the Vd/Vq sign difference.  R23/R24
    ; retain the Vd*sin/Vd*cos sign masks after sine/cosine become magnitudes.
    ; R10's enable lifetime has ended at the input boundary; R0's phase-duty
    ; lifetime begins only after this transform.
    rsb   r0, r18.b0, 0
    rsb   r10, r18.b1, 0

    lsr   r23, SIN_Q24, 31
    rsb   r23, r23, 0
    xor   r17, SIN_Q24, r23
    sub   r17, r17, r23
    xor   r23, r23, r0

    lsr   r24, COS_Q24, 31
    rsb   r24, r24, 0
    xor   COS_Q24, COS_Q24, r24
    sub   COS_Q24, COS_Q24, r24
    xor   r24, r24, r0

    ; Vd*cos -> TMP; Vq*cos -> R22; Vq*sin -> R19; Vd*sin -> TMP.
    ; Cosine and sine remain in the fixed MAC operand slot across their paired
    ; products, removing three operand-register moves without changing the
    ; per-product Q24 truncation contract.
    MUL_Q24_PRESCALED_KEEP_B TMP, r20, r24, 0
    MUL_Q24_PRESCALED_KEEP_B VBETA, r21, r24, r10
    MUL_Q24_PRESCALED_KEEP_A VALPHA, r17, r23, r10
    sub   VALPHA, TMP, VALPHA
    MUL_Q24_PRESCALED_KEEP_B TMP, r20, r23, 0
    add   VBETA, TMP, VBETA
    l_inverse_park_end:

    ; Conventional inverse Clarke/SVPWM:
    ; Va=Valpha; Vb=-0.5*Valpha+sqrt(3)/2*Vbeta;
    ; Vc=-0.5*Valpha-sqrt(3)/2*Vbeta.
    l_inverse_clarke_start:
    mov   VA, VALPHA
    lsr   TMP, VALPHA, 1
    and   TMP2.b3, VALPHA.b3, 128
    or    TMP.b3, TMP.b3, TMP2.b3
    rsb   TMP, TMP, 0
    ; Vbeta is bounded by the validated voltage circle, so its magnitude fits
    ; the unsigned upper-word identity.  The high-bit constant is an unsigned
    ; prescaled sqrt(3)/2 operand, never a signed Q24 value.
    lsr   r25, VBETA, 31
    rsb   r25, r25, 0
    xor   r28, VBETA, r25
    sub   r28, r28, r25
    ldi32 r29, 0xDDB3D700
    nop
    xin   0, &r27, 4
    xor   r27, r27, r25
    sub   r27, r27, r25
    add   VB, TMP, r27
    sub   VC, TMP, r27
    l_inverse_clarke_end:

    ; Signed Vmax/Vmin through sign-bit bias and arithmetic common-mode shift.
    l_svpwm_common_mode_start:
    ldi32 TMP, 0x80000000
    xor   r27, VA, TMP
    xor   r28, VB, TMP
    min   r29, r27, r28
    max   r28, r27, r28
    xor   r27, VC, TMP
    min   r29, r29, r27
    max   r28, r28, r27
    ; The two sign-bias offsets cancel in the wrapped sum, so the extrema
    ; need not be converted back before addition.
    add   TMP2, r28, r29
    lsr   r27, TMP2, 1
    and   TMP2, TMP2, TMP
    or    r27, r27, TMP2
    l_svpwm_common_mode_end:

    l_svpwm_duty_offset_start:
    ldi32 TMP, 0x00800000
    sub   TMP, TMP, r27
    add   VA, VA, TMP
    add   VB, VB, TMP
    add   VC, VC, TMP
    l_svpwm_duty_offset_end:

    ; Final physical-duty clamp and saturation status.
    l_duty_clamp_status_start:
    ldi   TMP, 0
    ldi32 TMP2, FOC_Q_ONE
    qbbc  l_ta_nonnegative, VA, 31
    ldi   VA, 0
    or    TMP, TMP, FOC_STATUS_SATURATED
    qba   l_ta_done
l_ta_nonnegative:
    qble  l_ta_done, TMP2, VA
    mov   VA, TMP2
    or    TMP, TMP, FOC_STATUS_SATURATED
l_ta_done:
    qbbc  l_tb_nonnegative, VB, 31
    ldi   VB, 0
    or    TMP, TMP, FOC_STATUS_SATURATED
    qba   l_tb_done
l_tb_nonnegative:
    qble  l_tb_done, TMP2, VB
    mov   VB, TMP2
    or    TMP, TMP, FOC_STATUS_SATURATED
l_tb_done:
    qbbc  l_tc_nonnegative, VC, 31
    ldi   VC, 0
    or    TMP, TMP, FOC_STATUS_SATURATED
    qba   l_tc_done
l_tc_nonnegative:
    qble  l_tc_done, TMP2, VC
    mov   VC, TMP2
    or    TMP, TMP, FOC_STATUS_SATURATED
l_tc_done:
    qba   l_computation_end

l_invalid_config:
    ldi   TMP, FOC_STATUS_INVALID_CONFIG
    ldi   TMP2, FOC_STATUS_SATURATED
    or    TMP, TMP, TMP2
    ldi32 VA, 0x00800000
    mov   VB, VA
    mov   VC, VA
    ldi   VALPHA, 0
    ldi   VBETA, 0
    qba   l_computation_end

l_disabled_output:
    ; Disabled execution is neutral and holds both SETPOINT and THETA_ACC.
    ldi32 VA, 0x00800000
    mov   VB, VA
    mov   VC, VA
    ldi   VALPHA, 0
    ldi   VBETA, 0
    ldi   TMP, FOC_STATUS_DISABLED
    qba   l_computation_end

l_computation_end:
l_prepare_publication:
l_deadline_start:
    ; Advance the next absolute deadline. Crossing it is a timing fault.
    add   DEADLINE_LO, DEADLINE_LO, PERIOD_IEP
    adc   DEADLINE_HI, DEADLINE_HI, 0
    lbco  &r25, c26, 0x10, 8
    mov   r27, r25
    mov   r28, r26
    sub   r27, r27, DEADLINE_LO
    suc   r28, r28, DEADLINE_HI
    qbbs  l_publish_pwm, r28, 31
    ldi   TMP2, FOC_STATUS_DEADLINE_MISS
    or    TMP, TMP, TMP2
    ldi32 VA, 0x00800000
    mov   VB, VA
    mov   VC, VA
    ldi   VALPHA, 0
    ldi   VBETA, 0

l_publish_pwm:
l_publication_start:
    add   LOOP_COUNTER, LOOP_COUNTER, 1
    lsl   TMP2, LOOP_COUNTER, 1
    add   TMP2, TMP2, 1
    sbbo  &TMP2, BASE_PWM, FOC_PWM_OUT_SEQ_OFF, 4
    sbbo  &VA, BASE_PWM, FOC_PWM_OUT_TA_Q24_OFF, 4
    sbbo  &VB, BASE_PWM, FOC_PWM_OUT_TB_Q24_OFF, 4
    sbbo  &VC, BASE_PWM, FOC_PWM_OUT_TC_Q24_OFF, 4
    sbbo  &VALPHA, BASE_PWM, FOC_PWM_OUT_VALPHA_Q24_OFF, 4
    sbbo  &VBETA, BASE_PWM, FOC_PWM_OUT_VBETA_Q24_OFF, 4
    sbbo  &THETA_ACC, BASE_PWM, FOC_PWM_OUT_THETA_CMD_U32_OFF, 4
    sbbo  &LOOP_COUNTER, BASE_PWM, FOC_PWM_OUT_LOOP_COUNTER_OFF, 4
    sbbo  &r25, BASE_PWM, FOC_PWM_OUT_TIMESTAMP_CYCLES_OFF, 8
    sbbo  &TMP, BASE_PWM, FOC_PWM_OUT_STATUS_OFF, 4
    sbbo  &SETPOINT, BASE_PWM, FOC_PWM_OUT_SPEED_CMD_Q24_OFF, 4
    add   TMP2, TMP2, 1
    sbbo  &TMP2, BASE_PWM, FOC_PWM_OUT_SEQ_OFF, 4
    qba   l_check_config
