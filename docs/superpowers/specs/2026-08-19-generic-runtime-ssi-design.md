# Generic runtime-configurable SSI — simulator design

## Problem statement

`ssi_reader_4mhz_12bit.asm` and `ssi_encoder_sequence_emulator_12bit.asm` hardcode
one encoder shape (12-bit straight binary, 4 MHz, 12.5 µs monoflop) as assembly
`.set` constants. Testing a different SICK SSI encoder family (e.g. AFS/AFM60
18-bit singleturn, ATM60/90 25-bit Tannenbaum multiturn) currently means writing
a new `.asm` file. The goal is one generic PRU0 emulator program and one generic
PRU1 reader program whose behavior — bit width, clock timing, encoding, sequence,
faults — is driven entirely by a shared-memory configuration block, switchable at
runtime without reloading either program.

This spec covers the **simulator-only** slice: the shared-memory ABI, the generic
PRU0/PRU1 assembly behavior, and the Python module that plays the role real R5
firmware will eventually play (profile table, validation, staged-config-then-apply).
Real R5/CCS work is a separate, later effort and is out of scope here.

## Scope decisions

- **Schema + generator now, C emitter later.** The field layout is described once,
  in a language-neutral schema file, and a generator script emits an assembly
  `.inc` and a Python module from it. Both generated files are committed (per the
  parent plan's "generated artifacts must be checked in" rule) alongside a test
  that regenerates and diffs them, so drift is caught, not silently tolerated. A
  C-header emitter is added to the same generator when real hardware work starts;
  the schema itself does not need to change for that.
- **R5 stand-in lives in Python, not in the PRU cores.** The simulator has no R5
  core. `pru_io/ssi_runtime.py` (new) holds the profile table and the
  staged/apply logic that real R5 firmware will later reimplement in C against
  the same shared-memory layout. Neither PRU program can tell the difference.
- **Gray/Tannenbaum/alignment/padding stays out of the PRU bit loop.** Per the
  parent plan's Task 3 note, the config-writer (the Python stand-in here)
  pre-packs each emulator frame's raw wire bits before writing them to shared
  memory. PRU0 only ever shifts out already-encoded bits, MSB-first, for
  `frame_width_bits` clocks. This is why the frame slots at `0x0100` are called
  *prepacked*.

## Memory map (ICSS_SHARED, base `0x00010000`, reached via `c28`)

`c28` already resolves to `0x00010000` in `config/constants_am243x.cfg` — no
simulator change needed there.

| Range | Size | Contents |
|---|---|---|
| `0x0000`–`0x00FF` | 256 B | Config + handshake (below) |
| `0x0100`–`0x01FF` | 256 B | 16 × 16 B prepacked emulator frame slots |
| `0x0200`–`0x023F` | 64 B | Latest-sample mailbox (seqlock) |
| `0x0240`–`0x027F` | 64 B | Capture control + live counters |
| `0x0400`–`0x63FF` | 24,576 B | 1,024 × 24 B trace records |

All multi-byte fields are little-endian (matches PRU native byte order and the
existing `int.from_bytes(..., "little")` convention already used in
`test_ssi_encoder_sequence_emulator.py`).

### Units convention

The PRU `LOOP` instruction caps at 256 iterations per invocation, which is why
the existing fixed firmware already nests two loops for its ~12.5 µs monoflop
(`PAUSE_OUTER=15` × `PAUSE_INNER=250`). To keep the config block free of
runtime division, every field below keeps that same convention explicitly:

- `clock_high_cycles`, `clock_low_cycles`, `sample_delay_cycles`, `tv_cycles`
  are each consumed by a single `loop label, N` instruction directly — the
  config-writer (host) must keep each ≤ 256, matching hardware's own limit.
- `tm_pause_outer_iters`, `tp_pause_outer_iters`, `formation_pause_outer_iters`
  are **outer-loop repeat counts** against a fixed inner count of 250
  (`SSI_PAUSE_INNER_ITERS` in the generated `.inc`, not configurable — it
  exists only to work around the 256-iteration cap, not as a timing knob).
  The host is responsible for converting a desired wait into this outer count
  (`outer = round(desired_iters / 250)`) — this is what the parent plan's
  Task 5 "Convert engineering units into PRU cycles" means in practice.

### Config + handshake block (`0x0000`–`0x00FF`)

| Offset | Size | Field | Meaning |
|---|---|---|---|
| `0x00` | u32 | `abi_version` | Fixed `1` for this schema. A core that finds a version it doesn't recognize halts/faults rather than misreading the layout. |
| `0x04` | u32 | `struct_size` | Bytes of the config block actually in use; lets a future larger struct coexist with an older reader of the same major version. |
| `0x08` | u32 | `requested_generation` | Written last by the config-writer when committing a staged change. The single "something changed" signal. |
| `0x0C` | u32 | `pru0_ack_generation` | PRU0 (emulator) writes this equal to `requested_generation` once it has applied it. |
| `0x10` | u32 | `pru1_ack_generation` | PRU1 (reader) writes this equal to `requested_generation` once it has applied it. |
| `0x14` | u8 | `topology` | `0`=loopback (PRU0 emulator + PRU1 reader both active, PRU1 waits for PRU0's ack), `1`=reader-only (PRU1 only; PRU0 ack is skipped). |
| `0x15` | u8 | `encoding_type` | `0`=binary, `1`=gray, `2`=gray-excess, `3`=tannenbaum. Informational for PRU1's decode step; PRU0 never encodes (see Scope decisions). |
| `0x16` | u8 | `alignment` | `0`=left-justified (position starts at frame bit 0, MSB-first), `1`=right-justified (position ends at the last clock, per family docs). |
| `0x17` | u8 | `formation_mode` | `0`=asynchronous, `1`=synchronous position formation. |
| `0x18` | u16 | `frame_width_bits` | Total bits clocked per frame. `1..64`. |
| `0x1A` | u16 | `position_offset_bits` | Bit offset of the position field from the frame's first clock. |
| `0x1C` | u16 | `position_width_bits` | Total position bits (singleturn + multiturn). |
| `0x1E` | u16 | `singleturn_width_bits` | Singleturn portion of `position_width_bits`. |
| `0x20` | u16 | `multiturn_width_bits` | Multiturn portion; `0` for singleturn-only families. |
| `0x22` | u16 | `error_offset_bits` | Bit offset of the error/status field; `0xFFFF` if none. |
| `0x24` | u16 | `error_width_bits` | Error/status bit count. |
| `0x26` | u16 | `padding_width_bits` | Zero-fill bits when resolution is below the allocated width. |
| `0x28` | u32 | `clock_high_cycles` | Single-loop iterations the SSI clock line stays high per bit (≤256). |
| `0x2C` | u32 | `clock_low_cycles` | Single-loop iterations the SSI clock line stays low per bit (≤256). |
| `0x30` | u32 | `sample_delay_cycles` | Reader: single-loop iterations after the rising edge before sampling data (≤256, and `< clock_high_cycles`). |
| `0x34` | u32 | `tv_cycles` | Emulator: single-loop iterations after the clock edge before data is valid (datasheet `tv`, ≤256). |
| `0x38` | u32 | `tm_pause_outer_iters` | Outer-loop count (× fixed inner 250) for the monoflop / inter-frame idle time the emulator holds before accepting a new frame. |
| `0x3C` | u32 | `tp_pause_outer_iters` | Outer-loop count (× fixed inner 250) for the reader's inter-frame gap; must be `> tm_pause_outer_iters`. |
| `0x40` | u32 | `formation_pause_outer_iters` | Outer-loop count (× fixed inner 250). Sync mode: delay from clock-train end to position latch. Async mode: refresh interval. |
| `0x44` | u8 | `sequence_hold_mode` | `0`=hold each sequence value for a frame count, `1`=hold for a time duration. |
| `0x45` | u8 | `fault_mode` | `0`=none, `1`=status/error bits, `2`=sentinel value, `3`=all-ones, `4`=missing response, `5`=shortened frame, `6`=excessive `tv`, `7`=data stuck low, `8`=data stuck high. |
| `0x46` | u8 | `capture_mode` | `0`=off, `1`=latest-sample only, `2`=latest-sample + trace. |
| `0x47` | u8 | *reserved* | Zero. |
| `0x48` | u32 | `sequence_hold_count` | Frames (mode 0) or PRU cycles (mode 1) to hold each sequence value. |
| `0x4C` | u32 | `fault_argument` | Mode-specific: sentinel value, stuck-pin level, etc. |
| `0x50` | u32 | `fault_repeat_count` | Frames the fault persists before auto-reverting to valid operation; `0`=until reconfigured. |
| `0x54`–`0xFF` | 172 B | *reserved* | Zero-filled; future fields land here without moving anything else. |

### Prepacked emulator frame slots (`0x0100`–`0x01FF`)

16 slots × 16 bytes. Slot *i* is at `0x0100 + 16*i`:

| Offset in slot | Size | Field |
|---|---|---|
| `+0x0` | u64 | `frame_bits` — raw wire value, right-aligned; PRU0 shifts out its low `frame_width_bits` bits, MSB-first. Already Gray/Tannenbaum/aligned/padded by the config-writer. |
| `+0x8` | u32 | `hold_override_cycles_or_frames` — `0` means "use the config block's `sequence_hold_count`"; nonzero overrides it for this slot only. |
| `+0xC` | u32 | *reserved* |

`sequence_length` (how many of the 16 slots are active before wrapping to slot 0)
is derived, not stored: the config-writer always fills slots contiguously from 0
and a slot's `frame_bits == 0xFFFFFFFFFFFFFFFF` sentinel marks "unused, stop here"
(all-ones is never a valid *prepacked* frame value because the packer always
zero-pads unused high bits, so this sentinel cannot collide with a real value).

### Latest-sample mailbox (`0x0200`–`0x023F`), seqlock

| Offset | Size | Field |
|---|---|---|
| `0x00` | u32 | `seq` — odd while being written, even and stable when readable. Consumers retry while odd or while it changed mid-read. |
| `0x04` | u64 | `raw_frame` — exactly what was clocked in, right-aligned. |
| `0x0C` | u32 | `position_value` — the position field's raw bits, structurally extracted at `position_offset_bits`/`position_width_bits`, zero-extended. **Still wire-encoded** (Gray/Tannenbaum, if any) — per the parent plan's Task 5, semantic decode is an R5/host responsibility, not PRU1's; PRU1 only ever does offset/width bit extraction, mirroring PRU0 never encoding. |
| `0x10` | u32 | `status_bits` — error/status field, zero-extended, `0` if `error_width_bits == 0`. |
| `0x14` | u32 | `frame_counter` — monotonic count of frames received. |
| `0x18` | u64 | `timestamp_cycles` — PRU cycle counter at frame completion (wide to avoid wraparound over a long run). |
| `0x20`–`0x3F` | 32 B | *reserved* |

### Capture control + counters (`0x0240`–`0x027F`)

| Offset | Size | Field |
|---|---|---|
| `0x00` | u32 | `trace_write_index` — monotonic; current slot is `trace_write_index % 1024`. |
| `0x04` | u32 | `trace_overrun_count` — incremented once per write once `trace_write_index >= 1024` (i.e. every write that overwrites a not-yet-read slot). |
| `0x08`–`0x3F` | 56 B | *reserved* |

### Trace buffer (`0x0400`–`0x63FF`)

1,024 records × 24 bytes, record *i* at `0x0400 + 24*i`:

| Offset in record | Size | Field |
|---|---|---|
| `+0x0` | u64 | `timestamp_cycles` |
| `+0x8` | u64 | `raw_frame` |
| `+0x10` | u32 | `position_value` |
| `+0x14` | u16 | `status_bits` |
| `+0x16` | u8 | `flags` (bit 0 = fault active) |
| `+0x17` | u8 | *reserved* |

Trace records are written by PRU1 (the reader) only, matching the parent plan's
Task 4 responsibility list.

## Schema + generator

Source of truth: `pru-simulator/schema/ssi_config_abi.json` — one entry per
field above (`name`, `offset`, `size`, `type`, `doc`), grouped by the four
memory-map sections. JSON, not YAML: equally language-neutral, and the repo
has no YAML dependency today (`requirements.txt` has none, and pulling one in
just for a schema file isn't worth a new dependency). `pru-simulator/tools/gen_ssi_abi.py`
reads it (stdlib `json`, no new dependency) and emits:

- `pru-simulator/source/ssi_config_abi.inc` — PRU-assembly `.set` constants,
  one per field (`SSI_CFG_ABI_VERSION_OFF`, `SSI_CFG_CLOCK_HIGH_CYCLES_OFF`, …)
  plus the four section base offsets (`SSI_CFG_BASE`, `SSI_FRAMES_BASE`,
  `SSI_MAILBOX_BASE`, `SSI_CAPTURE_BASE`, `SSI_TRACE_BASE`) and per-record/slot
  strides (`SSI_FRAME_SLOT_SIZE`, `SSI_TRACE_RECORD_SIZE`).
- `pru-simulator/pru_io/ssi_config_abi.py` — a Python module with the same
  offsets as module-level constants, plus `pack_config(**fields) -> bytes` and
  `unpack_mailbox(data: bytes) -> dict` helpers so `ssi_runtime.py` (the R5
  stand-in) and tests never hand-compute a byte offset.

A generated-file drift test (`tests/test_ssi_config_abi_generated.py`) imports
the generator, regenerates both outputs in memory, and asserts they equal the
checked-in files byte-for-byte — the same pattern already used elsewhere in
this repo for other generated artifacts, so a schema edit that forgets to
re-run the generator fails CI immediately instead of drifting silently.

## Runtime apply sequence (simulator)

Mirrors the parent plan's hardware sequence, with the Python stand-in playing
the R5 role:

1. `SSIRuntime.stage(**fields)` (in `ssi_runtime.py`) validates the staged
   fields against the active profile's documented limits (or the custom
   profile's generic limits) and stores them in a plain Python dict — nothing
   touches shared memory yet.
2. `SSIRuntime.apply(sim)` packs the complete staged block via
   `ssi_config_abi.pack_config`, writes it to `0x0000`–`0x00FF` **except**
   `requested_generation`, then writes `requested_generation` last (the
   commit signal), matching the parent plan's "R5 increments the requested
   generation last."
3. Stepping the simulator (`sim.step_paced(...)`) lets PRU1 finish its current
   frame and hold clock high, then PRU0 (unless `topology == 1`, reader-only)
   applies the new generation and writes `pru0_ack_generation`, then PRU1
   applies and writes `pru1_ack_generation`, then PRU1 resumes.
4. `SSIRuntime.wait_for_apply(sim, timeout_steps)` polls both ack fields (via
   `sim.memory_read`) and returns once they match `requested_generation` (or
   raises after `timeout_steps`), so tests don't hand-roll this polling loop
   per test.

## Profiles

`ssi_runtime.py` ships one `Profile` per named family from the SSI interface
PDF, each a set of config-block field values plus documented min/max clock
limits the `stage()` validator enforces:

`AHS_AHM36_SINGLETURN`, `AHS_AHM36_MULTITURN`, `AFS_AFM60_SINGLETURN`,
`AFS_AFM60_MULTITURN_30BIT`, `AFS_AFM60_MULTITURN_27BIT`,
`AFS_AFM60S_PRO_SINGLETURN`, `AFS_AFM60S_PRO_MULTITURN`, `ATM60_90`,
`ARS60_SHORT`, `ARS60_LONG`, `TTK70`, `KH53`, and `CUSTOM_LEGACY_12BIT_4MHZ`
(today's default: `frame_width_bits=12`, straight binary, 4 MHz, `tm≈12.5 µs`,
`topology=loopback`) — `CUSTOM_LEGACY_12BIT_4MHZ` is the profile active after
`hard_reset()` with no explicit `stage()`/`apply()` call, so every existing
test that doesn't opt into a profile keeps passing unmodified.

## Testing strategy

- Schema/generator: the drift test above, plus a round-trip test
  (`pack_config` → `unpack` → same values).
- Per profile: stage + apply + run the existing multi-core loopback pattern,
  read the mailbox's raw `position_value`, run it through `ssi_runtime.py`'s
  decode helper (Gray/Tannenbaum-aware per `encoding_type`), and assert the
  decoded result matches the value injected into that profile's emulator
  frames.
- Runtime switching: apply profile A, capture N frames, apply profile B
  mid-run, assert no frame straddles the switch (every captured frame's width
  matches the profile active on its captured timestamp) and both ack fields
  reached the new generation before profile B's first frame appears.
- Fault modes: one test per `fault_mode` value, asserting the mailbox/trace
  shows the expected malformed output and, where a `fault_repeat_count` is
  set, that operation returns to normal afterward.
- Trace buffer: fill past 1,024 records, assert `trace_overrun_count`
  matches the exact expected overwrite count and the buffer holds the most
  recent 1,024 records, not the oldest.
