# ssi_encoder_emulator_12bit — Fixed-Value 12-bit SSI Encoder Emulator

**PRU1** firmware that behaves as an absolute SSI encoder, driving a fixed
12-bit position value MSB-first in response to the SSI master's clock.

* **Position:** default `0xA5A` — a recognizable, non-trivial test value.
  Overridable at runtime by writing a new u32 to DRAM1 offset `0x08`.
* **Protocol:** idle CLK and DATA high; frame starts on the first falling clock
  edge; 12 bits MSB-first; DATA driven on each falling clock edge and sampled
  by the master on the rising edge.
* **Pins:**
  * `R31.16` (GPI16) — SSI clock input from PRU0 master
  * `R30.0` (GPO0) — SSI data output to PRU0 master
* **Clock:** purely reactive to PRU0 — no clock generation. Designed for a
  PRU0 master running at 4 MHz SSI with a 300 MHz core clock.

## DRAM1 memory map

| Offset | Contents |
|-------:|---------|
| `0x08` | Current 12-bit position to emit (u32, write here to change value) |
| `0x14` | Frame counter (u32, increments after each complete frame) |

## Files

| File | Purpose |
|------|---------|
| `ssi_encoder_emulator_12bit.asm` | PRU1 firmware |
| `README.md` | this file |
| `PROJECT_REPORT.md` | detailed project documentation |

## Run it in the simulator

Use with `source/ssi_reader_4mhz_12bit/` on PRU0. See that project's README
for the full multi-core setup procedure.

Required GPIO wires:
* `pru0:GPO0` → `pru1:GPI16` (clock from master to emulator)
* `pru1:GPO0` → `pru0:GPI8` (data from emulator to master)

To change the emitted value at runtime, write a 32-bit value to DRAM1 offset
`0x08` via the Memory panel or the WebSocket `write_memory` action before or
between frames.

## Clock speed note

The emulator is purely reactive and works with any master clock frequency.
For correct timing with the SSI reader, ensure the PRU0 master runs at 300 MHz
to produce an exact 4 MHz SSI clock (75 cycles/bit).