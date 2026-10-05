; Runtime-configurable SSI slave for the PRU1 reader board-loopback example.
; Connect PRU1 R30.0 to PRU0 R31.0 (clock), and PRU0 R30.16 to
; PRU1 R31.16 (data). At each frame's first falling clock edge it reads the
; emulator block from the generated ABI (host-packed 1-64 bit frame word),
; then presents it MSB first: one bit after each rising edge.
; An invalid ABI version or frame width writes status 1 and halts.

    .include "ssi_config_abi.inc"

CLK_PIN       .set 0
DATA_PIN      .set 16

; r0 ABI version, r1 frame width, r2/r3 frame word low/high, r4 status
; (the 20-byte block), r5 bits left to send, r6 bit index scratch.

start:
    ; Wait for the reader's high idle, then its first falling edge.
wait_for_idle:
    qbbc wait_for_idle, r31, CLK_PIN
wait_for_start:
    qbbs wait_for_start, r31, CLK_PIN
    lbco &r0, c28, SSI_EMULATOR_OFFSET, 20
    ldi  r6, SSI_ABI_VERSION
    qbne invalid_config, r0, r6
    qbeq invalid_config, r1, 0
    qble invalid_config, r1, 65
    mov  r5, r1

wait_for_rise:
    qbbc wait_for_rise, r31, CLK_PIN
send_bit:
    sub  r6, r5, 1
    qble high_word, r6, 32
    qbbs drive_one, r2, r6
    qba  drive_zero
high_word:
    sub  r6, r6, 32
    qbbc drive_zero, r3, r6
drive_one:
    set  r30, r30, DATA_PIN
    qba  bit_driven
drive_zero:
    clr  r30, r30, DATA_PIN
bit_driven:
wait_for_fall:
    qbbs wait_for_fall, r31, CLK_PIN
    sub  r5, r5, 1
    qbne wait_for_rise, r5, 0
    qba  start

invalid_config:
    ldi  r6, 1
    sbco &r6, c28, SSI_EMULATOR_STATUS_OFFSET_FROM_SHARED, 4
    halt
