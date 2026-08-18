# Encoder SSI Simulator Support — Design

**Date:** 2026-08-17
**Status:** Implemented

## Problem

Validate a 12-bit SSI absolute encoder transaction in simulation before running
the corresponding firmware on the AM243x LaunchPad hardware. The simulator needs:

1. A PRU0 **SSI reader** firmware that generates a 4 MHz clock and captures a
   12-bit word.
2. A PRU1 **SSI encoder emulator** firmware that responds to the reader's clock
   and drives a position value MSB-first.
3. A cycling emulator that cycles through known values for regression testing.
4. An automated test covering the sequence emulator end-to-end.
5. An MCP `pru_ssi_inject` tool for single-core, no-PRU1-required injection.

The firmware must match the hardware project's transaction and memory layout so
the same test values can be cross-checked between the simulator and the board.

## Scope decisions

- **Clock:** 300 MHz PRU core clock is mandatory for exact 4 MHz SSI (75 cycles/bit).
  200 MHz gives non-integer ratios and is explicitly not recommended.
- **Simulator only:** no SoC pad-mux or ICSSG clock configuration in these sources.
  Those belong in the hardware project (`Project_Tests/ssi_test/`).
- **Memory layout:** PRU0 DRAM0 offset `0x10` holds the captured 12-bit word;
  PRU1 DRAM1 offset `0x08` holds the emitter's current value;
  both offset `0x14` hold per-core frame counters.
- **Protocol:** standard SSI — idle high; falling edge starts frame; data driven
  on falling edge; master samples on rising edge; 12 pulses; MSB-first.

## Design

### 1. SSI Reader firmware (`source/ssi_reader_4mhz_12bit/`)

Pure PRU0 assembly. Self-contained timing constants for 300 MHz:
- `HIGH_DLY = 33`, `LOW_DLY = 35` — symmetric halves yielding 75 cycles/bit.
- `SETTLE_DLY = 60` — idle-high qualification before the first frame.
- Monoflop pause: nested loop `PAUSE_OUTER=15 × PAUSE_INNER=250` = 3750 cycles
  = 12.5 µs (datasheet minimum).

The capture loop shifts each sampled bit into `r2` using the descending bit
index stored in `r1`, giving an MSB-first natural shift with no post-processing.
After the 12th bit, the word is stored with `sbco &r2, c24, RESULT_OFF, 4`.

### 2. Fixed-value emulator (`source/ssi_encoder_emulator_12bit/`)

PRU1 assembly. Purely reactive — no clock generation. Uses `wbs`/`wbc` to halt
until clock edges rather than polling, so it works at any master clock rate
without timing constants.

The firmware stores the default position (`0xA5A`) in DRAM1 at boot, making
the value observable and writable by the host between frames.

A sync-detection loop (`PAUSE_THRESH = 300` consecutive HIGH samples) prevents
false triggering on noisy starts. After the last bit is sampled on the rising
edge, it waits for the final falling edge, returns DATA to idle HIGH, and
increments the frame counter before looping back to sync-detect.

### 3. Sequence emulator (`source/ssi_encoder_sequence_emulator_12bit/`)

Same reactive design as the fixed emulator, with a 5-slot jump table after each
completed frame to advance through `0xABC → 0xAAA → 0xBCA → 0x12A → 0xCC2`.
The sequence index mirrors the frame counter, so both are reset together on wrap.

`PAUSE_THRESH = 1` is intentional here — the reader's first-frame settle
(`SETTLE_DLY = 60` cycles) is much shorter than the monoflop pause between
real frames, so the emulator must be ready quickly for the very first clock edge.

### 4. MCP `pru_ssi_inject` tool

Attaches an `SSIEncoderEdgeModel` to the simulator's IO port on the specified
`data_pin`. The model drives the correct MSB-first bit sequence in response to
`clk_pin` transitions, without requiring PRU1 to be loaded or running. The tool
returns `{expected, captured, match, frames_captured, cycle_count}`.

### 5. GPIO wiring for dual-core runs

The simulator's GPIO loopback must be configured with:
- `pru0:GPO0` → `pru1:GPI16` (clock out → clock in)
- `pru1:GPO0` → `pru0:GPI8` (data out → data in)

In multi-core paced mode the simulator keeps both cores synchronized on the
shared perif clock, so the reader's clock transitions appear at the emulator's
GPI16 on the same step.

## Testing

### Automated (Python/pytest)

`tests/test_ssi_encoder_sequence_emulator.py` — loads reader on PRU0 and
sequence emulator on PRU1, wires both GPIO pairs, runs paced multi-core
execution, and verifies:

- DRAM0 offset `0x10` equals the current sequence value.
- Both frame counters advance.
- All five sequence values are captured in order.

### MCP injection

```python
result = mcp.pru_ssi_inject(source=..., value=0xABC, clk_pin="GPO0", data_pin="GPI8")
assert result["match"] is True
assert result["frames_captured"] >= 1
```

Validated for `0x000`, `0xA5A`, and `0xFFF` per the handoff note.

### UI validation

1. Load reader on PRU0, sequence emulator on PRU1.
2. Wire GPIO as above; enable Signal Graph.
3. Run multi-core; confirm 12 clock transitions visible in both clock lanes.
4. Use **Fit frame** to zoom to the newest complete SSI frame.
5. Check DRAM0 `0x10` matches the expected sequence value.

## Files

| File | Purpose |
|------|---------|
| `source/ssi_reader_4mhz_12bit/ssi_reader_4mhz_12bit.asm` | PRU0 reader |
| `source/ssi_reader_4mhz_12bit/README.md` | reader project doc |
| `source/ssi_encoder_emulator_12bit/ssi_encoder_emulator_12bit.asm` | PRU1 fixed emulator |
| `source/ssi_encoder_emulator_12bit/README.md` | fixed emulator doc |
| `source/ssi_encoder_sequence_emulator_12bit/ssi_encoder_sequence_emulator_12bit.asm` | PRU1 sequence emulator |
| `source/ssi_encoder_sequence_emulator_12bit/README.md` | sequence emulator doc |
| `tests/test_ssi_encoder_sequence_emulator.py` | automated regression |
| `docs/handoff/2026-08-17-encoder-ssi-testing.md` | session handoff |

## Hardware counterpart

`Project_Tests/ssi_test/` — dual-PRU CCS workspace for the AM243x LaunchPad.
PRU0 and PRU1 firmware mirror these simulator sources; the R5F host polls
PRU0 DRAM0 and reports via UART. See the design spec
`docs/superpowers/specs/2026-08-16-ssi-dual-pru-hardware-test-design.md`.
