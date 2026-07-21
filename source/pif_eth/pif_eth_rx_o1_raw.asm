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
        jal  r29, post_frame
        add  r14, r14, 1
        ldi  r3, 0x0F00
        sbbo r14, r3, 0, 4          ; frame counter published LAST, so the
        jmp  frame_loop             ; host never sees a half-written stats block

; -------------------------------------------------------------
; post_frame: capture buffer -> decoded octets at 0x0E00.  ret r29
;
; Walks the captured bytes as a bit stream, taking every 2nd sample
; (phase-insensitive at zero drift), assembling 10-bit symbols and
; decoding them through the LUT.  The symbol grid is anchored on the
; first comma found; octets before that are discarded as pre-alignment.
;
;   r1 cap ptr   r2 cap_bytes   r7 out ptr   r10 rd(0=neg,1=pos)
;   r11 bit acc  r12 nbits      r15 sym_err  r16 aligned flag
;   r19 sample toggle           r20 byte     r21 bit index
;   r22 symbol   r23 LUT entry  r24 LUT offset
; -------------------------------------------------------------
post_frame:
        ldi  r1, 0x0800
        ldi  r3, 0x0F00
        lbbo r2, r3, 4, 4           ; captured_bytes
        ldi  r7, 0x0E00             ; frame output pointer
        ldi  r10, 0                 ; rd = negative
        ldi  r11, 0                 ; bit accumulator
        ldi  r12, 0                 ; nbits held
        ldi  r15, 0                 ; symbol errors
        ldi  r16, 0                 ; aligned = false
        ldi  r19, 0                 ; sample toggle (take every 2nd)

pf_byte:
        qbeq pf_done, r2, 0
        lbbo r20, r1, 0, 1          ; one captured byte = 8 samples
        add  r1, r1, 1
        sub  r2, r2, 1
        ldi  r21, 7                 ; MSB = oldest sample

pf_bit:
        xor  r19, r19, 1            ; toggle; take the sample when it is 1
        qbeq pf_bit_next, r19, 0
        lsr  r6, r20, r21
        and  r6, r6, 1
        lsl  r11, r11, 1
        or   r11, r11, r6
        add  r12, r12, 1
        qbgt pf_bit_next, r12, 10   ; nbits < 10 -> keep filling
        jal  r28, pf_symbol
pf_bit_next:
        qbeq pf_byte, r21, 0
        sub  r21, r21, 1
        jmp  pf_bit

pf_done:
        sbbo r15, r3, 12, 4         ; publish symbol_errors
        jal  r28, rx_crc_check
        jal  r28, rx_ber_check
        jmp  r29

; -------------------------------------------------------------
; pf_symbol: consume the 10 bits in r11 as one symbol.  ret r28
;
; Before alignment (r16==0) the last-10-bits window in r11 is a bit-by-bit
; sliding search: r12 is rewound by 1 (not reset) on every miss, so the very
; next accepted bit re-triggers a check one bit further along, scanning every
; candidate phase until the first comma anchors the grid.  Once aligned, r12
; resets fully to 0 so subsequent symbols tile in clean, non-overlapping
; 10-bit groups from that anchor.
; -------------------------------------------------------------
pf_symbol:
        ldi  r6, 0x03FF
        and  r22, r11, r6           ; 10-bit symbol (always the last 10 bits
                                    ; pushed, regardless of r12 bookkeeping --
                                    ; r11 is a running shift register)
        lsl  r24, r22, 1            ; LUT offset = symbol * 2
        lbco r23, c24, r24, 2       ; decode entry (c24 = own DRAM = DRAM1)

        qbbc ps_bad, r23, 8         ; valid?
        qbbs ps_comma, r23, 11      ; comma -> alignment anchor

        qbeq ps_skip, r16, 0        ; not aligned yet -> discard octet
        and  r6, r23, 0xFF
        sbbo r6, r7, 0, 1           ; store decoded octet
        add  r7, r7, 1

        qbbs ps_rd_done, r23, 9     ; neutral -> RD unchanged
        lsr  r6, r23, 10
        and  r6, r6, 1
        qbne ps_rd_ok, r6, r10      ; must flip RD, else violation
        add  r15, r15, 1
ps_rd_ok:
        mov  r10, r6
ps_rd_done:
        ldi  r12, 0                 ; aligned: tile the next 10 fresh bits
        jmp  r28

ps_comma:
        ldi  r16, 1                 ; symbol grid is now anchored
        lsr  r6, r23, 10
        and  r6, r6, 1
        mov  r10, r6                ; comma always flips RD
        ldi  r12, 0                 ; anchor established: tile from here
        jmp  r28

ps_bad:
        qbeq ps_bad_scan, r16, 0    ; pre-alignment garbage is expected
        add  r15, r15, 1
        ldi  r12, 0                 ; aligned: tile the next 10 fresh bits
        jmp  r28
ps_bad_scan:
        sub  r12, r12, 1            ; still scanning -> slide window by 1 bit
        jmp  r28
ps_skip:
        sub  r12, r12, 1            ; still scanning -> slide window by 1 bit
        jmp  r28

; -------------------------------------------------------------
; rx_crc_check: CRC-32 over the reconstructed payload at 0x0E00,
;   compared against the 4 received FCS octets.  ret r28
;   The inner loop is the shared crc32_core from pif_eth_crc32.inc.
;   r26 is free here (post_frame uses r28/r29 for returns).
; -------------------------------------------------------------
rx_crc_check:
        ldi  r21, 0x0E00
        ldi  r23, 0                 ; crc32_core's LBBO byte-read only strobes
                                    ; r23's low byte (real PRU byte-write
                                    ; semantics); pf_symbol leaves stale
                                    ; LUT-entry garbage in r23's upper bits,
                                    ; which would otherwise XOR into every
                                    ; byte of the CRC.  TX's call site never
                                    ; hits this because r23 is untouched (and
                                    ; thus already 0) before its own call.
        jal  r26, crc32_core        ; -> r20 = computed FCS, r21 = end of payload
        lbbo r25, r21, 0, 4         ; received FCS (little-endian)
        ldi  r6, 0
        qbne rc_store, r20, r25
        ldi  r6, 1                  ; match
rc_store:
        sbbo r6, r3, 16, 4          ; crc_ok
        jmp  r28

; -------------------------------------------------------------
; rx_ber_check: regenerate the BERT payload with the same xorshift32
;   seed and count differing bits against the received payload.  ret r28
;   Skipped (counters zeroed) when mode != 0.
;   r20 prng state  r21 ptr  r22 i  r23 rx byte  r24 xor  r25 tmp
;   r17 bit errors
; -------------------------------------------------------------
rx_ber_check:
        ldi  r17, 0
        ldi  r6, 0
        qbne rb_publish, r18, 0     ; mode != 0 -> not a BERT frame
        mov  r20, r13               ; PRNG state = seed
        ldi  r21, 0x0E00
        ldi  r22, 0
rb_byte:
        qble rb_bits, r22, r8       ; i >= payload_len
        lsl  r25, r20, 13           ; xorshift32
        xor  r20, r20, r25
        lsr  r25, r20, 17
        xor  r20, r20, r25
        lsl  r25, r20, 5
        xor  r20, r20, r25
        lbbo r23, r21, 0, 1
        and  r24, r20, 0xFF
        xor  r24, r24, r23          ; differing bits in this octet
        ldi  r6, 0
rb_pop:
        qbeq rb_popdone, r24, 0
        and  r25, r24, 1
        add  r17, r17, r25
        lsr  r24, r24, 1
        jmp  rb_pop
rb_popdone:
        add  r21, r21, 1
        add  r22, r22, 1
        jmp  rb_byte
rb_bits:
        mov  r13, r20               ; persist PRNG state -> next frame continues
        lsl  r6, r8, 3              ; total_bits = payload_len * 8
rb_publish:
        sbbo r17, r3, 20, 4         ; prng_bit_errors
        sbbo r6, r3, 24, 4          ; total_bits_checked
        jmp  r28

        .include "pif_eth_crc32.inc"
