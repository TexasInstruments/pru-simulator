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
  debounce logic in this file at all.
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

See the `.asm` file's own header comment for the full register map, including
which registers are reused across a frame's phases and why.

## Pins (virtual loopback convention)

Same pin convention as `ssi_reader_4mhz_12bit.asm` / `ssi_generic_emulator.asm`:

| Signal | Pin | Direction |
|---|---|---|
| Clock out | `R30.0` (GPO0) | this program's clock out → emulator's `R31.16` clock in |
| Data in | `R31.8` (GPI8) | emulator's `R30.0` data out → this program's data in |

## Files

| File | Purpose |
|------|---------|
| `ssi_generic_reader.asm` | PRU1 firmware |
| `README.md` | this file |
| `PROJECT_REPORT.md` | detailed project documentation |

## Run it in the simulator

### Multi-core mode (paired with the generic emulator)

1. Load this firmware on **PRU1**.
2. Load `source/ssi_generic_emulator/ssi_generic_emulator.asm` on **PRU0**.
3. Wire GPIO: `pru1:GPO0` → `pru0:GPI16` (clock), `pru0:GPO0` → `pru1:GPI8`
   (data).
4. `hard_reset()`, then either poke the config block + frame slots directly
   (see `tests/test_ssi_generic_reader.py` for the byte-level convention),
   or drive the pair through `pru_io/ssi_runtime.py`'s `SSIRuntime` (stage a
   named profile, `apply()`, then step).
5. Step both cores in lockstep (`sim.step_paced("pru1", "pru0")`) and inspect
   the mailbox at `0x0200` / trace buffer at `0x0400`.

### Reader-only mode (`topology=1`)

This program can also run alone with no PRU0 loaded (`topology=1`), sampling
whatever is on its floating/injected data pin — used by the trace-wraparound
and reader-only-ack tests, and by `sim.ssi_inject`-driven single-core tests.

### Automated tests

```bash
python -m pytest tests/test_ssi_generic_reader.py -v
```

### Runtime/profile layer

The config block this program reads is generated from
`schema/ssi_config_abi.json` (see `pru_io/ssi_config_abi.py` /
`source/ssi_config_abi.inc`). `pru_io/ssi_runtime.py` plays the role real R5
firmware will eventually play: named encoder profiles, staged-then-validated
configuration, atomic apply, mailbox/trace read helpers, and position
decode. See
`docs/superpowers/specs/2026-08-19-generic-runtime-ssi-design.md` for the
full memory map and design rationale.
