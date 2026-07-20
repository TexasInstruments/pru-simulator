; =============================================================
; pif_eth_tx_n2 — EXPERIMENTAL: n=2 divider (125 Mbaud @ 250 MHz core)
; variant of pif_eth_tx.asm with a batched-load, fixed-shift unrolled
; send_frame loop. See docs/superpowers/specs/2026-07-21-pif-eth-n2-design.md
; for the cycle-budget analysis this implements.
;
; Differences from pif_eth_tx.asm:
;   - TXCFG = 0x00010010 (div=1, frac=0, core clk -> n=2)
;   - send_frame's octet loop loads 4 pre-encoded... no: loads 4 RAW
;     octets per LBBO (core_len assumed a multiple of 4, true for both
;     BERT=132 and UDP=68), still LUT-encodes each octet individually
;     (data-dependent, can't batch), but bit-packs with COMPILE-TIME
;     FIXED shift amounts (0,10,20,30 mod 8 -> 2,4,6,8/0 pattern) instead
;     of the original's runtime nbits-tracked variable shift + a
;     separate push_byte subroutine call.
;   - FIFO-full check ("and r6,r31,0x1C; qbeq ...,r6,0x10") is KEPT on
;     all 5 pushes in this "control" build (all-checks baseline).
;     A second variant (pif_eth_tx_n2_skipchecks.asm) drops the checks
;     on phase 0 and phase 1 to test whether that's safe.
; =============================================================

start:
        ; --- self-configure perif ch0 ---
        ldi  r0, 0x0000             ; GPCFG0 mux_sel=1 (perif mode)
        ldi  r0.w2, 0x0400
        ldi  r1, 0x6008
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4
        ldi  r0, 0x0010            ; TXCFG = 0x00010010 (core clk, div=1 -> n=2)
        ldi  r0.w2, 0x0001
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
; send_frame: stream one continuous burst of core_len bytes, batched
;   4-octet loads + fixed-shift unrolled bit-packing. ret r29
;   PRECONDITION: core_len % 4 == 0 (true for BERT=132, UDP=68)
; -------------------------------------------------------------
send_frame:
        ldi  r17, 0                ; burst pushed byte count
        ; --- 4 leading commas, fixed-shift unrolled (RD sequence from r10=0
        ;     is statically known: comma bit-packing follows the same
        ;     0,2,4,6-remainder cycle as the data loop; r12 (nbits) is kept
        ;     in sync via cheap ldi constants so flush_pad's generic tail
        ;     logic still works correctly) ---
        ; comma 1 (pre-go): phase 0, shift=2, r10 0->1 (RD-)
        ldi  r4, 0x00FA
        ldi  r10, 1
        lsl  r11, r11, 10
        or   r11, r11, r4
        lsr  r5, r11, 2
        and  r5, r5, 0xFF
        ldi  r12, 2
c1:
        and  r6, r31, 0x1C
        qbeq c1, r6, 0x10
        mov  r30.b0, r5
        add  r17, r17, 1

        ldi  r0, 0                 ; tx_go: R31 bit 18
        ldi  r0.w2, 0x0004
        mov  r31, r0

        ; comma 2 (post-go): phase 1, shift=4, r10 1->0 (RD+)
        ldi  r4, 0x0305
        ldi  r10, 0
        lsl  r11, r11, 10
        or   r11, r11, r4
        lsr  r5, r11, 4
        and  r5, r5, 0xFF
        ldi  r12, 4
c2:
        and  r6, r31, 0x1C
        qbeq c2, r6, 0x10
        mov  r30.b0, r5
        add  r17, r17, 1

        ; comma 3 (post-go): phase 2, shift=6, r10 0->1 (RD-)
        ldi  r4, 0x00FA
        ldi  r10, 1
        lsl  r11, r11, 10
        or   r11, r11, r4
        lsr  r5, r11, 6
        and  r5, r5, 0xFF
        ldi  r12, 6
c3:
        and  r6, r31, 0x1C
        qbeq c3, r6, 0x10
        mov  r30.b0, r5
        add  r17, r17, 1

        ; comma 4 (post-go): phase 3, DOUBLE emit shift=8 then 0, r10 1->0 (RD+)
        ldi  r4, 0x0305
        ldi  r10, 0
        lsl  r11, r11, 10
        or   r11, r11, r4
        lsr  r5, r11, 8
        and  r5, r5, 0xFF
c4a:
        and  r6, r31, 0x1C
        qbeq c4a, r6, 0x10
        mov  r30.b0, r5
        add  r17, r17, 1
        and  r5, r11, 0xFF
c4b:
        and  r6, r31, 0x1C
        qbeq c4b, r6, 0x10
        mov  r30.b0, r5
        add  r17, r17, 1
        ldi  r12, 0

        ldi  r21, 0x0500
        ldi  r22, 0
grp_loop:
        qble sf_tail, r22, r9      ; i >= core_len -> done
        lbbo r19, r21, 0, 4        ; load 4 pre-computed... raw octets
        add  r21, r21, 4

        ; ---- octet r19.b0 ----
        lsl  r24, r19.b0, 2
        lbco r7, c24, r24, 4
        qbeq k0, r10, 0
        lsr  r7, r7, 16
k0:
        and  r4, r7, r3
        lsr  r10, r7, 10
        and  r10, r10, 1
        ; phase 0: L=0, +10=10, emit 1 @ shift=2, remainder=2
        lsl  r11, r11, 10
        or   r11, r11, r4
        lsr  r5, r11, 2
        and  r5, r5, 0xFF
        ldi  r12, 2
p0:
        and  r6, r31, 0x1C
        qbeq p0, r6, 0x10          ; full -> wait
        mov  r30.b0, r5
        add  r17, r17, 1

        ; ---- octet r19.b1 ----
        lsl  r24, r19.b1, 2
        lbco r7, c24, r24, 4
        qbeq k1, r10, 0
        lsr  r7, r7, 16
k1:
        and  r4, r7, r3
        lsr  r10, r7, 10
        and  r10, r10, 1
        ; phase 1: L=2, +10=12, emit 1 @ shift=4, remainder=4
        lsl  r11, r11, 10
        or   r11, r11, r4
        lsr  r5, r11, 4
        and  r5, r5, 0xFF
        ldi  r12, 4
p1:
        and  r6, r31, 0x1C
        qbeq p1, r6, 0x10
        mov  r30.b0, r5
        add  r17, r17, 1

        ; ---- octet r19.b2 ----
        lsl  r24, r19.b2, 2
        lbco r7, c24, r24, 4
        qbeq k2, r10, 0
        lsr  r7, r7, 16
k2:
        and  r4, r7, r3
        lsr  r10, r7, 10
        and  r10, r10, 1
        ; phase 2: L=4, +10=14, emit 1 @ shift=6, remainder=6
        lsl  r11, r11, 10
        or   r11, r11, r4
        lsr  r5, r11, 6
        and  r5, r5, 0xFF
        ldi  r12, 6
p2:
        and  r6, r31, 0x1C
        qbeq p2, r6, 0x10
        mov  r30.b0, r5
        add  r17, r17, 1

        ; ---- octet r19.b3 ----
        lsl  r24, r19.b3, 2
        lbco r7, c24, r24, 4
        qbeq k3, r10, 0
        lsr  r7, r7, 16
k3:
        and  r4, r7, r3
        lsr  r10, r7, 10
        and  r10, r10, 1
        ; phase 3: L=6, +10=16, emit TWO @ shift=8 then shift=0, remainder=0
        lsl  r11, r11, 10
        or   r11, r11, r4
        lsr  r5, r11, 8
        and  r5, r5, 0xFF
p3a:
        and  r6, r31, 0x1C
        qbeq p3a, r6, 0x10
        mov  r30.b0, r5
        add  r17, r17, 1
        and  r5, r11, 0xFF         ; shift=0, no lsr needed
p3b:
        and  r6, r31, 0x1C
        qbeq p3b, r6, 0x10
        mov  r30.b0, r5
        add  r17, r17, 1
        ldi  r12, 0

        add  r22, r22, 4
        jmp  grp_loop
sf_tail:
        ; --- 2 trailing commas: shift is statically known (phase 0,1) but
        ;     RD-dependent code must still be picked at runtime (r10's value
        ;     here depends on the actual payload bytes, not known ahead) ---
        ; trailing comma 1: phase 0, shift=2
        qbeq tc1_neg, r10, 0
        ldi  r4, 0x0305
        ldi  r10, 0
        jmp  tc1_push
tc1_neg:
        ldi  r4, 0x00FA
        ldi  r10, 1
tc1_push:
        lsl  r11, r11, 10
        or   r11, r11, r4
        lsr  r5, r11, 2
        and  r5, r5, 0xFF
        ldi  r12, 2
tc1:
        and  r6, r31, 0x1C
        qbeq tc1, r6, 0x10
        mov  r30.b0, r5
        add  r17, r17, 1

        ; trailing comma 2: phase 1, shift=4
        qbeq tc2_neg, r10, 0
        ldi  r4, 0x0305
        ldi  r10, 0
        jmp  tc2_push
tc2_neg:
        ldi  r4, 0x00FA
        ldi  r10, 1
tc2_push:
        lsl  r11, r11, 10
        or   r11, r11, r4
        lsr  r5, r11, 4
        and  r5, r5, 0xFF
        ldi  r12, 4
tc2:
        and  r6, r31, 0x1C
        qbeq tc2, r6, 0x10
        mov  r30.b0, r5
        add  r17, r17, 1

        jal  r28, flush_pad        ; pad remaining bits to a byte
        jal  r28, drain_wait       ; let the burst fully serialize
        ldi  r1, 0x0414
        sbbo r17, r1, 0, 4         ; publish burst pushed count
        jmp  r29

; -------------------------------------------------------------
; push_byte: push r5 to ch0 FIFO, spin while full. ret r27
;   (only used by flush_pad now -- the hot paths above inline everything)
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
