; pru0_main.asm - bounded compile-time SSI encoder emulator (PRU0)
; ---------------------------------------------------------------------------
; PRU0 is the reactive encoder end of the simple SSI loopback:
;   PRU1 R30.0 -> PRU0 R31.8  (clock)
;   PRU0 R30.0 -> PRU1 R31.16 (data)
;
; POSITION is read exactly once, at the start of each qualified request. The
; resulting frame is then held in registers and shifted without any further
; memory reads. All field layout and timing values come from the generated
; numeric profile; there is no runtime SSI configuration block.

    .include "ssi_test_abi.inc"
    .include "ssi_build_config.inc"
    .include "AM243_AM64_PRU_pinmux.inc"

AM261       .set 0
CLK_PIN     .set 8
DATA_PIN    .set 0

    .retain
    .retainrefs
    .global main
    .sect ".text"

    ; r0 position (one aligned load per frame, copied from the central load)
    ; r1 normalized frame high, r2 normalized frame low
    ; r3 shift carry/bit scratch, r4 pack/extraction scratch
    ; r5 bits remaining, r6 stop/compare scratch
    ; r7 qualification high-start/deadline, r8 current IEP
    ; r9 expected-edge deadline, r10 complete frames
    ; r11 resynchronizations, r12 aborted frames, r13 edge timeout
    ; r14 encoded position/error scratch, r15 reserved
    ; r16 packed frame high, r17 packed frame low

main:
    .if (!AM261)
    ldi32 r4, PAD_PARTITION0_L0
    ldi32 r5, PAD_PARTITION0_L1
    ldi32 r6, PAD_KEY0
    ldi32 r7, PAD_KEY1
    sbbo  &r6, r4, 0, 4
    sbbo  &r7, r5, 0, 4
    ldi32 r4, PAD_PARTITION1_L0
    ldi32 r5, PAD_PARTITION1_L1
    sbbo  &r6, r4, 0, 4
    sbbo  &r7, r5, 0, 4
    ldi32 r4, PRU_GPO
    ldi32 r5, PRG0_PRU0_GPO0
    sbbo  &r4, r5, 0, 4
    ldi32 r4, PRU_GPI
    ldi32 r5, PRG0_PRU0_GPO8
    sbbo  &r4, r5, 0, 4
    .endif

    set   r30, r30, DATA_PIN
    ldi32 r13, SSI_EDGE_TIMEOUT_CYCLES
    ldi   r10, 0
    ldi   r11, 0
    ldi   r12, 0
    ldi   r4, 0
    sbco  &r4, c24, SSI_PRU0_DONE_OFF, 4
    sbco  &r4, c24, SSI_PRU0_FRAMES_OFF, 4
    sbco  &r4, c24, SSI_PRU0_RESYNCS_OFF, 4
    sbco  &r4, c24, SSI_PRU0_ABORTS_OFF, 4
    sbco  &r4, c24, SSI_PRU0_LAST_POSITION_OFF, 4
    ldi   r4, SSI_READY_RUNNING
    sbco  &r4, c24, SSI_PRU0_READY_OFF, 4

l_wait_reader:
    lbco  &r6, c25, SSI_PRU1_STOP_OFF, 4
    qbne  l_stop, r6, 0
    lbco  &r6, c25, SSI_PRU1_READER_READY_OFF, 4
    qbeq  l_wait_reader, r6, 0

; A fresh request starts only after a continuously observed idle-high interval.
; The low check occurs before and after every IEP read, so a short low pulse
; restarts the interval even if it arrives during the timestamp read.
l_wait_high:
    lbco  &r6, c25, SSI_PRU1_STOP_OFF, 4
    qbne  l_stop, r6, 0
    qbbc  l_wait_high, r31, CLK_PIN
    lbco  &r7, c26, 0x10, 4

    ldi32 r4, SSI_TM_CYCLES
    add   r9, r7, r4
l_qualify:
    lbco  &r6, c25, SSI_PRU1_STOP_OFF, 4
    qbne  l_stop, r6, 0
    qbbc  l_wait_high, r31, CLK_PIN
    lbco  &r8, c26, 0x10, 4
    qbbc  l_wait_high, r31, CLK_PIN
    sub   r6, r8, r9
    qbbs  l_qualify, r6, 31

    ; Qualification ended while CLK was high. A falling edge observed after
    ; this point is the only request edge accepted for this frame.
    add   r9, r8, r13
l_wait_request_falling:
    lbco  &r6, c25, SSI_PRU1_STOP_OFF, 4
    qbne  l_stop, r6, 0
    qbbc  l_frame_start, r31, CLK_PIN
    lbco  &r8, c26, 0x10, 4
    sub   r6, r8, r9
    qbbs  l_wait_request_falling, r6, 31
    qba   l_abort_frame

l_frame_start:
    ; This is the single producer-consumption point for the whole frame.
    lbco  &r2, c24, SSI_PRU0_POSITION_OFF, 4
    mov   r0, r2
    sbco  &r0, c24, SSI_PRU0_LAST_POSITION_OFF, 4

    ; Build a right-aligned frame in r16:r17, with r16 the high word.
    ldi   r16, 0
    ldi   r17, 0
    .if SSI_ENCODING_GRAY
    lsr   r4, r0, 1
    xor   r14, r0, r4
    .else
    mov   r14, r0
    .endif
    ldi32 r4, SSI_POSITION_MASK
    and   r14, r14, r4

    .if SSI_POSITION_LOW_WORD
    lsl   r17, r14, SSI_POSITION_OFFSET
    .endif
    .if SSI_POSITION_CROSSES_32
    lsl   r17, r14, SSI_POSITION_OFFSET
    lsr   r4, r14, 32 - SSI_POSITION_OFFSET
    or    r16, r16, r4
    .endif
    .if SSI_POSITION_HIGH_WORD
    lsl   r16, r14, SSI_POSITION_OFFSET - 32
    .endif

    .if SSI_ERROR_BITS > 0
    ldi32 r14, SSI_ERROR_VALUE
    .if SSI_ERROR_LOW_WORD
    lsl   r4, r14, SSI_ERROR_OFFSET
    or    r17, r17, r4
    .endif
    .if SSI_ERROR_CROSSES_32
    lsl   r4, r14, SSI_ERROR_OFFSET
    or    r17, r17, r4
    lsr   r4, r14, 32 - SSI_ERROR_OFFSET
    or    r16, r16, r4
    .endif
    .if SSI_ERROR_HIGH_WORD
    lsl   r4, r14, SSI_ERROR_OFFSET - 32
    or    r16, r16, r4
    .endif
    .endif

    ; Normalize the selected frame so its first transmitted bit is r1.31.
    .if SSI_FRAME_LOW_WORD
    lsl   r1, r17, 32 - SSI_FRAME_BITS
    ldi   r2, 0
    .endif
    .if SSI_FRAME_FULL_64
    mov   r1, r16
    mov   r2, r17
    .endif
    .if SSI_FRAME_CROSSES_32
    lsl   r1, r16, 64 - SSI_FRAME_BITS
    lsr   r4, r17, SSI_FRAME_BITS - 32
    or    r1, r1, r4
    lsl   r2, r17, 64 - SSI_FRAME_BITS
    .endif

    ; The packing path is bounded by the same expected-edge timeout as the
    ; wire loop. It must finish before the reader's first rising edge.
    lbco  &r8, c26, 0x10, 4
    add   r9, r8, r13
    ldi   r5, SSI_FRAME_BITS

l_drive_bit:
    qbbs  l_drive_one, r1, 31
    clr   r30, r30, DATA_PIN
    qba   l_bit_driven
l_drive_one:
    set   r30, r30, DATA_PIN
l_bit_driven:
    ; Wait for rising edge with an absolute timeout. STOP is intentionally
    ; observed after the active frame: PRU1 owns the clock and finishes the
    ; burst before PRU0 releases DATA.
l_wait_rising:
    qbbs  l_have_rising, r31, CLK_PIN
    lbco  &r8, c26, 0x10, 4
    sub   r6, r8, r9
    qbbs  l_wait_rising, r6, 31
    qba   l_abort_frame

l_have_rising:
    sub   r5, r5, 1
    lbco  &r8, c26, 0x10, 4
    add   r9, r8, r13
    qbeq  l_wait_final_falling, r5, 0

l_wait_bit_falling:
    qbbc  l_have_falling, r31, CLK_PIN
    lbco  &r8, c26, 0x10, 4
    sub   r6, r8, r9
    qbbs  l_wait_bit_falling, r6, 31
    qba   l_abort_frame

l_have_falling:
    lbco  &r8, c26, 0x10, 4
    add   r9, r8, r13
    lsr   r3, r2, 31
    lsl   r1, r1, 1
    or    r1, r1, r3
    lsl   r2, r2, 1
    qba   l_drive_bit

l_wait_final_falling:
    qbbc  l_frame_complete, r31, CLK_PIN
    lbco  &r8, c26, 0x10, 4
    sub   r6, r8, r9
    qbbs  l_wait_final_falling, r6, 31
    qba   l_abort_frame

l_frame_complete:
    set   r30, r30, DATA_PIN
    add   r10, r10, 1
    sbco  &r10, c24, SSI_PRU0_FRAMES_OFF, 4
    lbco  &r6, c25, SSI_PRU1_STOP_OFF, 4
    qbne  l_stop, r6, 0
    qba   l_wait_high

l_abort_frame:
    add   r11, r11, 1
    add   r12, r12, 1
    set   r30, r30, DATA_PIN
    qba   l_wait_high

l_stop:
    set   r30, r30, DATA_PIN
    sbco  &r10, c24, SSI_PRU0_FRAMES_OFF, 4
    sbco  &r11, c24, SSI_PRU0_RESYNCS_OFF, 4
    sbco  &r12, c24, SSI_PRU0_ABORTS_OFF, 4
    ldi   r4, SSI_DONE_COMPLETE
    sbco  &r4, c24, SSI_PRU0_DONE_OFF, 4
    halt
