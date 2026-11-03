; =============================================================
; PDM delay-loop producer (PRU1) - test stimulus for idle skip
; =============================================================
; Same pattern word and pin use as pdm_pattern_producer.asm (GPO0
; clock, GPO1 data, PDM_PATTERN shifted out LSB first, one bit per
; clock period, sampled by the rising edge), but each clock half is
; stretched with a NOP delay loop, the way real PDM clock firmware
; (audio_pipe pdm_clk.asm) does it. Most producer cycles are then NOPs
; inside a LOOP, so a lockstep run with idle skip collapses them and
; the consumer's R31 poll can fast-forward across the same span.
;
; Timing (1 cycle per instruction, cycles counted from label LOOP):
;   +3       data <- bit k   (clock low)
;   +4..+24  delay: loop + 20 nops
;   +25      clock rising edge
;   +26..27  bit index update
;   +28..+48 delay: loop + 20 nops
;   +49      clock falling edge (data unchanged)
;   +50      qba LOOP
; One clock period = 51 cycles.
; =============================================================

PDM_PATTERN .set 0xC3A5_9E17

start:
    ldi32 r2, PDM_PATTERN   ; pattern word
    ldi   r3, 0             ; bit index 0..31
    ldi   r30, 0            ; clock low, data low

LOOP:
    lsr   r4, r2, r3        ; r4.t0 = pattern bit k
    and   r4, r4, 1
    lsl   r4, r4, 1         ; move it to GPO1
    mov   r30, r4           ; data <- bit k, clock stays low
    loop  LOW_DLY, 20
    nop
LOW_DLY:
    or    r30, r4, 1        ; RISING edge (data unchanged)
    add   r3, r3, 1
    and   r3, r3, 31        ; wrap the bit index
    loop  HIGH_DLY, 20
    nop
HIGH_DLY:
    mov   r30, r4           ; FALLING edge (data unchanged)
    qba   LOOP
