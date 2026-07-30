; SPDX-License-Identifier: BSD-3-Clause
; Copyright (C) 2024-2025 Texas Instruments Incorporated - http://www.ti.com/



; source/uart_rx_11frame.asm
; PRU0 UART Receiver — 11-byte frame, 4.0 Mbaud @ 200 MHz
; Receives 8N1 UART frames on GPI0 (R31 bit 0)
; Stores 11 bytes packed into R2-R4 then SBCO to DRAM0
; Framing error flag at DRAM0+0x0FFE
;
; Register Map:
;   R0  — unused (reserved)
;   R1  — DRAM0 byte offset for frame storage
;   R2  — frame bytes 0-3 (packed little-endian)
;   R3  — frame bytes 4-7 (packed little-endian)
;   R4  — frame bytes 8-10 (b0=byte8, b1=byte9, b2=byte10)
;   R13 — byte accumulator (current byte being assembled)
;   R14 — bit mask (1, 2, 4, 8, 16, 32, 64, 128)
;   R15 — GPI0 sample value (0 or 1)
;   R16 — byte index counter (0-10)
;   R17 — bit counter (0-7)
;   R18 — delay counter
;   R19 — error flags (bit 0 = framing error, sticky)
;   R20 — frame counter (number of frames received)
;
; Timing:
;   PRU clock = 200 MHz, baud = 4 Mbaud => 50 cycles/bit
;   Sample loop: AND(1) + QBEQ(1) + [OR(1)|NOP(1)] + LSL(1) + ADD(1) + LDI(1)
;                + delay(2*BIT_TIME) + QBNE(1) = 7 + 2*BIT_TIME
;   With BIT_TIME=21: 7 + 42 = 49 cycles (target ~50, within tolerance)
;   Actually: bit=1 path = QBEQ(not taken)+OR = 2 instr before LSL
;             bit=0 path = QBEQ(taken) skips to NOP+JMP...
;   EQUALIZED approach: both paths = 7 overhead + 2*BIT_TIME = 50
;   BIT_TIME = (50 - 7) / 2 = 21.5 => use 22 (gives 51) or 21 (gives 49)
;
;   With equalized paths (both paths = 2 cycles after QBEQ):
;   bit=1: QBEQ(1,not taken) + OR(1)     → 2 cycles → falls to advance_bit
;   bit=0: QBEQ(1,taken) → advance_bit via JMP(1) → 2 cycles
;   Wait — JMP takes 1 cycle but QBEQ-taken also takes 1 cycle.
;
;   Revised equalized design:
;   bit=1: QBEQ bit_is_zero(1, not taken) + OR(1) + JMP advance_bit(1) = 3 instrs
;   bit=0: QBEQ bit_is_zero(1, taken) [now at bit_is_zero] + JMP advance_bit(1) = 2 instrs
;   Still unequal. Need explicit NOP. Or:
;
;   Final approach — keep it simple with accurate delay compensation:
;   Average overhead per bit = 6.5 cycles (6 for bit=0, 7 for bit=1)
;   Use BIT_TIME=22 giving average spacing = 50.5 cycles/bit
;   Over 8 bits: max drift ~4 cycles (well within ±20 tolerance)
;   Key fix: proper inter-byte delay (NEXT_DELAY) to reset alignment

BIT_TIME    .set 22
START_DELAY .set 34
NEXT_DELAY  .set 34
GPI0_MASK   .set 1
ERR_ADDR    .set 0x0FFE

    ; Initialize
    ldi  r1, 0
    ldi  r16, 0
    ldi  r19, 0
    ldi  r20, 0
    ldi  r2, 0
    ldi  r3, 0
    ldi  r4, 0

wait_start:
    ; Poll GPI0 for LOW (start bit)
    and  r15, r31, GPI0_MASK
    qbne wait_start, r15, 0

    ; Start bit detected! Delay to sample mid-first-data-bit
    ldi  r18, START_DELAY
delay_start:
    sub  r18, r18, 1
    qbne delay_start, r18, 0

    ; Now at center of first data bit — begin byte reception
    ldi  r16, 0
    jmp  receive_byte

receive_byte:
    ; Receive 8 data bits for current byte (LSB first)
    ldi  r13, 0
    ldi  r14, 1
    ldi  r17, 0

sample_bit:
    ; Sample GPI0
    and  r15, r31, GPI0_MASK
    qbeq bit_is_zero, r15, 0
    ; Bit is 1: set bit in accumulator
    or   r13, r13, r14
bit_is_zero:
    ; Advance bit mask
    lsl  r14, r14, 1
    add  r17, r17, 1

    ; Delay one bit period (compensates for overhead)
    ldi  r18, BIT_TIME
delay_bit:
    sub  r18, r18, 1
    qbne delay_bit, r18, 0

    ; Check if all 8 bits received
    qbne sample_bit, r17, 8

    ; All 8 bits received — store byte into packed register buffer
    qbeq store_b0,  r16, 0
    qbeq store_b1,  r16, 1
    qbeq store_b2,  r16, 2
    qbeq store_b3,  r16, 3
    qbeq store_b4,  r16, 4
    qbeq store_b5,  r16, 5
    qbeq store_b6,  r16, 6
    qbeq store_b7,  r16, 7
    qbeq store_b8,  r16, 8
    qbeq store_b9,  r16, 9
    qbeq store_b10, r16, 10

store_b0:  mov r2.b0, r13.b0
    jmp check_stop
store_b1:  mov r2.b1, r13.b0
    jmp check_stop
store_b2:  mov r2.b2, r13.b0
    jmp check_stop
store_b3:  mov r2.b3, r13.b0
    jmp check_stop
store_b4:  mov r3.b0, r13.b0
    jmp check_stop
store_b5:  mov r3.b1, r13.b0
    jmp check_stop
store_b6:  mov r3.b2, r13.b0
    jmp check_stop
store_b7:  mov r3.b3, r13.b0
    jmp check_stop
store_b8:  mov r4.b0, r13.b0
    jmp check_stop
store_b9:  mov r4.b1, r13.b0
    jmp check_stop
store_b10: mov r4.b2, r13.b0
    jmp check_stop

check_stop:
    ; The last bit delay positions us near the stop bit.
    ; Verify STOP bit (should be HIGH)
    and  r15, r31, GPI0_MASK
    qbeq framing_error, r15, 0

    ; STOP bit valid — advance to next byte
    add  r16, r16, 1

    ; Check if all 11 bytes received
    qbeq store_frame, r16, 11

    ; Wait for next start bit (falling edge) to resynchronize
wait_next_start:
    and  r15, r31, GPI0_MASK
    qbne wait_next_start, r15, 0

    ; Next start bit detected — delay to center of first data bit
    ldi  r18, NEXT_DELAY
delay_next:
    sub  r18, r18, 1
    qbne delay_next, r18, 0
    jmp  receive_byte

store_frame:
    ; All 11 bytes packed in R2-R4 — store to DRAM0 via single SBCO
    sbco &r2, c24, r1, 11

    ; Advance storage offset for next frame
    add  r1, r1, 11
    add  r20, r20, 1

    ; Clear frame buffer for next frame
    ldi  r2, 0
    ldi  r3, 0
    ldi  r4, 0

    ; Go back to waiting for next frame's start bit
    jmp  wait_start

framing_error:
    ; Set sticky error flag
    ldi  r19, 1

    ; Write error flag to DRAM0+0x0FFE
    ldi  r18, ERR_ADDR
    sbco &r19, c24, r18, 1

    ; Reset byte index and clear buffer
    ldi  r16, 0
    ldi  r2, 0
    ldi  r3, 0
    ldi  r4, 0
    jmp  wait_start
