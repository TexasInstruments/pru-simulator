Ctx .struct
count .u32
Ctx_len .endstruct
    .sassign r5, Ctx
    ldi r5, 7
    ldi r1, 0
    add r1, r1, Ctx.count
    halt
