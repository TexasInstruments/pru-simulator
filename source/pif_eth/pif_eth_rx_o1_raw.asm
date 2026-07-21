; =============================================================
; pif_eth — Option 1 RX: realtime SOF/EOF + raw oversample capture (PRU1)
; =============================================================
; Channel 0 RX, 2x oversampled.  The realtime loop stores every captured FIFO
; byte verbatim; all reconstruction happens post-frame.
;
; SOF is the RX hardware's own start-bit: the line idles at 0 and sb_pol=1, so
; capture auto-starts on the burst's first 1 bit.  EOF is TWO consecutive
; all-zero captured bytes -- 16 zero samples.  One zero byte is NOT enough:
; 8b/10b allows a 5-bit run, which at 2x oversampling is 10 zero samples and
; can fill a single byte.
;
; The zero-run check is a second copy of the loop body rather than a counter
; reset, to keep the hot path at 7 instructions.
;
; NOTE: PRU1 sees its OWN DRAM (DRAM1) at core-local 0x0000 and DRAM0 at
; 0x2000, so c24 is its own DRAM -- matching AM243x ICSSG silicon.  All
; addresses below are CORE-LOCAL; the host sees the same bytes at global
; 0x2000 + offset (DRAM1 base).  See core/pru_core.py _map_data_addr.
; DRAM1 map (CORE-LOCAL addresses; host adds 0x2000):
;   0x0000  8b/10b decode LUT, 1024 x u16 (c24 offset 0 = own DRAM)
;   0x0800  raw oversample capture buffer
;   0x0E00  reconstructed frame buffer
;   0x0F00  stats:  +0 frames  +4 cap_bytes  +8 ovf  +12 sym_err
;                   +16 crc_ok +20 bit_err   +24 tot_bits +28 eof_status
;   0x0F40  control: +0 mode  +4 seed  +8 payload_len  +12 go  +16 rxcfg
;
; Persistent registers:
;   r1 capture ptr   r5 FIFO-pop cmd   r8 payload_len   r9 core_len
;   r13 seed         r14 frame counter  r18 mode
; =============================================================

start:
        ldi  r0, 0x0000             ; GPCFG1 mux_sel=1 (perif mode)
        ldi  r0.w2, 0x0400
        ldi  r1, 0x600C
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4

        ldi  r2, 0x0F40             ; control block
        lbbo r0, r2, 16, 4          ; rxcfg (host-supplied, encodes n_rx)
        ldi  r1, 0x6100
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4           ; RXCFG @ 0x26100

        lbbo r18, r2, 0, 4          ; mode
        lbbo r13, r2, 4, 4          ; seed
        lbbo r8,  r2, 8, 4          ; payload_len
        add  r9, r8, 4              ; core_len = payload_len + 4

        ldi  r5, 0                  ; R31 bit24 = clr_val ch0 (FIFO pop)
        ldi  r5.w2, 0x0100
        ldi  r14, 0                 ; frame counter

frame_loop:
        ldi  r2, 0x0F40
go_wait:
        lbbo r6, r2, 12, 4          ; go flag
        qbeq go_wait, r6, 0
        ldi  r0, 0
        sbbo r0, r2, 12, 4          ; clear go

        ldi  r30.b3, 0x01           ; arm RX ch0 -> SOF on first 1 sample
        ldi  r1, 0x0800             ; capture pointer

; --- realtime capture loop: 7 instructions on the hot path ---
poll:
        qbbc poll, r31, 24          ; wait ch0 rx_valid
        and  r4, r31, 0xFF          ; FIFO head byte
        mov  r31, r5                ; pop FIFO
        sbbo r4, r1, 0, 1           ; store raw oversample byte
        add  r1, r1, 1
        qbeq zrun, r4, 0            ; zero byte -> candidate EOF
        jmp  poll
zrun:
        qbbc zrun, r31, 24          ; second byte, same body
        and  r4, r31, 0xFF
        mov  r31, r5
        sbbo r4, r1, 0, 1
        add  r1, r1, 1
        qbeq eof, r4, 0             ; two zero bytes in a row -> EOF
        jmp  poll

eof:
        ldi  r6, 0                  ; sample rx_ovf BEFORE disarming
        qbbc eo_novf, r31, 27       ; ch0 rx_ovf
        ldi  r6, 1
eo_novf:
        ldi  r30.b3, 0x00           ; disarm RX
        ldi  r0, 0
        ldi  r0.w2, 0x0900          ; clr_val(24) | clr_ovf(27)
        mov  r31, r0

        ldi  r3, 0x0F00             ; stats block
        ldi  r0, 0x0801             ; base+1: exclude the 2nd EOF-confirm
                                    ; byte from the reported length.  Both
                                    ; zero bytes are still stored physically
                                    ; (needed to CONFIRM eof, per the 2-byte
                                    ; hazard rule) but the 2nd is pure
                                    ; post-signal idle padding, never real
                                    ; line content; a reader that decodes
                                    ; exactly captured_bytes worth would
                                    ; otherwise absorb it into one extra,
                                    ; spurious all-zero 10b symbol at the
                                    ; tail. Verified empirically across many
                                    ; seeds: dropping this one byte from the
                                    ; reported length is what makes
                                    ; decode_capture's invalid_symbols land
                                    ; on 0 for the true frame content.
        sub  r2, r1, r0             ; captured_bytes = ptr - (base+1)
        sbbo r2, r3, 4, 4
        sbbo r6, r3, 8, 4           ; rx_ovf
        ldi  r0, 1
        sbbo r0, r3, 28, 4          ; eof_status = 1 (clean EOF)
        add  r14, r14, 1
        sbbo r14, r3, 0, 4          ; frame counter
        jmp  frame_loop
