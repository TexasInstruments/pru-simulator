; Open-loop fixed-point SVGEN with actual PRU0 R30 software PWM.
; The config-only ABI is generated from schema/foc_control_abi.json.
; R30.0..2 are phase A/B/C; GPI.3/.4 are pin-coupled current modulators.

    .include "foc_control_abi.inc"

IEP_COUNT_ADDR .set 0x0002E010
PWM_MASK      .set 7
SIGN_BIT      .set 0x80000000
Q15_HALF      .set 16384
Q15_CENTER_CLAMP .set 14189
HALF_PERIOD_TICKS .set 6250

start:
    LDI32 r27, IEP_COUNT_ADDR
    LDI   r29, 1
    LDI32 r25, 0x0002E000
    LDI   r24, 0x11              ; enable the external IEP clock, increment 1
    SBBO  &r24, r25, 0, 4

control_update:
    ; ABI input is signed Q15 alpha/beta. A non-zero phase increment selects
    ; the six-sector open-loop rotating vector with the requested magnitude.
    LDI32 r18, FOC_CONTROL_ADDRESS
    LBBO  &r0, r18, 0, 32
    LDI   r16, FOC_ABI_VERSION
    QBNE  invalid_config, r0, r16
    QBNE  invalid_config, r1, r2
    LDI   r16, 12500
    QBNE  invalid_config, r1, r16
    QBEQ  phase_initialized, r29, 0
    MOV   r8, r7
phase_initialized:
    QBEQ  direct_vector, r3, 0
    ADD   r8, r8, r3
    LDI32 r17, 0x2AAAAAAB
    QBGT  sector_0, r8, r17
    LDI32 r17, 0x55555555
    QBGT  sector_1, r8, r17
    LDI32 r17, 0x80000000
    QBGT  sector_2, r8, r17
    LDI32 r17, 0xAAAAAAAA
    QBGT  sector_3, r8, r17
    LDI32 r17, 0xD5555555
    QBGT  sector_4, r8, r17
    QBA   sector_5

direct_vector:
    MOV   r10, r5
    MOV   r11, r6
    QBA   svgen

sector_0:
    MOV   r10, r4
    LDI   r11, 0
    QBA   svgen
sector_1:
    LSR   r10, r4, 1
    JAL   r28, multiply_sqrt3_over_2
    MOV   r11, r21
    QBA   svgen
sector_2:
    LSR   r10, r4, 1
    RSB   r10, r10, 0
    JAL   r28, multiply_sqrt3_over_2
    MOV   r11, r21
    QBA   svgen
sector_3:
    RSB   r10, r4, 0
    LDI   r11, 0
    QBA   svgen
sector_4:
    LSR   r10, r4, 1
    RSB   r10, r10, 0
    JAL   r28, multiply_sqrt3_over_2
    RSB   r11, r21, 0
    QBA   svgen
sector_5:
    LSR   r10, r4, 1
    JAL   r28, multiply_sqrt3_over_2
    RSB   r11, r21, 0

svgen:
    ; TI SVGEN_runCom phase order: A=alpha, B=-alpha/2+sqrt(3)/2*beta,
    ; C=-alpha/2-sqrt(3)/2*beta. The max/min common-mode is removed below.
    MOV   r18, r11
    LDI   r16, 0
    QBBC  beta_magnitude_ready, r18, 31
    RSB   r18, r18, 0
    LDI   r16, 1
beta_magnitude_ready:
    ; Signed Q15 multiply by 28378/32768 ~= sqrt(3)/2.
    LDI   r12, 0
    LSL   r13, r18, 1
    LSL   r14, r18, 3
    ADD   r12, r12, r13
    ADD   r12, r12, r14
    LSL   r13, r18, 4
    LSL   r14, r18, 6
    ADD   r12, r12, r13
    ADD   r12, r12, r14
    LSL   r13, r18, 7
    LSL   r14, r18, 9
    ADD   r12, r12, r13
    ADD   r12, r12, r14
    LSL   r13, r18, 10
    LSL   r14, r18, 11
    ADD   r12, r12, r13
    ADD   r12, r12, r14
    LSL   r13, r18, 13
    LSL   r14, r18, 14
    ADD   r12, r12, r13
    ADD   r12, r12, r14
    LSR   r12, r12, 15
    QBEQ  beta_signed, r16, 0
    RSB   r12, r12, 0
beta_signed:
    ; r12 = sqrt(3)/2*beta; r16 = -alpha/2.
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
    MOV   r26, r25

    ; Centered phases map to duty = clamp(0.5 + phase - common mode).
    SUB   r20, r13, r26
    JAL   r28, q15_to_ticks
    MOV   r10, r21
    SUB   r20, r14, r26
    JAL   r28, q15_to_ticks
    MOV   r11, r21
    SUB   r20, r15, r26
    JAL   r28, q15_to_ticks
    MOV   r12, r21

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
    QBEQ  first_period, r29, 1
    QBA   wait_period
first_period:
    LDI   r29, 0
    ; First period starts now; subsequent periods begin at a fixed IEP target.
    LBBO  &r19, r27, 0, 4
    ADD   r9, r19, r2
    LDI   r16, PWM_MASK
    MOV   r30, r16

pwm_edges:
    ADD   r23, r19, r10
edge_a_wait:
    LBBO  &r24, r27, 0, 4
    QBLE  edge_a, r24, r23
    QBA   edge_a_wait
edge_a:
    CLR   r30, r30, r13
    ADD   r23, r19, r11
edge_b_wait:
    LBBO  &r24, r27, 0, 4
    QBLE  edge_b, r24, r23
    QBA   edge_b_wait
edge_b:
    CLR   r30, r30, r14
    ADD   r23, r19, r12
edge_c_wait:
    LBBO  &r24, r27, 0, 4
    QBLE  edge_c, r24, r23
    QBA   edge_c_wait
edge_c:
    CLR   r30, r30, r15

    ; Compute the next vector while all PWM outputs are low, then wait for the
    ; exact configured control-period boundary on the external IEP counter.
    QBA   control_update
wait_period:
    LBBO  &r24, r27, 0, 4
    QBLE  next_period, r24, r9
    QBA   wait_period
next_period:
    MOV   r19, r9
    ADD   r9, r9, r1
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

multiply_sqrt3_over_2:
    ; r4 is unsigned Q15 magnitude, return Q15 product in r21.
    LDI   r12, 0
    LSL   r13, r4, 1
    LSL   r14, r4, 3
    ADD   r12, r12, r13
    ADD   r12, r12, r14
    LSL   r13, r4, 4
    LSL   r14, r4, 6
    ADD   r12, r12, r13
    ADD   r12, r12, r14
    LSL   r13, r4, 7
    LSL   r14, r4, 9
    ADD   r12, r12, r13
    ADD   r12, r12, r14
    LSL   r13, r4, 10
    LSL   r14, r4, 11
    ADD   r12, r12, r13
    ADD   r12, r12, r14
    LSL   r13, r4, 13
    LSL   r14, r4, 14
    ADD   r12, r12, r13
    ADD   r12, r12, r14
    LSR   r21, r12, 15
    JMP   r28

q15_to_ticks:
    ; Clamp centered Q15 to +/-sqrt(3)/4 for linear SVGEN and control margin.
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
q15_scaled:
    ; x*12500 = x*(8192+4096+128+64+16+4), then divide by 32768.
    LSL   r24, r20, 13
    LSL   r25, r20, 12
    ADD   r24, r24, r25
    LSL   r25, r20, 7
    ADD   r24, r24, r25
    LSL   r25, r20, 6
    ADD   r24, r24, r25
    LSL   r25, r20, 4
    ADD   r24, r24, r25
    LSL   r25, r20, 2
    ADD   r24, r24, r25
    LSR   r21, r24, 15
    QBEQ  q15_positive_ticks, r23, 0
    LDI   r16, HALF_PERIOD_TICKS
    RSB   r21, r21, r16
    JMP   r28
q15_positive_ticks:
    LDI   r16, HALF_PERIOD_TICKS
    ADD   r21, r21, r16
    JMP   r28

invalid_config:
    HALT
