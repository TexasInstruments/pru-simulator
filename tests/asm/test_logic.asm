; Test AND, OR, XOR, NOT, LSL, LSR
; Expected: r10=0xF0, r11=0xFFFF, r12=0x0FF0, r13=0xFFFFFF00, r14=16, r15=1
    ldi r1, 0xFFF0
    and r10, r1, 0xFF       ; r10 = 0xFFF0 & 0xFF = 0xF0
    ldi r2, 0xFF00
    ldi r3, 0x00FF
    or  r11, r2, r3         ; r11 = 0xFF00 | 0x00FF = 0xFFFF
    ldi r4, 0xF0F0
    xor r12, r2, r4         ; r12 = 0xFF00 ^ 0xF0F0 = 0x0FF0
    not r13, r3             ; r13 = ~0x00FF = 0xFFFFFF00
    ldi r5, 1
    lsl r14, r5, 4          ; r14 = 1 << 4 = 16
    ldi r6, 0x80
    lsr r15, r6, 7          ; r15 = 0x80 >> 7 = 1
    halt
