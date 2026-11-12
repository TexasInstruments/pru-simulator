A .set 7
B .set A / 2
NEG .set -7 / 2
REM .set -7 % 2
M .set ((3 + 5) * 4 - 2) >> 1
N .set ((~0 & 15) ^ 3) | 16
    ldi r1, B
    ldi r2, (NEG + 10)
    ldi r3, (REM + 10)
    ldi r4, M
    ldi r5, N
    halt
