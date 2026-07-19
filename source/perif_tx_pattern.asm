; =============================================================
; Peripheral Interface — continuous TX of a counter pattern (ch0)
; =============================================================
; Streams the byte pattern 0x00,0x01,...,0xFF,0x00,... prefixed by a
; single start bit, in perif CONTINUOUS mode (tx_frame_size == 0).
;
; The RX consumes the first '1' as its start bit, so each pushed byte
; is the pattern pre-shifted right by one:  push_k = carry | (p_k >> 1),
; carry_next = (p_k & 1) << 7, with carry_0 = 0x80 (the start bit).
; The receiver then captures exactly p_0, p_1, ... byte-aligned.
;
; Self-configuring: the prologue writes GPCFG0 mux=1 (perif mode),
; TXCFG (0x260E4) = 0x00070010 (clk_sel=core, div=7 -> 25 MHz bit clock)
; and CH0CFG0 (0x260E8) = 0 (tx_frame_size=0 -> continuous mode).
; Host prerequisite: enable loopback ch0 (harness, not memory-mapped).
;
; Register map: r2=pattern byte p, r3=carry bits, r4=byte to push,
;               r5=R31 status scratch, r6=prefill counter,
;               r0=prologue value scratch / TX-go word, r1=prologue addr scratch
; =============================================================

start:
        ldi  r0, 0x0000         ; GPCFG0: mux_sel=1 (bits 29:26)
        ldi  r0.w2, 0x0400
        ldi  r1, 0x6008
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4
        ldi  r0, 0x0010         ; TXCFG = 0x00070010
        ldi  r0.w2, 0x0007
        ldi  r1, 0x60E4
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4
        ldi  r0, 0              ; CH0CFG0 = 0 (continuous mode)
        ldi  r0.w2, 0
        sbbo r0, r1, 4, 4       ; 0x260E8, full 32-bit clear

        ldi  r30.b2, 0x00       ; select ch0 (byte2 strobe; clk_mode 0)
        ldi  r2, 0              ; p = 0
        ldi  r3, 0x80           ; carry = start bit
        ldi  r6, 0

prefill:                        ; fill the 4-deep FIFO before go
        lsr  r4, r2, 1
        or   r4, r4, r3
        and  r3, r2, 1
        lsl  r3, r3, 7
        add  r2, r2, 1
        and  r2, r2, 0xFF
        mov  r30.b0, r4         ; push (byte0 strobe)
        add  r6, r6, 1
        qbne prefill, r6, 4

        ldi  r0, 0              ; TX go: R31 bit 18
        ldi  r0.w2, 0x0004
        mov  r31, r0

loop:                           ; generate next byte, wait for room, push
        lsr  r4, r2, 1
        or   r4, r4, r3
        and  r3, r2, 1
        lsl  r3, r3, 7
        add  r2, r2, 1
        and  r2, r2, 0xFF
wait_room:
        and  r5, r31, 0x1C      ; ch0 tx_fifo count, bits [4:2]
        qbeq wait_room, r5, 0x10 ; spin while FIFO full (count == 4)
        mov  r30.b0, r4
        jmp  loop
