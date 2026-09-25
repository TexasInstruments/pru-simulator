; ssi_encoder_sequence_emulator_12bit.asm - 12-bit SSI encoder emulator with a repeating test sequence
; Simulator-only PRU1 firmware. Values are latched once per frame:
;   0xABC -> 0xAAA -> 0xBCA -> 0x12A -> 0xCC2 -> repeat
;
; Virtual loopback:
;   PRU0 GPO0 (clock) -> PRU1 GPI16 (CLK_PIN)
;   PRU1 GPO0 (data)  -> PRU0 GPI8  (DATA_PIN)
;
; The value changes only after the previous frame has completed. This keeps
; every SSI transaction stable while still making runtime captures easy to
; distinguish in the Signal Graph.

CLK_PIN       .set    16
DATA_PIN      .set    0
DATA_LENGTH   .set    12
; The reader settles for about 60 cycles before its first falling edge. Keep
; this startup qualification below that interval; later frames still have a
; much longer monoflop pause before the next frame.
PAUSE_THRESH  .set    1

VALUE_ABC     .set    0xABC
VALUE_AAA     .set    0xAAA
VALUE_BCA     .set    0xBCA
VALUE_12A     .set    0x12A
VALUE_CC2     .set    0xCC2

    .retain
    .retainrefs
    .global main
    .sect ".text"

main:
    ldi      r19, 0                    ; sequence index / frame counter
    ldi32    r2, VALUE_ABC
    sbco     &r2, c24, 8, 4            ; current value, PRU1 DRAM offset 8
    zero     &r19, 4
    sbco     &r19, c24, 20, 4          ; frame counter, PRU1 DRAM offset 20
    set      r30, r30, DATA_PIN       ; data idles HIGH

; ---- Wait for the clock idle period and frame start -----------------------
l_restart_sync:
    ldi      r3, PAUSE_THRESH
l_check_high:
    qbbc     l_restart_sync, r31, CLK_PIN
    sub      r3, r3, 1
    qbne     l_check_high, r3, 0
    wbc      r31, CLK_PIN              ; first falling edge starts a frame

; ---- Emit one stable 12-bit MSB-first frame -------------------------------
l_new_frame:
    lbco     &r2, c24, 8, 4
    ldi      r21.w0, DATA_LENGTH-1

l_next_bit:
    lsr      r4.b0, r2, r21.w0
    and      r4.b0, r4.b0, 1
    qbeq     l_data_low, r4.b0, 0
    set      r30, r30, DATA_PIN
    qba      l_data_done
l_data_low:
    clr      r30, r30, DATA_PIN
l_data_done:
    wbs      r31, CLK_PIN              ; wait for rising edge / reader sample
    qbeq     l_eof, r21.w0, 0
    sub      r21, r21, 1
    wbc      r31, CLK_PIN
    qba      l_next_bit

l_eof:
    wbc      r31, CLK_PIN              ; final falling edge ends the frame
    set      r30, r30, DATA_PIN
    add      r19, r19, 1               ; completed frame count
    sbco     &r19, c24, 20, 4

; ---- Select the next value only between frames ----------------------------
    qbeq     l_next_aaa, r19, 1
    qbeq     l_next_bca, r19, 2
    qbeq     l_next_12a, r19, 3
    qbeq     l_next_cc2, r19, 4
    ; Five frames use indexes 0..4. Wrap after the CC2 frame.
    zero     &r19, 4
    ldi32    r2, VALUE_ABC
    qba      l_store_next
l_next_aaa:
    ldi32    r2, VALUE_AAA
    qba      l_store_next
l_next_bca:
    ldi32    r2, VALUE_BCA
    qba      l_store_next
l_next_12a:
    ldi32    r2, VALUE_12A
    qba      l_store_next
l_next_cc2:
    ldi32    r2, VALUE_CC2

l_store_next:
    sbco     &r2, c24, 8, 4
    qba      l_restart_sync
