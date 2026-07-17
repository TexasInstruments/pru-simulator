; Test XFR scratchpad write/read
; Expected: r10=0xCAFE after XOUT+XIN via SPAD Bank0 (device 10)
; Note: xfr_offset = (reg_index - 2) * 4, so r2 uses offset 0
    fill &r2, 32
    ldi r2, 0xCAFE
    xout 10, &r2, 32       ; write r2 to scratchpad offset 0
    zero &r2, 32           ; clear r2
    xin 10, &r2, 32         ; read scratchpad offset 0 back into r2
    xout 11, &r2, 32
    xout 12, &r2, 32
    xout 15, &r2, 32
    mov r10, r2            ; copy result to r10
    ldi r0.b0, 4           ; shift operation by 4 bytes
    xout  10, &r2, 32      ; shifted out.
    ldi r0.b0, 0
    xin   10, &r2, 32
    
    halt