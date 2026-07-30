; uart_tx.asm — Bit-bang UART TX at 115200 baud (PRU @ 200 MHz)
; Transmits one byte (ASCII 'A' = 0x41) on GPO pin 0 (R30.t0)
; Frame: 8N1  START(0) | b0..b7 LSB-first | STOP(1)
; Timing: 200 MHz / 115200 baud = 1736 cycles/bit
;         Delay loop: SUB + QBNE = 2 cycles/iter  →  BAUD_COUNT = 868
BAUD_COUNT .set 868
TX_CHAR    .set 0x41           ; ASCII 'A'  (patch to change character)
TX_BIT     .set 0              ; R30.t0  =  GPO pin 0

    set  r30, r30, TX_BIT   ; idle line HIGH

    ldi  r0, TX_CHAR        ; r0 = byte to transmit
    ldi  r1, 8              ; r1 = bit counter (8 bits)

    ; ---- START bit (LOW) ----
    clr  r30, r30, TX_BIT
    ldi  r2, BAUD_COUNT
delay_start:
    sub  r2, r2, 1
    qbne delay_start, r2, 0

    ; ---- 8 data bits, LSB first ----
txloop:
    qbbs bit_high, r0, 0    ; jump if bit 0 of r0 is set
    clr  r30, r30, TX_BIT   ; bit = 0  →  drive LOW
    qba  bit_done
bit_high:
    set  r30, r30, TX_BIT   ; bit = 1  →  drive HIGH
bit_done:
    lsr  r0, r0, 1          ; shift right: next bit → position 0
    ldi  r2, BAUD_COUNT
delay_data:
    sub  r2, r2, 1
    qbne delay_data, r2, 0
    sub  r1, r1, 1
    qbne txloop, r1, 0

    ; ---- STOP bit (HIGH) ----
    set  r30, r30, TX_BIT
    ldi  r2, BAUD_COUNT
delay_stop:
    sub  r2, r2, 1
    qbne delay_stop, r2, 0

    halt
