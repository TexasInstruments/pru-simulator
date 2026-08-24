# ssi_generic_emulator — Runtime-Configurable SSI Encoder Emulator (PRU0)

Single-core **PRU0** firmware that emulates an SSI absolute encoder whose bit
width, timing, sequence, and fault behavior are all driven by a shared-memory
configuration block instead of hardcoded `.set` constants. It is the generic
replacement for `ssi_encoder_sequence_emulator_12bit.asm` /
`ssi_encoder_emulator_12bit.asm` — those fixed files are untouched and still
work exactly as before.

* **Config-driven, not hardcoded:** on load, this program reads the entire
  config block at `ICSS_SHARED` (`c28`, base `0x00010000`) once, then watches
  `requested_generation` every idle boundary and re-reads config + frame
  slots only when it changes (writing `pru0_ack_generation` once applied).
* **Bit shifter, not an encoder:** PRU0 never Gray-, Tannenbaum-, alignment-,
  or padding-encodes anything. It only shifts out already-encoded bits,
  MSB-first, from a prepacked frame slot for `frame_width_bits` clocks
  (1..64). All encoding-aware logic lives in the Python runtime module (see
  below) — mirrors the design doc's rule that PRU0 never encodes and PRU1
  never decodes.
* **Reactive clock role:** unlike the fixed emulator, this program idles data
  high and synchronizes to an externally-driven clock via a
  stability-debounced falling-edge detector (debounce threshold derived from
  `tm_pause_outer_iters`, documented at the top of the `.asm` file next to
  `DEBOUNCE_MULTIPLIER_SHIFT`/`DEBOUNCE_POLL_CAP`).
  Byte-valued `formation_mode` reads clear the destination register before
  `LBCO`; asynchronous mode therefore does not inherit stale upper scratch
  bits and does not insert an unintended formation pause between frames.
* **Sequencing:** advances through up to 16 prepacked frame slots per
  `sequence_hold_mode` (frame-count or estimated-time hold) /
  `sequence_hold_count`, with a per-slot `hold_override_cycles_or_frames`
  override, wrapping at the first slot whose `frame_bits` is the
  `0xFFFFFFFFFFFFFFFF` sentinel.
* **Fault injection:** implements all 8 `fault_mode` values from the design
  doc (status/error bits, sentinel value, all-ones, missing response,
  shortened frame, excessive `tv`, data stuck low, data stuck high), each
  with a `fault_argument` and a `fault_repeat_count` that auto-reverts to
  valid frames once it elapses.
* **Topology:** when `topology == 1` (reader-only), this program simply isn't
  loaded at all — there is no runtime special-case for it in the assembly.

See the `.asm` file's own header comment for the full register map (which
registers are persistent config cache vs. per-frame scratch) and a detailed
rationale for the debounce/hold-time approximations used.

## Pins (virtual loopback convention)

Same pin convention as `ssi_encoder_sequence_emulator_12bit.asm`:

| Signal | Pin | Direction |
|---|---|---|
| Clock in | `R31.8` (GPI8) | reader's `R30.0` clock out → this program's clock in |
| Data out | `R30.0` (GPO0) | this program's data out → reader's `R31.16` data in |

## Files

| File | Purpose |
|------|---------|
| `ssi_generic_emulator.asm` | PRU0 firmware |
| `README.md` | this file |
| `PROJECT_REPORT.md` | detailed project documentation |
| `ui/server.py` | Dashboard WebSocket actions for the generic SSI pair |
| `ui/static/index.html`, `ui/static/app.js` | Generic SSI Runtime controls and state rendering |
| `tests/test_ssi_runtime_ui.py` | Browser-contract and paired-runtime UI tests |

## Run it in the simulator

### Multi-core mode (paired with the generic reader)

1. Load `source/ssi_generic_reader/ssi_generic_reader.asm` on **PRU1**.
2. Load this firmware on **PRU0**.
3. Wire GPIO: `pru1:GPO0` → `pru0:GPI8` (clock), `pru0:GPO0` → `pru1:GPI16`
   (data).
4. `hard_reset()`, then either poke the config block + frame slots directly
   (see `tests/test_ssi_generic_emulator.py` for the byte-level convention),
   or drive the pair through `pru_io/ssi_runtime.py`'s `SSIRuntime` (stage a
   named profile, `apply()`, then step).
5. Step both cores in lockstep (`sim.step_paced("pru1", "pru0")`) and inspect
   the mailbox at `0x0200` / trace buffer at `0x0400` from either core's
   `c28`-mapped shared memory.

### Dashboard UI (generic runtime panel)

The dashboard can load and configure this emulator together with the generic
reader without manual assembly loading or memory pokes:

1. Start `python ui/server.py` and open `http://localhost:8080`.
2. In **Generic SSI Runtime**, click **Load PRU0 emulator + PRU1 reader**.
   The server loads this program on PRU0, the reader on PRU1, and installs the
   virtual wires `pru1:GPO0 -> pru0:GPI8` and `pru0:GPO0 -> pru1:GPI16`.
3. Select a profile, configure timing/capture/fault fields, and enter natural
   positions. **Pack positions** applies the selected binary, Gray,
   Gray-excess, or Tannenbaum host-side layout; complete raw frame slots can
   also be supplied by the API. The default sequence is
   `ABC, AAA, BCA, 12A, CC2`.
4. Click **Stage** when you want to inspect a proposed configuration. Click
   **Apply atomically** to send one complete configuration-and-frame
   transaction; the server validates every value before publishing a new
   generation and waits for the acknowledgements at an idle frame boundary.
5. Click **Multi-core** when both register panels are needed. After the
   generic pair is loaded, the second panel is automatically set to **PRU1**;
   the toolbar **Run**, **Step**, **SIM**, and **Reset** controls operate PRU1
   (reader) together with PRU0 (emulator).
6. Click **Run pair**, optionally after enabling Signal Graph recording, then
   click **Refresh** to inspect the latest mailbox and trace counters.
   Paired capture batches are merged by their shared run-step axis, so PRU0
   and PRU1 clock/data lanes remain visible together in the graph.
7. The memory panels use absolute/global addresses. Use `0x00000000` for
   PRU0 DRAM, `0x00002000` for PRU1 DRAM, and `0x00010000` for shared RAM.
   The generic SSI pair stores its live configuration, mailbox, and optional
   trace in Shared RAM; it does not write either local DRAM window, so those
   two local regions can correctly remain all-zero. The dashboard provides an
   `SSI mailbox (global 0x00010200)` quick-jump for the changing sample fields.
   Each read carries a panel request ID, so an older response cannot replace a
   newer address window. Auto-refresh keeps one read in flight per panel and
   coalesces later state updates, preventing the memory grids from saturating
   the dashboard while the pair runs.

The runtime panel also renders the live mailbox as an address-labeled
seqlock snapshot. The addresses below are absolute Shared RAM addresses, so
they can be checked directly in either memory panel:

| Field | Address | Meaning |
|---|---:|---|
| `sequence` | `0x00010200` | Even and unchanged before/after a read means the snapshot is coherent |
| `raw frame` | `0x00010204` | Complete MSB-first wire frame, up to 64 bits |
| `raw position` | `0x0001020C` | Position field exactly as stored in the mailbox |
| `position` | `0x0001020C` | The same field after the active encoding is decoded |
| `status` | `0x00010210` | Extracted status/error bits |
| `frame counter` | `0x00010214` | Number of completed reader frames |
| `timestamp` | `0x00010218` | Reader timestamp in PRU cycles |

The trace counters are shown below the mailbox at `0x00010240`
(`write index`) and `0x00010244` (`overrun count`). The UI uses fixed-width
hex strings, including 64-bit raw frames and timestamps, so leading zeroes
and values above JavaScript's safe integer range are preserved. A decoded
position marked `invalid for active width` means the raw field did not fit
the active semantic layout; it is not a transport failure.

Loading the generic pair publishes a normal source/register state for both
PRUs immediately. In Multi-core mode this fills the PRU0 emulator and PRU1
reader source tabs without requiring a switch to Single-core and back.

Stopping the toolbar Run, resetting, or reconnecting clears stale client run
ownership. A previously completed or stopped request therefore cannot leave
**Run pair** disabled.

Loading the generic pair removes stale user-created GPIO wires and installs
only the two documented loopback wires. The panel displays the actual wiring
under the status line so a test can confirm it before running.

Natural positions and complete raw wire frames are rejected when they do not
fit the selected resolution; the UI never silently truncates them. Detailed
manual examples are in `docs/handoff/2026-08-20-generic-runtime-ssi.md`.

### Pairing with a fixed reader

This program can also be paired with the existing fixed
`ssi_reader_4mhz_12bit.asm` for a quick default-shape sanity check, since the
`CUSTOM_LEGACY_12BIT_4MHZ` profile reproduces that program's exact 12-bit/
4 MHz timing — but the fixed reader has no config block awareness, so
anything beyond the legacy default profile needs the generic reader instead.

### Automated tests

```bash
python -m pytest tests/test_ssi_generic_emulator.py -v
```

### Runtime/profile layer

The config block this program reads is generated from
`schema/ssi_config_abi.json` (see `pru_io/ssi_config_abi.py` /
`source/ssi_config_abi.inc`). `pru_io/ssi_runtime.py` mirrors the R5 layer:
named encoder profiles, staged-then-validated configuration, frame packing,
and atomic apply. See
`docs/superpowers/specs/2026-08-19-generic-runtime-ssi-design.md` for the
full memory map and design rationale.
