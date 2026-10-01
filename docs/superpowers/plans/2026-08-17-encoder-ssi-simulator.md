# Encoder SSI Simulator Support — Implementation Plan

**Goal:** Add 12-bit SSI encoder simulator support: a PRU1 reader, a PRU0
sequence emulator, a runtime-configurable emulator, an MCP injection tool, and
automated regression tests. The original fixed-value emulator was an early
prototype and is no longer maintained.
The firmware and memory layout must match the hardware project in
`Project_Tests/ssi_test/` so simulation results can be cross-validated on board.

**Architecture:** Three standalone assembly sources under `source/`, each with
its own README. An `SSIEncoderGenerator` attached to the simulator's IO port
provides single-core MCP injection without requiring PRU1. The sequence emulator
regression test loads both cores, wires the GPIO loopback, and verifies all five
values are captured in order.

**Tech Stack:** PRU assembler (this project's ISA), Python (pytest),
`PRUSimulatorMCP`.

## Global Constraints

- PRU core clock: 300 MHz. Exact 4 MHz SSI requires 75 cycles/bit — do not run
  at 200 MHz.
- Memory layout must match the hardware firmware: PRU1 DRAM1 offset `0x10` for
  the captured word; PRU0 DRAM0 offsets `0x08` (value) and `0x14` (counter).
- Simulator sources omit all SoC initialization (pad-mux, clock setup). Those
  live in the hardware CCS project.
- These sources are canonical for simulator execution. If a parent workspace
  copies them for convenience, any test comparing the two belongs in the parent
  repo, not here.

---

### Task 1: SSI Reader firmware

**Files:**
- Create: `source/ssi_reader_4mhz_12bit/ssi_reader_4mhz_12bit.asm`
- Create: `source/ssi_reader_4mhz_12bit/README.md`

- [x] Implement PRU1 reader with exact 300 MHz timing constants.
- [x] Capture 12 bits MSB-first; store u32 to DRAM1 offset `0x10`.
- [x] Implement inter-frame monoflop pause (12.5 µs minimum).
- [x] Verify single-step behavior in the simulator.
- [x] Write project README matching `source/pif_eth/README.md` style.

### Task 2: Fixed-value SSI emulator firmware (historical, retired)

This task recorded the initial fixed-value prototype. That candidate has been
retired; current development uses the sequence fixture or the generic
runtime-configurable emulator.

### Task 3: Sequence emulator firmware

**Files:**
- Create: `source/ssi_encoder_sequence_emulator_12bit/ssi_encoder_sequence_emulator_12bit.asm`
- Create: `source/ssi_encoder_sequence_emulator_12bit/README.md`

- [x] Implement 5-value repeating sequence: `0xABC → 0xAAA → 0xBCA → 0x12A → 0xCC2`.
- [x] Advance sequence index after each completed frame.
- [x] Set `PAUSE_THRESH = 1` for fast first-frame sync.
- [x] Write project README including sequence table.

### Task 4: MCP injection tool

**Files:**
- Modify: `mcp_server/server.py`

- [x] Add `SSIEncoderGenerator` that drives DATA on CLK transitions.
- [x] Expose `pru_ssi_inject(source, value, clk_pin, data_pin)` on `PRUSimulatorMCP`.
- [x] Return `{expected, captured, match, frames_captured, cycles}`.
- [x] Validate for `0x000`, `0xA5A`, `0xFFF`.

### Task 5: Automated regression test

**Files:**
- Create: `tests/test_ssi_encoder_sequence_emulator.py`

- [x] Load reader on PRU1 and sequence emulator on PRU0.
- [x] Wire GPIO: `pru1:GPO0` → `pru0:GPI16`, `pru0:GPO0` → `pru1:GPI8`.
- [x] Run paced multi-core execution for all five sequence values.
- [x] Assert each DRAM1 capture matches the expected sequence slot.
- [x] Assert both frame counters advance.

### Task 6: Documentation

**Files:**
- Create: `docs/superpowers/specs/2026-08-17-encoder-ssi-simulator-design.md`
- Create: `docs/superpowers/plans/2026-08-17-encoder-ssi-simulator.md` (this file)
- Create: `docs/handoff/2026-08-17-encoder-ssi-testing.md`

- [x] Write design spec with protocol, timing, and memory map.
- [x] Write this plan recording what was built.
- [x] Write handoff note for cross-PC continuity.
