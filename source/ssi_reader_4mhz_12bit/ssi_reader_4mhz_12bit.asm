; ssi_reader_4mhz_12bit.asm - 12-bit / 4 MHz SSI master (reader), straight binary, MSB-first
; ---------------------------------------------------------------------------
; Canonical SIMULATOR build: no SoC init. Clock + pinmux are configured by the
; separate hardware project. The SSI transaction and register contract match
; the board firmware; the files are intentionally separate because the
; simulator has no SoC initialization.
;
; Pins (AM243x):  SSI CLK   = R30.0  (GPO0)   -> encoder clock in
;                 SSI DATA  = R31.8  (GPI8)   <- encoder data out
; Result:         captured 12-bit word -> DRAM0 offset 16 (4 bytes, little-endian)
;                 frame counter        -> R20
;
; Protocol: idle CLK & DATA HIGH; frame starts on the first FALLING clock edge;
; 12 clock pulses; data shifted MSB-first; sampled during the high phase.
;
; Monoflop tm: datasheet requires 12.5 us <= tm <= 20.5 us.
;
; Clock speed: Firmware targets @300 MHz (3.33 ns/cycle) for exact timing.
; Simulator default is 300 MHz. To override at runtime: Settings > Core Clock Speed.
; Note: 300 MHz gives exact 75 cycles/bit (4.0 MHz SSI). 333 MHz is non-integer (83.3 cyc/bit).
;
; ---------------------------------------------------------------------------

CLK_PIN       .set 0
DATA_IN_PIN   .set 8
DATA_LENGTH   .set 12
RESULT_OFF    .set 16        ; DRAM0 byte offset for the captured word

; ---- @300 MHz (3.33 ns/cycle) - ACTIVE / RECOMMENDED ----
; Why 300 MHz: exact integer ratios (75 cycles/bit = 4.0 MHz SSI, no rounding)
; Timing: 75 cycles/bit x 3.33 ns = 250 ns = 4.0 MHz clock
; Monoflop tm: outer(15) x inner(250) = 3750 cyc x 3.33 ns = 12.5 us (datasheet min)
HIGH_DLY      .set 33        ; high-phase pad loops  (high half = 37 cyc = 123 ns)
LOW_DLY       .set 35        ; low-phase pad loops   (low half  = 38 cyc = 127 ns)
SETTLE_DLY    .set 60        ; idle-high settle before first frame
PAUSE_OUTER   .set 15
PAUSE_INNER   .set 250

; ---- @200 MHz (5 ns/cycle) - ALTERNATIVE / NOT RECOMMENDED ----
; Use this ONLY if forced to 200 MHz core clock. Timing is non-integer rounding.
;HIGH_DLY      .set 20        ; high-phase pad loops  (high half = 24 cyc = 120 ns)
;LOW_DLY       .set 23        ; low-phase pad loops   (low half  = 27 cyc = 135 ns)
;SETTLE_DLY    .set 40        ; idle-high settle before first frame
;PAUSE_OUTER   .set 10
;PAUSE_INNER   .set 250

    .retain
    .retainrefs
    .global main
    .sect ".text"

main:
    ldi   r20, 0                    ; frame counter = 0
    set   r30, r30, CLK_PIN         ; idle SSI clock HIGH
    loop  settle_end, SETTLE_DLY
    nop
settle_end:

new_frame:
    zero  &r2, 4                    ; clear capture accumulator
    ldi   r1, DATA_LENGTH           ; bit counter: 12 -> 1 (position = counter-1)
    clr   r30, r30, CLK_PIN         ; FALLING edge = frame start
    loop  fstart_end, LOW_DLY
    nop
fstart_end:

bit_loop:
    set   r30, r30, CLK_PIN         ; RISING edge -> encoder presents next bit (MSB first)
    loop  high_end, HIGH_DLY
    nop
high_end:
    sub   r1, r1, 1                 ; bit position 11..0
    qbbc  skip_bit, r31, DATA_IN_PIN   ; sample DATA during high phase
    set   r2, r2, r1.b0             ; record a 1 at the MSB-first position
skip_bit:
    clr   r30, r30, CLK_PIN         ; FALLING edge
    loop  low_end, LOW_DLY
    nop
low_end:
    qbne  bit_loop, r1, 0           ; repeat until all 12 bits are clocked

    set   r30, r30, CLK_PIN         ; idle SSI clock HIGH
    sbco  &r2, c24, RESULT_OFF, 4   ; store captured word -> DRAM0[16]
    add   r20, r20, 1               ; frame counter++
    ; Monoflop tm: PRU LOOP max count = 256, so nest two loops.
    ; @300 MHz: outer(15) x inner(250) = 3750 cyc x 3.33 ns = 12.5 us (datasheet min)
    ; @200 MHz: outer(10) x inner(250) = 2500 cyc x 5 ns    = 12.5 us
    ldi   r3, PAUSE_OUTER
pause_outer:
    loop  pause_inner_end, PAUSE_INNER
    nop
pause_inner_end:
    sub   r3, r3, 1
    qbne  pause_outer, r3, 0
    qba   new_frame
