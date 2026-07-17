; mvi_gpio_loopback.asm
; Walking-bit GPO → GPI loopback demo using MVIB register-file indirect.
;
; Hardware setup (simulator):
;   1. Load this file.
;   2. In the IO panel, enable "Loopback" groups 3:0 and 7:4.
;   3. Step or Run.
;   4. After 16 steps of the inner loop, R10-R13 mirror R2-R5.
;
; Register layout:
;   R0          iteration counter (0-15)
;   R1.b0       TX pointer — byte address into register file (starts at R2.b0 = 8)
;   R1.b1       RX pointer — byte address into register file (starts at R10.b0 = 40)
;   R2-R5       TX pattern: 16-byte walking-bit sequence (loaded at init)
;   R10-R13     RX capture buffer (filled by MVIB from GPI/R31.b0)
;   R30.b0      GPO byte output (driven by MVIB)
;   R31.b0      GPI byte input  (read by MVIB)

; ---- Initialise TX pattern (walking bit: 0x01 0x02 0x04 0x08 ... 0x80 × 2) ----
START:
    ldi  r2.w0,  0x0201     ; r2.b0=0x01  r2.b1=0x02
    ldi  r2.w2,  0x0804     ; r2.b2=0x04  r2.b3=0x08
    ldi  r3.w0,  0x2010     ; r3.b0=0x10  r3.b1=0x20
    ldi  r3.w2,  0x8040     ; r3.b2=0x40  r3.b3=0x80
    ldi  r4.w0,  0x0201     ; repeat cycle
    ldi  r4.w2,  0x0804
    ldi  r5.w0,  0x2010
    ldi  r5.w2,  0x8040

; ---- Initialise pointers and counter ----
    ldi  r1.b0,  8          ; TX pointer → R2.b0 (byte 8 in register file)
    ldi  r1.b1,  40         ; RX pointer → R10.b0 (byte 40 in register file)
    ldi  r0,     0          ; iteration counter

; ---- Main loop ----
LOOP:
    mvib r30.b0, *r1.b0++   ; GPO byte ← TX pattern[r1.b0]; advance TX ptr
    mvib *r1.b1++, r31.b0   ; RX buf[r1.b1] ← GPI byte (R31.b0); advance RX ptr

    add  r0, r0, 1
    qble RELOAD, r0, 16     ; if r0 >= 16: reload pointers
    qba  LOOP

RELOAD:
    ldi  r0,    0
    ldi  r1.b0, 8           ; reset TX → R2
    ldi  r1.b1, 40          ; reset RX → R10
    qba  LOOP
