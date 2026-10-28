    ldi r14, 1
    call fn
    halt
fn:
    ldi r14, 99
    ret
