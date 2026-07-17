; Test QBA, QBEQ, QBGT, QBBS, LOOP
; Expected: r10=1, r11=1, r12=1, r13=10
    ; Test QBEQ taken
    ldi r1, 5
    qbeq eq_pass, r1, 5
    ldi r10, 0
    qba eq_done
eq_pass:
    ldi r10, 1
eq_done:

    ; Test QBGT taken (branch if OP > Reg1: 10 > 5)
    ldi r2, 5
    qbgt gt_pass, r2, 10
    ldi r11, 0
    qba gt_done
gt_pass:
    ldi r11, 1
gt_done:

    ; Test QBBS (bit 3 of 0x08 is set)
    ldi r3, 0x08
    qbbs bs_pass, r3, 3
    ldi r12, 0
    qba bs_done
bs_pass:
    ldi r12, 1
bs_done:

    ; Test LOOP (count to 10)
    ldi r13, 0
    ldi r0.b0, 10
    loop loop_end, r0.b0
    add r13, r13, 1
loop_end:

    halt
