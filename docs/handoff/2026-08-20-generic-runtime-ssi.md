# Generic runtime-configurable SSI — simulator-scoped wrap-up

This handoff closes out the simulator-only slice of the generic
runtime-configurable SSI effort (Tasks 2-6 of
`docs/superpowers/plans/2026-08-19-generic-runtime-ssi.md`, design reference
`docs/superpowers/specs/2026-08-19-generic-runtime-ssi-design.md`). All code
was implemented and independently reviewed in prior tasks; this note
documents what exists and audits test coverage against the parent plan's
Task 7 verification list
(`encoder-workspace/docs/protocol/generic_runtime_ssi_implementation_plan.md`,
`## Task 7`). No new PRU/runtime behavior was added by this task.

## What was built

- **Task 2 — shared-memory ABI (`0x00010000`, `c28`):** a language-neutral
  schema (`schema/ssi_config_abi.json`) generates
  `source/ssi_config_abi.inc` (PRU `.set` constants) and
  `pru_io/ssi_config_abi.py` (Python offsets + `pack_config`/`unpack_config`/
  `unpack_mailbox`/`pack_frame_slot`/`unpack_trace_record` helpers). Four
  memory-map sections: config+handshake (`0x0000`-`0x00FF`), 16 prepacked
  frame slots (`0x0100`-`0x01FF`), a seqlock mailbox (`0x0200`-`0x023F`),
  capture control+counters (`0x0240`-`0x027F`), and a 1,024-record trace
  ring buffer (`0x0400`-`0x63FF`). A drift test
  (`tests/test_ssi_config_abi_generated.py`) regenerates both outputs and
  asserts byte-identity with the checked-in files.
- **Task 3 — `source/ssi_generic_emulator/ssi_generic_emulator.asm` (PRU0):**
  a generic, config-driven encoder emulator: debounced clock sync, up to
  16-slot sequencing with frame-count/time-based hold, all 8 fault modes,
  and generation switching at an idle boundary. A deadlock in the original
  implementation (`l_restart_sync`'s blocking `wbc` could park PRU0 forever
  if a `topology=0` generation change landed right after debounce finished)
  was found and fixed by replacing the blocking wait with a bounded,
  generation-checking poll loop — see
  `.superpowers/sdd/generic_runtime_ssi_implementation_plan/task-3-fix-1-brief.md`
  and
  `tests/test_ssi_generic_emulator.py::test_generation_change_during_idle_gap_after_debounce_does_not_deadlock`.
- **Task 4 — `source/ssi_generic_reader/ssi_generic_reader.asm` (PRU1):** a
  generic, config-driven reader: self-generated clock, up to 64-bit
  structural field extraction across two accumulator registers, seqlock
  mailbox publication, optional trace capture, and reader-only
  (`topology=1`) support that skips waiting for PRU0's ack.
- **Task 5 — `pru_io/ssi_runtime.py`:** the Python stand-in for real R5
  firmware — a `Profile` dataclass, 13 named profiles (12 SICK SSI encoder
  families plus `CUSTOM_LEGACY_12BIT_4MHZ`, the constructor's implicit
  default so every pre-existing fixed-firmware test keeps passing
  unmodified), `stage()`/`apply()`/`wait_for_apply()` for validated,
  transactional configuration, and `decode_position()` for
  binary/Gray/Tannenbaum semantic decode (Gray-excess raises
  `NotImplementedError` by design — no profile in this table uses it and no
  family-specific offset formula is documented anywhere in this ABI).
- **Task 6 — trace/capture parity + MCP:** `SSIRuntime.read_trace()`/
  `read_mailbox()` (ring-buffer-aware, seqlock-safe), and five new MCP
  operations in `mcp_server/server.py` (`ssi_profile_list`, `ssi_stage`,
  `ssi_apply`, `ssi_read_mailbox`, `ssi_read_trace`) mirroring how
  `pru_ssi_inject` already wraps `Simulator` for the fixed-profile case.

## Known limitations

1. **`formation_mode`/`formation_pause_outer_iters` are unimplemented.**
   Both fields exist in the config ABI and are set per-profile (e.g.
   `ATM60_90` sets `formation_mode=1`, `formation_pause_outer_iters=180`),
   but neither `ssi_generic_emulator.asm` nor `ssi_generic_reader.asm` reads
   either field — every profile behaves as asynchronous regardless of
   configuration. This was a gap in the original Tasks 2-4 briefs, not a
   regression introduced later, and it is **not fixed** by this task.
2. **No encode-side value-range validation.** `ssi_runtime.py` has
   `decode_position` (wire → natural value) but no encode-side counterpart
   — nothing rejects a natural position value that doesn't fit the
   configured resolution before it is packed into a frame slot. Every
   existing test hand-constructs already-valid wire values, bypassing this
   gap entirely rather than exercising it.
3. **Additional observation (found during this audit, not one of the two
   items named in the task brief): `alignment` is also currently unread.**
   The field is defined in the schema and in every `Profile`, but neither
   generic `.asm` file reads it, and `ssi_runtime.py` never derives
   `position_offset_bits` from it — it is pure passthrough state with no
   behavioral effect. Flagged here per this repo's "surface tradeoffs,
   don't hide gaps" convention; not fixed by this task.

None of the three items above are testable as a coverage gap to close now —
there is no behavior to assert against. They are noted so anyone adapting
this code doesn't mistake the *absence* of a test for *proof the feature
works*.

## What's out of scope

- **The real CCS/R5/hardware project.** Deferred per explicit user
  instruction; tracked separately (see
  `encoder-workspace/docs/protocol/generic_runtime_ssi_implementation_plan.md`'s
  own Tasks 1/2/3/4/5/6 hardware-facing halves and "Hardware acceptance"
  list). Not started in this repository.
- **Any web/dashboard UI for SSI configuration.** No SSI config panel exists
  in `pru-simulator`'s UI today; the only surfaces are the MCP operations
  listed above and direct Python (`SSIRuntime`) / memory-poke access used by
  tests.

## Verification checklist audit

Against the parent master plan's `## Task 7` "Automated tests must cover"
list. Simulator-testable items are confirmed against a named, currently
passing test function; hardware-only items are called out as deferred, not
silently dropped.

| Checklist item | Coverage |
|---|---|
| Default switched-core loopback sequence | `tests/test_ssi_encoder_sequence_emulator.py::test_ssi_encoder_sequence_emulator_repeats_distinct_values` (fixed pair, PRU0=emulator/PRU1=reader per the role-reversal handoff) plus `tests/test_ssi_runtime.py::test_default_behavior_matches_fixed_12bit_4mhz` (generic pair under the implicit `CUSTOM_LEGACY_12BIT_4MHZ` default) |
| Binary packing | End-to-end via multiple profile tests, e.g. `tests/test_ssi_runtime.py::test_profile_ahs_ahm36_singleturn_end_to_end`, `::test_profile_afs_afm60_multiturn_30bit_end_to_end`, `::test_profile_custom_legacy_12bit_4mhz_explicit_apply_round_trips`; decode passthrough asserted directly in `::test_decode_position_binary_and_tannenbaum_are_passthrough` |
| Gray packing | Decode-side only: `tests/test_ssi_runtime.py::test_decode_position_gray_4bit_hand_computed` is a standalone hand-traced unit test of `decode_position`, not an end-to-end PRU frame. **Gap:** no named profile sets `encoding_type=1`, so there is no simulator end-to-end test that clocks a Gray-encoded frame through the emulator/reader pair and decodes it — and there is no encode-side (natural → Gray wire bits) helper to test in the first place (see Known limitation 2, the same missing-encode-side gap in different clothing) |
| Gray-excess packing | Explicitly `NotImplementedError` by design — `tests/test_ssi_runtime.py::test_decode_position_gray_excess_not_implemented` confirms the `raise`, correctly not testing a behavior that doesn't exist |
| Tannenbaum packing | `tests/test_ssi_runtime.py::test_profile_atm60_90_tannenbaum_sync_end_to_end` (25-bit multiturn, `encoding_type=3`) plus the passthrough assertion in `::test_decode_position_binary_and_tannenbaum_are_passthrough` |
| Left/right alignment | **Gap, not a regression:** `alignment` is unread by both `.asm` files and unused by `ssi_runtime.py` (see Known limitation 3 above) — there is nothing to test, and no test asserts alignment behavior |
| Padding | Not independently exercised as a distinct behavior — `padding_width_bits` is enforced only as part of `stage()`'s `frame_width_bits >= position+error+padding` bound check (`tests/test_ssi_runtime.py::test_stage_rejects_frame_width_smaller_than_position_plus_error` uses this rule, with `padding_width_bits=0` in every named profile) |
| Status bits | `tests/test_ssi_generic_reader.py::test_normal_transmission_position_value_matches_injected` (forced to 0 with no error field), `::test_position_and_error_field_afs_afm60_shape` (non-zero status extraction), and `tests/test_ssi_runtime.py::test_profile_ahs_ahm36_singleturn_end_to_end` (status through the runtime layer) |
| Sentinels | `tests/test_ssi_generic_emulator.py::test_fault_mode_2_sentinel_value` (fault-mode sentinel) and `::test_sequence_hold_mode_0_wraps_at_16th_slot` (the `0xFFFFFFFFFFFFFFFF` frame-slot-unused sentinel) |
| Every named PDF frame family, incl. frames >32 bits | All 13 profiles exist in `pru_io/ssi_runtime.PROFILES`; at least 3 have direct end-to-end tests (`AHS_AHM36_SINGLETURN`, `AFS_AFM60_MULTITURN_30BIT` at 33 bits, `ATM60_90`, `CUSTOM_LEGACY_12BIT_4MHZ`) per the task brief's ">32 bits" requirement, plus `tests/test_ssi_generic_emulator.py::test_frame_width_40_bits_straddles_both_halves` / `::test_frame_width_64_bits_straddles_both_halves` and `tests/test_ssi_generic_reader.py::test_position_field_straddles_32_bit_boundary` at the bit-mechanics level. The remaining 9 named profiles (`AHS_AHM36_MULTITURN`, `AFS_AFM60_SINGLETURN`, `AFS_AFM60_MULTITURN_27BIT`, `AFS_AFM60S_PRO_SINGLETURN`, `AFS_AFM60S_PRO_MULTITURN`, `ARS60_SHORT`, `ARS60_LONG`, `TTK70`, `KH53`) are constructed and validated by `stage()` but have no dedicated end-to-end frame test of their own — `ARS60_SHORT` is exercised indirectly by `tests/test_ssi_runtime.py::test_runtime_switch_no_frame_straddles_profile_change` |
| Exact 4 MHz legacy timing | `tests/test_ssi_runtime.py::test_default_behavior_matches_fixed_12bit_4mhz` and `::test_profile_custom_legacy_12bit_4mhz_explicit_apply_round_trips` |
| Named-profile clock limits | `tests/test_ssi_runtime.py::test_stage_rejects_named_profile_clock_override_exceeding_max_clock_hz` (KH53's `max_clock_hz` ceiling) |
| `tv` | `tests/test_ssi_generic_emulator.py::test_fault_mode_6_excessive_tv_delays_data_validity` exercises `tv_cycles` directly; every other emulator test relies on a valid `tv_cycles < clock_high_cycles` |
| `tm` | Debounce threshold derived from `tm_pause_outer_iters`; exercised by `tests/test_ssi_generic_emulator.py::test_generation_change_during_idle_gap_after_debounce_does_not_deadlock` (deliberately timed against the debounce window) and every idle-boundary generation test |
| `Tp` | `tp_pause_outer_iters` exercised by `tests/test_ssi_generic_reader.py::test_capture_mode_2_trace_overrun_and_wrap` (fastest legal `tp`) and enforced by `stage()`'s `tp_pause_outer_iters > tm_pause_outer_iters` rule (no dedicated rejection unit test exists for this specific rule — see Concerns below) |
| Async/sync formation behavior | **Not testable — not implemented**, see Known limitation 1. `ATM60_90` stages `formation_mode=1` and its end-to-end test (`test_profile_atm60_90_tannenbaum_sync_end_to_end`) passes, but only because formation mode has no observable effect on either PRU program yet |
| Runtime switching without mixed-generation frames | `tests/test_ssi_runtime.py::test_runtime_switch_no_frame_straddles_profile_change`. **This test only covers `topology=1`** (reader-only) — deliberately, per its own docstring: a `topology=0` loopback switch during the settled idle gap deadlocks the *fixed* generic programs by construction (PRU0's debounce always finishes before PRU1's longer inter-frame pause elapses), and the *actual* `topology=0` switching mechanism is already covered directly by the deadlock fix's own regression test, `tests/test_ssi_generic_emulator.py::test_generation_change_during_idle_gap_after_debounce_does_not_deadlock`. Per this task's brief, a new `topology=0` runtime-switch test through `ssi_runtime.py` was explicitly not added here |
| Rejection of out-of-range sequence values | Layout-only, as scoped: `tests/test_ssi_runtime.py::test_stage_rejects_frame_width_smaller_than_position_plus_error` (frame-too-small) and `::test_stage_rejects_clock_high_cycles_over_256`/`::test_stage_rejects_unknown_field_name`. **Value-range (natural position value) rejection is not testable — not implemented**, see Known limitation 2 |
| Latest-mailbox coherence | `tests/test_ssi_generic_reader.py::test_mailbox_seq_even_between_frames_and_counters_monotonic` (seq always even between frames, counters strictly increasing) and `tests/test_ssi_runtime_trace.py::test_read_mailbox_matches_raw_and_is_decoded` (seqlock-safe read via `SSIRuntime.read_mailbox`) |
| Trace overflow and exact overrun counting | `tests/test_ssi_generic_reader.py::test_capture_mode_2_trace_overrun_and_wrap` (raw memory, exact `n_frames - 1024`) and `tests/test_ssi_runtime_trace.py::test_read_trace_overrun_ordering_and_limit_after_wraparound` (through `SSIRuntime.read_trace`, ordering + limit + overrun count) |
| Every fault-injection mode | All 8: `tests/test_ssi_generic_emulator.py::test_fault_mode_1_status_bits_is_pure_passthrough` through `::test_fault_mode_8_data_stuck_high` |
| UI and MCP configuration parity | **MCP:** `tests/test_mcp_server.py::test_ssi_profile_list`, `::test_ssi_stage_rejects_unknown_profile`, `::test_ssi_stage_apply_read_mailbox_and_trace_end_to_end` (full stage→apply→read-mailbox→read-trace round trip). **UI:** no SSI configuration UI exists in this repository for this feature — there is nothing to have parity with yet; noted explicitly rather than silently dropped |

### Hardware acceptance (deferred to the hardware phase)

The parent plan's Task 7 "Hardware acceptance" list is entirely
real-hardware-only and out of scope for this simulator-only slice:

1. Build both PRU projects with TI PRU CGT.
2. Regenerate and include both firmware headers in the R5 project.
3. Configure both cores for 300 MHz.
4. Connect BP.11→BP.51 (clock) and BP.33→BP.57 (data).
5. Verify frequency and frame width with a logic analyzer.
6. Change width, frequency, `tm`, sequence, and encoding through UART.
7. Compare UART-decoded values with raw captured frames.
8. Confirm reader-only mode works without waiting for PRU0.

None of these are started in `pru-simulator`; they are tracked against the
separate, explicitly-deferred CCS/R5/hardware project.

### Concerns raised by this audit (not fixed, per this task's scope)

- `stage()`'s `tp_pause_outer_iters > tm_pause_outer_iters` rule (in
  `pru_io/ssi_runtime.py`) has no dedicated rejection unit test exercising
  the case where that inequality is violated — every named profile happens
  to satisfy it already, and no test stages a profile that doesn't. This is
  a minor coverage gap (not a known limitation) worth a test if this module
  is touched again.
- The 9 named profiles listed above with no dedicated end-to-end frame test
  are validated by `stage()`'s field-consistency rules only; their exact
  bit-width/error-bit-count values (from the SICK SSI interface PDF,
  reviewed earlier in this project's history) are not independently
  re-verified against a clocked frame in the simulator.

## Full suite confirmation

```
python -m pytest -q --ignore=references --ignore=tests/test_trace_plot.py --ignore=tests/test_trace_viewer.py
```

Result: **1290 passed, 1 failed, 2 xfailed** — matches the current baseline
exactly. The 1 failure
(`tests/test_perif_drift_experiment.py::test_roundtrip_with_no_host_register_setup`)
is pre-existing and unrelated to the SSI work in this handoff.
