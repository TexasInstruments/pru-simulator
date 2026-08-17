# Encoder SSI simulator support

This handoff records the simulator-side support for validating a 12-bit SSI
absolute encoder transaction before running the corresponding firmware on
hardware.

## Simulator examples

| Example | Role | Pins |
|---|---|---|
| `source/ssi_reader_4mhz_12bit/ssi_reader_4mhz_12bit.asm` | PRU0 master; generates a 4 MHz SSI clock and stores the captured word at DRAM0 offset `0x10` | GPO0 = CLK, GPI8 = DATA |
| `source/ssi_encoder_emulator_12bit/ssi_encoder_emulator_12bit.asm` | PRU1 reactive emulator for one fixed 12-bit value | GPI16 = CLK, GPO0 = DATA |
| `source/ssi_encoder_sequence_emulator_12bit/ssi_encoder_sequence_emulator_12bit.asm` | PRU1 emulator that presents `ABC`, `AAA`, `BCA`, `12A`, `CC2` in a repeating sequence | GPI16 = CLK, GPO0 = DATA |

The examples use the same SSI transaction and memory convention as the
hardware firmware, but omit SoC-specific pad and clock initialization.
`memory.cfg` sets both simulated PRU clocks to 300 MHz; the reader therefore
uses 75 core cycles per SSI bit for an exact 4 MHz clock.

## UI validation

1. Start the dashboard with `python ui/server.py`.
2. Load the reader on `pru0` and the sequence emulator on `pru1`.
3. Add GPIO wires:
   - `pru0:GPO0 -> pru1:GPI16` (clock)
   - `pru1:GPO0 -> pru0:GPI8` (data)
4. Enable Signal Graph recording, then run the multi-core pair.
5. Check DRAM0 offset `0x10` on PRU0 and the PRU0 frame counter.

The graph keeps a shared cycle axis for both cores, preserves transitions that
fall within one pixel, and records each core as separate lanes. Use **Fit
frame** only when you want to inspect the newest complete SSI transaction.
The capture stride is 10 instructions in GP mode, which gives enough samples
for the 4 MHz waveform without overwhelming the browser.

For a single-core reader test, use the **SSI Encoder Inject** panel instead of
the PRU1 emulator. Enter a 12-bit value such as `ABC`, select `GPO0`/`GPI8`,
inject, and run.

## Automated validation

The repeating emulator regression is:

```text
python -m pytest tests/test_ssi_encoder_sequence_emulator.py -q
```

The MCP server exposes `pru_ssi_inject` for single-core end-to-end checks. It
loads a reader source, attaches the edge-driven encoder model, runs it, and
returns the expected value, captured value, frame count, and cycle count.

## Repository boundary

The simulator sources in `source/` are canonical for simulator execution.
A parent firmware workspace may keep copies for convenience, but the simulator
repository must remain runnable when cloned by itself. Any test that compares
simulator sources with a parent workspace belongs in the parent repository,
not in this repository's test suite.
