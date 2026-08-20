; ssi_generic_emulator.asm - runtime-configurable SSI encoder emulator (PRU0)
; ---------------------------------------------------------------------------
; Generic replacement for the fixed ssi_encoder_sequence_emulator_12bit.asm /
; ssi_encoder_emulator_12bit.asm behavior: bit width, timing, sequencing and
; fault injection are all driven by the shared-memory config block described
; in ssi_config_abi.inc / docs/superpowers/specs/2026-08-19-generic-runtime-ssi-design.md
; instead of hardcoded .set constants. The old fixed files are untouched.
;
; Virtual loopback (same pin convention as ssi_encoder_sequence_emulator_12bit.asm):
;   reader  R30.0  (CLK out) -> this program R31.16 (CLK in)
;   this program R30.0 (DATA out) -> reader R31.8   (DATA in)
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

CLK_PIN       .set 16
DATA_PIN      .set 0

; Real (non-reserved) config fields run from SSI_CONFIG_BASE for this many
; bytes; the 172-byte reserved tail is never read.
SSI_CONFIG_REAL_FIELDS_SIZE .set 0x54

; SSI_FRAMES_BASE (0x00010100) - SSI_CONFIG_BASE (0x00010000). ADD's immediate
; operand is limited to 8 bits by this assembler, so 0x100 has to live in a
; register rather than as a literal.
SSI_FRAMES_OFFSET_FROM_CFG .set 0x100

; Debounce threshold = tm_pause_outer_iters * 16, via a 4-bit left shift.
; Rationale for 16: the existing fixed emulator (ssi_encoder_emulator_12bit.asm)
; uses PAUSE_THRESH=300 consecutive-high polls against its reader's
; PAUSE_OUTER=15 monoflop count -- a ~20:1 ratio. 16 keeps that same order of
; magnitude while being a clean shift (x16 = <<4) instead of a runtime divide.
DEBOUNCE_MULTIPLIER_SHIFT .set 4
; Cap so a pathologically large tm_pause_outer_iters can't make idle-sync
; effectively never happen; a few thousand polls is still negligible next to
; a real monoflop wait and comfortably bounds worst-case sync latency.
DEBOUNCE_POLL_CAP .set 4000

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

    sbco  &APPLIED_GEN, c28, SSI_CONFIG_PRU0_ACK_GENERATION_OFF, 4
    set   r30, r30, DATA_PIN

    qba   l_load_active_slot

; =============================================================================
; l_load_active_slot -- load SLOT_INDEX's frame_bits + hold override, derive
; HOLD_THRESH, then fall into idle sync. Reached whenever slot_index changes
; and whenever a new generation is applied (never re-read otherwise).
; =============================================================================
l_load_active_slot:
    lsl   SLOTOFF, SLOT_INDEX, 4
    add   SLOTOFF, SLOTOFF, FRAMES_OFF
    lbco  &FRAME_LO, c28, SLOTOFF, 12   ; FRAME_LO, FRAME_HI, raw hold_override (r14)
    qbne  l_use_override, r14, 0
    mov   HOLD_THRESH, HOLD_COUNT
    qba   l_restart_sync
l_use_override:
    mov   HOLD_THRESH, r14
    qba   l_restart_sync

; =============================================================================
; l_restart_sync -- once per frame, at the idle boundary: cheap generation
; check (single 4-byte read), then the fixed program's debounce/sync pattern
; using the config-derived DEBOUNCE_THRESH instead of a hardcoded constant.
; =============================================================================
l_restart_sync:
    lbco  &TMP, c28, SSI_CONFIG_REQUESTED_GENERATION_OFF, 4
    qbne  l_apply_config, TMP, APPLIED_GEN   ; changed -> full reapply, nothing else read here
    mov   LOOPCNT, DEBOUNCE_THRESH
l_check_high:
    qbbc  l_restart_sync, r31, CLK_PIN
    sub   LOOPCNT, LOOPCNT, 1
    qbne  l_check_high, LOOPCNT, 0

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
    ldi   TMP, 64                     ; poll-batch size before re-checking generation
l_wait_batch:
    qbbc  l_falling_edge_seen, r31, CLK_PIN
    sub   TMP, TMP, 1
    qbne  l_wait_batch, TMP, 0
    lbco  &TMP2, c28, SSI_CONFIG_REQUESTED_GENERATION_OFF, 4
    qbne  l_apply_config, TMP2, APPLIED_GEN
    qba   l_wait_falling_edge
l_falling_edge_seen:

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
    qba   l_load_active_slot
l_seq_done:
    qba   l_restart_sync
