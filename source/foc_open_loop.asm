; Open-loop FOC (V/f) with actual PRU0 R30 software PWM.
; Every control period: ramp the speed, integrate the electrical angle,
; look up sin/cos, inverse Park (vd,vq -> alpha,beta), Q15 SVGEN, then drive
; R30.0..2 with real per-instruction software PWM. The motor model sees only
; those pins. The config/ack ABI is generated from schema/foc_control_abi.json.
; GPI.3/.4 carry the pin-coupled current modulators; this firmware ignores them.
;
; Persistent registers:
;   r0 angle (Q32 turn)   r1 ramped speed (Q28 pu)  r2 speed_ref   r3 ramp_rate
;   r4 vd (Q15)           r5 vq (Q15)               r6 adopted generation
;   r7 status             r8 first-period flag      r9 control period ticks
;   r19 period start      r27 IEP count address
; r28/r29 are MAC operands and r26 receives the multiply-only product.

    .include "foc_control_abi.inc"

IEP_COUNT_ADDR .set 0x0002E010
SINE_TABLE    .set 0x00001000        ; PRU0 DRAM0, 1024 signed Q15 words
SINE_STEP_C2  .set 2147443223        ; ~2*cos(2*pi/1024) in Q30, tuned
SINE_STEP_S1  .set 6588376           ; ~sin(2*pi/1024) in Q30, tuned
PWM_MASK      .set 7
SIGN_BIT      .set 0x80000000
Q15_CENTER_CLAMP .set 14189
HALF_PERIOD_TICKS .set 6250
Q15_SQRT3_OVER_2 .set 28378

start:
    LDI   r25, 0                 ; MAC multiply-only mode
    XOUT  0, &r25, 1

    ; Build the sine table with the recurrence s[k+1] = c2*s[k] - s[k-1]
    ; (Q30, sign-magnitude multiply), storing each entry rounded to Q15.
    LDI32 r10, SINE_TABLE
    LDI32 r12, SINE_STEP_S1      ; s[-1] = -s[1] makes the first step s[1]
    RSB   r12, r12, 0
    LDI   r13, 0
    LDI32 r14, SINE_STEP_C2
    LDI   r15, 0
    LDI   r18, 0x4000
    LDI   r21, 1024
sine_loop:
    LSR   r16, r13, 31
    RSB   r16, r16, 0
    XOR   r17, r13, r16
    SUB   r17, r17, r16
    ADD   r20, r17, r18
    LSR   r20, r20, 15
    XOR   r20, r20, r16
    SUB   r20, r20, r16
    SBBO  &r20, r10, 0, 4
    ADD   r10, r10, 4
    MOV   r28, r14
    MOV   r29, r17
    XIN   0, &r26, 8
    LSR   r26, r26, 30
    LSL   r27, r27, 2
    OR    r26, r26, r27
    XOR   r26, r26, r16
    SUB   r26, r26, r16
    SUB   r26, r26, r12
    MOV   r12, r13
    MOV   r13, r26
    ADD   r15, r15, 1
    QBNE  sine_loop, r15, r21

    LDI32 r27, IEP_COUNT_ADDR
    LDI32 r25, 0x0002E000
    LDI   r24, 0x11              ; enable the external IEP clock, increment 1
    SBBO  &r24, r25, 0, 4
    LDI   r1, 0
    LDI   r7, 0
    LDI   r8, 1
    LDI   r9, 12500
    LDI32 r18, FOC_CONTROL_ADDRESS
    LBBO  &r6, r18, FOC_REQUESTED_GENERATION_OFFSET, 4
    NOT   r6, r6                 ; the first update always adopts the config

control_update:
    LDI32 r18, FOC_CONTROL_ADDRESS
    LBBO  &r10, r18, FOC_REQUESTED_GENERATION_OFFSET, 4
    QBEQ  config_ready, r10, r6

    ; Adopt a new generation atomically; any out-of-range field leaves the
    ; output neutral with STATUS_INVALID_CONFIG until the next generation.
    LBBO  &r10, r18, 0, 20
    LBBO  &r20, r18, FOC_SPEED_REF_Q28_OFFSET, 16
    MOV   r6, r14
    LDI   r7, FOC_STATUS_INVALID_CONFIG
    LDI   r15, FOC_ABI_VERSION
    QBNE  config_ack, r10, r15
    QBNE  config_ack, r11, r9
    QBNE  config_ack, r12, r9
    LDI   r15, 1
    QBLT  config_ack, r13, r15
    LDI32 r15, 0x10000000
    ADD   r16, r20, r15
    LSL   r17, r15, 1
    QBLT  config_ack, r16, r17
    QBLT  config_ack, r21, r15
    LDI   r15, 0x8000
    LDI32 r17, 0xFFFF
    ADD   r16, r22, r15
    QBLT  config_ack, r16, r17
    ADD   r16, r23, r15
    QBLT  config_ack, r16, r17
    MOV   r2, r20
    MOV   r3, r21
    MOV   r4, r22
    MOV   r5, r23
    LDI   r7, 0
    QBEQ  config_ack, r13, 0
    LDI   r7, FOC_STATUS_RUNNING
    QBEQ  config_ack, r8, 0
    LBBO  &r0, r18, FOC_INITIAL_PHASE_Q32_OFFSET, 4
config_ack:
    SBBO  &r14, r18, FOC_ACK_GENERATION_OFFSET, 4
config_ready:
    CLR   r7, r7, 2
    QBBS  running, r7, 0
    ; Disabled or invalid: zero voltage, ramp and angle hold at zero speed.
    LDI   r1, 0
    LDI   r10, 0
    LDI   r11, 0
    QBA   svgen

running:
    ; Ramp toward the reference, then integrate the angle.
    SUB   r10, r2, r1
    QBBS  ramp_down, r10, 31
    QBLE  ramp_snap, r3, r10
    ADD   r1, r1, r3
    QBA   ramp_done
ramp_down:
    RSB   r10, r10, 0
    QBLE  ramp_snap, r3, r10
    SUB   r1, r1, r3
    QBA   ramp_done
ramp_snap:
    MOV   r1, r2
ramp_done:
    ADD   r0, r0, r1             ; 1 pu = 2^28 per 16 kHz update = 1 kHz

    ; sin/cos from the table: index = round(angle / 2^22), cos = sin(angle + a quarter turn).
    LDI32 r13, 0x00200000        ; half a table step: round to nearest entry
    ADD   r10, r0, r13
    LSR   r12, r10, 22
    LSL   r12, r12, 2
    LDI32 r13, 0x40000000
    ADD   r11, r10, r13
    LSR   r11, r11, 22
    LSL   r11, r11, 2
    LDI32 r13, SINE_TABLE
    ADD   r12, r12, r13
    ADD   r11, r11, r13
    LBBO  &r14, r12, 0, 4
    LBBO  &r15, r11, 0, 4

    ; Inverse Park with sign-magnitude Q15 products on the MAC:
    ; alpha = vd*cos - vq*sin, beta = vd*sin + vq*cos.
    LSR   r16, r14, 31
    RSB   r16, r16, 0
    XOR   r14, r14, r16
    SUB   r14, r14, r16
    LSR   r17, r15, 31
    RSB   r17, r17, 0
    XOR   r15, r15, r17
    SUB   r15, r15, r17
    LSR   r21, r4, 31
    RSB   r21, r21, 0
    XOR   r20, r4, r21
    SUB   r20, r20, r21
    LSR   r23, r5, 31
    RSB   r23, r23, 0
    XOR   r22, r5, r23
    SUB   r22, r22, r23

    MOV   r28, r20
    MOV   r29, r15
    XOR   r24, r21, r17
    XIN   0, &r26, 4
    LSR   r26, r26, 15
    XOR   r26, r26, r24
    SUB   r10, r26, r24          ; vd*cos
    MOV   r28, r22
    MOV   r29, r14
    XOR   r24, r23, r16
    XIN   0, &r26, 4
    LSR   r26, r26, 15
    XOR   r26, r26, r24
    SUB   r26, r26, r24          ; vq*sin
    SUB   r10, r10, r26          ; alpha
    MOV   r28, r20
    MOV   r29, r14
    XOR   r24, r21, r16
    XIN   0, &r26, 4
    LSR   r26, r26, 15
    XOR   r26, r26, r24
    SUB   r11, r26, r24          ; vd*sin
    MOV   r28, r22
    MOV   r29, r15
    XOR   r24, r23, r17
    XIN   0, &r26, 4
    LSR   r26, r26, 15
    XOR   r26, r26, r24
    SUB   r26, r26, r24          ; vq*cos
    ADD   r11, r11, r26          ; beta

svgen:
    ; TI SVGEN_runCom phase order: A=alpha, B=-alpha/2+sqrt(3)/2*beta,
    ; C=-alpha/2-sqrt(3)/2*beta. The max/min common-mode is removed below.
    LSR   r18, r11, 31
    RSB   r18, r18, 0
    XOR   r28, r11, r18
    SUB   r28, r28, r18
    LDI   r29, Q15_SQRT3_OVER_2
    XIN   0, &r26, 4
    LSR   r26, r26, 15
    XOR   r12, r26, r18
    SUB   r12, r12, r18          ; sqrt(3)/2*beta
    ; r16 = -alpha/2, truncated on the magnitude.
    QBBS  alpha_negative, r10, 31
    LSR   r16, r10, 1
    RSB   r16, r16, 0
    QBA   alpha_half_ready
alpha_negative:
    RSB   r16, r10, 0
    LSR   r16, r16, 1
alpha_half_ready:
    MOV   r13, r10
    ADD   r14, r16, r12
    SUB   r15, r16, r12

    ; Sort the signed phases by flipping their sign bits, then take max/min.
    LDI32 r17, SIGN_BIT
    XOR   r20, r13, r17
    XOR   r21, r14, r17
    XOR   r22, r15, r17
    MIN   r23, r20, r21
    MIN   r23, r23, r22
    MAX   r24, r20, r21
    MAX   r24, r24, r22
    LSR   r25, r23, 1
    LSR   r26, r24, 1
    ADD   r25, r25, r26
    AND   r26, r23, r24
    AND   r26, r26, 1
    ADD   r25, r25, r26
    XOR   r25, r25, r17
    MOV   r18, r25

    ; Centered phases map to duty = clamp(0.5 + phase - common mode).
    SUB   r20, r13, r18
    JAL   r12, q15_to_ticks
    MOV   r10, r21
    SUB   r20, r14, r18
    JAL   r12, q15_to_ticks
    MOV   r11, r21
    SUB   r20, r15, r18
    JAL   r12, q15_to_ticks
    MOV   r12, r21

    LDI32 r18, FOC_CONTROL_ADDRESS
    SBBO  &r7, r18, FOC_STATUS_OFFSET, 4

    ; Sort compare times and their pin numbers for one event-driven PWM period.
    LDI   r13, 0
    LDI   r14, 1
    LDI   r15, 2
    QBGT  sort_01, r11, r10
sort_01_done:
    QBGT  sort_12, r12, r11
sort_12_done:
    QBGT  sort_01_again, r11, r10
sort_done:
    QBEQ  first_period, r8, 1
    QBA   wait_period
first_period:
    LDI   r8, 0
    ; Keep the period start; elapsed-time comparisons remain valid across wrap.
    LBBO  &r19, r27, 0, 4
    LDI   r16, PWM_MASK
    MOV   r30, r16

pwm_edges:
edge_a_wait:
    LBBO  &r24, r27, 0, 4
    SUB   r24, r24, r19
    QBLE  edge_a, r24, r10
    QBA   edge_a_wait
edge_a:
    CLR   r30, r30, r13
edge_b_wait:
    LBBO  &r24, r27, 0, 4
    SUB   r24, r24, r19
    QBLE  edge_b, r24, r11
    QBA   edge_b_wait
edge_b:
    CLR   r30, r30, r14
edge_c_wait:
    LBBO  &r24, r27, 0, 4
    SUB   r24, r24, r19
    QBLE  edge_c, r24, r12
    QBA   edge_c_wait
edge_c:
    CLR   r30, r30, r15

    ; Compute the next vector while all PWM outputs are low, then wait for the
    ; exact configured control-period boundary on the external IEP counter.
    QBA   control_update
wait_period:
    LBBO  &r24, r27, 0, 4
    SUB   r24, r24, r19
    QBLE  next_period, r24, r9
    QBA   wait_period
next_period:
    ADD   r19, r19, r9
    LDI   r16, PWM_MASK
    MOV   r30, r16
    QBA   pwm_edges

sort_01:
    MOV   r16, r10
    MOV   r10, r11
    MOV   r11, r16
    MOV   r16, r13
    MOV   r13, r14
    MOV   r14, r16
    QBA   sort_01_done
sort_12:
    MOV   r16, r11
    MOV   r11, r12
    MOV   r12, r16
    MOV   r16, r14
    MOV   r14, r15
    MOV   r15, r16
    QBA   sort_12_done
sort_01_again:
    MOV   r16, r10
    MOV   r10, r11
    MOV   r11, r16
    MOV   r16, r13
    MOV   r13, r14
    MOV   r14, r16
    QBA   sort_done

q15_to_ticks:
    ; r20 centered signed Q15 -> r21 compare ticks (clamped to +/-sqrt(3)/4
    ; for linear SVGEN and control margin); r12 is the link register.
    LDI   r22, Q15_CENTER_CLAMP
    LDI   r23, 0
    QBBC  q15_positive, r20, 31
    RSB   r20, r20, 0
    LDI   r23, 1
q15_positive:
    QBGT  q15_clamp, r22, r20
    QBA   q15_scaled
q15_clamp:
    MOV   r20, r22
    SET   r7, r7, 2              ; STATUS_SATURATED
q15_scaled:
    MOV   r28, r20
    LDI   r29, 12500
    XIN   0, &r26, 4
    LSR   r21, r26, 15
    LDI   r16, HALF_PERIOD_TICKS
    QBEQ  q15_positive_ticks, r23, 0
    RSB   r21, r21, r16
    JMP   r12
q15_positive_ticks:
    ADD   r21, r21, r16
    JMP   r12
