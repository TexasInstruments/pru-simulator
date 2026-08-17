; ssi_encoder_emulator_12bit.asm - fixed-value 12-bit SSI Encoder Emulator (PRU1, @300 MHz)
; Emulates an absolute SSI encoder. Watches PRU0's clock, shifts out 12-bit position MSB-first.
;
; Virtual loopback in simulator:
;   PRU0 R30.0 (CLK out) -> LOOPBACK -> PRU1 R31.16 (CLK in)
;   PRU1 R30.0 (DATA out) -> LOOPBACK -> PRU0 R31.8 (DATA in)
;
; Timing @300 MHz: CLK = 4 MHz (exact 75 cycles/bit). No clock generation - purely reactive to PRU0.
; Protocol: idle CLK & DATA HIGH; frame starts on first FALLING edge; 12 bits MSB-first.
;           DATA changes on FALLING clock edge; master samples DATA on RISING clock edge.

CLK_PIN      .set    16      ; R31.16 - SSI clock in from PRU0 master
DATA_PIN     .set    0       ; R30.0  - SSI data out to PRU0 master
DATA_LENGTH  .set    12      ; bits to emit
DEFAULT_VAL  .set    0xA5A   ; default test position (recognisable pattern)
PAUSE_THRESH .set    300     ; consecutive HIGH samples to confirm idle/pause

    .retain
    .retainrefs
    .global main
    .sect ".text"

main:
    ldi32    r2, DEFAULT_VAL
    sbco     &r2, c24, 8, 4          ; store default test position in DRAM1[8]  (c24 = own DRAM for PRU1)
    zero     &r19, 4
    sbco     &r19, c24, 20, 4        ; clear frame counter in DRAM1[20]
    set      r30, r30, DATA_PIN      ; data idles HIGH

; ---- Wait for clock to stabilise HIGH (pause detect) -----------------------
l_restart_sync:
    ldi      r3, PAUSE_THRESH        ; require this many consecutive high samples
l_check_high:
    qbbc     l_restart_sync, r31, CLK_PIN   ; if clock is LOW, restart
    sub      r3, r3, 1
    qbne     l_check_high, r3, 0     ; count down; must stay HIGH throughout

; ---- Wait for the FIRST FALLING edge = frame start -------------------------
    wbc      r31, CLK_PIN            ; halt until CLK goes LOW (falling edge)

; ---- New frame: load data, drive MSB immediately (CLK is already LOW) ------
l_new_frame:
    lbco     &r2, c24, 8, 4          ; load position to emit from DRAM1[8]
    ldi      r21.w0, DATA_LENGTH-1   ; shift index: 11 (MSB) down to 0

; ---- Bit loop: drive each bit on FALLING clock edge, sample on RISING -------
; Enter here with CLK LOW (first time: frame-start falling edge already detected;
; subsequent times: just returned from wbc at bottom of loop).
l_drive_bit:
    ; Drive bit r21.w0 while CLK is LOW - DATA stable before master raises CLK.
    lsr      r4.b0, r2, r21.w0       ; r4.b0.bit0 = bit at position r21.w0
    and      r4.b0, r4.b0, 1
    qbeq     l_data_low, r4.b0, 0
    set      r30, r30, DATA_PIN
    qba      l_data_done
l_data_low:
    clr      r30, r30, DATA_PIN
l_data_done:

    wbs      r31, CLK_PIN            ; halt until CLK goes HIGH - master samples DATA here
    qbeq     l_eof, r21.w0, 0        ; if this was the last bit, exit
    sub      r21, r21, 1             ; advance to next (lower) bit
    wbc      r31, CLK_PIN            ; halt until CLK goes LOW - drive next bit
    qba      l_drive_bit

l_eof:
    ; Last bit has been sampled on the rising edge - wait for the final falling
    ; edge, then return data to idle HIGH.
    wbc      r31, CLK_PIN            ; halt until CLK goes LOW (last falling edge)
    set      r30, r30, DATA_PIN      ; data back to idle HIGH

    ; Update frame counter
    add      r19, r19, 1
    sbco     &r19, c24, 20, 4

    ; Go back to sync-detect for the next frame
    qba      l_restart_sync
