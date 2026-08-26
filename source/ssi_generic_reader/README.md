# ssi_generic_reader — Runtime-Configurable SSI Encoder Reader (PRU1)

Single-core **PRU1** firmware that reads an SSI absolute encoder whose bit
width, clock timing, and structural field layout are all driven by a
shared-memory configuration block instead of hardcoded `.set` constants. It
is the generic replacement for `ssi_reader_4mhz_12bit.asm`, which remains
untouched and still works exactly as before.

* **Config-driven, not hardcoded:** on load, this program reads the entire
  config block at `ICSS_SHARED` (`c28`, base `0x00010000`) once, then applies
  a new `requested_generation` at its own idle boundary and writes
  `pru1_ack_generation` once applied.
* **Active clock master:** unlike `ssi_generic_emulator.asm` (PRU0, which is
  reactive and needs a debounce/sync mechanism), this program generates its
  own clock from `clock_high_cycles`/`clock_low_cycles` — there is no
  debounce logic in this file at all. These are LOOP-body counts; the
  balanced hot path adds 15 fixed cycles per bit, so effective frequency is
  `300 MHz / (high + low + 15)`. The default 29+31 values are exactly 4 MHz.
* **Pure bit extractor, not a decoder:** samples data after
  `sample_delay_cycles`, supports up to 64 bits across two 32-bit
  accumulator registers, and extracts `position_value`/`status_bits` by
  structural offset/width only (`position_offset_bits`/`position_width_bits`,
  `error_offset_bits`/`error_width_bits`) — no Gray/Tannenbaum awareness.
  Semantic decode of *how* those bits are encoded is the Python runtime
  module's job, not this program's (mirrors PRU0 never encoding).
* **Mailbox publication:** publishes the latest sample via the seqlock
  pattern (odd → write fields → even) at `0x0200` on every completed frame.
* **Trace capture:** when `capture_mode == 2`, appends a 24-byte trace
  record per frame at `0x0400` without stalling normal acquisition,
  incrementing `trace_overrun_count` once the 1,024-slot ring buffer wraps.
* **Topology:** when `topology == 1` (reader-only), this program skips
  waiting for `pru0_ack_generation` entirely — it can run with no PRU0 core
  loaded at all.
* **IEP timebase:** PRU1 owns startup ordering. It holds the SSI clock high,
  enables `IEPCLK.OCP_EN` through `c4 + 0x30`, then enables IEP0 through `c26`
  with increment 1 (`0x11`). This gives both paired programs a shared 300 MHz
  tick source; PRU0 only reads the latched counter during timestamped mode.

See the `.asm` file's own header comment for the full register map, including
which registers are reused across a frame's phases and why.

## Pins (virtual loopback convention)

Same pin convention as `ssi_reader_4mhz_12bit.asm` / `ssi_generic_emulator.asm`:

| Signal | Pin | Direction |
|---|---|---|
| Clock out | `R30.0` (GPO0) | this program's clock out → emulator's `R31.8` clock in |
| Data in | `R31.16` (GPI16) | emulator's `R30.0` data out → this program's data in |

## Files

| File | Purpose |
|------|---------|
| `ssi_generic_reader.asm` | PRU1 firmware |
| `README.md` | this file |
| `PROJECT_REPORT.md` | detailed project documentation |
| `ui/server.py` | Dashboard WebSocket actions for the generic SSI pair |
| `ui/static/index.html`, `ui/static/app.js` | Generic SSI Runtime controls and state rendering |
| `tests/test_ssi_runtime_ui.py` | Browser-contract and paired-runtime UI tests |

## Run it in the simulator

### Multi-core mode (paired with the generic emulator)

1. Load this firmware on **PRU1**.
2. Load `source/ssi_generic_emulator/ssi_generic_emulator.asm` on **PRU0**.
3. Wire GPIO: `pru1:GPO0` → `pru0:GPI8` (clock), `pru0:GPO0` → `pru1:GPI16`
   (data).
4. `hard_reset()`, then either poke the config block + frame slots directly
   (see `tests/test_ssi_generic_reader.py` for the byte-level convention),
   or drive the pair through `pru_io/ssi_runtime.py`'s `SSIRuntime` (stage a
   named profile, `apply()`, then step).
5. Step both cores in lockstep (`sim.step_paced("pru1", "pru0")`) and inspect
   the mailbox at `0x0200` / trace buffer at `0x0400`.

### Dashboard UI (generic runtime panel)

The dashboard can load and configure this reader together with the generic
emulator without manual assembly loading or memory pokes:

1. Start `python ui/server.py` and open `http://localhost:8080`.
2. In **Generic SSI Runtime**, click **Load PRU0 emulator + PRU1 reader**.
   The server loads this program on PRU1, the emulator on PRU0, and installs
   the virtual wires `pru1:GPO0 -> pru0:GPI8` and `pru0:GPO0 -> pru1:GPI16`.
3. Select a profile, configure timing/capture/fault fields, and enter natural
   positions. **Pack positions** applies the selected binary, Gray,
   Gray-excess, or Tannenbaum host-side layout; complete raw frame slots can
   also be supplied by the API. The default sequence is
   `ABC, AAA, BCA, 12A, CC2`.
4. Click **Stage** when you want to inspect a proposed configuration. Click
   **Apply atomically** to send one complete configuration-and-frame
   transaction; the server validates every value before publishing a new
   generation and waits for the acknowledgements at an idle frame boundary.
   Until both acknowledgements complete, active mailbox/trace decoding stays
   on the previous applied layout.
5. Click **Multi-core** when both register panels are needed. After the
   generic pair is loaded, the second panel is automatically set to **PRU1**;
   the toolbar **Run**, **Step**, **SIM**, and **Reset** controls operate PRU1
   (reader) together with PRU0 (emulator).
6. Click **Run pair**, optionally after enabling Signal Graph recording, then
   click **Refresh** to inspect the latest mailbox and trace counters.
7. The memory panels use absolute/global addresses. Use `0x00000000` for
   PRU0 DRAM, `0x00002000` for PRU1 DRAM, and `0x00010000` for shared RAM.
   Each read carries a panel request ID, so an older response cannot replace a
   newer address window.

Loading the generic pair removes stale user-created GPIO wires and installs
only the two documented loopback wires. The panel displays the actual wiring
under the status line so a test can confirm it before running.

The mailbox shows the raw frame and structurally extracted position/status
fields. The runtime readback adds semantic host-side decoding. Detailed
manual examples are in `docs/handoff/2026-08-20-generic-runtime-ssi.md`.

### Reader-only mode (`topology=1`)

This program can also run alone with no PRU0 loaded (`topology=1`), sampling
whatever is on its floating/injected data pin — used by the trace-wraparound
and reader-only-ack tests, and by `sim.ssi_inject`-driven single-core tests.

### Automated tests

```bash
python -m pytest tests/test_ssi_generic_reader.py -v
python -m pytest -q tests/test_ssi_task_d.py
```

### Runtime/profile layer

The config block this program reads is generated from
`schema/ssi_config_abi.json` (see `pru_io/ssi_config_abi.py` /
`source/ssi_config_abi.inc`). `pru_io/ssi_runtime.py` mirrors the R5 layer:
named encoder profiles, staged-then-validated configuration, atomic apply,
frame packing, mailbox/trace read helpers, and position decode. See
`docs/superpowers/specs/2026-08-19-generic-runtime-ssi-design.md` for the
full existing memory map and design rationale. The timestamped producer
sample ABI is consumed by the PRU0 estimator; this reader only initializes the
shared IEP timebase and generates the SSI request clock. Producer and paired
estimator coverage is in `tests/test_ssi_task_d.py`.
