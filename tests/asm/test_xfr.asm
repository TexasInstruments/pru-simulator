; Test XFR scratchpad write/read
; Expected: r10=0xCAFE after XOUT+XIN via SPAD Bank0 (device 10)
; Note: xfr_offset = (reg_index - 2) * 4, so r2 uses offset 0
    ldi r2, 0xCAFE
    xout 10, &r2, 4        ; write r2 to scratchpad offset 0
    ldi r2, 0              ; clear r2
    xin 10, &r2, 4         ; read scratchpad offset 0 back into r2
    mov r10, r2            ; copy result to r10
    halt
