# ssi_generic_reader — Project Report

Runtime-configurable SSI encoder reader (PRU1) implementation in the
pru-simulator repository, replacing hardcoded bit width/clock-timing `.set`
constants with a shared-memory configuration block.

## 1. Initial prompt

Task 4 of the generic runtime-configurable SSI effort (see
`docs/superpowers/plans/2026-08-19-generic-runtime-ssi.md`): implement a
generic PRU1 SSI reader driven entirely by the config ABI produced by
Task 2, generating its own clock from configured cycle counts, supporting up
to 64-bit frames across two accumulator registers, publishing a seqlock
mailbox, optionally appending trace records, and switching generation
atomically at its own idle boundary.

## 2. Design choices and decisions

### 2.1 Active clock master, no debounce needed

Unlike `ssi_generic_emulator.asm` (PRU0, reactive to an externally driven
clock), this program generates its own `clock_high_cycles`/
`clock_low_cycles` timing exactly like the existing fixed
`ssi_reader_4mhz_12bit.asm` reader already does. There is no debounce/sync
logic anywhere in this file.

### 2.2 Structural extraction only, no semantic decode

`position_value`/`status_bits` are extracted purely by
offset/width — `l_extract_field` has no idea whether the bits it pulled out
are straight binary, Gray, or Tannenbaum. Semantic decode of *how* they are
encoded is `pru_io/ssi_runtime.py`'s job (`decode_position`), mirroring
PRU0's "never encodes" rule from the other side of the wire.

### 2.3 64-bit extraction across a 32-bit boundary

Because `frame_width_bits` can be up to 64 and the position/error fields can
therefore straddle the low/high accumulator words, `l_extract_field`
explicitly handles three cases (field entirely in the low word, field
entirely in the high word, field straddling both) rather than assuming a
single-word extraction. `test_position_field_straddles_32_bit_boundary`
exercises the previously-untested third case with hand-verified numbers in
its own docstring.

### 2.4 Seqlock publication

The mailbox at `0x0200` is published every completed frame using the
standard seqlock pattern (write an odd `seq`, write every field, write the
matching even `seq`), so a consumer mid-read never observes a torn write —
it retries while `seq` is odd or changed across the read.

### 2.5 Trace capture without stalling acquisition

When `capture_mode == 2`, a 24-byte trace record is appended per frame at
`0x0400` (a 1,024-slot ring buffer) using the same monotonic
`trace_write_index`/`trace_overrun_count` counters the design doc specifies,
without adding a stall to the normal per-frame path.

### 2.6 Dashboard integration

The simulator dashboard now exposes the paired generic SSI workflow through
the **Generic SSI Runtime** panel. The server loads this reader on PRU1, the
generic emulator on PRU0, installs the virtual loopback wires, and wraps the
existing `SSIRuntime` staging/apply API. The browser sends configuration, raw
frame slots, and the apply request in order, then runs both cores and displays
the mailbox and trace counters.

The reader remains responsible only for timing, sampling, structural field
extraction, mailbox publication, and trace capture. Raw frame values are
validated by the host-side runtime before they reach the emulator; semantic
encoding decode remains outside this PRU program.

## 3. Files generated

| File | Purpose |
|------|---------|
| `source/ssi_generic_reader/ssi_generic_reader.asm` | PRU1 firmware |
| `source/ssi_generic_reader/README.md` | Usage documentation |
| `source/ssi_generic_reader/PROJECT_REPORT.md` | This report |
| `tests/test_ssi_generic_reader.py` | Bit-level and integration tests |
| `tests/test_ssi_runtime_ui.py` | Dashboard contract, websocket workflow, and frame-width validation tests |
| `ui/server.py` | Generic SSI load/stage/frame/apply/read WebSocket actions |
| `ui/static/index.html` | Generic SSI Runtime panel markup |
| `ui/static/app.js` | Generic SSI Runtime panel behavior and state rendering |

## 4. Testing

* **Unit/bit-level:** direct `sim.memory_read`/`memory_write` pokes of the
  config block and frame slots, paired with Task 3's
  `ssi_generic_emulator.asm` on `pru0` for anything needing a real encoder on
  the other end of the wire.
* **Structural extraction:**
  `test_normal_transmission_position_value_matches_injected`,
  `test_position_and_error_field_afs_afm60_shape` (position and error at
  different non-zero offsets, both within the low word), and
  `test_position_field_straddles_32_bit_boundary` (the >32-bit straddling
  case).
* **Seqlock/counters:**
  `test_mailbox_seq_even_between_frames_and_counters_monotonic` — `seq`
  always even between frames, `frame_counter`/`timestamp_cycles` strictly
  increasing.
* **Trace buffer:** `test_capture_mode_2_trace_overrun_and_wrap` — runs past
  1,024 frames and asserts the exact overrun count and that the
  most-recently-written slot holds the latest frame, not a stale one.
* **Generation handshake / topology:**
  `test_topology_reader_only_skips_pru0_ack_wait` and
  `test_generation_change_applied_only_at_idle_boundary` (this file's own
  active-master mirror of the emulator's identically-named test).
* **Dashboard integration:** `tests/test_ssi_runtime_ui.py` verifies the
  panel contract, profile catalog, raw-frame parser, paired load/stage/frame/
  apply/read WebSocket workflow, and rejection of a value wider than the
  selected frame width.
* **Full suite:** `python -m pytest tests/test_ssi_generic_reader.py -v`
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
  into "natural" position values — it only ever samples raw wire bits and
  extracts them structurally. `pru_io/ssi_runtime.py` has `decode_position`
  (wire → natural) but no encode-side counterpart that rejects a natural
  value too large for the configured resolution before it is packed into a
  frame slot; existing tests hand-construct already-valid wire values,
  bypassing this gap entirely rather than exercising it.
* **`alignment` is also currently unread.** Observed during this
  documentation/audit pass (not one of the two limitations named in the
  originating task brief, but grounded in the same kind of gap): the
  `alignment` config field is defined in the schema and in every
  `Profile`, but neither this program nor `ssi_generic_emulator.asm` reads
  it, and `pru_io/ssi_runtime.py` never derives `position_offset_bits` from
  it. It is currently pure passthrough state with no behavioral effect —
  left as-is per this task's no-new-code-behavior scope, flagged here so it
  is not mistaken for working left/right-justification support.
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
2. Load this file on **PRU1** and `ssi_generic_emulator.asm` on **PRU0**.
3. Add GPIO wires: `pru1:GPO0` → `pru0:GPI16` (clock), `pru0:GPO0` →
   `pru1:GPI8` (data).
4. Drive the config block through `pru_io/ssi_runtime.py`'s `SSIRuntime`
   (stage a named profile, `apply()`) or poke it directly as the automated
   tests do — a bare `hard_reset()` alone leaves a degenerate, non-working
   config (e.g. `frame_width_bits=0`).
5. Enable Signal Graph recording, then run in multi-core mode.
6. Inspect the mailbox (`0x0200`) or trace buffer (`0x0400`) via
   `memory_read`, or through the MCP server's `ssi_read_mailbox`/
   `ssi_read_trace` operations.
7. To exercise reader-only mode (`topology=1`), load only this file on
   **PRU1** and either leave the data pin floating or drive it with
   `sim.ssi_inject`.
