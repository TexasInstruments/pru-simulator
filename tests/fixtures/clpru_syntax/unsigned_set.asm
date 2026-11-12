M .set 0xFFFFFFFF
H .set 0x80000000
S .set 0xFFFF0000 >> 16
B .set 31
T .set 1 << B
A .set 0xFFFFFFFF & 0xFF
    ldi32 r1, M
    ldi32 r2, H
    ldi r3, S
    ldi32 r4, T
    ldi r5, A
    halt
