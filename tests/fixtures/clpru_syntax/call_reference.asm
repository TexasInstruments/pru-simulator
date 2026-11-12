    ldi r14, 1
    jal r3.w2, fn
    halt
fn:
    ldi r14, 99
    jmp r3.w2
