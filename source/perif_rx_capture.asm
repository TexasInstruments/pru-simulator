; =============================================================
; Peripheral Interface — RX capture to DRAM1 (ch0), for PRU1
; =============================================================
; Arms RX ch0, then forever: wait for rx_valid (R31 bit 24), store the
; FIFO head byte (R31 byte 0) to DRAM1, pop the FIFO (write R31 bit 24),
; and maintain a received-byte counter.
;
; Memory map (DRAM1): capture buffer 0x2000.. (grows up, max ~8 KB),
;                     received-byte count (u32) at 0x3FF8.
;
; Self-configuring: the prologue writes GPCFG1 mux=1 (perif mode) and
; RXCFG (0x26100) = 0x0007001F (sample_size=7, sb_pol=1, core clk, div=7).
; Host prerequisite: enable loopback ch0 (harness, not memory-mapped).
;
; Register map: r1=buffer pointer (prologue addr scratch first),
;               r2=byte count, r3=count address, r4=received byte,
;               r5=FIFO-pop command word, r0=prologue value scratch
; =============================================================

start:
        ldi  r0, 0x0000         ; GPCFG1: mux_sel=1 (bits 29:26)
        ldi  r0.w2, 0x0400
        ldi  r1, 0x600C
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4
        ldi  r0, 0x001F         ; RXCFG = 0x0007001F
        ldi  r0.w2, 0x0007
        ldi  r1, 0x6100
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4

        ldi  r1, 0x2000         ; capture buffer
        ldi  r2, 0              ; byte count
        ldi  r3, 0x3FF8         ; count address
        ldi  r5, 0
        ldi  r5.w2, 0x0100      ; R31 bit 24 = clr_val ch0 (FIFO pop)
        ldi  r30.b3, 0x01       ; arm RX ch0 (byte3 strobe, bit 24)

poll:
        qbbc poll, r31, 24      ; wait for ch0 rx_valid
        and  r4, r31, 0xFF      ; byte 0 = ch0 RX FIFO head
        sbbo r4, r1, 0, 1       ; store to buffer
        add  r1, r1, 1
        add  r2, r2, 1
        sbbo r2, r3, 0, 4       ; publish count
        mov  r31, r5            ; pop FIFO
        jmp  poll
