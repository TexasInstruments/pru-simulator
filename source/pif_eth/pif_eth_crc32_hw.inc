; =============================================================
; crc32_core — Ethernet FCS on the ICSSG CRC16/32 broadside accelerator
; (XFR device_id 1; AM64x/AM243x TRM SPRUIM2H 6.4.6.2.2.1, Table 6-429).
; Drop-in replacement for the bit-serial routine in pif_eth_crc32.inc,
; with the same entry/exit contract.  Shared by all pif_eth TX and RX
; firmware.
; =============================================================
; Entry:  r21 = buffer base address, r8 = byte count
; Exit:   r20 = final CRC (already inverted), r21 = base + count
;         (so the caller can read/append the 4 FCS octets at r21)
; Clobbers: r22, r23, r24, r25 (r28/r29 are saved and restored)
; Returns via r26.
;
; The accelerator is programmed through the fixed broadside window
; R25-R29 (R25 CRC_CFG, R28 CRC_SEED, R29 CRC_DATA).  Callers use r28/r29
; as return-address registers, so both are saved in r22/r24 on entry and
; restored before returning.
;
; CRC-32 mode is the reflected IEEE 802.3 polynomial (0xEDB88320), seeded
; with 0xFFFFFFFF by the CRC_CFG write, fed LSB first -- the same
; algorithm as the software loop, minus the final XOR, which is done here.
;
; A session must keep one CRC_DATA write width, so the buffer is pushed as
; count/4 32-bit words, and any 1-3 trailing bytes go through a second,
; byte-wide session seeded (CRC_SEED) with the word session's result.
;
; MUST be included at the END of a program: it is straight-line code and
; would execute at startup if pasted before the entry point.
; =============================================================
CRC_XID .set 1

crc32_core:
    mov  r24, r29               ; save caller's r29 (broadside window)
    mov  r22, r28               ; save caller's r28
    ldi  r25, 0x01              ; CRC_CFG: bit0 = CRC32_ENABLE
    xout CRC_XID, &r25, 1       ; also reloads crc_reg = seed = 0xFFFFFFFF
    lsr  r23, r8, 2             ; whole 32-bit words
    qbeq c3_words_done, r23, 0  ; LOOP with a count of 0 would run once
    loop c3_words_done, r23     ; zero-overhead hardware loop
    lbbo &r29, r21, 0, 4
    xout CRC_XID, &r29, 4       ; word-wide session
    add  r21, r21, 4
c3_words_done:
    nop                         ; TRM: 1-2 NOPs between the last XOUT
    nop                         ;   and the XIN of the result
    xin  CRC_XID, &r29, 4       ; r29 = crc (read resets crc_reg to seed)
    and  r23, r8, 3             ; trailing bytes (0..3)
    qbeq c3_done, r23, 0
    mov  r28, r29
    xout CRC_XID, &r28, 4       ; CRC_SEED = word-session result
    loop c3_tail_done, r23
    lbbo &r29, r21, 0, 1        ; only r29.b0 is sent by the 1-byte XOUT
    xout CRC_XID, &r29, 1       ; byte-wide session
    add  r21, r21, 1
c3_tail_done:
    nop
    nop
    xin  CRC_XID, &r29, 4
c3_done:
    not  r20, r29               ; final XOR 0xFFFFFFFF
    mov  r29, r24               ; restore caller's r28/r29
    mov  r28, r22
    jmp  r26
