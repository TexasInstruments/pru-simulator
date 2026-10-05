; Runtime-configurable SSI reader used with pru_io.ssi_runtime.SSIRuntime.
; Clock is generated on R30.0; encoder data is sampled on R31.16.
; Config and latest-frame mailbox offsets come from the JSON-generated ABI.

    .include "ssi_config_abi.inc"

CLK_PIN  .set 0
DATA_PIN .set 16

; r0 ABI version, r1 frame width (1-64), r2 clock-delay loop count,
; r3 inter-frame idle loop count. r4/r5 raw sample low/high word,
; r6 bit count, r7 delay scratch, r8-r10 mailbox scratch.

start:
    set  r30, r30, CLK_PIN
    lbco &r0, c28, SSI_CONFIG_OFFSET, 16
    ldi  r10, SSI_ABI_VERSION
    qbne invalid_config, r0, r10
    qbeq invalid_config, r1, 0
    qble invalid_config, r1, 65

    ldi  r8, 0
    sbco &r8, c28, SSI_MAILBOX_SEQUENCE_OFFSET_FROM_SHARED, 4
    sbco &r8, c28, SSI_MAILBOX_RAW_FRAME_LO_OFFSET_FROM_SHARED, 4
    sbco &r8, c28, SSI_MAILBOX_RAW_FRAME_HI_OFFSET_FROM_SHARED, 4
    sbco &r8, c28, SSI_MAILBOX_FRAME_COUNT_OFFSET_FROM_SHARED, 4
    sbco &r8, c28, SSI_MAILBOX_STATUS_OFFSET_FROM_SHARED, 4

frame_start:
    clr  r30, r30, CLK_PIN        ; first falling edge latches the position
    ldi  r4, 0
    ldi  r5, 0
    mov  r6, r1

read_bit:
    set  r30, r30, CLK_PIN        ; rising edge presents the next MSB-first bit
    mov  r7, r2
    qbeq sample_bit, r7, 0
high_delay:
    sub  r7, r7, 1
    qbne high_delay, r7, 0

sample_bit:
    lsl  r5, r5, 1                ; 64-bit shift left: carry bit 31 of r4 into r5
    qbbc no_carry, r4, 31
    add  r5, r5, 1
no_carry:
    lsl  r4, r4, 1
    qbbc bit_zero, r31, DATA_PIN
    add  r4, r4, 1
bit_zero:
    clr  r30, r30, CLK_PIN        ; falling edge starts/refreshes monoflop Tm
    mov  r7, r2
    qbeq bit_done, r7, 0
low_delay:
    sub  r7, r7, 1
    qbne low_delay, r7, 0
bit_done:
    sub  r6, r6, 1
    qbne read_bit, r6, 0

    set  r30, r30, CLK_PIN        ; reader's final fall/high-idle return
    lbco &r8, c28, SSI_MAILBOX_SEQUENCE_OFFSET_FROM_SHARED, 4
    add  r8, r8, 1
    sbco &r8, c28, SSI_MAILBOX_SEQUENCE_OFFSET_FROM_SHARED, 4
    sbco &r4, c28, SSI_MAILBOX_RAW_FRAME_LO_OFFSET_FROM_SHARED, 4
    sbco &r5, c28, SSI_MAILBOX_RAW_FRAME_HI_OFFSET_FROM_SHARED, 4
    lbco &r9, c28, SSI_MAILBOX_FRAME_COUNT_OFFSET_FROM_SHARED, 4
    add  r9, r9, 1
    sbco &r9, c28, SSI_MAILBOX_FRAME_COUNT_OFFSET_FROM_SHARED, 4
    ldi  r10, 0
    sbco &r10, c28, SSI_MAILBOX_STATUS_OFFSET_FROM_SHARED, 4
    add  r8, r8, 1
    sbco &r8, c28, SSI_MAILBOX_SEQUENCE_OFFSET_FROM_SHARED, 4

    mov  r7, r3
    qbeq frame_start, r7, 0
idle_delay:
    sub  r7, r7, 1
    qbne idle_delay, r7, 0
    qba  frame_start

invalid_config:
    ldi  r10, 1
    sbco &r10, c28, SSI_MAILBOX_STATUS_OFFSET_FROM_SHARED, 4
    halt
