; Test SBBO/LBBO roundtrip
; Expected: r10=0xDEAD after store+load
    ldi r1, 0x100
    ldi r2, 0xDEAD
    sbbo &r2, r1, 0, 4     ; store r2 (0xDEAD) to addr 0x100
    ldi r2, 0              ; clear r2
    lbbo &r10, r1, 0, 4   ; load from addr 0x100 into r10
    halt
