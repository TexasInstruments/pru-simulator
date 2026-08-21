# ssi_generic_emulator — Project Report

Runtime-configurable SSI encoder emulator (PRU0) implementation in the
pru-simulator repository, replacing hardcoded bit width/timing/sequence
`.set` constants with a shared-memory configuration block.

## 1. Initial prompt

Task 3 of the generic runtime-configurable SSI effort (see
`docs/superpowers/plans/2026-08-19-generic-runtime-ssi.md`): implement a
generic PRU0 SSI encoder emulator driven entirely by the config ABI produced
by Task 2, supporting all 8 fault modes, sequence hold (frame-count or
time-based), a stability-debounced clock sync, and atomic generation
switching at an idle frame boundary.

## 2. Design choices and decisions

### 2.1 Config caching, not per-instruction reads

The program reads the entire config block once per applied generation (in
`l_apply_config`) into persistent registers, rather than re-reading shared
memory every bit or every frame. `requested_generation` is checked once per
idle boundary; a mismatch triggers a full re-read of config + frame slots and
a `pru0_ack_generation` write.

### 2.2 Debounce formula for clock synchronization

This program is reactive (an externally driven clock), unlike the fixed
emulator's simpler polling. The debounce threshold is derived from
`tm_pause_outer_iters * 16` (a left shift by `DEBOUNCE_MULTIPLIER_SHIFT=4`),
chosen to keep roughly the same order-of-magnitude ratio (~20:1) the existing
fixed emulator uses against its reader's monoflop count, while staying a
clean shift instead of a runtime divide. `DEBOUNCE_POLL_CAP=4000` bounds
worst-case sync latency against a pathologically large
`tm_pause_outer_iters`.

### 2.3 Time-based sequence hold is an estimate, not measured

`sequence_hold_mode==1` has no free-running timer register available, so
elapsed time per completed frame is estimated as
`frame_width_bits * 8 + tv_cycles` (`HOLD_TIME_BIT_OVERHEAD_SHIFT=3`, a
single shift since this ISA has no multiply), approximating the fixed
emulator's actual ~9-10 instruction per-bit loop body. This is a documented
approximation, not exact wall-clock cycles.

### 2.4 Deadlock fix (task-3-fix-1)

A regression was found and fixed after the initial Task 3 implementation:
`l_restart_sync`'s original blocking `wbc r31, CLK_PIN` could park this
program forever if a `topology=0` (loopback) generation change landed right
after debounce finished and before the next falling edge, because the
reader (PRU1) would detour to wait for this program's ack instead of sending
that edge — neither side could make progress. The fix replaces the blocking
wait with a bounded, generation-checking poll loop, so a pending generation
change is always noticed instead of stalling indefinitely. See
`.superpowers/sdd/generic_runtime_ssi_implementation_plan/task-3-fix-1-brief.md`
and `tests/test_ssi_generic_emulator.py::test_generation_change_during_idle_gap_after_debounce_does_not_deadlock`.

### 2.5 PRU0 never encodes

Per the design doc, all Gray/Tannenbaum/alignment/padding logic stays in the
Python runtime module (`pru_io/ssi_runtime.py`), which pre-packs each frame
slot's raw wire bits before this program ever sees them. This program is a
pure bit-shifter: MSB-first, `frame_width_bits` clocks, from whatever is
already in the active slot's `frame_bits`.

### 2.6 Dashboard integration

The simulator dashboard now exposes the paired generic SSI workflow through
the **Generic SSI Runtime** panel. The server loads this emulator on PRU0, the
generic reader on PRU1, installs the virtual loopback wires, and wraps the
existing `SSIRuntime` staging/apply API. The browser sends configuration, raw
frame slots, and the apply request in order, so a complete generation is
committed before the PRUs acknowledge it. The panel also runs the pair and
displays the latest mailbox and trace counters.

`SSIRuntime.set_raw_frames()` validates that every raw wire value fits the
staged frame width and clears unused slots. This is UI support only; the PRU
bit loop and its deterministic edge-driven behavior are unchanged.

## 3. Files generated

| File | Purpose |
|------|---------|
| `source/ssi_generic_emulator/ssi_generic_emulator.asm` | PRU0 firmware |
| `source/ssi_generic_emulator/README.md` | Usage documentation |
| `source/ssi_generic_emulator/PROJECT_REPORT.md` | This report |
| `tests/test_ssi_generic_emulator.py` | Bit-level and fault-mode tests |
| `tests/test_ssi_runtime_ui.py` | Dashboard contract, websocket workflow, and frame-width validation tests |
| `ui/server.py` | Generic SSI load/stage/frame/apply/read WebSocket actions |
| `ui/static/index.html` | Generic SSI Runtime panel markup |
| `ui/static/app.js` | Generic SSI Runtime panel behavior and state rendering |

## 4. Testing

* **Unit/bit-level:** direct `sim.memory_read`/`memory_write` pokes of the
  config block and frame slots (no runtime module involved), mirroring
  `test_ssi_encoder_sequence_emulator.py`'s existing style.
* **Fault modes:** one test per `fault_mode` value (1 through 8) —
  `test_fault_mode_1_status_bits_is_pure_passthrough` through
  `test_fault_mode_8_data_stuck_high` — each asserting the malformed output
  and, where `fault_repeat_count` is set, that operation returns to normal
  afterward.
* **Generation handshake:** `test_generation_change_applied_only_at_idle_boundary`
  (mid-frame bump has no effect until the next idle boundary) and
  `test_generation_change_during_idle_gap_after_debounce_does_not_deadlock`
  (the fix-1 regression test).
* **Wide frames:** `test_frame_width_40_bits_straddles_both_halves` and
  `test_frame_width_64_bits_straddles_both_halves` exercise the two-register
  (`FRAME_LO`/`FRAME_HI`) accumulator path.
* **Sequencing:** `test_sequence_hold_mode_0_frame_count_with_sentinel_wrap`,
  `test_sequence_hold_mode_0_wraps_at_16th_slot`, and
  `test_sequence_hold_mode_1_time_based`.
* **Integration:** `test_normal_transmission_matches_reader_capture` pairs
  this program with `ssi_generic_reader.asm` end-to-end.
* **Dashboard integration:** `tests/test_ssi_runtime_ui.py` verifies the
  panel contract, profile catalog, raw-frame parser, paired load/stage/frame/
  apply/read WebSocket workflow, and rejection of a value wider than the
  selected frame width.
* **Full suite:** `python -m pytest tests/test_ssi_generic_emulator.py -v`
  must pass in isolation, and the full repository suite must show no
  regressions.

## 5. Notes for adaptation

* **`formation_mode`/`formation_pause_outer_iters` are unimplemented.**
  These fields exist in the config ABI and are set per-profile (e.g.
  `ATM60_90` sets `formation_mode=1`, `formation_pause_outer_iters=180`
  in `pru_io/ssi_runtime.py`), but this program never reads either field —
  every profile behaves as asynchronous regardless of the configured
  `formation_mode`. This was a gap in the original task briefs for Tasks 2-4,
  not a bug introduced later, and it is not fixed by this documentation
  task. Anyone adapting this code for a family that requires synchronous
  position formation timing must implement the read/behavior first.
* **No encode-side value-range validation.** This program has no visibility
  into "natural" position values at all — it only ever shifts prepacked wire
  bits. The corresponding gap lives one layer up, in
  `pru_io/ssi_runtime.py` (see that project's own notes): there is a
  `decode_position` (wire → natural) but no encode-side counterpart that
  rejects a natural value too large for the configured resolution before it
  is packed into a frame slot.
* **`alignment` is also currently unread.** Observed during this
  documentation/audit pass (not one of the two limitations named in the
  originating task brief, but grounded in the same kind of gap): the
  `alignment` config field is defined in the schema and in every
  `Profile`, but neither this program nor `ssi_generic_reader.asm` reads it,
  and `pru_io/ssi_runtime.py` never derives `position_offset_bits` from it.
  It is currently pure passthrough state with no behavioral effect — left
  as-is per this task's no-new-code-behavior scope, flagged here so it is
  not mistaken for working left/right-justification support.
* When adapting for real hardware: clock/pinmux initialization, the actual
  300 MHz core clock, and pin assignments per schematic are still owed by
  the (separate, deferred) CCS/R5 project — none of that exists in this
  simulator-only slice.

## 6. Running with the UI simulator

### 6.1 Dashboard panel

1. Start `python ui/server.py` and open `http://localhost:8080`.
2. In **Generic SSI Runtime**, click **Load PRU0 emulator + PRU1 reader**.
3. Leave `ABC, AAA, BCA, 12A, CC2`, choose **Mailbox + trace**, and click
   **Apply atomically**.
4. Enable Signal Graph recording if waveforms are needed, click **Run pair**,
   then **Refresh** to inspect the mailbox and trace counters.
5. Change the profile, width, timing, sequence, or fault fields and apply
   again. Values wider than the selected frame width are rejected.

### 6.2 Direct simulator path

1. Start the dashboard: `python ui/server.py`.
2. Load `ssi_generic_reader.asm` on **PRU1** and this file on **PRU0**.
3. Add GPIO wires: `pru1:GPO0` → `pru0:GPI16` (clock), `pru0:GPO0` →
   `pru1:GPI8` (data).
4. Because both programs require a fully-populated config block before they
   do anything sensible (a bare `hard_reset()` leaves e.g.
   `frame_width_bits=0`), either drive the config block through
   `pru_io/ssi_runtime.py`'s `SSIRuntime` (recommended — staging + `apply()`
   handles this automatically, including the default 12-bit/4 MHz profile)
   or poke it directly as the automated tests do.
5. Enable Signal Graph recording, then run in multi-core mode.
6. Inspect the mailbox (`0x0200`) or trace buffer (`0x0400`) via
   `memory_read`, or through the MCP server's `ssi_read_mailbox`/
   `ssi_read_trace` operations.
