    .text
STORE .macro reg
    sbco &reg, c24, r0, 4
    add r0, r0, 4
    .endm
    ldi r4, 7
    STORE r4
    halt
