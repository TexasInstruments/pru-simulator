BIT .set 5
WORDS .set 64
LIT .set (1 << 5)
MASK .set (1 << BIT)
DIV .set WORDS / 8
    ldi r1, LIT
    ldi r2, MASK
    ldi r3, DIV
    halt
