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
    .asg r15, APPLIED_GEN
    .asg r0,  VC
    .asg r16, TMP
    .asg r17, SIN_Q24
    .asg r18, COS_Q24
    .asg r19, VALPHA
    .asg r21, TMP2
    .asg r22, VBETA
    .asg r23, VA
    .asg r24, VB

; MUL_Q24: signed Q24 x Q24 -> Q24 using the simulator's broadside MAC.
; The MAC operands are unsigned, so both magnitudes are formed first and the
; combined sign is applied after the Q48 product is shifted by 24 bits.
    .macro MUL_Q24 out, a, b
    lsr   r20, a, 31
    rsb   r20, r20, 0
    xor   r28, a, r20
    sub   r28, r28, r20
    lsr   r21, b, 31
    rsb   r21, r21, 0
    xor   r29, b, r21
    sub   r29, r29, r21
    xor   r20, r20, r21
    ldi   r25, 0
    xout  0, &r25, 1
    nop
    xin   0, &r26, 4
    xin   0, &r27, 4
    lsr   r26, r26, 24
    lsl   r27, r27, 8
    or    r26, r26, r27
    xor   r27, r26, r20
    sub   out, r27, r20
    .endm

    .retain
    .retainrefs
    .global main
    .sect ".text"

main:
    ldi32 BASE_CTRL, FOC_CONTROL_BASE
    ldi32 BASE_PWM, FOC_PWM_OUT_BASE
    ldi32 BASE_LUT, FOC_SINE_LUT_BASE
    ldi32 APPLIED_GEN, 0xFFFFFFFF
    ldi   SETPOINT, 0
    ldi   THETA_ACC, 0
    ldi   LOOP_COUNTER, 0
    ldi   PERIOD_IEP, FOC_DEFAULT_CONTROL_PERIOD_IEP_TICKS

    ; Establish the first absolute deadline from the IEP snapshot.
    lbco  &DEADLINE_LO, c26, 0x10, 8
    add   DEADLINE_LO, DEADLINE_LO, PERIOD_IEP
    adc   DEADLINE_HI, DEADLINE_HI, 0

l_check_config:
    lbbo  &TMP, BASE_CTRL, FOC_CONTROL_REQUESTED_GENERATION_OFF, 4
    lbbo  &ENABLE, BASE_CTRL, FOC_CONTROL_ENABLE_OFF, 4
    qbeq  l_wait_deadline, TMP, APPLIED_GEN

    ; Speed, Vd, Vq, and slew are consecutive. The period is the final
    ; versioned control word and is read separately for legacy writers.
    lbbo  &SPEED_REF, BASE_CTRL, FOC_CONTROL_SPEED_REF_Q24_OFF, 16
    lbbo  &PERIOD_IEP, BASE_CTRL, FOC_CONTROL_PERIOD_IEP_TICKS_OFF, 4
    qbeq  l_config_default_period, PERIOD_IEP, 0
    qba   l_config_ack
l_config_default_period:
    ldi   PERIOD_IEP, FOC_DEFAULT_CONTROL_PERIOD_IEP_TICKS
l_config_ack:
    mov   APPLIED_GEN, TMP
    sbbo  &APPLIED_GEN, BASE_CTRL, FOC_CONTROL_PRU_ACK_GENERATION_OFF, 4

l_wait_deadline:
    ; Wait for (now - deadline) to be non-negative in modular signed 64-bit
    ; arithmetic. This is an absolute deadline and remains correct at rollover.
    lbco  &r27, c26, 0x10, 8
    sub   r27, r27, DEADLINE_LO
    suc   r28, r28, DEADLINE_HI
    qbbs  l_wait_deadline, r28, 31

    ; Enable is sampled at the boundary independently of generation changes.
    lbbo  &ENABLE, BASE_CTRL, FOC_CONTROL_ENABLE_OFF, 4
    qbbc  l_disabled_output, ENABLE, 0

    ; Reject a voltage vector outside the linear SVPWM circle before changing
    ; the open-loop angle.  The squared magnitude is represented in Q24.
    MUL_Q24 TMP, VD_REF, VD_REF
    MUL_Q24 TMP2, VQ_REF, VQ_REF
    add   TMP, TMP, TMP2
    ldi32 TMP2, FOC_MAX_VOLTAGE_MAGNITUDE_SQUARED_Q24
    qble  l_vector_valid, TMP2, TMP
    ldi   TMP, FOC_STATUS_INVALID_CONFIG
    ldi   TMP2, FOC_STATUS_SATURATED
    or    TMP, TMP, TMP2
    ldi32 VA, 0x00800000
    mov   VB, VA
    mov   VC, VA
    ldi   VALPHA, 0
    ldi   VBETA, 0
    qba   l_prepare_publication

l_vector_valid:

    ; RC: slew the signed speed command by one per-loop Q24 increment.
    sub   TMP, SPEED_REF, SETPOINT
    lsr   TMP2, TMP, 31
    rsb   TMP2, TMP2, 0
    xor   TMP, TMP, TMP2
    sub   TMP, TMP, TMP2
    qbgt  l_rc_clamp, TMP, RAMP_RATE
    xor   TMP, RAMP_RATE, TMP2
    sub   TMP, TMP, TMP2
    add   SETPOINT, SETPOINT, TMP
    qba   l_rc_done
l_rc_clamp:
    mov   SETPOINT, SPEED_REF
l_rc_done:

    ; RG: the 32-bit accumulator is one electrical revolution.
    ldi32 r30, FOC_SPEED_SCALE
    MUL_Q24 TMP, SETPOINT, r30
    add   THETA_ACC, THETA_ACC, TMP

    ; Host-seeded sine/cosine lookup. The quarter-turn offset is 512 entries.
    lsr   TMP, THETA_ACC, 21
    lsl   TMP2, TMP, 2
    lbbo  &SIN_Q24, BASE_LUT, TMP2, 4
    ldi   TMP2, 512
    add   TMP, TMP, TMP2
    ldi   TMP2, 0x7FF
    and   TMP, TMP, TMP2
    lsl   TMP, TMP, 2
    lbbo  &COS_Q24, BASE_LUT, TMP, 4

    ; Conventional inverse Park.
    MUL_Q24 TMP, VD_REF, COS_Q24
    MUL_Q24 TMP2, VQ_REF, SIN_Q24
    sub   VALPHA, TMP, TMP2
    MUL_Q24 TMP, VD_REF, SIN_Q24
    MUL_Q24 TMP2, VQ_REF, COS_Q24
    add   VBETA, TMP, TMP2

    ; Conventional inverse Clarke/SVPWM:
    ; Va=Valpha; Vb=-0.5*Valpha+sqrt(3)/2*Vbeta;
    ; Vc=-0.5*Valpha-sqrt(3)/2*Vbeta.
    mov   VA, VALPHA
    lsr   TMP, VALPHA, 1
    ldi32 TMP2, 0x80000000
    and   TMP2, VALPHA, TMP2
    or    TMP, TMP, TMP2
    rsb   TMP, TMP, 0
    ldi32 r30, 0x00DDB3D7
    MUL_Q24 TMP2, r30, VBETA
    add   VB, TMP, TMP2
    sub   VC, TMP, TMP2

    ; Signed Vmax/Vmin through sign-bit bias and arithmetic common-mode shift.
    ldi32 TMP, 0x80000000
    xor   r27, VA, TMP
    xor   r28, VB, TMP
    min   r29, r27, r28
    max   r28, r27, r28
    xor   r27, VC, TMP
    min   r29, r29, r27
    max   r28, r28, r27
    xor   r29, r29, TMP
    xor   r28, r28, TMP
    add   TMP2, r28, r29
    lsr   r27, TMP2, 1
    and   TMP2, TMP2, TMP
    or    r27, r27, TMP2

    ldi32 TMP, 0x00800000
    sub   VA, VA, r27
    add   VA, VA, TMP
    sub   VB, VB, r27
    add   VB, VB, TMP
    sub   VC, VC, r27
    add   VC, VC, TMP

    ; Final physical-duty clamp and saturation status.
    ldi   TMP, 0
    qbbc  l_ta_nonnegative, VA, 31
    ldi   VA, 0
    ldi   TMP2, FOC_STATUS_SATURATED
    or    TMP, TMP, TMP2
    qba   l_ta_done
l_ta_nonnegative:
    ldi32 TMP2, FOC_Q_ONE
    qble  l_ta_done, TMP2, VA
    mov   VA, TMP2
    ldi   TMP2, FOC_STATUS_SATURATED
    or    TMP, TMP, TMP2
l_ta_done:
    qbbc  l_tb_nonnegative, VB, 31
    ldi   VB, 0
    ldi   TMP2, FOC_STATUS_SATURATED
    or    TMP, TMP, TMP2
    qba   l_tb_done
l_tb_nonnegative:
    ldi32 TMP2, FOC_Q_ONE
    qble  l_tb_done, TMP2, VB
    mov   VB, TMP2
    ldi   TMP2, FOC_STATUS_SATURATED
    or    TMP, TMP, TMP2
l_tb_done:
    qbbc  l_tc_nonnegative, VC, 31
    ldi   VC, 0
    ldi   TMP2, FOC_STATUS_SATURATED
    or    TMP, TMP, TMP2
    qba   l_tc_done
l_tc_nonnegative:
    ldi32 TMP2, FOC_Q_ONE
    qble  l_tc_done, TMP2, VC
    mov   VC, TMP2
    ldi   TMP2, FOC_STATUS_SATURATED
    or    TMP, TMP, TMP2
l_tc_done:
    qba   l_prepare_publication

l_disabled_output:
    ; Disabled execution is neutral and holds both SETPOINT and THETA_ACC.
    ldi32 VA, 0x00800000
    mov   VB, VA
    mov   VC, VA
    ldi   VALPHA, 0
    ldi   VBETA, 0
    ldi   TMP, FOC_STATUS_DISABLED

l_prepare_publication:
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
