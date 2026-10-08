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
; (the 24-byte block includes monoflop_ticks), r5 bits left, r6 bit index,
; r7 timeout ticks, r8/r9 last falling timestamp, r10/r11 current time,
; r12/r13 elapsed (64-bit), r14 IEP base.

start:
    ldi32 r14, 0x0002e000
    set r30, r30, DATA_PIN
    ; Wait for the reader's high idle, then its first falling edge.
wait_for_idle:
    qbbc wait_for_idle, r31, CLK_PIN
wait_for_start:
    qbbs wait_for_start, r31, CLK_PIN
    lbco &r0, c28, SSI_EMULATOR_OFFSET, SSI_EMULATOR_SIZE
    ldi  r6, SSI_ABI_VERSION
    qbne invalid_config, r0, r6
    qbeq invalid_config, r1, 0
    qble invalid_config, r1, 65
    mov  r7, r5
    qbeq invalid_config, r7, 0
    mov  r5, r1
    lbbo &r8, r14, 0x10, 8

wait_for_rise:
    ; Expiry wins over an edge observed late after another core advanced IEP.
    lbbo &r10, r14, 0x10, 8
    sub r12, r10, r8
    suc r13, r11, r9
    qbne timeout_abort, r13, 0
    qble timeout_abort, r12, r7
    qbbs send_bit, r31, CLK_PIN
    qba wait_for_rise
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
    lbbo &r10, r14, 0x10, 8
    sub r12, r10, r8
    suc r13, r11, r9
    qbne timeout_abort, r13, 0
    qble timeout_abort, r12, r7
    qbbc falling_edge, r31, CLK_PIN
    qba wait_for_fall
falling_edge:
    lbbo &r8, r14, 0x10, 8
    sub  r5, r5, 1
    qbne wait_for_rise, r5, 0
    ; A completed word holds DATA low through the closing-fall monoflop guard.
    clr r30, r30, DATA_PIN
wait_for_monoflop:
    lbbo &r10, r14, 0x10, 8
    sub r12, r10, r8
    suc r13, r11, r9
    qbne start, r13, 0
    qble start, r12, r7
    qba wait_for_monoflop

timeout_abort:
    ; Discard the partial bit index; require high idle before a new falling start.
    set r30, r30, DATA_PIN
    qba wait_for_idle

invalid_config:
    ldi  r6, 1
    sbco &r6, c28, SSI_EMULATOR_STATUS_OFFSET_FROM_SHARED, 4
    halt
