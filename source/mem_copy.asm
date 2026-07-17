start:
    ldi   r2, 1
    loop endloop, 100
    sbco &r2, c28, r2.b0, 1
    add  r2, r2, 1
endloop:
    ldi   r2, 1
    loop endloop1, 25
    lbco &r3, c28, r2.b0, 1
    sbco &r3, c24, r2.b0, 1
    add  r2, r2, 1
endloop1:
    halt