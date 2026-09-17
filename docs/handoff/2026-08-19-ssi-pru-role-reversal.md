# SSI reader/emulator PRU role reversal

This handoff records a role reversal for the two-core loopback SSI test
described in `2026-08-17-encoder-ssi-testing.md`: which PRU core loads the
reader firmware and which loads the sequence emulator firmware has swapped.
Neither `.asm` file changed — `c24` always resolves to "my own DRAM" for
whichever core executes it, so this is a pure relabeling of which core loads
which program.

## What changed

- **Before:** PRU0 ran `ssi_reader_4mhz_12bit.asm` (reader/master); PRU1 ran
  `ssi_encoder_sequence_emulator_12bit.asm` (emulator).
- **After:** PRU0 now runs `ssi_encoder_sequence_emulator_12bit.asm`
  (emulator); PRU1 now runs `ssi_reader_4mhz_12bit.asm` (reader/master).

The reader's captured result now lives in DRAM1 offset `0x10` (was DRAM0).
The emulator's current value/frame counter now live in DRAM0 offsets
`0x08`/`0x14` (was DRAM1). The physical/virtual pin numbers each program uses
internally (GPO0, GPI8, GPI16) did not change — only which core loads which
program.

## Why

Parent-repo plan `encoder-workspace/docs/protocol/generic_runtime_ssi_implementation_plan.md`
requires this reversal so hardware pin/jumper wiring can stay physically
unchanged while the emulator role moves to PRU0.

## Files changed

- `tests/test_ssi_encoder_sequence_emulator.py` — swapped `sim.load` core
  args, `add_gpio_wire` src/dst cores, `step_paced` lead/follow order, the
  frame-counter register read, and the DRAM read address (now
  `0x2000 + 16` / DRAM1) to match the reader now running on `pru1` and the
  emulator on `pru0`.
- `tools/plot_ssi_trace.py` — swapped the `"pru0"`/`"pru1"` role checks used
  to pick clock/data columns and the mirror-check block; docstring reworded
  to not hardcode a core.
- `tools/view_ssi_trace.py` — swapped the role check that selects frames for
  decoding, reordered `lane_specs` so each core is paired with the pin it
  owns post-swap, swapped the `series_for_core` role check, and reordered the
  y-tick labels.
- `source/ssi_reader_4mhz_12bit/README.md` — swapped PRU0/PRU1 and
  DRAM0/DRAM1 throughout (now describes PRU1 firmware, DRAM1 result).
- `source/ssi_reader_4mhz_12bit/PROJECT_REPORT.md` — same swap.
- `source/ssi_encoder_sequence_emulator_12bit/README.md` — swapped PRU0/PRU1
  and DRAM0/DRAM1 throughout (now describes PRU0 firmware, DRAM0 state).
- `source/ssi_encoder_sequence_emulator_12bit/PROJECT_REPORT.md` — same swap.
- `docs/handoff/2026-08-19-ssi-pru-role-reversal.md` — this file.

The "MCP Server Validation" code samples in the READMEs/PROJECT_REPORTs were
left unchanged — they exercise a single-core inject-and-capture flow on the
MCP server's own default core and are unrelated to this multi-core role
assignment.

## Verification

```text
python -m pytest tests/test_ssi_encoder_sequence_emulator.py -v
```

Result: **1 passed**.

```
tests/test_ssi_encoder_sequence_emulator.py::test_ssi_encoder_sequence_emulator_repeats_distinct_values PASSED [100%]
============================== 1 passed in 0.71s ==============================
```

## Out of scope (not touched by this change)

- The original fixed-value emulator variant was later retired; use the generic
  emulator for configurable values and the sequence fixture for regression
  coverage.
- The real hardware CCS project under
  `encoder-workspace/firmware/ccs-tests/ssi_test/` and R5 host firmware.
- `encoder-workspace/firmware/simulator-tests/` (parent-repo copies of these
  same three `.asm` files).
- `docs/superpowers/` and `docs/reports/` (historical snapshots).
- `docs/handoff/2026-08-17-encoder-ssi-testing.md` (its opening table preserves
  the prior role assignment as a point-in-time snapshot; its validation section
  now points to the current assignment).
