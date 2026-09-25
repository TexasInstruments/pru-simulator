; ssi_generic_emulator.asm - runtime-configurable SSI encoder emulator (PRU0)
; ---------------------------------------------------------------------------
; Maintained configurable SSI emulator with the same wire protocol as the
; legacy fixed test fixtures, but runtime-configurable instead of using
; hardcoded .set constants. Bit width, timing, sequencing and fault injection
; are driven by the shared-memory config block described in ssi_config_abi.inc /
; docs/superpowers/specs/2026-08-19-generic-runtime-ssi-design.md.
;
; LaunchPad loopback pin contract:
;   reader  R30.0  (CLK out, BP.11) -> this program R31.8 (CLK in, BP.51)
;   this program R30.0 (DATA out, BP.33) -> reader R31.16 (DATA in, BP.57)
;
; This program never encodes/decodes position data and never checks
; `topology` -- when topology==1 (reader-only) it simply is not loaded, per
; the design doc.
;
; ---------------------------------------------------------------------------
; Register map (r30/r31 are the GPIO pins; everything else is ours to pick):
;
;   Persistent constants, set once at boot, never touched again:
;     r1  FRAMES_OFF        SSI_FRAMES_BASE - SSI_CONFIG_BASE (0x100)
;     r18 SENTINEL_ONES     0xFFFFFFFF (also FRAME_SLOT_UNUSED_SENTINEL half)
;
;   Persistent config cache, refreshed only in l_apply_config (once per
;   applied generation):
;     r4  APPLIED_GEN       cached requested_generation we have applied
;     r7  FAULT_MODE        cached fault_mode (0..8)
;     r8  FRAME_WIDTH       cached frame_width_bits (1..64)
;     r15 TV_CYCLES         cached tv_cycles
;     r16 DEBOUNCE_THRESH   derived from tm_pause_outer_iters (see below)
;     r19 HOLD_MODE         cached sequence_hold_mode (0=frames, 1=time)
;     r20 HOLD_COUNT        cached sequence_hold_count
;     r21 FAULT_ARG         cached fault_argument
;     r22 FAULT_REPEAT      cached fault_repeat_count
;
;   Persistent sequence/fault state, reset in l_apply_config, updated once
;   per completed frame or on a slot change:
;     r9  SLOT_INDEX        active frame slot, 0..15
;     r10 HOLD_PROGRESS     frame-count or estimated-cycles accumulator
;     r11 FAULT_REMAINING   frames left before an expiring fault reverts
;     r12 FRAME_LO          active slot's frame_bits, low word
;     r13 FRAME_HI          active slot's frame_bits, high word
;     r17 HOLD_THRESH       effective hold threshold for the active slot
;
;   Per-frame scratch, recomputed every frame, never relied on across frames:
;     r0  ACTIVE_FAULT      this frame's resolved fault mode (0..8)
;     r2  EFF_LO            low word actually shifted out this frame
;     r3  EFF_HI            high word actually shifted out this frame
;     r5  EFF_TV            tv delay loop count actually used this frame
;     r6  BIT_COUNT         bits actually driven this frame (<=FRAME_WIDTH)
;     r14 (scratch)/BITCTR  raw hold_override word while loading a slot; reused as
;                           the "bits still owed this frame" countdown in the bit
;                           loop (distinct from BITPOS -- see l_bit_loop comment)
;     r23 BITPOS            MSB-first bit-position countdown
;     r24 BITPOS_HI         BITPOS - 32, used only in the high-word path
;     r25 BITVAL            extracted bit value, 0 or 1
;     r26 TMP               generic scratch; also the post-debounce
;                           poll-batch countdown in l_restart_sync's
;                           l_wait_falling_edge loop (see task-3-fix-1)
;     r27 TMP2              generic scratch (frame-slot peek, high word);
;                           also l_wait_falling_edge's requested_generation
;                           reread (see task-3-fix-1) -- both reuses are
;                           safe because TMP/TMP2 are dead at that point:
;                           TMP's last write was the generation check at the
;                           top of l_restart_sync (already compared), and
;                           TMP2 isn't touched again until l_hold_advance,
;                           reached only after a full frame completes
;     r28 LOOPCNT           debounce poll counter (copy of DEBOUNCE_THRESH)
;     r29 SLOTOFF           byte offset into the frame-slot table
; ---------------------------------------------------------------------------

    .include "ssi_config_abi.inc"

CLK_PIN       .set 8
DATA_PIN      .set 0

; Real (non-reserved) config fields run from SSI_CONFIG_BASE for this many
; bytes; the 172-byte reserved tail is never read.
SSI_CONFIG_REAL_FIELDS_SIZE .set 0x54

; SSI_FRAMES_BASE (0x00010100) - SSI_CONFIG_BASE (0x00010000). ADD's immediate
; operand is limited to 8 bits by this assembler, so 0x100 has to live in a
; register rather than as a literal.
SSI_FRAMES_OFFSET_FROM_CFG .set 0x100

; Debounce threshold = tm_pause_outer_iters * 16, via a 4-bit left shift.
; Rationale for 16: the legacy fixed-value SSI behavior uses PAUSE_THRESH=300
; consecutive-high polls against its reader's PAUSE_OUTER=15 monoflop count --
; a ~20:1 ratio. 16 keeps that same order of magnitude while being a clean
; shift (x16 = <<4) instead of a runtime divide.
DEBOUNCE_MULTIPLIER_SHIFT .set 4
; Cap so a pathologically large tm_pause_outer_iters can't make idle-sync
; effectively never happen; a few thousand polls is still negligible next to
; a real monoflop wait and comfortably bounds worst-case sync latency.
DEBOUNCE_POLL_CAP .set 4000

; ---------------------------------------------------------------------------
; Timestamped producer state in PRU0 local DRAM.  The ring itself remains in
; shared RAM at c28; this local block is the bounded cache/prepared-frame
; state.  No ring scan is performed.
; Generated ABI symbols: PRODUCER_SAMPLES_BASE and
; PRODUCER_SAMPLE_DIAGNOSTICS_BASE (the relative constants below are used
; because c28 addressing is relative to SSI_CONFIG_BASE).
; ---------------------------------------------------------------------------
DYN_PREP_LO             .set 0x00
DYN_PREP_HI             .set 0x04
DYN_LAST_FRAME_LO       .set 0x08
DYN_LAST_FRAME_HI       .set 0x0C
DYN_FRAME_PERIOD_LO     .set 0x10
DYN_FRAME_PERIOD_HI     .set 0x14
DYN_HAVE_FRAME          .set 0x18
DYN_STATUS              .set 0x1C
DYN_ACCEPTED            .set 0x20
DYN_RETRIES             .set 0x24
DYN_STALE               .set 0x28
DYN_OVERRUN             .set 0x2C
DYN_LAST_REQUEST_LO     .set 0x30
DYN_LAST_REQUEST_HI     .set 0x34
DYN_LAST_ESTIMATE_LO    .set 0x38
DYN_LAST_ESTIMATE_HI    .set 0x3C
DYN_GENERATION          .set 0x40
DYN_HEAD_SEQ            .set 0x44
DYN_HEAD_SLOT           .set 0x48
DYN_HEAD_SAMPLE_LO      .set 0x4C
DYN_HEAD_SAMPLE_HI      .set 0x50
DYN_CUR_TS_LO           .set 0x54
DYN_CUR_TS_HI           .set 0x58
DYN_CUR_POS_LO          .set 0x5C
DYN_CUR_POS_HI          .set 0x60
DYN_CUR_GEN             .set 0x64
DYN_CUR_FLAGS           .set 0x68
DYN_PREV_TS_LO          .set 0x6C
DYN_PREV_TS_HI          .set 0x70
DYN_PREV_POS_LO         .set 0x74
DYN_PREV_POS_HI         .set 0x78
DYN_PREV_GEN            .set 0x7C
DYN_PREV_FLAGS          .set 0x80
DYN_PREV_SLOT           .set 0x84
DYN_PREV_SEQ_HI         .set 0x88
DYN_LAST_ACCEPTED_LO    .set 0x8C
DYN_LAST_ACCEPTED_HI    .set 0x90
DYN_HAVE_ACCEPTED       .set 0x94

; Estimator status bits.  A fallback never overwrites the last prepared
; frame, and therefore can never turn a producer fault into an all-ones SSI
; response unless the configured SSI fault mode explicitly requests it.
DYN_STATUS_NO_PAIR      .set 0x00000001
DYN_STATUS_STALE        .set 0x00000002
DYN_STATUS_HORIZON      .set 0x00000004
DYN_STATUS_GENERATION   .set 0x00000008
DYN_STATUS_COHERENCE    .set 0x00000010
DYN_STATUS_OVERRUN      .set 0x00000020
DYN_STATUS_FRACTIONAL   .set 0x00000040
DYN_STATUS_LAYOUT       .set 0x00000080
DYN_STATUS_OVERFLOW     .set 0x00000100
DYN_STATUS_MISSED_PREP  .set 0x00000200

SSI_PRODUCER_DIAG_OFF_FROM_CFG .set 0x8400
SSI_PRODUCER_RING_OFF_FROM_CFG .set 0x6400
; Keep these as literal .set values: the simulator assembler expands LDI32
; only when its operand is a single literal, and these offsets are hot-path
; addresses rather than runtime arithmetic.
SSI_PRODUCER_DIAG_HEAD_OFF       .set 0x8430
SSI_PRODUCER_DIAG_SLOT_OFF       .set 0x8434
SSI_PRODUCER_DIAG_SAMPLE_SEQ_OFF .set 0x8438

; sequence_hold_mode==1 (time-based hold) has no free-running timer register
; available here, so elapsed time per completed frame is *estimated* as
; frame_width_bits * BIT_OVERHEAD + tv_cycles, not measured. BIT_OVERHEAD is
; approximated as 8 (2^3, so the multiply is a single shift on this
; no-multiply ISA) -- close to the fixed emulator's actual per-bit loop body
; of ~9-10 instructions (lsr/and/qbeq/set-or-clr/wbs/qbeq/sub/wbc/qba). This
; is a documented approximation, not exact wall-clock cycles.
HOLD_TIME_BIT_OVERHEAD_SHIFT .set 3

; ---- Register aliases (pure text substitution via .asg; see map above) ----
    .asg r1,  FRAMES_OFF
    .asg r18, SENTINEL_ONES

    .asg r4,  APPLIED_GEN
    .asg r7,  FAULT_MODE
    .asg r8,  FRAME_WIDTH
    .asg r15, TV_CYCLES
    .asg r16, DEBOUNCE_THRESH
    .asg r19, HOLD_MODE
    .asg r20, HOLD_COUNT
    .asg r21, FAULT_ARG
    .asg r22, FAULT_REPEAT

    .asg r9,  SLOT_INDEX
    .asg r10, HOLD_PROGRESS
    .asg r11, FAULT_REMAINING
    .asg r12, FRAME_LO
    .asg r13, FRAME_HI
    .asg r17, HOLD_THRESH

    .asg r0,  ACTIVE_FAULT
    .asg r2,  EFF_LO
    .asg r3,  EFF_HI
    .asg r5,  EFF_TV
    .asg r6,  BIT_COUNT
    .asg r14, BITCTR
    .asg r23, BITPOS
    .asg r24, BITPOS_HI
    .asg r25, BITVAL
    .asg r26, TMP
    .asg r27, TMP2
    .asg r28, LOOPCNT
    .asg r29, SLOTOFF

    .retain
    .retainrefs
    .global main
    .sect ".text"

main:
    ldi   FRAMES_OFF, SSI_FRAMES_OFFSET_FROM_CFG
    set   r30, r30, DATA_PIN          ; idle data HIGH before anything else
    qba   l_apply_config

; =============================================================================
; l_apply_config -- (re)load the entire config block, reset sequence/fault
; state to post-apply starting values, ack the generation, and fall through
; into loading the active slot + idle sync. Reached at boot and whenever
; l_restart_sync notices requested_generation has changed.
; =============================================================================
l_apply_config:
    lbco  &r2, c28, 0, SSI_CONFIG_REAL_FIELDS_SIZE   ; raw config words 0..20 -> r2..r22
    ; Timestamped preparation uses r1 as the high word of 64-bit scratch
    ; values.  Re-establish this persistent frame-table offset on every
    ; generation before l_load_active_slot uses it.
    ldi   FRAMES_OFF, SSI_FRAMES_OFFSET_FROM_CFG

    ; -- pull the packed sub-word fields out before their words get reused --
    lsr   FAULT_MODE, HOLD_MODE, 8      ; word17 byte1 (@0x45) = fault_mode
    and   FAULT_MODE, FAULT_MODE, 0xFF
    and   HOLD_MODE, HOLD_MODE, 0xFF    ; word17 byte0 (@0x44) = sequence_hold_mode, in place

    ldi   TMP, 0xFFFF
    and   FRAME_WIDTH, FRAME_WIDTH, TMP ; word6 low half (@0x18) = frame_width_bits, in place

    lsl   DEBOUNCE_THRESH, DEBOUNCE_THRESH, DEBOUNCE_MULTIPLIER_SHIFT
    ldi   TMP, DEBOUNCE_POLL_CAP
    min   DEBOUNCE_THRESH, DEBOUNCE_THRESH, TMP

    ; -- reset sequence + fault state to their post-apply starting values --
    ldi   SLOT_INDEX, 0
    ldi   HOLD_PROGRESS, 0
    mov   FAULT_REMAINING, FAULT_REPEAT

    ; the big lbco above just clobbered r18 with formation_pause_outer_iters'
    ; raw value; restore the sentinel constant before anything reads it again
    ldi32 SENTINEL_ONES, 0xFFFFFFFF

    ; Reset only PRU0-owned estimator state.  The producer owns the ring/head
    ; publication, so the head fields are intentionally not cleared here.
    ldi   TMP2, 0
    ldi   TMP, 0
    sbbo  &TMP, TMP2, DYN_LAST_FRAME_LO, 4
    sbbo  &TMP, TMP2, DYN_LAST_FRAME_HI, 4
    sbbo  &TMP, TMP2, DYN_FRAME_PERIOD_LO, 4
    sbbo  &TMP, TMP2, DYN_FRAME_PERIOD_HI, 4
    sbbo  &TMP, TMP2, DYN_HAVE_FRAME, 4
    sbbo  &TMP, TMP2, DYN_STATUS, 4
    sbbo  &TMP, TMP2, DYN_ACCEPTED, 4
    sbbo  &TMP, TMP2, DYN_RETRIES, 4
    sbbo  &TMP, TMP2, DYN_STALE, 4
    sbbo  &TMP, TMP2, DYN_OVERRUN, 4
    sbbo  &TMP, TMP2, DYN_GENERATION, 4
    sbbo  &TMP, TMP2, DYN_LAST_ACCEPTED_LO, 4
    sbbo  &TMP, TMP2, DYN_LAST_ACCEPTED_HI, 4
    sbbo  &TMP, TMP2, DYN_HAVE_ACCEPTED, 4

    set   r30, r30, DATA_PIN

    qba   l_load_active_slot

; =============================================================================
; l_load_active_slot -- load SLOT_INDEX's frame_bits + hold override, derive
; HOLD_THRESH, then fall into idle sync. Reached whenever slot_index changes
; and whenever a new generation is applied (never re-read otherwise).
; =============================================================================
l_load_active_slot:
    ; Both timestamped preparation and the edge timestamp read use r1 as
    ; 64-bit scratch. Static sequence advancement reaches this label without
    ; a generation apply, so restore the persistent table offset here too.
    ldi   FRAMES_OFF, SSI_FRAMES_OFFSET_FROM_CFG
    lsl   SLOTOFF, SLOT_INDEX, 4
    add   SLOTOFF, SLOTOFF, FRAMES_OFF
    lbco  &FRAME_LO, c28, SLOTOFF, 12   ; FRAME_LO, FRAME_HI, raw hold_override (r14)
    ; The static sequence is also the initial safe fallback for timestamped
    ; mode.  Once the first request timestamp exists, preserve the last
    ; prepared frame across static-slot advancement: a stale/overrun producer
    ; must hold that frame, not silently fall back to an unrelated sequence
    ; value.  Apply resets DYN_HAVE_FRAME, so the newly selected slot is still
    ; loaded as the fallback at a generation boundary.
    ldi   TMP2, 0
    lbco  &TMP2, c28, SSI_CONFIG_PRODUCER_MODE_OFF, 1
    qbeq  l_cache_prepared_static, TMP2, 0
    ldi   TMP, 0
    lbbo  &TMP, TMP, DYN_HAVE_FRAME, 4
    qbne  l_skip_prepared_cache, TMP, 0
l_cache_prepared_static:
    ldi   TMP2, 0
    sbbo  &FRAME_LO, TMP2, DYN_PREP_LO, 8
l_skip_prepared_cache:
    qbne  l_use_override, r14, 0
    mov   HOLD_THRESH, HOLD_COUNT
    qba   l_config_ready
l_use_override:
    mov   HOLD_THRESH, r14
l_config_ready:
    ; The generation acknowledgement is published at l_wait_falling_edge,
    ; after any timestamped idle preparation has completed.  That keeps the
    ; reader from launching its first falling edge while PRU0 is still doing
    ; the bounded producer-head read.
    qba   l_restart_sync

; =============================================================================
; l_restart_sync -- once per frame, at the idle boundary: cheap generation
; check (single 4-byte read), then the fixed program's debounce/sync pattern
; using the config-derived DEBOUNCE_THRESH instead of a hardcoded constant.
; =============================================================================
l_restart_sync:
    lbco  &TMP, c28, SSI_CONFIG_REQUESTED_GENERATION_OFF, 4
    qbne  l_apply_config, TMP, APPLIED_GEN   ; changed -> full reapply, nothing else read here
    ; The synchronous master already guarantees a valid high idle interval
    ; (Tp > formation pause). Avoid spending the asynchronous qualification
    ; polls a second time on this deterministic request-relative path.
    ; LBCO of one byte updates only the low byte of the destination register.
    ; Clear the word first so stale scratch bits cannot turn formation mode on.
    ldi   TMP, 0
    lbco  &TMP, c28, SSI_CONFIG_FORMATION_MODE_OFF, 1
    ; Timestamped mode prepares from the constant-time producer head during
    ; this idle interval.  The actual falling edge below only latches the
    ; already prepared frame and enters the unchanged bit loop.
    ldi   TMP2, 0
    lbco  &TMP2, c28, SSI_CONFIG_PRODUCER_MODE_OFF, 1
    qbeq  l_dynamic_prepare_done, TMP2, 0
    qba   l_dynamic_prepare
l_dynamic_prepare_done:
    ; The preparation path reuses the temporary registers, so reread the
    ; formation byte before choosing the asynchronous qualification path.
    ldi   TMP, 0
    lbco  &TMP, c28, SSI_CONFIG_FORMATION_MODE_OFF, 1
    qbne  l_wait_falling_edge, TMP, 0
    mov   LOOPCNT, DEBOUNCE_THRESH
l_check_high:
    qbbc  l_restart_sync, r31, CLK_PIN
    sub   LOOPCNT, LOOPCNT, 1
    qbne  l_check_high, LOOPCNT, 0
    qba   l_wait_falling_edge

; =============================================================================
; l_dynamic_prepare -- bounded timestamped producer consumer
;
; The producer head gives the current slot in constant time.  This routine
; reads that slot and its predecessor with per-entry seqlock checks, validates
; the pair, and prepares the next raw SSI frame while the line is idle.  It
; intentionally supports the deterministic hardware subset: binary or Gray,
; integer Q31.32 counts up to 31 position bits, any 1..64-bit frame width,
; explicit or right-aligned placement, and zero padding.  The sample ABI does
; not carry a per-sample status value, so nonzero status fields and
; Gray-excess metadata are rejected explicitly instead of being guessed.
;
; The arithmetic is bounded: one 32-bit shift/add multiply and one 32-step
; restoring divide.  No loop here depends on producer ring length or on an
; unbounded value.  The hot edge/bit path never enters this label.
; =============================================================================
l_dynamic_prepare:
    ldi   r14, 0                         ; PRU0 local DRAM base
    ldi   r0, 0
    sbbo  &r0, r14, DYN_STATUS, 4        ; clear status for this preparation

    ; Measure the period between completed frame-start captures during the
    ; idle interval.  The falling-edge path only captures COUNT_LO/COUNT_HI
    ; and stores DYN_LAST_REQUEST; it never subtracts, branches on a 64-bit
    ; result, or updates the predictor before entering the bit loop.
    lbbo  &r6, r14, DYN_HAVE_FRAME, 4
    qbeq  l_dyn_period_ready, r6, 0
    lbbo  &r0, r14, DYN_LAST_REQUEST_LO, 8
    lbbo  &r2, r14, DYN_LAST_FRAME_LO, 8
    sub   r5, r0, r2
    suc   r6, r1, r3
    qbeq  l_dyn_period_ready, r5, 0
    qbne  l_dyn_fail_overflow, r6, 0
    sbbo  &r5, r14, DYN_FRAME_PERIOD_LO, 8
    sbbo  &r0, r14, DYN_LAST_FRAME_LO, 8
l_dyn_period_ready:

    ; -- coherent producer head, maximum three attempts --
    ldi   r29, 3
l_dyn_head_try:
    ldi32 r18, SSI_PRODUCER_DIAG_HEAD_OFF
    lbco  &r23, c28, r18, 4              ; head sequence, first read
    qbbs  l_dyn_head_retry, r23, 0       ; odd means producer is publishing
    ldi32 r18, SSI_PRODUCER_DIAG_SLOT_OFF
    lbco  &r24, c28, r18, 4              ; latest slot index
    ldi32 r18, SSI_PRODUCER_DIAG_SAMPLE_SEQ_OFF
    lbco  &r25, c28, r18, 8              ; stable sample sequence
    ldi32 r18, SSI_PRODUCER_DIAG_HEAD_OFF
    lbco  &r27, c28, r18, 4              ; head sequence, second read
    qbne  l_dyn_head_retry, r27, r23
    mov   r23, r27
    qba   l_dyn_head_good
l_dyn_head_retry:
    sub   r29, r29, 1
    qbne  l_dyn_head_try, r29, 0
    ldi   r5, DYN_STATUS_COHERENCE
    qba   l_dyn_fail_coherence
l_dyn_head_good:
    sbbo  &r23, r14, DYN_HEAD_SEQ, 4
    and   r24, r24, 0xFF
    sbbo  &r24, r14, DYN_HEAD_SLOT, 4
    sbbo  &r25, r14, DYN_HEAD_SAMPLE_LO, 8

    ; A latest/predecessor pair is sufficient for interpolation, but a
    ; producer can still lap the consumer by more than one complete ring
    ; while PRU0 is between frame preparations.  Keep the last accepted head
    ; sequence and count a jump of >=256 samples as an overwrite.  Do not
    ; reject the newest pair: it is still coherent and is exactly the pair
    ; needed to resume interpolation.  This is constant time and uses the
    ; producer's monotonic 64-bit sample sequence; no ring scan is needed.
    ; An apply reset clears the valid flag, so the first pair of a new
    ; configuration is not penalized.
    lbbo  &r0, r14, DYN_HAVE_ACCEPTED, 4
    qbeq  l_dyn_progress_checked, r0, 0
    lbbo  &r0, r14, DYN_HEAD_SAMPLE_LO, 8
    lbbo  &r2, r14, DYN_LAST_ACCEPTED_LO, 8
    sub   r5, r0, r2
    suc   r6, r1, r3
    qbne  l_dyn_progress_overrun, r6, 0
    ldi   r2, 512
    qble  l_dyn_progress_overrun, r5, r2
    qba   l_dyn_progress_checked
l_dyn_progress_overrun:
    lbbo  &r0, r14, DYN_OVERRUN, 4
    add   r0, r0, 1
    sbbo  &r0, r14, DYN_OVERRUN, 4
l_dyn_progress_checked:

    ; -- current slot: sequence, payload, sequence --
    lsl   r18, r24, 5
    ldi32 r27, SSI_PRODUCER_RING_OFF_FROM_CFG
    add   r18, r18, r27
    lbco  &r23, c28, r18, 8
    qbbs  l_dyn_fail_coherence_now, r23, 0
    qbne  l_dyn_fail_coherence_now, r23, r25
    qbne  l_dyn_fail_coherence_now, r24, r26
    add   r27, r18, 8
    lbco  &r0, c28, r27, 8
    sbbo  &r0, r14, DYN_CUR_TS_LO, 8
    add   r27, r18, 16
    lbco  &r0, c28, r27, 8
    sbbo  &r0, r14, DYN_CUR_POS_LO, 8
    add   r27, r18, 24
    lbco  &r0, c28, r27, 8
    sbbo  &r0, r14, DYN_CUR_GEN, 8
    lbco  &r23, c28, r18, 8
    qbne  l_dyn_fail_coherence_now, r23, r25
    qbne  l_dyn_fail_coherence_now, r24, r26

    ; -- predecessor slot: sequence, payload, sequence --
    lbbo  &r24, r14, DYN_HEAD_SLOT, 4
    sub   r24, r24, 1
    and   r24, r24, 0xFF
    sbbo  &r24, r14, DYN_PREV_SLOT, 4
    lsl   r18, r24, 5
    ldi32 r27, SSI_PRODUCER_RING_OFF_FROM_CFG
    add   r18, r18, r27
    lbco  &r25, c28, r18, 8
    qbbs  l_dyn_fail_coherence_now, r25, 0
    add   r27, r18, 8
    lbco  &r0, c28, r27, 8
    sbbo  &r0, r14, DYN_PREV_TS_LO, 8
    add   r27, r18, 16
    lbco  &r0, c28, r27, 8
    sbbo  &r0, r14, DYN_PREV_POS_LO, 8
    add   r27, r18, 24
    lbco  &r0, c28, r27, 8
    sbbo  &r0, r14, DYN_PREV_GEN, 8
    lbco  &r23, c28, r18, 8
    qbne  l_dyn_fail_coherence_now, r23, r25
    qbne  l_dyn_fail_coherence_now, r24, r26
    sbbo  &r24, r14, DYN_PREV_SEQ_HI, 4

    ; Both samples must be valid and from one producer generation.
    lbbo  &r0, r14, DYN_CUR_FLAGS, 4
    and   r0, r0, SSI_PRODUCER_SAMPLE_FLAG_VALID
    qbeq  l_dyn_fail_no_pair, r0, 0
    lbbo  &r0, r14, DYN_PREV_FLAGS, 4
    and   r0, r0, SSI_PRODUCER_SAMPLE_FLAG_VALID
    qbeq  l_dyn_fail_no_pair, r0, 0
    lbbo  &r0, r14, DYN_CUR_GEN, 4
    lbbo  &r1, r14, DYN_PREV_GEN, 4
    qbne  l_dyn_fail_generation, r0, r1

    ; The predecessor must really be the immediately preceding publication.
    ; A gap means the ring wrapped before PRU0 consumed it.
    lbbo  &r25, r14, DYN_HEAD_SAMPLE_LO, 8
    sub   r25, r25, 2
    suc   r26, r26, 0
    qbne  l_dyn_fail_overrun, r23, r25
    lbbo  &r24, r14, DYN_PREV_SEQ_HI, 4
    qbne  l_dyn_fail_overrun, r24, r26
    lbbo  &r24, r14, DYN_PREV_SLOT, 4
    lbbo  &r27, r14, DYN_HEAD_SLOT, 4
    sub   r27, r27, 1
    and   r27, r27, 0xFF
    qbne  l_dyn_fail_overrun, r24, r27

    ; dt = current sample timestamp - predecessor timestamp.  The low-word
    ; subtraction naturally handles a small IEP rollover; a nonzero high word
    ; is outside this bounded 32-bit arithmetic subset.
    lbbo  &r0, r14, DYN_CUR_TS_LO, 8
    lbbo  &r2, r14, DYN_PREV_TS_LO, 8
    sub   r5, r0, r2
    suc   r6, r1, r3
    qbeq  l_dyn_fail_no_pair, r5, 0
    qbne  l_dyn_fail_overflow, r6, 0
    mov   r27, r5                        ; dt

    ; Reject a sample pair that is already too old at preparation time.
    lbco  &r0, c26, 0x10, 4
    lbco  &r1, c26, 0x14, 4
    lbbo  &r2, r14, DYN_CUR_TS_LO, 8
    sub   r5, r0, r2
    suc   r6, r1, r3
    qbne  l_dyn_fail_stale, r6, 0
    ldi   r23, 0
    lbco  &r23, c28, SSI_CONFIG_PRODUCER_SAMPLE_AGE_LIMIT_IEP_TICKS_OFF, 4
    qbgt  l_dyn_fail_stale, r23, r5

    ; target = last actual frame start + measured frame period; dr is the
    ; bounded prediction distance from the newest producer sample.
    lbbo  &r0, r14, DYN_FRAME_PERIOD_LO, 8
    qbeq  l_dyn_fail_no_pair, r0, 0
    lbbo  &r2, r14, DYN_LAST_FRAME_LO, 8
    add   r0, r0, r2
    adc   r1, r1, r3
    lbbo  &r2, r14, DYN_CUR_TS_LO, 8
    sub   r5, r0, r2
    suc   r6, r1, r3
    qbne  l_dyn_fail_horizon, r6, 0
    mov   r28, r5                        ; dr
    ldi   r23, 0
    lbco  &r23, c28, SSI_CONFIG_PRODUCER_PREDICTION_HORIZON_LIMIT_IEP_TICKS_OFF, 4
    qbgt  l_dyn_fail_horizon, r23, r28

    ; The first safe dynamic implementation handles integral Q31.32 counts.
    ; Fractional payloads are rejected explicitly rather than truncated.
    lbbo  &r0, r14, DYN_CUR_POS_LO, 4
    lbbo  &r2, r14, DYN_PREV_POS_LO, 4
    or    r0, r0, r2
    qbne  l_dyn_fail_fractional, r0, 0
    lbbo  &r1, r14, DYN_CUR_POS_HI, 4
    lbbo  &r3, r14, DYN_PREV_POS_HI, 4
    sub   r5, r1, r3
    ldi   r6, 0
    qbbc  l_dyn_delta_positive, r5, 31
    rsb   r5, r5, 0
    ldi   r6, 1                         ; signed delta was negative
l_dyn_delta_positive:

    ; Validate the constant-time wire-layout subset.  Position arithmetic is
    ; intentionally bounded to 31 bits, but the prepared raw frame is still
    ; allowed to occupy any 1..64-bit SSI frame.
    ldi   r23, 0
    lbco  &r23, c28, SSI_CONFIG_ENCODING_TYPE_OFF, 1
    qbeq  l_dyn_encoding_ready, r23, 0
    qbne  l_dyn_fail_layout, r23, 1
l_dyn_encoding_ready:
    ldi   r23, 0
    lbco  &r23, c28, SSI_CONFIG_FRAME_WIDTH_BITS_OFF, 2
    qbeq  l_dyn_fail_layout, r23, 0
    qbgt  l_dyn_fail_layout, 64, r23
    lbco  &r24, c28, SSI_CONFIG_POSITION_WIDTH_BITS_OFF, 2
    qbeq  l_dyn_fail_layout, r24, 0
    qbgt  l_dyn_fail_layout, 31, r24

    ; Timestamped samples carry no status payload.  Padding is safe because
    ; the frame starts at zero; the padding bits therefore remain zero.
    ldi   r26, 0
    lbco  &r26, c28, SSI_CONFIG_ERROR_WIDTH_BITS_OFF, 2
    qbne  l_dyn_fail_layout, r26, 0
    ldi   r0, 0
    lbco  &r0, c28, SSI_CONFIG_PADDING_WIDTH_BITS_OFF, 2

    ; For alignment=right with an omitted offset, place the position just
    ; above the configured zero padding.  Otherwise use the explicit MSB-side
    ; offset.  Keep r27 (dt) intact for the divide below.
    ldi   r26, 0
    lbco  &r26, c28, SSI_CONFIG_ALIGNMENT_OFF, 1
    qbeq  l_dyn_explicit_offset, r26, 0
    qbne  l_dyn_fail_layout, r26, 1
    ldi   r26, 0
    lbco  &r26, c28, SSI_CONFIG_POSITION_OFFSET_BITS_OFF, 2
    qbne  l_dyn_offset_ready, r26, 0
    sub   r26, r23, r24
    sub   r26, r26, r0
    qba   l_dyn_offset_ready
l_dyn_explicit_offset:
    ldi   r26, 0
    lbco  &r26, c28, SSI_CONFIG_POSITION_OFFSET_BITS_OFF, 2
l_dyn_offset_ready:
    add   r25, r26, r24                  ; position end in MSB coordinates
    qbgt  l_dyn_fail_layout, r23, r25    ; end > frame width
    sub   r25, r23, r25                  ; LSB shift for the encoded field

    ; 32-bit shift/add multiply: product = abs(delta_position) * dr.
    ldi   r0, 0                           ; product low
    ldi   r1, 0                           ; product high
    mov   r2, r5                          ; multiplicand low
    ldi   r3, 0                           ; multiplicand high
    loop  l_dyn_mul_done, 32
    qbbc  l_dyn_mul_skip_add, r28, 0
    add   r0, r0, r2
    adc   r1, r1, r3
l_dyn_mul_skip_add:
    lsr   r5, r2, 31
    lsl   r2, r2, 1
    lsl   r3, r3, 1
    or    r3, r3, r5
    lsr   r28, r28, 1
l_dyn_mul_done:

    ; 32-step restoring divide: quotient = product / dt.  The quotient is
    ; bounded by the configured position field check below.
    ldi   r2, 0                           ; quotient
    ldi   r3, 0                           ; remainder
    loop  l_dyn_div_done, 32
    lsl   r3, r3, 1
    qbbc  l_dyn_div_no_input, r1, 31
    set   r3, r3, 0
l_dyn_div_no_input:
    lsr   r5, r0, 31
    lsl   r1, r1, 1
    or    r1, r1, r5
    lsl   r0, r0, 1
    lsl   r2, r2, 1
    qbge  l_dyn_div_subtract, r27, r3
    qba   l_dyn_div_next
l_dyn_div_subtract:
    sub   r3, r3, r27
    set   r2, r2, 0
l_dyn_div_next:
l_dyn_div_done:

    ; estimate = newest integer position +/- quotient, then range-check before
    ; the value is encoded and inserted into the raw SSI frame.
    lbbo  &r5, r14, DYN_CUR_POS_HI, 4
    qbeq  l_dyn_est_positive, r6, 0
    sub   r5, r5, r2
    qba   l_dyn_est_checked
l_dyn_est_positive:
    add   r5, r5, r2
l_dyn_est_checked:
    qbbs  l_dyn_fail_overflow, r5, 31
    ldi   r0, 1
    lsl   r0, r0, r24
    sub   r0, r0, 1
    qbgt  l_dyn_fail_overflow, r0, r5

    ; Encode the natural position.  Gray is a single xor/shift operation; no
    ; data-dependent loop is added to the frame preparation path.
    ldi   r23, 0
    lbco  &r23, c28, SSI_CONFIG_ENCODING_TYPE_OFF, 1
    mov   r0, r5
    qbeq  l_dyn_pack_encoded, r23, 0
    lsr   r1, r5, 1
    xor   r0, r5, r1

    ; Place the encoded value into a two-word MSB-first frame.  The branch is
    ; selected once per prepared frame, so the per-bit SSI loop remains
    ; unchanged and deterministic.
l_dyn_pack_encoded:
    ldi   r1, 0                           ; frame high word
    qbgt  l_dyn_pack_high_word, 31, r25
    lsl   r2, r0, r25                     ; frame low word
    qbeq  l_dyn_pack_commit, r25, 0
    ldi   r26, 32
    sub   r26, r26, r25
    lsr   r1, r0, r26
    qba   l_dyn_pack_commit
l_dyn_pack_high_word:
    sub   r25, r25, 32
    ldi   r2, 0
    qbeq  l_dyn_pack_high_shift_zero, r25, 0
    lsl   r1, r0, r25
    qba   l_dyn_pack_commit
l_dyn_pack_high_shift_zero:
    mov   r1, r0

l_dyn_pack_commit:
    ; Commit the prepared raw frame only after every validation passed.
    sbbo  &r2, r14, DYN_PREP_LO, 4
    sbbo  &r1, r14, DYN_PREP_HI, 4
    ldi   r0, 0
    sbbo  &r0, r14, DYN_LAST_ESTIMATE_LO, 4
    sbbo  &r5, r14, DYN_LAST_ESTIMATE_HI, 4
    lbbo  &r0, r14, DYN_CUR_GEN, 4
    sbbo  &r0, r14, DYN_GENERATION, 4
    lbbo  &r0, r14, DYN_ACCEPTED, 4
    add   r0, r0, 1
    sbbo  &r0, r14, DYN_ACCEPTED, 4
    lbbo  &r0, r14, DYN_HEAD_SAMPLE_LO, 8
    sbbo  &r0, r14, DYN_LAST_ACCEPTED_LO, 8
    ldi   r0, 1
    sbbo  &r0, r14, DYN_HAVE_ACCEPTED, 4
    qba   l_dynamic_publish_diag

l_dyn_fail_coherence_now:
    ldi   r5, DYN_STATUS_COHERENCE
l_dyn_fail_coherence:
    lbbo  &r0, r14, DYN_RETRIES, 4
    add   r0, r0, 1
    sbbo  &r0, r14, DYN_RETRIES, 4
    qba   l_dyn_fail_common
l_dyn_fail_no_pair:
    ldi   r5, DYN_STATUS_NO_PAIR
    qba   l_dyn_fail_common
l_dyn_fail_generation:
    ldi   r5, DYN_STATUS_GENERATION
    qba   l_dyn_fail_common
l_dyn_fail_overrun:
    ldi   r5, DYN_STATUS_OVERRUN
    lbbo  &r0, r14, DYN_OVERRUN, 4
    add   r0, r0, 1
    sbbo  &r0, r14, DYN_OVERRUN, 4
    qba   l_dyn_fail_common
l_dyn_fail_stale:
    ldi   r5, DYN_STATUS_STALE
    lbbo  &r0, r14, DYN_STALE, 4
    add   r0, r0, 1
    sbbo  &r0, r14, DYN_STALE, 4
    qba   l_dyn_fail_common
l_dyn_fail_horizon:
    ldi   r5, DYN_STATUS_HORIZON
    qba   l_dyn_fail_common
l_dyn_fail_fractional:
    ldi   r5, DYN_STATUS_FRACTIONAL
    qba   l_dyn_fail_common
l_dyn_fail_layout:
    ldi   r5, DYN_STATUS_LAYOUT
    qba   l_dyn_fail_common
l_dyn_fail_overflow:
    ldi   r5, DYN_STATUS_OVERFLOW
l_dyn_fail_common:
    ; Once at least one timestamped estimate has been accepted, any later
    ; failed preparation means the next request had no fresh prepared frame.
    ; Keep the specific reason as well as the explicit missed-preparation bit;
    ; the prepared frame itself is intentionally left untouched.
    lbbo  &r0, r14, DYN_ACCEPTED, 4
    qbeq  l_dyn_status_write, r0, 0
    ldi   r2, DYN_STATUS_MISSED_PREP
    or    r5, r5, r2
l_dyn_status_write:
    sbbo  &r5, r14, DYN_STATUS, 4
l_dynamic_publish_diag:
    ; Copy PRU0-owned diagnostics to the fixed shared block.  The producer
    ; head fields at +0x30/+0x34/+0x38 are deliberately left untouched: they
    ; are the producer's live seqlock publication and are also the consumer's
    ; constant-time input.  Rewriting them here would race the producer and
    ; make a fresh ring head look stale.
    ldi32 r18, SSI_PRODUCER_DIAG_OFF_FROM_CFG
    lbbo  &r0, r14, DYN_HEAD_SAMPLE_LO, 8
    sbco  &r0, c28, r18, 8
    add   r18, r18, 8
    lbbo  &r0, r14, DYN_ACCEPTED, 4
    sbco  &r0, c28, r18, 4
    add   r18, r18, 4
    lbbo  &r0, r14, DYN_RETRIES, 4
    sbco  &r0, c28, r18, 4
    add   r18, r18, 4
    lbbo  &r0, r14, DYN_STALE, 4
    sbco  &r0, c28, r18, 4
    add   r18, r18, 4
    lbbo  &r0, r14, DYN_OVERRUN, 4
    sbco  &r0, c28, r18, 4
    add   r18, r18, 4
    lbbo  &r0, r14, DYN_LAST_REQUEST_LO, 8
    sbco  &r0, c28, r18, 8
    add   r18, r18, 8
    lbbo  &r0, r14, DYN_LAST_ESTIMATE_LO, 8
    sbco  &r0, c28, r18, 8
    add   r18, r18, 8
    lbbo  &r0, r14, DYN_STATUS, 4
    sbco  &r0, c28, r18, 4
    add   r18, r18, 4
    lbbo  &r0, r14, DYN_GENERATION, 4
    sbco  &r0, c28, r18, 4
    add   r18, r18, 4
    ldi32 SENTINEL_ONES, 0xFFFFFFFF
    qba   l_dynamic_prepare_done

; =============================================================================
; l_wait_falling_edge -- bounded poll for the first falling edge that starts
; the next frame. This used to be a single blocking `wbc r31, CLK_PIN`, which
; is a real deadlock for a topology=0 (loopback) pair: if requested_generation
; changes while this program is parked here (debounce above finishes well
; before the reader's own, longer inter-frame pause elapses, since
; tp_pause_outer_iters > tm_pause_outer_iters is an enforced invariant),
; ssi_generic_reader.asm (PRU1) will -- correctly, per its own already-
; approved logic -- detour to wait for this program's pru0_ack_generation
; write instead of sending the falling edge this program is blocked on.
; Neither side can then make progress: independently reproduced by stepping
; 200,000+ instructions with both PCs frozen. See this repo's
; .superpowers/sdd/generic_runtime_ssi_implementation_plan/
; task-3-fix-1-brief.md for the full root-cause writeup.
;
; Fix: poll for the edge in batches of 64 iterations, re-checking
; requested_generation between batches and escaping straight to
; l_apply_config if it has changed instead of continuing to wait for an edge
; that may never come. TMP/TMP2 are both dead here (see the register-map
; comment above), so no new register allocation is needed.
; =============================================================================
l_wait_falling_edge:
    ; PRU1 waits on this acknowledgement during startup/reconfiguration.  It
    ; is deliberately after preparation and just before edge polling, so the
    ; first frame cannot be sampled while PRU0 is still becoming ready.
    sbco  &APPLIED_GEN, c28, SSI_CONFIG_PRU0_ACK_GENERATION_OFF, 4
    ldi   TMP, 64                     ; poll-batch size before re-checking generation
l_wait_batch:
    qbbc  l_falling_edge_seen, r31, CLK_PIN
    sub   TMP, TMP, 1
    qbne  l_wait_batch, TMP, 0
    lbco  &TMP2, c28, SSI_CONFIG_REQUESTED_GENERATION_OFF, 4
    qbne  l_apply_config, TMP2, APPLIED_GEN
    qba   l_wait_falling_edge
l_falling_edge_seen:
    ; COUNT_LO latches COUNT_HI on AM243x.  This is the only timestamp work
    ; on the edge; interpolation, ring reads, division, and packing happen in
    ; the preceding idle interval.
    ldi   TMP, 0
    lbco  &TMP, c28, SSI_CONFIG_PRODUCER_MODE_OFF, 1
    qbeq  l_new_frame, TMP, 0
    ; l_read_iep is deliberately kept as a named, inline operation: the
    ; falling-edge path must read COUNT_LO first (which latches COUNT_HI),
    ; then read COUNT_HI through c26 without a call/return overhead.
l_read_iep:
    ldi   TMP2, 0
    lbco  &r0, c26, 0x10, 4
    lbco  &r1, c26, 0x14, 4
    sbbo  &r0, TMP2, DYN_LAST_REQUEST_LO, 8
    lbbo  &r2, TMP2, DYN_HAVE_FRAME, 4
    qbne  l_new_frame, r2, 0
    ; Seed the previous-request anchor on the first edge.  The next idle
    ; preparation will see a nonzero delta and publish the first period.
    ldi   r2, 1
    sbbo  &r2, TMP2, DYN_HAVE_FRAME, 4
    sbbo  &r0, TMP2, DYN_LAST_FRAME_LO, 8

; =============================================================================
; l_new_frame -- resolve this frame's fault behavior, set up the defaults a
; normal frame would use, then dispatch to the mode-specific frame body.
; =============================================================================
l_new_frame:
    ldi   ACTIVE_FAULT, 0
    qbeq  l_af_resolved, FAULT_MODE, 0        ; no fault configured
    qbeq  l_af_apply, FAULT_REPEAT, 0         ; repeat count 0 -> fault never expires
    qbeq  l_af_resolved, FAULT_REMAINING, 0   ; finite repeat, already expired
l_af_apply:
    mov   ACTIVE_FAULT, FAULT_MODE
    qbeq  l_af_resolved, FAULT_REPEAT, 0      ; never-expiring -> never decrement
    sub   FAULT_REMAINING, FAULT_REMAINING, 1
l_af_resolved:

    ; -- defaults: a normal frame shifts out the active slot's frame_bits --
    mov   EFF_LO, FRAME_LO
    mov   EFF_HI, FRAME_HI
    mov   BIT_COUNT, FRAME_WIDTH
    mov   EFF_TV, TV_CYCLES

    ; Only a fault-free timestamped frame consumes the prepared dynamic
    ; value.  Explicit fault modes retain the existing static injection path.
    qbeq  l_check_dynamic_mode, ACTIVE_FAULT, 0

    qbeq  l_bit_loop, ACTIVE_FAULT, 0
    qbeq  l_bit_loop, ACTIVE_FAULT, 1          ; status/error bits: host already baked
                                                ; them into frame_bits; no PRU-side override
    qbeq  l_fb_sentinel, ACTIVE_FAULT, 2
    qbeq  l_fb_allones, ACTIVE_FAULT, 3
    qbeq  l_wait_only_frame, ACTIVE_FAULT, 4
    qbeq  l_fb_short, ACTIVE_FAULT, 5
    qbeq  l_fb_extv, ACTIVE_FAULT, 6
    qbeq  l_stuck_low_frame, ACTIVE_FAULT, 7
    qbeq  l_fb_allones, ACTIVE_FAULT, 8        ; stuck-high looks identical to all-ones
                                                ; during a frame (documented, not a bug);
                                                ; the idle-restore below covers "between frames"
    qba   l_bit_loop                            ; unrecognized value -> defensive fallback

l_check_dynamic_mode:
    ldi   TMP, 0
    lbco  &TMP, c28, SSI_CONFIG_PRODUCER_MODE_OFF, 1
    qbeq  l_bit_loop, TMP, 0
    ldi   TMP2, 0
    lbbo  &EFF_LO, TMP2, DYN_PREP_LO, 8
    qba   l_bit_loop

l_fb_sentinel:
    mov   EFF_LO, FAULT_ARG                     ; fault_argument zero-extended to 64 bits
    ldi   EFF_HI, 0
    qba   l_bit_loop

l_fb_allones:
    mov   EFF_LO, SENTINEL_ONES
    mov   EFF_HI, SENTINEL_ONES
    qba   l_bit_loop

l_fb_short:
    min   BIT_COUNT, FAULT_ARG, FRAME_WIDTH     ; clamp to [0, frame_width_bits]
    qbeq  l_frame_drive_done, BIT_COUNT, 0      ; degenerate 0-bit case: straight to idle
    qba   l_bit_loop

l_fb_extv:
    add   EFF_TV, TV_CYCLES, FAULT_ARG
    ldi   TMP, 256
    min   EFF_TV, EFF_TV, TMP                    ; clamp to the single-loop HW max
    qba   l_bit_loop

; =============================================================================
; l_bit_loop -- shared MSB-first shift-out for every fault mode that still
; drives real bits (normal, status/error, sentinel, all-ones, shortened,
; excessive-tv, stuck-high). frame_bits is 64 bits split across two 32-bit
; words; bit position BITPOS>=32 selects the high word, else the low word --
; two plain 32-bit-register paths, picked once per bit, rather than one
; contorted 64-bit shift sequence.
; =============================================================================
l_bit_loop:
    sub   BITPOS, FRAME_WIDTH, 1      ; BITPOS always starts at the frame's true MSB --
    mov   BITCTR, BIT_COUNT           ; BITCTR (separate!) counts how many bits we still
                                       ; owe this frame, so a shortened frame (mode 5,
                                       ; BIT_COUNT<FRAME_WIDTH) sends the TOP BIT_COUNT
                                       ; bits, not the bottom ones
l_bl_next_bit:
    loop  l_bl_tv_done, EFF_TV        ; tv: data becomes valid EFF_TV loop-iterations
    nop                               ; after this bit period's starting falling edge
l_bl_tv_done:
    qbbs  l_bl_high_half, BITPOS, 5   ; bit 5 of BITPOS set => BITPOS>=32 => high word
    lsr   BITVAL, EFF_LO, BITPOS
    qba   l_bl_test
l_bl_high_half:
    sub   BITPOS_HI, BITPOS, 32
    lsr   BITVAL, EFF_HI, BITPOS_HI
l_bl_test:
    and   BITVAL, BITVAL, 1
    qbeq  l_bl_data_low, BITVAL, 0
    set   r30, r30, DATA_PIN
    qba   l_bl_drive_done
l_bl_data_low:
    clr   r30, r30, DATA_PIN
l_bl_drive_done:
    wbs   r31, CLK_PIN                ; rising edge = reader's sample point
    sub   BITCTR, BITCTR, 1
    qbne  l_bl_more, BITCTR, 0
    qbeq  l_bl_skip_final_wbc, ACTIVE_FAULT, 5   ; mode 5: abandon before the last falling edge
    wbc   r31, CLK_PIN
l_bl_skip_final_wbc:
    qba   l_frame_drive_done
l_bl_more:
    sub   BITPOS, BITPOS, 1
    wbc   r31, CLK_PIN
    qba   l_bl_next_bit

; =============================================================================
; l_wait_only_frame -- mode 4 (missing response): data is left exactly where
; it is (idle high) and never touched; the frame's clock edges are still
; consumed so the next idle boundary is detected correctly.
; =============================================================================
l_wait_only_frame:
    sub   BITPOS, FRAME_WIDTH, 1
l_wo_next_bit:
    wbs   r31, CLK_PIN
    qbne  l_wo_more, BITPOS, 0
    wbc   r31, CLK_PIN
    qba   l_frame_drive_done
l_wo_more:
    sub   BITPOS, BITPOS, 1
    wbc   r31, CLK_PIN
    qba   l_wo_next_bit

; =============================================================================
; l_stuck_low_frame -- mode 7 (data stuck low): force the pin low immediately
; and never restore idle-high, including between frames -- the one mode whose
; end-of-frame behavior actually differs from every other mode.
; =============================================================================
l_stuck_low_frame:
    clr   r30, r30, DATA_PIN
    sub   BITPOS, FRAME_WIDTH, 1
l_sl_next_bit:
    wbs   r31, CLK_PIN
    qbne  l_sl_more, BITPOS, 0
    wbc   r31, CLK_PIN
    qba   l_frame_end_no_idle_restore
l_sl_more:
    sub   BITPOS, BITPOS, 1
    wbc   r31, CLK_PIN
    qba   l_sl_next_bit

; =============================================================================
; l_frame_drive_done / l_frame_end_no_idle_restore -- common end-of-frame
; path. Every mode except stuck-low (7) falls through the idle-high restore;
; stuck-low jumps straight past it. From here on, sequence/hold bookkeeping
; is identical for every mode -- a fault frame still completes a frame for
; sequence-advancement purposes.
; =============================================================================
l_frame_drive_done:
    set   r30, r30, DATA_PIN
l_frame_end_no_idle_restore:
    ; Dynamic mode has no static sequence/hold cursor to advance.  Returning
    ; directly to the idle boundary ensures the next prepared frame is based
    ; on the next observed producer head, while static mode below is unchanged.
    ldi   TMP, 0
    lbco  &TMP, c28, SSI_CONFIG_PRODUCER_MODE_OFF, 1
    qbne  l_dynamic_frame_done, TMP, 0
    qbeq  l_hold_time, HOLD_MODE, 1
    add   HOLD_PROGRESS, HOLD_PROGRESS, 1       ; mode 0: one completed frame
    qba   l_hold_check
l_hold_time:
    lsl   TMP, FRAME_WIDTH, HOLD_TIME_BIT_OVERHEAD_SHIFT
    add   TMP, TMP, TV_CYCLES
    add   HOLD_PROGRESS, HOLD_PROGRESS, TMP
l_hold_check:
    qbge  l_hold_advance, HOLD_THRESH, HOLD_PROGRESS   ; HOLD_PROGRESS >= HOLD_THRESH ?
    qba   l_seq_done
l_hold_advance:
    ldi   HOLD_PROGRESS, 0
    add   SLOT_INDEX, SLOT_INDEX, 1
    qbeq  l_slot_wrap, SLOT_INDEX, 16
    lsl   SLOTOFF, SLOT_INDEX, 4
    add   SLOTOFF, SLOTOFF, FRAMES_OFF
    lbco  &TMP, c28, SLOTOFF, 8                 ; peek candidate frame_bits for sentinel check
    qbne  l_hold_finish, TMP, SENTINEL_ONES
    qbne  l_hold_finish, TMP2, SENTINEL_ONES
l_slot_wrap:
    ldi   SLOT_INDEX, 0
l_hold_finish:
; Synchronous SSI profiles form/latch the next position during the bounded
; inter-frame window. The reader's Tp validation guarantees that this wait
; completes before its next falling edge, so no mixed-generation frame can
; be observed. Asynchronous profiles keep the existing shortest path.
l_formation_gate:
    ; LBCO of one byte preserves the upper register bytes on PRU hardware.
    ; Clear them before testing the byte-valued formation mode.
    ldi   TMP, 0
    lbco  &TMP, c28, SSI_CONFIG_FORMATION_MODE_OFF, 1
    qbeq  l_load_active_slot, TMP, 0
    lbco  &TMP, c28, SSI_CONFIG_FORMATION_PAUSE_OUTER_ITERS_OFF, 4
    qbeq  l_load_active_slot, TMP, 0
    mov   LOOPCNT, TMP
l_formation_outer:
    loop  l_formation_inner_done, SSI_PAUSE_INNER_ITERS
    nop
l_formation_inner_done:
    sub   LOOPCNT, LOOPCNT, 1
    qbne  l_formation_outer, LOOPCNT, 0
    qba   l_load_active_slot
l_seq_done:
    qba   l_restart_sync

l_dynamic_frame_done:
    qba   l_restart_sync
