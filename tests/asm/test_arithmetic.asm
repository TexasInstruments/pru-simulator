; Test ADD, SUB, ADC
; Expected: r10=30, r11=10, r12=0xFFFFFFFF, r13=0
    ldi r1, 10
    ldi r2, 20
    add r10, r1, r2         ; r10 = 10 + 20 = 30
    sub r11, r2, r1         ; r11 = 20 - 10 = 10
    ldi r3, 0
    sub r12, r3, 1          ; r12 = 0 - 1 = 0xFFFFFFFF (borrow → carry=0)
    ldi r4, 0
    adc r13, r4, 0          ; r13 = 0 + 0 + carry(0) = 0
    halt
