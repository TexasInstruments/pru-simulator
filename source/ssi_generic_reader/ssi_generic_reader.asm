; ssi_generic_reader.asm - runtime-configurable SSI encoder reader (PRU1)
; ---------------------------------------------------------------------------
; Generic replacement for the fixed ssi_reader_4mhz_12bit.asm behavior: bit
; width and clock timing are driven by the shared-memory config block
; described in ssi_config_abi.inc / docs/superpowers/specs/
; 2026-08-19-generic-runtime-ssi-design.md instead of hardcoded .set
; constants. The fixed file is untouched.
;
; Unlike ssi_generic_emulator.asm (PRU0, reactive: waits for an external
; clock and needs a debounce/sync mechanism), THIS program is the *active*
; clock master -- it generates its own clock_high_cycles/clock_low_cycles
; timing exactly like the existing fixed reader already does, so there is
; no debounce logic anywhere in this file.
;
; This program is also the one that: extracts structural bit-fields
; (position_value/status_bits) out of the raw frame, publishes the seqlock
; mailbox every frame, and optionally appends 24-byte trace records --
; none of which PRU0 needs to do.
;
; Virtual loopback (same pin convention as ssi_reader_4mhz_12bit.asm /
; ssi_generic_emulator.asm's header comment):
;   this program R30.0  (CLK out)  -> emulator R31.16 (CLK in)
;   emulator     R30.0  (DATA out) -> this program R31.8 (DATA in)
;
; ---------------------------------------------------------------------------
; Register map (r30/r31 are the GPIO pins; everything else is ours to pick).
; Several registers are deliberately reused across a frame's phases once
; their earlier value is no longer needed -- each reuse is called out with
; an inline comment at the point of reuse, matching ssi_generic_emulator.asm's
; own documented reuse of its BITCTR register.
;
;   Persistent config cache, refreshed only in l_apply_config (once per
;   applied generation):
;     r4  APPLIED_GEN       cached requested_generation we have applied
;     r7  TOPOLOGY          cached topology (0=loopback, 1=reader-only)
;     r8  FRAME_WIDTH       cached frame_width_bits (1..64)
;     r9  POS_WIDTH         cached position_width_bits
;     r10 ERR_OFFSET        cached error_offset_bits (0xFFFF sentinel if none)
;     r11 ERR_WIDTH         cached error_width_bits (0 => no error field)
;     r12 CLK_HIGH          cached clock_high_cycles (total high-phase length)
;     r13 CLK_LOW           cached clock_low_cycles
;     r14 SAMPLE_DELAY      cached sample_delay_cycles
;     r15 POS_OFFSET        cached position_offset_bits
;     r16 REMAINING_HIGH    derived: CLK_HIGH - SAMPLE_DELAY (see below)
;     r17 TP_PAUSE_OUTER    cached tp_pause_outer_iters
;     r19 CAPTURE_MODE      cached capture_mode (0/1/2)
;
;   Persistent state, reset in l_apply_config (per-generation semantics):
;     r20 FRAME_COUNTER     frames received since this config took effect
;     r21 TIMESTAMP         estimated elapsed-cycles accumulator (see below)
;
;   Persistent state, NOT reset in l_apply_config -- set once at boot in
;   `main` and never re-initialized, so they survive reconfiguration
;   (capture history and mailbox freshness both outlive a profile switch):
;     r23 TRACE_WRITE_IDX   monotonic trace-buffer write cursor
;     r24 TRACE_OVERRUN     monotonic trace-buffer overrun counter
;     r25 SEQ               persistent seqlock parity counter
;
;   Persistent boot-once constant, never touched again:
;     r26 ONE_REG           1 (register form of the literal 1, needed
;                            because this ISA's LSL/LSR value operand is
;                            exercised here as "shift a variable amount",
;                            and every other example of that pattern in this
;                            codebase shifts a register, not an immediate)
;
;   Per-frame scratch (bit-clock phase), recomputed every frame:
;     r0  RAW_LO            64-bit raw-frame accumulator, low word
;     r1  RAW_HI            64-bit raw-frame accumulator, high word
;     r2  BITCTR            bits still owed this frame, counts to 0;
;                            reused as POS_VALUE for the rest of the frame
;                            once the bit loop is done (see l_extract_done)
;     r3  BITVAL            sampled bit value (0/1) this iteration;
;                            reused as STATUS_BITS for the rest of the frame
;     r5  TMP                generic scratch, reused throughout every phase
;     r6  TMP2               generic scratch / EXT_OFFSET (extraction call)
;
;   Extraction-subroutine scratch (l_extract_field), reused as generic
;   scratch afterward for mailbox/trace address arithmetic:
;     r18 EXT_WIDTH          input param; reused as OFF (address scratch)
;     r22 EXT_RESULT         output; reused as TSINC / a zero constant / FLAGS
;     r27 BIT_START          internal; reused as trace SLOTOFF accumulator
;     r28 EXT_TMP            internal; reused as a second address/shift temp
;     r29 RETLINK            jal return address; reused as LOOPCNT
; ---------------------------------------------------------------------------

    .include "ssi_config_abi.inc"

CLK_PIN       .set 0     ; this program's GPO clock output
DATA_PIN      .set 8     ; this program's GPI data input

; Real (non-reserved) config fields run from SSI_CONFIG_BASE for this many
; bytes; the 172-byte reserved tail is never read. Matches
; ssi_generic_emulator.asm's identical constant.
SSI_CONFIG_REAL_FIELDS_SIZE .set 0x54

; Section base offsets relative to c28 (== SSI_CONFIG_BASE). SBCO/LBCO's
; offset operand is only allowed as an 8-bit (0-255) immediate by this
; assembler, so anything past 0xFF has to be built in a register at runtime
; via ldi+add -- same reasoning as ssi_generic_emulator.asm's
; SSI_FRAMES_OFFSET_FROM_CFG.
SSI_MAILBOX_OFF_FROM_CFG .set 0x200
SSI_CAPTURE_OFF_FROM_CFG .set 0x240
SSI_TRACE_OFF_FROM_CFG   .set 0x400

TRACE_SLOT_COUNT .set 1024   ; must match the design doc's 1,024-slot buffer

; ---- Register aliases (pure text substitution via .asg; see map above) ----
    .asg r4,  APPLIED_GEN
    .asg r7,  TOPOLOGY
    .asg r8,  FRAME_WIDTH
    .asg r9,  POS_WIDTH
    .asg r10, ERR_OFFSET
    .asg r11, ERR_WIDTH
    .asg r12, CLK_HIGH
    .asg r13, CLK_LOW
    .asg r14, SAMPLE_DELAY
    .asg r15, POS_OFFSET
    .asg r16, REMAINING_HIGH
    .asg r17, TP_PAUSE_OUTER
    .asg r19, CAPTURE_MODE

    .asg r20, FRAME_COUNTER
    .asg r21, TIMESTAMP

    .asg r23, TRACE_WRITE_IDX
    .asg r24, TRACE_OVERRUN
    .asg r25, SEQ

    .asg r26, ONE_REG

    .asg r0,  RAW_LO
    .asg r1,  RAW_HI
    .asg r2,  BITCTR
    .asg r3,  BITVAL
    .asg r5,  TMP
    .asg r6,  TMP2

    .asg r18, EXT_WIDTH
    .asg r22, EXT_RESULT
    .asg r27, BIT_START
    .asg r28, EXT_TMP
    .asg r29, RETLINK

    .retain
    .retainrefs
    .global main
    .sect ".text"

main:
    set   r30, r30, CLK_PIN      ; idle SSI clock HIGH before anything else
    ldi   TRACE_WRITE_IDX, 0     ; boot-once: persists across every future
    ldi   TRACE_OVERRUN, 0       ; l_apply_config (capture history and
    ldi   SEQ, 0                 ; mailbox freshness outlive a profile switch)
    ldi   ONE_REG, 1
    qba   l_apply_config

; =============================================================================
; l_apply_config -- (re)load the entire config block, wait for PRU0's ack
; (unless reader-only), ack our own generation, reset per-generation state,
; and fall into the first frame of the new configuration. Reached at boot
; and whenever the idle-boundary generation check (bottom of the main loop)
; notices requested_generation has changed.
; =============================================================================
l_apply_config:
    lbco  &r2, c28, 0, SSI_CONFIG_REAL_FIELDS_SIZE   ; raw config words 0..20 -> r2..r22

    ; -- topology: word5 (@0x14) byte0, no shift needed --
    and   TOPOLOGY, TOPOLOGY, 0xFF

    ; -- word6 (@0x18): frame_width_bits (low16) | position_offset_bits (high16) --
    ; POS_OFFSET must be pulled out before FRAME_WIDTH's mask overwrites it.
    lsr   POS_OFFSET, FRAME_WIDTH, 16
    ldi   TMP2, 0xFFFF
    and   FRAME_WIDTH, FRAME_WIDTH, TMP2

    ; -- word7 (@0x1C): position_width_bits (low16) | singleturn_width_bits (high16, unused) --
    and   POS_WIDTH, POS_WIDTH, TMP2

    ; -- word8 (@0x20): multiturn_width_bits (low16, unused) | error_offset_bits (high16) --
    lsr   ERR_OFFSET, ERR_OFFSET, 16

    ; -- word9 (@0x24): error_width_bits (low16) | padding_width_bits (high16, unused) --
    and   ERR_WIDTH, ERR_WIDTH, TMP2

    ; -- words 10/11/12 (@0x28/0x2C/0x30): clock_high_cycles/clock_low_cycles/
    ;    sample_delay_cycles are standalone u32 words, no extraction needed --

    ; High-phase split: the config-writer promises sample_delay_cycles > 0
    ; and < clock_high_cycles (design doc's field doc for sample_delay_cycles),
    ; so this can never underflow. See the bit loop below for how the two
    ; pieces are used.
    sub   REMAINING_HIGH, CLK_HIGH, SAMPLE_DELAY

    ; -- word15 (@0x3C): tp_pause_outer_iters, standalone, no extraction --

    ; -- word17 (@0x44): sequence_hold_mode (byte0,unused) | fault_mode (byte1,unused)
    ;    | capture_mode (byte2) | reserved (byte3) --
    lsr   CAPTURE_MODE, CAPTURE_MODE, 16
    and   CAPTURE_MODE, CAPTURE_MODE, 0xFF

    ; -- per-generation state reset (frames/time since THIS config applied) --
    ldi   FRAME_COUNTER, 0
    ldi   TIMESTAMP, 0

    ; -- generation handshake: wait for PRU0's ack unless reader-only --
    qbeq  l_skip_pru0_wait, TOPOLOGY, 1
l_wait_pru0_ack:
    lbco  &TMP, c28, SSI_CONFIG_PRU0_ACK_GENERATION_OFF, 4
    qbne  l_wait_pru0_ack, TMP, APPLIED_GEN
l_skip_pru0_wait:
    sbco  &APPLIED_GEN, c28, SSI_CONFIG_PRU1_ACK_GENERATION_OFF, 4

    qba   l_new_frame

; =============================================================================
; l_new_frame -- clock out frame_width_bits bits, sampling DATA
; sample_delay_cycles after each rising edge, and shift the sampled bits
; MSB-first into the 64-bit RAW_LO/RAW_HI accumulator so it ends up
; right-aligned regardless of frame_width_bits. Mirrors
; ssi_reader_4mhz_12bit.asm's new_frame/bit_loop shape, but with every
; timing constant replaced by its config-cache register.
; =============================================================================
l_new_frame:
    ldi   RAW_LO, 0
    ldi   RAW_HI, 0
    mov   BITCTR, FRAME_WIDTH
    clr   r30, r30, CLK_PIN       ; FALLING edge = frame start
    loop  l_fstart_done, CLK_LOW
    nop
l_fstart_done:

l_bit_loop:
    set   r30, r30, CLK_PIN       ; RISING edge -> encoder presents next bit
    loop  l_sample_pt, SAMPLE_DELAY   ; tv-equivalent: wait until data is valid
    nop
l_sample_pt:
    qbbc  l_bit_zero, r31, DATA_PIN
    ldi   BITVAL, 1
    qba   l_bit_shift
l_bit_zero:
    ldi   BITVAL, 0
l_bit_shift:
    ; Shift the 64-bit accumulator left by 1 across both words, then OR the
    ; newly sampled bit into RAW_LO's LSB, MSB-first:
    ;   RAW_HI = (RAW_HI << 1) | (RAW_LO >> 31)
    ;   RAW_LO = (RAW_LO << 1) | new_bit
    lsr   TMP, RAW_LO, 31
    lsl   RAW_HI, RAW_HI, 1
    or    RAW_HI, RAW_HI, TMP
    lsl   RAW_LO, RAW_LO, 1
    or    RAW_LO, RAW_LO, BITVAL

    ; Remainder of the high phase after the sample point, so the total
    ; high-phase duration is exactly clock_high_cycles, matching the config
    ; field's documented meaning ("the SSI clock line stays high per bit").
    loop  l_high_done, REMAINING_HIGH
    nop
l_high_done:
    clr   r30, r30, CLK_PIN       ; FALLING edge
    loop  l_low_done, CLK_LOW
    nop
l_low_done:
    sub   BITCTR, BITCTR, 1
    qbne  l_bit_loop, BITCTR, 0

    set   r30, r30, CLK_PIN       ; idle CLK high again between frames

; =============================================================================
; Structural field extraction: position_value from position_offset_bits/
; position_width_bits, and status_bits from error_offset_bits/error_width_bits
; (skipped -- status_bits forced to 0 -- when error_width_bits == 0, since
; error_offset_bits may be the 0xFFFF "no error field" sentinel in that case).
; =============================================================================
    mov   TMP2, POS_OFFSET         ; TMP2 = EXT_OFFSET param
    mov   EXT_WIDTH, POS_WIDTH
    jal   RETLINK, l_extract_field
    mov   r2, EXT_RESULT           ; r2 (was BITCTR) now holds POS_VALUE

    qbeq  l_status_zero, ERR_WIDTH, 0
    mov   TMP2, ERR_OFFSET
    mov   EXT_WIDTH, ERR_WIDTH
    jal   RETLINK, l_extract_field
    mov   r3, EXT_RESULT           ; r3 (was BITVAL) now holds STATUS_BITS
    qba   l_extract_done
l_status_zero:
    ldi   r3, 0                    ; r3 (was BITVAL) now holds STATUS_BITS
l_extract_done:

; =============================================================================
; Frame counter + estimated-timestamp accumulator. This ISA has no
; free-running cycle counter readable by a running program, so
; timestamp_cycles is a documented *approximation* (same framing as
; ssi_generic_emulator.asm's hold-time estimate): frame_width_bits *
; (clock_high_cycles + clock_low_cycles) + tp_pause_outer_iters *
; SSI_PAUSE_INNER_ITERS, added to the running accumulator every completed
; frame. Not exact wall-clock cycles, but monotonic and deterministic.
;
; This ISA also has no multiply instruction (confirmed by
; ssi_generic_emulator.asm's own comment on its hold-time-estimate shift
; trick). Unlike that estimate, this one is specified as an exact product of
; two runtime values that aren't powers of two, so it can't be approximated
; away with a shift. Both factors here are small and already bounded by the
; hardware LOOP instruction's own 256-iteration limit (frame_width_bits is
; 1..64; SSI_PAUSE_INNER_ITERS is the fixed 250), so LOOP itself is used as
; an exact, bounded repeated-addition multiply instead of a hand-rolled
; counting loop.
; =============================================================================
    add   FRAME_COUNTER, FRAME_COUNTER, 1

    add   TMP, CLK_HIGH, CLK_LOW      ; TMP = clock_high_cycles + clock_low_cycles
    ldi   EXT_TMP, 0                  ; EXT_TMP (was subroutine scratch) = TSINC
    loop  l_ts_mul1_done, FRAME_WIDTH
    add   EXT_TMP, EXT_TMP, TMP
l_ts_mul1_done:
    loop  l_ts_mul2_done, SSI_PAUSE_INNER_ITERS
    add   EXT_TMP, EXT_TMP, TP_PAUSE_OUTER
l_ts_mul2_done:
    add   TIMESTAMP, TIMESTAMP, EXT_TMP

; =============================================================================
; Mailbox publish (seqlock). Runs every frame regardless of capture_mode --
; the mailbox is always "latest sample"; capture_mode only gates the trace
; buffer below.
; =============================================================================
    add   SEQ, SEQ, 1                 ; odd: write in progress
    ldi   EXT_WIDTH, SSI_MAILBOX_OFF_FROM_CFG
    add   EXT_WIDTH, EXT_WIDTH, SSI_MAILBOX_SEQ_OFF
    sbco  &SEQ, c28, EXT_WIDTH, 4

    ldi   EXT_WIDTH, SSI_MAILBOX_OFF_FROM_CFG
    add   EXT_WIDTH, EXT_WIDTH, SSI_MAILBOX_RAW_FRAME_OFF
    sbco  &RAW_LO, c28, EXT_WIDTH, 8            ; RAW_LO/RAW_HI adjacent registers

    ldi   EXT_WIDTH, SSI_MAILBOX_OFF_FROM_CFG
    add   EXT_WIDTH, EXT_WIDTH, SSI_MAILBOX_POSITION_VALUE_OFF
    sbco  &r2, c28, EXT_WIDTH, 4                ; r2 = POS_VALUE

    ldi   EXT_WIDTH, SSI_MAILBOX_OFF_FROM_CFG
    add   EXT_WIDTH, EXT_WIDTH, SSI_MAILBOX_STATUS_BITS_OFF
    sbco  &r3, c28, EXT_WIDTH, 4                ; r3 = STATUS_BITS

    ldi   EXT_WIDTH, SSI_MAILBOX_OFF_FROM_CFG
    add   EXT_WIDTH, EXT_WIDTH, SSI_MAILBOX_FRAME_COUNTER_OFF
    sbco  &FRAME_COUNTER, c28, EXT_WIDTH, 4

    ldi   EXT_WIDTH, SSI_MAILBOX_OFF_FROM_CFG
    add   EXT_WIDTH, EXT_WIDTH, SSI_MAILBOX_TIMESTAMP_CYCLES_OFF
    ldi   EXT_RESULT, 0                          ; zero high word of the u64
    sbco  &TIMESTAMP, c28, EXT_WIDTH, 8          ; TIMESTAMP/EXT_RESULT adjacent

    add   SEQ, SEQ, 1                 ; even: write complete, stable
    ldi   EXT_WIDTH, SSI_MAILBOX_OFF_FROM_CFG
    add   EXT_WIDTH, EXT_WIDTH, SSI_MAILBOX_SEQ_OFF
    sbco  &SEQ, c28, EXT_WIDTH, 4

; =============================================================================
; Trace buffer (only when capture_mode == 2). A fixed handful of extra sbco's
; per frame, done after the mailbox publish above -- never blocks the clock
; generation/sampling timing.
; =============================================================================
    qbne  l_no_trace, CAPTURE_MODE, 2

    ; slot byte offset = (trace_write_index % 1024) * SSI_TRACE_RECORD_SIZE(24)
    ; 1024 is a power of two -> "% 1024" is "& 1023"; 24 = (n<<3)+(n<<4), the
    ; usual no-multiply-ISA trick (matches ssi_generic_emulator.asm's DEBOUNCE
    ; and HOLD_TIME shift-based approximations, just exact here since 24 is a
    ; sum of two powers of two).
    ldi   TMP, 1023
    and   BIT_START, TRACE_WRITE_IDX, TMP        ; BIT_START (free) = idx & 1023
    lsl   TMP, BIT_START, 3
    lsl   TMP2, BIT_START, 4
    add   BIT_START, TMP, TMP2                    ; BIT_START = slot * 24
    ldi   TMP, SSI_TRACE_OFF_FROM_CFG
    add   BIT_START, BIT_START, TMP               ; BIT_START = absolute record offset

    mov   EXT_WIDTH, BIT_START
    ldi   EXT_RESULT, 0
    sbco  &TIMESTAMP, c28, EXT_WIDTH, 8           ; +0x0: timestamp_cycles (u64)
    add   EXT_WIDTH, EXT_WIDTH, 8
    sbco  &RAW_LO, c28, EXT_WIDTH, 8              ; +0x8: raw_frame (u64)
    add   EXT_WIDTH, EXT_WIDTH, 8
    sbco  &r2, c28, EXT_WIDTH, 4                  ; +0x10: position_value (u32)
    add   EXT_WIDTH, EXT_WIDTH, 4
    sbco  &r3, c28, EXT_WIDTH, 2                  ; +0x14: status_bits (u16, truncated
                                                    ; per the trace record's ABI layout)
    add   EXT_WIDTH, EXT_WIDTH, 2

    ldi   EXT_TMP, 0
    qbeq  l_flags_done, r3, 0
    ldi   EXT_TMP, 1
l_flags_done:
    sbco  &EXT_TMP, c28, EXT_WIDTH, 2             ; +0x16: flags (bit0), +0x17: reserved=0

    ; Overrun test uses the PRE-increment write index, then persist both
    ; counters (they're the host-visible source of truth, not just local
    ; bookkeeping).
    ldi   TMP, TRACE_SLOT_COUNT
    qbgt  l_no_overrun, TRACE_WRITE_IDX, TMP   ; taken (skip) if TMP(1024) > TRACE_WRITE_IDX
    add   TRACE_OVERRUN, TRACE_OVERRUN, 1      ; falls through when TRACE_WRITE_IDX >= 1024
l_no_overrun:
    add   TRACE_WRITE_IDX, TRACE_WRITE_IDX, 1

    ldi   TMP, SSI_CAPTURE_OFF_FROM_CFG
    sbco  &TRACE_WRITE_IDX, c28, TMP, 4
    add   TMP, TMP, SSI_CAPTURE_TRACE_OVERRUN_COUNT_OFF
    sbco  &TRACE_OVERRUN, c28, TMP, 4
l_no_trace:

; =============================================================================
; Inter-frame idle: hold the clock high for tp_pause_outer_iters * 250,
; matching ssi_reader_4mhz_12bit.asm's PAUSE_OUTER/PAUSE_INNER nested-loop
; shape with the outer count taken from config instead of a .set constant.
; =============================================================================
    mov   RETLINK, TP_PAUSE_OUTER      ; RETLINK (free) = LOOPCNT
l_pause_outer:
    loop  l_pause_inner_done, SSI_PAUSE_INNER_ITERS
    nop
l_pause_inner_done:
    sub   RETLINK, RETLINK, 1
    qbne  l_pause_outer, RETLINK, 0

; =============================================================================
; Idle-boundary generation check: cheap single 4-byte read, once per frame.
; =============================================================================
    lbco  &TMP, c28, SSI_CONFIG_REQUESTED_GENERATION_OFF, 4
    qbne  l_apply_config, TMP, APPLIED_GEN
    qba   l_new_frame

; =============================================================================
; l_extract_field -- shared subroutine (single-level jal/jmp, never nested):
; given a field's (offset_bits, width_bits) in (TMP2, EXT_WIDTH), counted
; from the frame's first-clocked (MSB) bit, extract that field out of the
; 64-bit right-aligned RAW_LO/RAW_HI accumulator into EXT_RESULT.
;
; bit_start = frame_width_bits - offset_bits - width_bits (LSB-relative).
; Three cases, exactly per the design brief -- the standard composed-64-bit
; right-shift technique:
;   1. bit_start >= 32           : entirely in the high word.
;   2. bit_start+width_bits <= 32: entirely in the low word.
;   3. otherwise                 : straddles the boundary.
; MASK(width_bits) = (1<<width_bits)-1, skipped entirely when width_bits==32
; (an all-ones mask is a no-op, and 1<<32 isn't representable here).
;
; Hand-verified straddling example (see also
; test_position_field_straddles_32_bit_boundary in the paired test file):
; frame_width_bits=40, offset_bits=0, width_bits=20 -> bit_start=20 (case 3).
; raw_frame=0xABCDE00000 -> RAW_LO=0xCDE00000, RAW_HI=0x000000AB.
;   RAW_LO >> 20        = 0xCDE
;   RAW_HI << (32-20)   = 0xAB000
;   OR                  = 0xABCDE  (== the injected 20-bit position value)
; =============================================================================
l_extract_field:
    sub   BIT_START, FRAME_WIDTH, TMP2       ; BIT_START = frame_width - offset
    sub   BIT_START, BIT_START, EXT_WIDTH    ; BIT_START -= width -> final bit_start
    qble  l_ex_high, BIT_START, 32           ; taken if BIT_START >= 32 -> case 1
    mov   EXT_TMP, BIT_START
    add   EXT_TMP, EXT_TMP, EXT_WIDTH        ; EXT_TMP = bit_start + width
    qbge  l_ex_low, EXT_TMP, 32              ; taken if EXT_TMP <= 32 -> case 2
    ; -- case 3: straddles both words --
    lsr   EXT_RESULT, RAW_LO, BIT_START
    ldi   TMP, 32
    sub   TMP, TMP, BIT_START                 ; TMP = 32 - bit_start (1..31, safe)
    lsl   EXT_TMP, RAW_HI, TMP
    or    EXT_RESULT, EXT_RESULT, EXT_TMP
    qba   l_ex_mask
l_ex_high:
    sub   BIT_START, BIT_START, 32
    lsr   EXT_RESULT, RAW_HI, BIT_START
    qba   l_ex_mask
l_ex_low:
    lsr   EXT_RESULT, RAW_LO, BIT_START
l_ex_mask:
    qbeq  l_ex_done, EXT_WIDTH, 32           ; width==32 -> skip mask (no-op)
    lsl   EXT_TMP, ONE_REG, EXT_WIDTH
    sub   EXT_TMP, EXT_TMP, 1
    and   EXT_RESULT, EXT_RESULT, EXT_TMP
l_ex_done:
    jmp   RETLINK
