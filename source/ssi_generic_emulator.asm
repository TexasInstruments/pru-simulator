; Fixed-word SSI slave for the PRU1 reader board-loopback example.
; Connect PRU1 R30.0 to PRU0 R31.0 (clock), and PRU0 R30.16 to
; PRU1 R31.16 (data). The emulated 12-bit position is 0xABC.

CLK_PIN       .set 0
DATA_PIN      .set 16
FRAME_SHIFT   .set 11
FRAME_BITS    .set 12
ENCODER_WORD  .set 0xABC

start:
    ; Wait for the reader's high idle, then its first falling edge.
wait_for_idle:
    qbbc wait_for_idle, r31, CLK_PIN
wait_for_start:
    qbbs wait_for_start, r31, CLK_PIN
    ldi  r2, ENCODER_WORD
    ldi  r3, FRAME_BITS

wait_for_rise:
    qbbc wait_for_rise, r31, CLK_PIN
send_bit:
    lsr  r4, r2, FRAME_SHIFT
    and  r4, r4, 1
    qbeq drive_zero, r4, 0
    set  r30, r30, DATA_PIN
    qba  bit_driven
drive_zero:
    clr  r30, r30, DATA_PIN
bit_driven:
wait_for_fall:
    qbbs wait_for_fall, r31, CLK_PIN
    lsl  r2, r2, 1
    sub  r3, r3, 1
    qbne wait_for_rise, r3, 0
    qba  start
