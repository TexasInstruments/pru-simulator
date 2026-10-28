CALL .macro fn
    jal r28.w0, fn
    .endm
    ldi r14, 1
    call f
    halt
f:  ldi r14, 99
    jmp r28.w0
