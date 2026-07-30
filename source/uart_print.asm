; uart_print.asm — UART TX of a null-terminated string from DRAM0
; Caller must pre-load the string at DRAM0 offset 0 before execution.
; c24 = DRAM0 base  (PRU constant table entry 24, address 0x00000000)
; r4.b0 = current byte offset  (max string length: 255 bytes)
BAUD_COUNT .set 868
TX_BIT     .set 0              ; R30.t0  =  GPO pin 0

    set  r30, r30, TX_BIT   ; idle HIGH

    ldi  r4, 0              ; r4.b0 = DRAM0 byte offset

next_char:
    lbco &r0, c24, r4.b0, 1 ; r0 = DRAM0[r4.b0]
    qbeq done, r0, 0         ; null terminator → stop
    ldi  r1, 8               ; r1 = bit counter

    ; ---- START bit (LOW) ----
    clr  r30, r30, TX_BIT
    ldi  r2, BAUD_COUNT
delay_start:
    sub  r2, r2, 1
    qbne delay_start, r2, 0

    ; ---- 8 data bits, LSB first ----
txloop:
    qbbs bit_high, r0, 0
    clr  r30, r30, TX_BIT
    qba  bit_done
bit_high:
    set  r30, r30, TX_BIT
bit_done:
    lsr  r0, r0, 1
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

    add  r4, r4, 1           ; advance to next byte
    qba  next_char

done:
    halt
