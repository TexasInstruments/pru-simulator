start:
    ldi   r0, 0x100              ; CTPPR0.blk_ptr = 0x100: C28 -> Shared RAM (0x10000)
    sbco &r0, c11, 0x28, 4       ; C11 = this core's CTRL block, CTPPR0 at +0x28
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