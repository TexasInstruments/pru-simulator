; ============================================================
;  MAC Accelerator Example
;  Demonstrates both MPY (multiply-only) and MAC (accumulate)
;  modes of the PRU ICSSG broadside multiplier (device_id=0)
;
;  Register map:
;    R25  MAC_CTRL_STATUS  (bit0=MAC_MODE, bit1=clear ACC_CARRY)
;    R26  Result low 32 bits
;    R27  Result high 32 bits
;    R28  Operand A  (auto-sampled by hardware each XOUT R25)
;    R29  Operand B  (auto-sampled by hardware each XOUT R25)
;
;  Expected results after HALT:
;    R26 = 0x00000020  (32 = dot product, overwrites MPY result)
;    R27 = 0x00000000
; ============================================================

DEVICE_ID     .set 0
MULTIPLY_ONLY .set 0
MAC_ENABLE    .set 1
MAC_CLEAR     .set 3     ; bit1=clear carry, bit0=keep MAC mode

start:
    zero  &r0, 120            ; clear all general-purpose registers

; ------------------------------------------------------------
;  Part 1 – MULTIPLY ONLY (MAC_MODE = 0)
;  Compute: 50 * 25 = 1250 -> R26=0x000004E2, R27=0x00000000
; ------------------------------------------------------------
mpy_demo:
    ldi   r25, MULTIPLY_ONLY
    xout  DEVICE_ID, &r25, 1  ; set multiply-only mode (clears accumulator)

    ldi   r28, 50             ; operand A
    ldi   r29, 25             ; operand B
    nop                       ; 1-cycle latency required before reading result

    xin   DEVICE_ID, &r25, 1  ; read status  -> R25 (bit0=0: MPY, bit1=0: no carry)
    xin   DEVICE_ID, &r26, 4  ; result low   -> R26 = 1250  (0x000004E2)
    xin   DEVICE_ID, &r27, 4  ; result high  -> R27 = 0

; At this point: R26=1250, R27=0, R28=50, R29=25

; ------------------------------------------------------------
;  Part 2 – MULTIPLY AND ACCUMULATE (MAC_MODE = 1)
;  Dot product of (1,2,3) . (4,5,6) = 4 + 10 + 18 = 32
;  -> R26=0x00000020, R27=0x00000000
; ------------------------------------------------------------
mac_demo:
    ; Load vector A into R10-R12, vector B into R13-R15
    ldi   r10, 1
    ldi   r11, 2
    ldi   r12, 3
    ldi   r13, 4
    ldi   r14, 5
    ldi   r15, 6

    ; Clear R28/R29 so init XOUTs below don't accumulate leftover operands
    zero  &r28, 8             ; R28=0, R29=0

    ; Enable accumulate mode (acc += R28*R29 = 0, acc stays 0)
    ldi   r25, MAC_ENABLE
    xout  DEVICE_ID, &r25, 1

    ; Clear carry flag while staying in MAC mode (acc += 0*0 = 0)
    ldi   r25, MAC_CLEAR
    xout  DEVICE_ID, &r25, 1

    ; Reload R25=1 for the accumulate triggers below
    ldi   r25, MAC_ENABLE

    ; acc += R10 * R13  (1 * 4 = 4;  running total = 4)
    mov   r28, r10
    mov   r29, r13
    xout  DEVICE_ID, &r25, 1

    ; acc += R11 * R14  (2 * 5 = 10; running total = 14)
    mov   r28, r11
    mov   r29, r14
    xout  DEVICE_ID, &r25, 1

    ; acc += R12 * R15  (3 * 6 = 18; running total = 32)
    mov   r28, r12
    mov   r29, r15
    xout  DEVICE_ID, &r25, 1

    ; Read accumulated result (32)
    xin   DEVICE_ID, &r25, 1  ; status -> R25 (bit0=1: ACC mode)
    xin   DEVICE_ID, &r26, 4  ; acc low  -> R26 = 32  (0x00000020)
    xin   DEVICE_ID, &r27, 4  ; acc high -> R27 = 0   (fits in 32 bits)

    halt
