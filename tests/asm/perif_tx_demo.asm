; =============================================================
; Peripheral Interface (3-channel SCU) — TX demo on channel 0
; =============================================================
; Prerequisites (set via the UI "GP Mux" selector + register edits,
; or by the host before running):
;   - GPCFG.PRU_GP_MUX_SEL = 1        (Peripheral mode)
;   - CH0 tx_frame_size configured    (e.g. 8 bits)
;
; R30 layout : [7:0]=TX data, [17:16]=channel sel,
;              [20:19]=clock mode, [26:24]=RX enable
; R31 writes : [18]=TX go, [19]=reinit, [20]=global start
;
; Enable the PRU0 TX ch0 -> core-1 RX ch0 loopback from the panel to
; watch the byte arrive in the core-1 RX FIFO.
; =============================================================

start:
    ldi   r30.b2, 0x00      ; select channel 0, clock mode 0 (byte2 strobe, no FIFO push)
    ldi   r30.b0, 0xB6      ; push 0xB6 into the ch0 TX FIFO (byte0 strobe)

    ; issue TX go: set R31 bit 18 (0x0004_0000) — built in r0 (LDI is 16-bit)
    ldi   r0, 0
    ldi   r0.w2, 0x0004
    mov   r31, r0           ; TX go for the selected channel

spin:
    jmp   spin              ; let the TX clock run out the frame
