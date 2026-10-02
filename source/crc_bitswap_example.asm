; ============================================================
;  CRC Bit-Swap Example
;  Demonstrates the bit-mirrored read ports of the PRU ICSSG CRC16/32
;  broadside accelerator (device_id=1):
;    R27  CRC_DATA_8_BFLIP   byte-wide flip: same byte order as CRC_DATA,
;                            each byte's 8 bits individually mirrored
;    R28  CRC_DATA_32_BFLIP  32-bit-wide flip: full 32-bit mirror of
;                            CRC_DATA (bit0<->bit31, ...)
;  (AM64x/AM243x TRM SPRUIM2H Sec 6.4.6.2.2.1, Table 6-429.)
;
;  The accelerator keeps its CRC LSB first (reflected), because the ICSS
;  bus presents data LSB first.  A protocol that wants the CRC in the
;  conventional MSB-first order -- the whole word, or each byte on its
;  own -- can read it pre-reversed from R27/R28 instead of mirroring
;  32 bits in software.  Neither read resets the accumulator; only the
;  R29 (CRC_DATA) read does, so R27 and R28 are read first and R29 last.
;
;  The same 8-byte frame, DE AD BE EF CA FE BA BE, is run through CRC-32
;  twice: once pushed byte-wide (XOUT size 1) and once pushed 32-bit wide
;  (XOUT size 4).  The data write width does not change the result, so
;  both passes must produce identical values.
;
;  Expected results after HALT (both passes):
;    R10 / R13 = 0x8DD43A38   R27  CRC_DATA_8_BFLIP  (each byte mirrored)
;    R11 / R14 = 0x383AD48D   R28  CRC_DATA_32_BFLIP (whole word mirrored)
;    R12 / R15 = 0xB12B5C1C   R29  CRC_DATA          (raw, LSB first)
;
;  Worked through byte by byte (CRC_DATA = 0xB12B5C1C, bytes 1C 5C 2B B1):
;    8-bit flip  : 1C->38  5C->3A  2B->D4  B1->8D   -> 0x8DD43A38
;    32-bit flip : the same mirrored bytes in reverse order -> 0x383AD48D
;  i.e. CRC_DATA_32_BFLIP is CRC_DATA_8_BFLIP with its bytes swapped.
;  (NOT of CRC_DATA, 0x4ED4A3E3, is the Ethernet FCS / zlib.crc32.)
;  In CRC-16 modes only bits [31:16] of the 32-bit flip are valid.
; ============================================================

CRC_XID .set 1

start:
    zero  &r0, 120            ; clear all general-purpose registers

; ------------------------------------------------------------
;  Pass 1 -- CRC-32, data pushed one byte at a time
; ------------------------------------------------------------
byte_pass:
    ldi   r25, 0x01            ; CRC_CFG: bit0=1 -> CRC-32
    xout  CRC_XID, &r25, 1     ; also reloads seed/crc_reg = 0xFFFFFFFF

    ldi   r29.b0, 0xDE
    xout  CRC_XID, &r29, 1     ; byte-wide write
    ldi   r29.b0, 0xAD
    xout  CRC_XID, &r29, 1
    ldi   r29.b0, 0xBE
    xout  CRC_XID, &r29, 1
    ldi   r29.b0, 0xEF
    xout  CRC_XID, &r29, 1
    ldi   r29.b0, 0xCA
    xout  CRC_XID, &r29, 1
    ldi   r29.b0, 0xFE
    xout  CRC_XID, &r29, 1
    ldi   r29.b0, 0xBA
    xout  CRC_XID, &r29, 1
    ldi   r29.b0, 0xBE
    xout  CRC_XID, &r29, 1
    nop                        ; TRM: 1-2 NOPs after the last XOUT
    nop

    xin   CRC_XID, &r27, 4     ; R27: each byte bit-mirrored (non-destructive)
    mov   r10, r27
    xin   CRC_XID, &r28, 4     ; R28: whole word bit-mirrored (non-destructive)
    mov   r11, r28
    xin   CRC_XID, &r29, 4     ; R29: raw CRC -- destructive, so read last
    mov   r12, r29

; ------------------------------------------------------------
;  Pass 2 -- CRC-32, same frame pushed one 32-bit word at a time
; ------------------------------------------------------------
word_pass:
    ldi   r25, 0x01
    xout  CRC_XID, &r25, 1     ; restart the session (seed 0xFFFFFFFF)

    ldi32 r29, 0xEFBEADDE      ; frame[0:4] = DE AD BE EF (LSB first)
    xout  CRC_XID, &r29, 4     ; 32-bit-wide write
    ldi32 r29, 0xBEBAFECA      ; frame[4:8] = CA FE BA BE
    xout  CRC_XID, &r29, 4
    nop
    nop

    xin   CRC_XID, &r27, 4
    mov   r13, r27
    xin   CRC_XID, &r28, 4
    mov   r14, r28
    xin   CRC_XID, &r29, 4
    mov   r15, r29

    halt
