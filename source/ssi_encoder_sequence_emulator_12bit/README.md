# ssi_encoder_sequence_emulator_12bit — Cycling 12-bit SSI Encoder Emulator

**PRU0** firmware that emulates an absolute SSI encoder, cycling through a
fixed repeating sequence of five 12-bit position values. Designed for
regression testing and signal-graph inspection.

* **Sequence:** `0xABC` → `0xAAA` → `0xBCA` → `0x12A` → `0xCC2` → repeat.
  Each value is held for a complete frame before advancing.
* **Protocol:** idle CLK and DATA high; frame starts on the first falling clock
  edge; 12 bits MSB-first; DATA driven on each falling clock edge.
* **Pins:**
  * `R31.16` (GPI16) — SSI clock input from PRU1 master
  * `R30.0` (GPO0) — SSI data output to PRU1 master

## DRAM0 memory map

| Offset | Contents |
|-------:|---------|
| `0x08` | Current value being emitted (u32, read-only at runtime) |
| `0x14` | Frame counter (u32, 0-indexed, increments each completed frame) |

The value at `0x08` advances after each completed frame. After frame 4
(counter wraps to 0), it returns to `0xABC`.

## Files

| File | Purpose |
|------|---------|
| `ssi_encoder_sequence_emulator_12bit.asm` | PRU0 firmware |
| `README.md` | this file |
| `PROJECT_REPORT.md` | detailed project documentation |

## Run it in the simulator

Use with `source/ssi_reader_4mhz_12bit/` on PRU1. See that project's README
for the full multi-core setup procedure.

Required GPIO wires:
* `pru1:GPO0` → `pru0:GPI16` (clock from master to emulator)
* `pru0:GPO0` → `pru1:GPI8` (data from emulator to master)

### Automated regression test

```bash
python -m pytest tests/test_ssi_encoder_sequence_emulator.py -q
```

This test runs both cores, wires the GPIO loopback, and verifies that PRU1
captures each sequence value in order.

## Sequence table

| Frame index | Value | Hex |
|-------------|-------|-----|
| 0 | Initial | `0xABC` |
| 1 | All-A | `0xAAA` |
| 2 | Mixed | `0xBCA` |
| 3 | Low range | `0x12A` |
| 4 | CC2 | `0xCC2` |
| 5+ | Repeats from 0 | — |

The values were chosen so every captured DRAM1 word is visually distinct in
the Memory panel, making manual inspection straightforward.

## Clock speed note

The emulator is purely reactive and works with any master clock frequency.
For correct timing with the SSI reader, ensure the PRU1 master runs at 300 MHz
to produce an exact 4 MHz SSI clock (75 cycles/bit).