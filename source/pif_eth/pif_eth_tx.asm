; =============================================================
; pif_eth — 8b/10b line-coded Ethernet TX over the Peripheral Interface
; Single-core PRU0, channel 0, CONTINUOUS mode.
; =============================================================
; Streams one Ethernet frame per perif "burst".  For each frame the firmware
;   1. (BERT mode) fills the payload with an xorshift32 PRNG, or (preloaded
;      mode) uses the payload the host placed in DRAM0,
;   2. computes the CRC-32 FCS in firmware and appends it,
;   3. 8b/10b-encodes every octet through a 256-entry LUT in DRAM0 (LBCO from
;      c24), tracking running disparity, packs the 10-bit symbols MSB-first
;      into bytes and feeds channel-0's TX FIFO (refilling at FIFO-not-full),
;   4. brackets the frame with K28.5 idle commas and drains the FIFO.
;
; A tiny host handshake (go flag @0x0418) releases one frame at a time so the
; host can capture each burst's serial line before the next tx_go.
;
; DRAM0 map (base 0x0000, reached via c24):
;   0x0000  8b/10b encode LUT, 256 x u32
;   0x0400  num_frames (u32)      0x0404  mode (u32, 0=PRNG, 1=preloaded)
;   0x0408  prng_state/seed (u32) 0x040C  payload_len (u32)
;   0x0410  frame_counter (u32, published)
;   0x0414  burst_pushed bytes (u32, published)
;   0x0418  go flag (u32, host->fw handshake)
;   0x0500  frame core buffer (payload followed by 4-byte FCS)
;
; LUT entry (u32, little-endian) for octet b:
;   [9:0] code10 when RD-   [10] next RD (0/1)
;   [25:16] code10 when RD+  [26] next RD (0/1)
;
; Persistent registers:
;   r8 payload_len  r9 core_len(=payload_len+4)  r10 rd(0=neg,1=pos)
;   r11 bit accumulator  r12 nbits  r13 prng_state  r14 frame_idx
;   r15 num_frames  r16 total_pushed  r17 burst_pushed  r18 mode
; Return-address registers (fixed per nesting level):
;   r29 main->{prng_fill,crc32_compute,send_frame}
;   r28 send_frame->{emit_comma,flush_pad,drain_wait}
;   r26 ->push_symbol   r27 push_symbol->push_byte
; =============================================================

start:
        ; --- self-configure perif ch0 ---
        ldi  r0, 0x0000             ; GPCFG0 mux_sel=1 (perif mode)
        ldi  r0.w2, 0x0400
        ldi  r1, 0x6008
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4
        ldi  r0, 0x0010            ; TXCFG = 0x00070010 (core clk, div=7 -> 25 MHz)
        ldi  r0.w2, 0x0007
        ldi  r1, 0x60E4
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4
        ldi  r0, 0                 ; CH0CFG0 = 0 (continuous mode)
        sbbo r0, r1, 4, 4          ; 0x260E8
        ldi  r30.b2, 0             ; select ch0 via byte2 strobe (no FIFO push)

        ; --- load control block ---
        ldi  r1, 0x0400
        lbbo r15, r1, 0, 4         ; num_frames
        lbbo r18, r1, 4, 4         ; mode
        lbbo r13, r1, 8, 4         ; prng seed/state
        lbbo r8,  r1, 12, 4        ; payload_len
        add  r9, r8, 4             ; core_len = payload_len + 4

        ldi  r3, 0x03FF            ; 10-bit code mask (constant)
        ldi  r10, 0                ; rd = negative
        ldi  r11, 0                ; acc
        ldi  r12, 0                ; nbits
        ldi  r14, 0                ; frame_idx
        ldi  r16, 0                ; total pushed

frame_loop:
        ldi  r1, 0x0418            ; wait for host go flag
hs_wait:
        lbbo r6, r1, 0, 4
        qbeq hs_wait, r6, 0        ; spin while flag == 0
        ldi  r0, 0
        sbbo r0, r1, 0, 4          ; clear flag

        qble all_done, r14, r15    ; frame_idx >= num_frames -> finished
        qbne skip_prng, r18, 0     ; mode != 0 -> payload preloaded
        jal  r29, prng_fill
skip_prng:
        jal  r29, crc32_compute    ; append FCS at 0x0500+payload_len
        jal  r29, send_frame       ; encode + stream one burst

        add  r14, r14, 1
        ldi  r1, 0x0410
        sbbo r14, r1, 0, 4         ; publish frame counter
        jmp  frame_loop

all_done:
        ldi  r1, 0x0410
        sbbo r14, r1, 0, 4
spin:
        jmp  spin

; -------------------------------------------------------------
; prng_fill: write payload_len xorshift32 bytes to 0x0500. ret r29
; -------------------------------------------------------------
prng_fill:
        ldi  r21, 0x0500
        ldi  r22, 0
pf_loop:
        qble pf_done, r22, r8      ; i >= payload_len
        lsl  r25, r13, 13          ; xorshift32
        xor  r13, r13, r25
        lsr  r25, r13, 17
        xor  r13, r13, r25
        lsl  r25, r13, 5
        xor  r13, r13, r25
        sbbo r13, r21, 0, 1        ; store low byte
        add  r21, r21, 1
        add  r22, r22, 1
        jmp  pf_loop
pf_done:
        jmp  r29

; -------------------------------------------------------------
; crc32_compute: reflected CRC-32 over payload_len bytes at 0x0500,
;   append 4 FCS bytes little-endian at 0x0500+payload_len. ret r29
;   r20 crc  r21 ptr  r22 i  r23 byte  r24 j  r25 tmp/poly
; -------------------------------------------------------------
crc32_compute:
        ldi  r20, 0xFFFF
        ldi  r20.w2, 0xFFFF        ; crc = 0xFFFFFFFF
        ldi  r21, 0x0500
        ldi  r22, 0
cc_byte:
        qble cc_done, r22, r8      ; i >= payload_len
        lbbo r23, r21, 0, 1
        xor  r20, r20, r23         ; crc ^= byte
        ldi  r24, 0
cc_bit:
        qble cc_bitend, r24, 8     ; j >= 8
        and  r25, r20, 1
        qbeq cc_noxor, r25, 0      ; (crc & 1) == 0
        lsr  r20, r20, 1
        ldi  r25, 0x8320
        ldi  r25.w2, 0xEDB8        ; poly 0xEDB88320
        xor  r20, r20, r25
        jmp  cc_bitnext
cc_noxor:
        lsr  r20, r20, 1
cc_bitnext:
        add  r24, r24, 1
        jmp  cc_bit
cc_bitend:
        add  r21, r21, 1
        add  r22, r22, 1
        jmp  cc_byte
cc_done:
        not  r20, r20              ; final XOR 0xFFFFFFFF
        sbbo r20, r21, 0, 4        ; append FCS (little-endian)
        jmp  r29

; -------------------------------------------------------------
; send_frame: stream one continuous burst of core_len bytes. ret r29
; -------------------------------------------------------------
send_frame:
        ldi  r17, 0                ; burst pushed byte count
        jal  r28, emit_comma       ; leading comma prefills >=1 FIFO byte
        ldi  r0, 0                 ; tx_go: R31 bit 18
        ldi  r0.w2, 0x0004
        mov  r31, r0
        jal  r28, emit_comma       ; a few idle commas
        jal  r28, emit_comma
        jal  r28, emit_comma

        ldi  r21, 0x0500
        ldi  r22, 0
sf_loop:
        qble sf_tail, r22, r9      ; i >= core_len
        lbbo r23, r21, 0, 1        ; octet
        lsl  r24, r23, 2           ; LUT offset = octet * 4
        lbco r7, c24, r24, 4       ; r7 = LUT word
        qbeq sf_keep, r10, 0       ; rd == 0 -> low half
        lsr  r7, r7, 16            ; rd == 1 -> high half
sf_keep:
        and  r4, r7, r3            ; code10 (mask 0x3FF)
        lsr  r10, r7, 10           ; next rd
        and  r10, r10, 1
        jal  r26, push_symbol
        add  r21, r21, 1
        add  r22, r22, 1
        jmp  sf_loop
sf_tail:
        jal  r28, emit_comma       ; trailing commas delimit the frame
        jal  r28, emit_comma
        jal  r28, flush_pad        ; pad remaining bits to a byte
        jal  r28, drain_wait       ; let the burst fully serialize
        ldi  r1, 0x0414
        sbbo r17, r1, 0, 4         ; publish burst pushed count
        jmp  r29

; -------------------------------------------------------------
; emit_comma: push a K28.5 comma, flip running disparity. ret r28
; -------------------------------------------------------------
emit_comma:
        qbeq ec_neg, r10, 0
        ldi  r4, 0x0305            ; RD+ : 1100000101
        ldi  r10, 0
        jmp  ec_push
ec_neg:
        ldi  r4, 0x00FA            ; RD- : 0011111010
        ldi  r10, 1
ec_push:
        jal  r26, push_symbol
        jmp  r28

; -------------------------------------------------------------
; push_symbol: pack code10 (r4) MSB-first, flush full bytes. ret r26
; -------------------------------------------------------------
push_symbol:
        lsl  r11, r11, 10
        or   r11, r11, r4
        add  r12, r12, 10
ps_flush:
        qble ps_emit, r12, 8       ; nbits >= 8
        jmp  r26
ps_emit:
        sub  r6, r12, 8            ; sh = nbits - 8
        lsr  r5, r11, r6
        and  r5, r5, 0xFF
        sub  r12, r12, 8
        jal  r27, push_byte
        jmp  ps_flush

; -------------------------------------------------------------
; push_byte: push r5 to ch0 FIFO, spin while full. ret r27
; -------------------------------------------------------------
push_byte:
        and  r6, r31, 0x1C         ; tx_fifo count field
        qbeq push_byte, r6, 0x10   ; full (count == 4) -> wait
        mov  r30.b0, r5
        add  r16, r16, 1
        add  r17, r17, 1
        jmp  r27

; -------------------------------------------------------------
; flush_pad: push the (<8) remaining bits left-aligned. ret r28
; -------------------------------------------------------------
flush_pad:
        qbeq fp_done, r12, 0
        ldi  r6, 8
        sub  r6, r6, r12           ; 8 - nbits
        lsl  r5, r11, r6
        and  r5, r5, 0xFF
        ldi  r12, 0
        ldi  r11, 0
        jal  r27, push_byte
fp_done:
        jmp  r28

; -------------------------------------------------------------
; drain_wait: spin until ch0 TX not busy (burst complete). ret r28
; -------------------------------------------------------------
drain_wait:
        and  r6, r31, 0x20         ; busy bit
        qbne drain_wait, r6, 0
        jmp  r28
