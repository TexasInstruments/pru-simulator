; ============================================================
;  CRC Accelerator Example
;  Demonstrates the PRU ICSSG CRC16/32 broadside accelerator (device_id=1)
;  running the SAME 8-byte frame through two of its modes.
;
;  Frame: DE AD BE EF CA FE BA BE
;
;  Register map (AM64x/AM243x TRM SPRUIM2H Sec 6.4.6.2.2.1, Table 6-429;
;  same fixed broadside window as the MAC accelerator, R25-R29, selected
;  here by device_id=1 instead of MAC's device_id=0):
;    R25  CRC_CFG    W: bit0=CRC32_ENABLE, bit2=CRC16_MOD_ENABLE
;                       (write also reloads the seed/crc_reg to the mode default)
;    R27  CRC_DATA_8_BFLIP   R: CRC_DATA with each byte's bits individually mirrored
;    R28  CRC_SEED   W(4): overrides the seed value (and reloads crc_reg)
;         CRC_DATA_32_BFLIP  R: full 32-bit mirror of CRC_DATA
;    R29  CRC_DATA   W(1/2/4): pushes data through the engine (fixed width
;                       per session)
;                    R: current CRC value -- reading it resets crc_reg
;                       back to the CRC_SEED state, so read it only once,
;                       after the last chunk of a session has been pushed
;
;  Per the TRM: "Firmware must add 1 to 2 NOPs after the last XOUT to the
;  XIN" -- there is no ready/status register to poll.
;
;  Expected results after HALT:
;    R10 = 0x0000EB93   (CRC-16 standard, half-word-wide writes)
;    R11 = 0xB12B5C1C   (CRC-32, word-wide writes)
; ============================================================

CRC_XID .set 1

start:
    zero  &r0, 120            ; clear all general-purpose registers

; ------------------------------------------------------------
;  Pass 1 -- CRC-16 standard (x^16+x^15+x^2+1)
;  Fed 16 bits (2 bytes) at a time -- exercises the half-word-wide
;  CRC_DATA path, not the byte-wide one (a byte-wide CRC is cheap to
;  do in software via an LBCO-based 256-entry lookup table instead).
; ------------------------------------------------------------
crc16_pass:
    ldi   r25, 0x00            ; bit0=0 -> CRC-16, bit2=0 -> standard poly
    xout  CRC_XID, &r25, 1     ; CRC_CFG write -> also reloads seed/crc_reg=0x0000

    ldi   r29.w0, 0xADDE       ; frame[0:2] = DE AD
    xout  CRC_XID, &r29, 2     ; half-word write -> crc_byten=0011, write+run

    ldi   r29.w0, 0xEFBE       ; frame[2:4] = BE EF
    xout  CRC_XID, &r29, 2

    ldi   r29.w0, 0xFECA       ; frame[4:6] = CA FE
    xout  CRC_XID, &r29, 2

    ldi   r29.w0, 0xBEBA       ; frame[6:8] = BA BE
    xout  CRC_XID, &r29, 2
    nop
    nop

    xin   CRC_XID, &r29, 4     ; r29 = 16-bit CRC-16 result (destructive read)
    mov   r10, r29             ; save off before Pass 2 reuses r29

; ------------------------------------------------------------
;  Pass 2 -- CRC-32 (x^32+x^26+x^23+x^22+x^16+x^12+x^11+x^10+x^8+x^7+x^5+x^4+x^2+x+1)
;  Fed 32 bits (4 bytes) at a time over the same frame.
; ------------------------------------------------------------
crc32_pass:
    ldi   r25, 0x01            ; bit0=1 -> CRC-32
    xout  CRC_XID, &r25, 1     ; CRC_CFG write -> also reloads seed/crc_reg=0xFFFFFFFF

    ldi32 r29, 0xEFBEADDE      ; frame[0:4] = DE AD BE EF
    xout  CRC_XID, &r29, 4     ; word write -> crc_byten=1111, write+run

    ldi32 r29, 0xBEBAFECA      ; frame[4:8] = CA FE BA BE
    xout  CRC_XID, &r29, 4
    nop
    nop

    xin   CRC_XID, &r29, 4     ; r29 = 32-bit CRC-32 result (destructive read)
    mov   r11, r29

    halt
