; =============================================================
; PDM pattern producer (PRU1) - test stimulus for pin-driven SD
; =============================================================
; Drives a PDM-style clock on GPO0 and a data line on GPO1 from the
; fixed 32-bit pattern PDM_PATTERN, shifted out LSB first, one bit
; per clock half-period, repeating forever:
;
;   pattern bit 2k   -> driven while the clock is LOW,  sampled by the
;                       k-th RISING edge  (CLK_INV = 0 sampler)
;   pattern bit 2k+1 -> driven while the clock is HIGH, sampled by the
;                       k-th FALLING edge (CLK_INV = 1 sampler)
;
; (k counts clock periods modulo 16.)  Data never changes in the same
; write as the clock: each data bit is set in the half-period opposite
; to the edge that samples it, so a rising-edge and a falling-edge
; sampler on the same data pin see two different, known bit streams.
;
; Timing (1 cycle per instruction, cycles counted from label LOOP):
;   +3  data <- bit 2k      (clock low)
;   +5  clock rising edge
;   +9  data <- bit 2k+1    (clock high)
;   +12 clock falling edge
;   +13 jmp LOOP
; One clock period = 14 cycles (70 ns at 200 MHz, ~14.3 MHz PDM clock),
; 7 cycles high, 7 cycles low.  The first rising edge is retired by the
; instruction at LOOP+5 of the first pass.
;
; tests/test_sd_pin_input.py mirrors PDM_PATTERN and the timing above
; (PDM_PATTERN, PDM_PERIOD_CYCLES, rising_bits(), falling_bits());
; keep the two in sync.
; =============================================================

PDM_PATTERN .set 0xC3A5_9E17

start:
    ldi32 r2, PDM_PATTERN   ; pattern word
    ldi   r3, 0             ; bit index 0..31
    ldi   r30, 0            ; clock low, data low

LOOP:
    lsr   r4, r2, r3        ; +0  r4.t0 = pattern bit 2k
    and   r4, r4, 1         ; +1
    lsl   r4, r4, 1         ; +2  move it to GPO1
    mov   r30, r4           ; +3  data <- bit 2k, clock stays low
    add   r3, r3, 1         ; +4
    or    r30, r4, 1        ; +5  RISING edge (data unchanged)
    lsr   r4, r2, r3        ; +6  r4.t0 = pattern bit 2k+1
    and   r4, r4, 1         ; +7
    lsl   r4, r4, 1         ; +8
    or    r30, r4, 1        ; +9  data <- bit 2k+1, clock stays high
    add   r3, r3, 1         ; +10
    and   r3, r3, 31        ; +11 wrap the bit index
    mov   r30, r4           ; +12 FALLING edge (data unchanged)
    jmp   LOOP              ; +13
