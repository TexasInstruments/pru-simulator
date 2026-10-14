; uart_print.asm — UART TX of a null-terminated string from DRAM0
; The firmware first writes the string "Hello 2026" to DRAM0 offset 0, then
; transmits it byte by byte until the null terminator.
; c24 = DRAM0 base  (PRU constant table entry 24, address 0x00000000)
; r4.b0 = current byte offset  (max string length: 255 bytes)
BAUD_COUNT .set 868
TX_BIT     .set 0              ; R30.t0  =  GPO pin 0

    set  r30, r30, TX_BIT   ; idle HIGH

    ; ---- write "Hello 2026\0" to DRAM0 (little-endian: first char in low byte) ----
    ldi32 r10, 0x6C6C6548    ; 'H' 'e' 'l' 'l'
    ldi32 r11, 0x3032206F    ; 'o' ' ' '2' '0'
    ldi32 r12, 0x00003632    ; '2' '6' '\0' '\0'
    ldi  r4, 0
    sbco &r10, c24, r4.b0, 12 ; DRAM0[0..11] = r10..r12

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
