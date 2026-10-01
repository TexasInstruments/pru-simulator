# ssi_reader_4mhz_12bit — 12-bit / 4 MHz SSI Master (Reader)

Single-core **PRU1** firmware that generates a 4 MHz SSI clock and captures a
12-bit absolute encoder position MSB-first, storing the result in DRAM1.

* **Clock rate:** 4 MHz (exact), requiring a **300 MHz PRU core clock**.
  75 cycles/bit × 3.33 ns/cycle = 250 ns/bit = 4.0 MHz.
* **Protocol:** idle CLK and DATA high; frame starts on the first falling clock
  edge; 12 pulses; data sampled on the rising edge; MSB-first.
* **Monoflop:** inter-frame pause of 12.5 µs (datasheet minimum).
* **Pins:**
  * `R30.0` (GPO0) — SSI clock output
  * `R31.8` (GPI8) — SSI data input

## DRAM1 memory map

| Offset | Contents |
|-------:|---------|
| `0x10` | Latest 12-bit position, zero-extended to 32 bits (u32) |

`R20` holds the running frame counter (accessible as a register; not stored to DRAM1).

## Files

| File | Purpose |
|------|---------|
| `ssi_reader_4mhz_12bit.asm` | PRU1 firmware |
| `README.md` | this file |
| `PROJECT_REPORT.md` | detailed project documentation |

## Run it in the simulator

### Multi-core mode (with PRU0 emulator)

1. Start the dashboard: `python ui/server.py`
2. Load this firmware on **PRU1**.
3. Load an SSI emulator firmware on **PRU0**:
   * `source/ssi_encoder_sequence_emulator_12bit/ssi_encoder_sequence_emulator_12bit.asm` — retained cycling sequence for regression testing.
   * `source/ssi_generic_emulator/ssi_generic_emulator.asm` — configurable emulator for interactive testing and new scenarios.

   The fixed reader and sequence emulator are the compatibility pair used by
   the legacy regression tests. For configurable runs, use the generic reader
   and generic emulator together; the dashboard's Generic SSI Runtime does
   this automatically.
4. Add GPIO wires in the IO panel:
   * `pru1:GPO0` → `pru0:GPI16` (clock)
   * `pru0:GPO0` → `pru1:GPI8` (data)
5. Enable Signal Graph recording, then click **Run** in multi-core mode.
6. Inspect DRAM1 offset `0x10` on PRU1 for the captured position.

### Single-core mode (SSI Encoder Inject panel)

1. Start the dashboard and load this firmware on **PRU1** only.
2. Open the **SSI Encoder Inject** panel, enter a 12-bit hex value (e.g. `ABC`),
   select pins `GPO0` / `GPI8`, then click **Inject**.
3. Click **Run**. Check DRAM1 offset `0x10` for the result.

### MCP server

```python
from mcp_server.server import PRUSimulatorMCP
mcp = PRUSimulatorMCP()
result = mcp.pru_ssi_inject(
    source=open("source/ssi_reader_4mhz_12bit/ssi_reader_4mhz_12bit.asm").read(),
    value=0xABC,
    clk_pin="GPO0",
    data_pin="GPI8",
    core="pru1",
)
print(result)  # expected, captured, match, frames_captured, cycles
```

### Automated tests

```bash
python -m pytest tests/test_ssi_encoder_sequence_emulator.py -q
```

## Clock speed note

The firmware is written for **300 MHz** (exact integer ratios). A 200 MHz clock
produces non-integer cycle counts (rounding) and is not recommended. To confirm
the active simulator clock, check **Settings > Core Clock Speed** in the
dashboard.
