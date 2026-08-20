# Generic runtime-configurable SSI — simulator implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> to implement this plan task-by-task. Design reference:
> `pru-simulator/docs/superpowers/specs/2026-08-19-generic-runtime-ssi-design.md`
> (read that first — it defines the exact byte layout every task below depends on).

**Goal:** Replace the fixed 12-bit/4 MHz PRU0 emulator and PRU1 reader with generic,
shared-memory-configured programs, plus a Python module that plays the role real
R5 firmware will later play (profiles, staged config, atomic apply), all provable
and testable in `pru-simulator` alone.

**Architecture:** One shared-memory ABI (ICSS_SHARED, `c28`) generated from a
schema; two generic PRU programs driven entirely by that ABI; one Python runtime
module for staging/applying config and decoding results, standing in for R5.

**Tech Stack:** PRU assembly (existing simulator dialect), Python 3 (stdlib
only — no new dependencies), pytest, JSON (schema).

## Global Constraints

- Default profile behavior (no `stage()`/`apply()` call) must remain 12-bit
  natural binary, ~4 MHz, ~12.5 µs `tm`, loopback topology — every existing
  test that loads the old fixed `.asm` files keeps passing unmodified.
- Configuration switches atomically at an idle frame boundary — no captured
  frame may straddle two generations.
- Support frames up to 64 bits; never silently truncate a value that exceeds
  the configured resolution — reject it instead (host-side validation).
- Do not interpolate missing frames — a missing/faulty frame is reported as
  such (via `status_bits`/fault detection), never synthesized.
- PRU0 never encodes (Gray/Tannenbaum/alignment/padding) and PRU1 never
  decodes — both are pure bit-shifters against prepacked/raw wire bits; all
  encoding-aware logic lives in the Python runtime module.
- Real CCS/R5/hardware work is explicitly out of scope for every task below.
- Do not commit or push (per this session's standing instruction) unless told.

---

### Task 2: SSI config ABI schema, generator, and generated artifacts

**Files:**
- Create: `pru-simulator/schema/ssi_config_abi.json`
- Create: `pru-simulator/tools/gen_ssi_abi.py`
- Create (generated, checked in): `pru-simulator/source/ssi_config_abi.inc`
- Create (generated, checked in): `pru-simulator/pru_io/ssi_config_abi.py`
- Test: `pru-simulator/tests/test_ssi_config_abi_generated.py`

**Interfaces (produced for later tasks):**
- `ssi_config_abi.py`: module-level `int` constants named exactly as in the
  design doc's tables (e.g. `ABI_VERSION_OFF`, `CLOCK_HIGH_CYCLES_OFF`,
  `FRAMES_BASE`, `FRAME_SLOT_SIZE`, `MAILBOX_BASE`, `CAPTURE_BASE`,
  `TRACE_BASE`, `TRACE_RECORD_SIZE`), plus `pack_config(**fields) -> bytes`
  (returns exactly `struct_size` bytes for the config block, zero-filling any
  field not passed) and `unpack_mailbox(data: bytes) -> dict` (inverse of the
  mailbox layout).
- `ssi_config_abi.inc`: PRU `.set` constants with the same names, upper-cased
  with `SSI_` prefix (e.g. `SSI_CLOCK_HIGH_CYCLES_OFF`, `SSI_FRAMES_BASE`).

**Acceptance:** implement exactly the four memory-map sections and every field
from the design doc's tables (config+handshake, frame slots, mailbox, capture
control+counters, trace buffer), using the units convention as written there
(outer/inner pause fields, ≤256 single-loop fields). Write
`test_ssi_config_abi_generated.py` with three tests: (1) regenerate both
outputs from the schema in a temp dir and assert byte-identical to the
checked-in files; (2) `pack_config` round-trips through a hand-built `dict` of
every field back to the same values via a matching `unpack_config` helper you
add for the config block; (3) `unpack_mailbox` correctly parses a hand-built
mailbox byte string with known field values. `python -m pytest
tests/test_ssi_config_abi_generated.py -v` must pass; full suite must show no
regressions.

---

### Task 3: Generic PRU0 SSI encoder emulator

**Depends on:** Task 2's `.inc` file.

**Files:**
- Create: `pru-simulator/source/ssi_generic_emulator/ssi_generic_emulator.asm`
- Test: `pru-simulator/tests/test_ssi_generic_emulator.py`

**Acceptance (written as a full brief when this task is dispatched, but
scope is):** on load, read the entire config block once; each pass through
the main loop, check `requested_generation` against a cached register; on
change, re-read config + frame slots and write `pru0_ack_generation`. Idle
data high; synchronize to the reader's clock using a stability-debounced
falling-edge detector (document the debounce formula chosen); shift out the
active frame slot's `frame_bits`, MSB-first, for `frame_width_bits` clocks,
applying `tv_cycles` before each bit is valid; advance through the sequence
per `sequence_hold_mode`/`sequence_hold_count`/per-slot override, wrapping at
the first `0xFFFFFFFFFFFFFFFF`-sentinel slot; apply `fault_mode` (all 8
modes from the design doc) with `fault_argument`/`fault_repeat_count`,
auto-reverting to valid frames after the repeat count elapses. Respect
`topology == 1` (reader-only) by not participating at all (this program
simply isn't loaded in that case — document, don't special-case in assembly).
Test with direct `sim.memory_read`/`memory_write` pokes of the config block
(no runtime module yet) mirroring `test_ssi_encoder_sequence_emulator.py`'s
existing style — one test per fault mode plus one confirming a mid-run
generation bump is picked up only at an idle boundary.

---

### Task 4: Generic PRU1 SSI reader

**Depends on:** Task 2's `.inc` file. Independent of Task 3's file (can run
in parallel with it), but its acceptance test needs *some* emulator to talk
to — use Task 3's `ssi_generic_emulator.asm` if Task 3 has landed, otherwise
the existing fixed `ssi_encoder_sequence_emulator_12bit.asm` for the parts of
this task that don't need the generic emulator's fault-mode support.

**Files:**
- Create: `pru-simulator/source/ssi_generic_reader/ssi_generic_reader.asm`
- Test: `pru-simulator/tests/test_ssi_generic_reader.py`

**Acceptance:** on load, read the config block once; generate the clock from
`clock_high_cycles`/`clock_low_cycles`; hold clock high between frames for
`tp_pause_outer_iters` (× fixed inner 250); sample data after
`sample_delay_cycles` (validate at the host layer, not in assembly, that
`tv_cycles < sample_delay_cycles < clock_high_cycles` — PRU1 just samples);
support up to 64 bits across two 32-bit accumulator registers; extract
`position_value`/`status_bits` by structural offset/width only (no
Gray/Tannenbaum awareness); publish the mailbox via the seqlock pattern
(odd → write fields → even); append a trace record when `capture_mode == 2`
without stalling normal acquisition; check `requested_generation` at its own
idle boundary and write `pru1_ack_generation` after applying. Respect
`topology == 1` by skipping the wait for `pru0_ack_generation`.

---

### Task 5: `ssi_runtime.py` — profiles, staged config, atomic apply, decode

**Depends on:** Task 2 (ABI module), and functionally exercises Tasks 3+4's
`.asm` files end-to-end, so should land after both.

**Files:**
- Create: `pru-simulator/pru_io/ssi_runtime.py`
- Test: `pru-simulator/tests/test_ssi_runtime.py`

**Acceptance:** a `Profile` dataclass/namedtuple (field values + documented
min/max clock limits) and one named instance per family in the parent plan's
Task 5 list (`AHS_AHM36_SINGLETURN`, `AHS_AHM36_MULTITURN`,
`AFS_AFM60_SINGLETURN`, `AFS_AFM60_MULTITURN_30BIT`,
`AFS_AFM60_MULTITURN_27BIT`, `AFS_AFM60S_PRO_SINGLETURN`,
`AFS_AFM60S_PRO_MULTITURN`, `ATM60_90`, `ARS60_SHORT`, `ARS60_LONG`, `TTK70`,
`KH53`, `CUSTOM_LEGACY_12BIT_4MHZ`), built from the source PDF's per-family
bit widths/error-bit counts (already reviewed earlier in this project's
history — the implementer brief will carry the exact numbers, this plan
entry is not the place to transcribe all twelve). `SSIRuntime.stage(**fields)`
validates against the active profile's limits (or generic limits in custom
mode) and raises on anything that would silently truncate. `apply(sim)` packs
and writes the config block (`requested_generation` last), then
`wait_for_apply(sim, timeout_steps)` polls both ack fields. A decode helper
(`decode_position(raw: int, encoding_type: int, width: int) -> int`) undoes
Gray/Gray-excess/Tannenbaum. Tests: default (no `stage`/`apply`) behaves like
today's fixed 12-bit/4 MHz test; at least three named profiles spanning
different bit widths/encodings run end-to-end (emulator frame → reader
mailbox → `decode_position` → original value); a runtime-switch test applies
profile A, captures frames, applies profile B mid-run, and asserts no
captured frame straddles the switch; a rejection test asserts `stage()`
raises for a value wider than the configured resolution instead of
truncating it.

---

### Task 6: Trace buffer, capture control, and MCP parity

**Depends on:** Tasks 2, 4, 5.

**Files:**
- Modify: `pru-simulator/pru_io/ssi_runtime.py` (add trace-read helpers)
- Modify: `pru-simulator/mcp_server/server.py` (add MCP-exposed operations
  for stage/apply/read-mailbox/read-trace — exact method signatures decided
  when this task is briefed, following this file's existing method style)
- Test: `pru-simulator/tests/test_ssi_runtime_trace.py`, MCP-layer test
  alongside the existing `test_mcp_server.py` patterns

**Acceptance:** a helper that reads all currently-valid trace records
(respecting `trace_write_index`/`trace_overrun_count`, oldest-overwritten
semantics); a test that runs past 1,024 captured frames and asserts the
overrun count and buffer contents are exactly right; MCP tool functions that
let an external client stage a profile, apply it, and read back the latest
mailbox/trace without touching `Simulator` internals directly, mirroring how
`pru_ssi_inject` already wraps `Simulator` for the fixed-profile case.

---

### Task 7: Verification sweep and documentation

**Depends on:** all of the above.

**Files:**
- New/expanded tests as gaps are found (exact files decided when briefed)
- Update: `pru-simulator/source/ssi_generic_emulator/README.md` +
  `PROJECT_REPORT.md`, `pru-simulator/source/ssi_generic_reader/README.md` +
  `PROJECT_REPORT.md` (per the `project-documentation` skill)
- Create: `pru-simulator/docs/handoff/<date>-generic-runtime-ssi.md`

**Acceptance:** run the full suite, confirm every family profile has at
least one passing end-to-end test, confirm the parent plan's Task 7
verification list is covered for everything that's simulator-testable
(binary/Gray/Gray-excess/Tannenbaum packing, alignment/padding/status bits,
every fault mode, runtime switching without mixed-generation frames,
rejection of out-of-range values, mailbox coherence, trace overflow/overrun
counting, MCP parity). Anything on that list that's real-hardware-only
(logic-analyzer verification, UART command parity) gets explicitly called
out as deferred to the hardware phase, not silently dropped.
