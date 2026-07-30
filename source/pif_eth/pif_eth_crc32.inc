; =============================================================
; crc32_core — reflected CRC-32 (poly 0xEDB88320, init/final 0xFFFFFFFF)
; Shared by pif_eth_tx.asm and the pif_eth RX firmware.
; =============================================================
; Entry:  r21 = buffer base address, r8 = byte count
; Exit:   r20 = final CRC (already inverted), r21 = base + count
;         (so the caller can read/append the 4 FCS octets at r21)
; Clobbers: r22, r23 (fully initialised by the routine, not assumed clean
;           on entry), r24, r25
; Returns via r26.
;
; MUST be included at the END of a program: it is straight-line code and
; would execute at startup if pasted before the entry point.
; =============================================================
crc32_core:
    ldi  r20, 0xFFFF
    ldi  r20.w2, 0xFFFF        ; crc = 0xFFFFFFFF
    ldi  r22, 0
    ldi  r23, 0                ; the per-byte lbbo below is a 1-byte load,
                                ; which only strobes r23's low byte -- its
                                ; upper 24 bits must start clean or stale
                                ; data XORs into every CRC byte
c3_byte:
    qble c3_done, r22, r8      ; i >= count
    lbbo &r23, r21, 0, 1
    xor  r20, r20, r23         ; crc ^= byte
    ldi  r24, 0
c3_bit:
    qble c3_bitend, r24, 8     ; j >= 8
    and  r25, r20, 1
    qbeq c3_noxor, r25, 0
    lsr  r20, r20, 1
    ldi  r25, 0x8320
    ldi  r25.w2, 0xEDB8        ; poly 0xEDB88320
    xor  r20, r20, r25
    jmp  c3_bitnext
c3_noxor:
    lsr  r20, r20, 1
c3_bitnext:
    add  r24, r24, 1
    jmp  c3_bit
c3_bitend:
    add  r21, r21, 1
    add  r22, r22, 1
    jmp  c3_byte
c3_done:
    not  r20, r20              ; final XOR 0xFFFFFFFF
    jmp  r26
