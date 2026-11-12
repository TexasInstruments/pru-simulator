CtxType .struct
count .uint
low .ushort
flag .ubyte
last .ubyte
next .uint
Ctx_len .endstruct
Ctx .sassign r5, CtxType
    ldi Ctx.count, 7
    ldi r1, 0
    add r1, r1, Ctx.count
    ldi Ctx.low, 8
    ldi Ctx.flag, 9
    ldi Ctx.last, 10
    ldi Ctx.next, Ctx_len
    halt
