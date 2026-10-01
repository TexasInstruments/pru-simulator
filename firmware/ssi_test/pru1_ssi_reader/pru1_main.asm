; pru1_main.asm - bounded compile-time SSI clock and reader (PRU1)
; ---------------------------------------------------------------------------
; PRU1 owns the SSI clock and samples PRU0's latched wire frame:
;   PRU1 R30.0 -> PRU0 R31.8  (clock)
;   PRU0 R30.0 -> PRU1 R31.16 (data)
;
; The bit path contains only fixed generated delays, GPIO edges, a sample,
; and the two-word shift. Result decoding and mailbox writes happen after the
; final falling edge, while CLK is idle high. RTU_PRU1, not this core, owns
; IEP initialization.

    .include "ssi_test_abi.inc"
    .include "ssi_build_config.inc"
    .include "AM243_AM64_PRU_pinmux.inc"

AM261       .set 0
CLK_PIN     .set 0
DATA_PIN    .set 16

    .retain
    .retainrefs
    .global main
    .sect ".text"

    ; r0 raw frame low, r1 raw frame high
    ; r2 bits remaining, r3 sampled bit, r4 shift/temp
    ; r5 complete frames, r6 stop/ready scratch
    ; r7 delay counter, r8 delay/extraction scratch
    ; r9 decoded position, r10 decoded error
    ; r11 extraction mask/temp, r12 extraction high part
    ; r13 aborted frames, r14 pin setup scratch, r15 in-frame flag

main:
    .if (!AM261)
    ldi32 r14, PAD_PARTITION0_L0
    ldi32 r15, PAD_PARTITION0_L1
    ldi32 r6, PAD_KEY0
    ldi32 r7, PAD_KEY1
    sbbo  &r6, r14, 0, 4
    sbbo  &r7, r15, 0, 4
    ldi32 r14, PAD_PARTITION1_L0
    ldi32 r15, PAD_PARTITION1_L1
    sbbo  &r6, r14, 0, 4
    sbbo  &r7, r15, 0, 4
    ldi32 r14, PRU_GPO
    ldi32 r15, PRG0_PRU1_GPO0
    sbbo  &r14, r15, 0, 4
    ldi32 r14, PRU_GPI
    ldi32 r15, PRG0_PRU1_GPO16
    sbbo  &r14, r15, 0, 4
    .endif

    ldi   r0, 0
    ldi   r1, 0
    ldi   r5, 0
    ldi   r13, 0
    ldi   r15, 0
    sbco  &r0, c24, SSI_PRU1_READER_DONE_OFF, 4
    sbco  &r0, c24, SSI_PRU1_READER_FRAMES_OFF, 4
    sbco  &r0, c24, SSI_PRU1_READER_RAW_LO_OFF, 4
    sbco  &r0, c24, SSI_PRU1_READER_RAW_HI_OFF, 4
    sbco  &r0, c24, SSI_PRU1_READER_POSITION_OFF, 4
    sbco  &r0, c24, SSI_PRU1_READER_ERRORS_OFF, 4
    sbco  &r0, c24, SSI_PRU1_READER_ABORTS_OFF, 4

    set   r30, r30, CLK_PIN
    ldi   r6, SSI_READY_RUNNING
    sbco  &r6, c24, SSI_PRU1_READER_READY_OFF, 4

l_wait_emulator:
    lbco  &r6, c24, SSI_PRU1_STOP_OFF, 4
    qbne  l_stop, r6, 0
    lbco  &r6, c25, SSI_PRU0_READY_OFF, 4
    qbeq  l_wait_emulator, r6, 0

; Inter-frame pause is measured from the idle-high transition to the next
; request falling edge. The configured reader bookkeeping runs before this
; delay; the small generated remainder keeps the pause bounded.
l_idle_pause:
    ldi   r7, SSI_TP_OUTER_COUNT
l_tp_outer:
    loop  l_tp_inner_done, SSI_TP_INNER_COUNT
    nop
l_tp_inner_done:
    sub   r7, r7, 1
    qbne  l_tp_outer, r7, 0
    .if SSI_TP_REMAINDER_CYCLES > 0
    loop  l_tp_remainder_done, SSI_TP_REMAINDER_CYCLES
    nop
l_tp_remainder_done:
    .endif
    lbco  &r6, c24, SSI_PRU1_STOP_OFF, 4
    qbne  l_stop, r6, 0

l_new_frame:
    ldi   r0, 0
    ldi   r1, 0
    ldi   r2, SSI_FRAME_BITS
    ldi   r15, 1
    clr   r30, r30, CLK_PIN       ; fresh falling edge starts the burst
    loop  l_frame_start_low_done, SSI_FRAME_START_LOW_PAD_CYCLES
    nop
l_frame_start_low_done:

l_bit_loop:
    set   r30, r30, CLK_PIN       ; rising edge presents the next bit
    loop  l_sample_point, SSI_SAMPLE_PAD_CYCLES
    nop
l_sample_point:
    ldi   r3, 0
    qbbs  l_sample_one, r31, DATA_PIN
    qba   l_sample_done
l_sample_one:
    ldi   r3, 1
l_sample_done:
    ; Shift MSB first into a right-aligned 64-bit accumulator.
    lsr   r4, r0, 31
    lsl   r1, r1, 1
    or    r1, r1, r4
    lsl   r0, r0, 1
    or    r0, r0, r3

    loop  l_high_tail_done, SSI_HIGH_REMAINING_PAD_CYCLES
    nop
l_high_tail_done:
    sub   r2, r2, 1
    clr   r30, r30, CLK_PIN       ; falling edge ends the bit
    loop  l_low_done, SSI_LOW_PAD_CYCLES
    nop
l_low_done:
    qbne  l_bit_loop, r2, 0

    set   r30, r30, CLK_PIN       ; final edge returns CLK to idle high
    ldi   r15, 0

; Decode only after the complete frame. The source branches are specialized
; by the generator-visible constants so no variable-width loop is in the wire
; path.
    .if SSI_POSITION_LOW_WORD
    lsr   r9, r0, SSI_POSITION_OFFSET
    .endif
    .if SSI_POSITION_CROSSES_32
    lsr   r9, r0, SSI_POSITION_OFFSET
    ldi32 r11, SSI_POSITION_CROSS_LOW_MASK
    and   r9, r9, r11
    ldi32 r11, SSI_POSITION_CROSS_HIGH_MASK
    and   r12, r1, r11
    lsl   r12, r12, 32 - SSI_POSITION_OFFSET
    or    r9, r9, r12
    .endif
    .if SSI_POSITION_HIGH_WORD
    lsr   r9, r1, SSI_POSITION_OFFSET - 32
    .endif
    ldi32 r11, SSI_POSITION_MASK
    and   r9, r9, r11

    .if SSI_ENCODING_GRAY
    mov   r12, r9
    lsr   r11, r12, 1
    xor   r12, r12, r11
    lsr   r11, r12, 2
    xor   r12, r12, r11
    lsr   r11, r12, 4
    xor   r12, r12, r11
    lsr   r11, r12, 8
    xor   r12, r12, r11
    lsr   r11, r12, 16
    xor   r12, r12, r11
    mov   r9, r12
    ldi32 r11, SSI_POSITION_MASK
    and   r9, r9, r11
    .endif

    .if SSI_ERROR_BITS > 0
    .if SSI_ERROR_LOW_WORD
    lsr   r10, r0, SSI_ERROR_OFFSET
    .endif
    .if SSI_ERROR_CROSSES_32
    lsr   r10, r0, SSI_ERROR_OFFSET
    ldi32 r11, SSI_ERROR_CROSS_LOW_MASK
    and   r10, r10, r11
    ldi32 r11, SSI_ERROR_CROSS_HIGH_MASK
    and   r12, r1, r11
    lsl   r12, r12, 32 - SSI_ERROR_OFFSET
    or    r10, r10, r12
    .endif
    .if SSI_ERROR_HIGH_WORD
    lsr   r10, r1, SSI_ERROR_OFFSET - 32
    .endif
    ldi32 r11, SSI_ERROR_MASK
    and   r10, r10, r11
    .else
    ldi   r10, 0
    .endif

    add   r5, r5, 1
    sbco  &r0, c24, SSI_PRU1_READER_RAW_LO_OFF, 4
    sbco  &r1, c24, SSI_PRU1_READER_RAW_HI_OFF, 4
    sbco  &r9, c24, SSI_PRU1_READER_POSITION_OFF, 4
    sbco  &r10, c24, SSI_PRU1_READER_ERRORS_OFF, 4
    sbco  &r5, c24, SSI_PRU1_READER_FRAMES_OFF, 4
l_reader_frame_published:
    qba   l_idle_pause

l_stop:
    set   r30, r30, CLK_PIN
    qbne  l_stop_counted, r15, 0
    qba   l_stop_publish
l_stop_counted:
    add   r13, r13, 1
l_stop_publish:
    sbco  &r0, c24, SSI_PRU1_READER_RAW_LO_OFF, 4
    sbco  &r1, c24, SSI_PRU1_READER_RAW_HI_OFF, 4
    sbco  &r9, c24, SSI_PRU1_READER_POSITION_OFF, 4
    sbco  &r10, c24, SSI_PRU1_READER_ERRORS_OFF, 4
    sbco  &r5, c24, SSI_PRU1_READER_FRAMES_OFF, 4
    sbco  &r13, c24, SSI_PRU1_READER_ABORTS_OFF, 4
    ldi   r6, SSI_DONE_COMPLETE
    sbco  &r6, c24, SSI_PRU1_READER_DONE_OFF, 4
    halt
