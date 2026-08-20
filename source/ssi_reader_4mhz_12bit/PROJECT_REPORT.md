# ssi_reader_4mhz_12bit — Project Report

12-bit / 4 MHz SSI master (reader) implementation in the pru-simulator repository.

## Overview

This project provides PRU1 assembly firmware for reading 12-bit absolute encoder data via Synchronous Serial Interface (SSI) at 4 MHz. The firmware generates the SSI clock, captures encoder data MSB-first, and stores the result in simulator DRAM1 for validation and testing purposes.

## Design and Implementation

### Clock Generation and Timing

The SSI reader targets a 300 MHz PRU core clock to achieve an exact 4 MHz SSI clock rate:
- 75 cycles per SSI bit (300 MHz / 4 MHz = 75)
- Symmetric high/low phase timing: HIGH_DLY=33, LOW_DLY=35 cycles
- This yields 33 + 1 + 35 + 1 = 70 cycles per bit plus edge transitions
- Settle delay of 60 cycles ensures idle-high qualification before first frame
- Monoflop pause of 12.5 µs minimum implemented via nested loop (PAUSE_OUTER=15 × PAUSE_INNER=250 = 3750 cycles)

### Data Capture Protocol

The firmware implements standard SSI protocol:
- Idle state: Clock and data lines high
- Frame start: First falling clock edge
- Data sampling: During clock high phase (rising edge)
- Bit order: Most Significant Bit (MSB) first
- Frame length: 12 clock pulses for 12-bit word
- After capture: Returns to idle high state

Each sampled bit is shifted into a capture register using a descending bit counter, naturally producing MSB-first ordering without post-processing.

### Memory Interface

Results are stored in the simulator's DRAM1 (accessed via C24 constant table):
- Offset 0x10: 32-bit captured word (12-bit position zero-extended)
- Register R20: Running frame counter increments per frame for frame counting (accessible directly)

The firmware uses `sbco &r2, c24, RESULT_OFF, 4` to store the captured word after each complete frame.

### Synchronization and Frame Handling

To ensure reliable frame detection:
- Initial settle period qualifies idle-high state before first frame
- Each frame begins with explicit falling edge detection
- Bit loop counts down from 11 to 0 for the 12 data bits
- Inter-frame monoflop pause prevents frame overlap
- Frame counter increments after each successful capture

## Files

| File | Purpose |
|------|---------|
| `ssi_reader_4mhz_12bit.asm` | PRU1 firmware implementing SSI reader |
| `README.md` | This documentation |

## Running in the Simulator

### Multi-core Validation (with PRU0 Emulator)

1. Start the simulator dashboard: `python ui/server.py`
2. Load this firmware on **PRU1**
3. Load a compatible SSI encoder emulator on **PRU0** (fixed-value or sequence variant)
4. Configure GPIO loopback in the IO panel:
   - PRU1 GPO0 (clock out) → PRU0 GPI16 (clock in)
   - PRU0 GPO0 (data out) → PRU1 GPI8 (data in)
5. Enable Signal Graph recording and run in multi-core mode
6. Observe captured values at DRAM1 offset 0x10 on PRU1
7. Monitor frame counter in PRU1 R20 register

### Single-core Validation (SSI Encoder Inject)

1. Load this firmware on **PRU1** only
2. Use the SSI Encoder Inject panel in the UI:
   - Enter a 12-bit hex test value (e.g. `ABC`)
   - Select clock pin: GPO0, data pin: GPI8
   - Click **Inject** to prepare simulator state
3. Click **Run** and check DRAM1 offset 0x10 for the result

### MCP Server Validation

```python
from mcp_server.simulator_mcp import PRUSimulatorMCP
mcp = PRUSimulatorMCP()
result = mcp.pru_ssi_inject(
    source=open("source/ssi_reader_4mhz_12bit/ssi_reader_4mhz_12bit.asm").read(),
    value=0xABC,
    clk_pin="GPO0",
    data_pin="GPI8",
)
# Returns: {expected, captured, match, frames_captured, cycle_count}
```

## Notes for Hardware Adaptation

When adapting this firmware for real hardware execution:
- Clock and pinmux initialization must be added (handled by hardware project's SysConfig)
- The core clock speed must be configured to 300 MHz for exact 4 MHz SSI
- Pin assignments should be verified against hardware schematic
- Frame counter may be accessed differently depending on host interface requirements
- Consider adding error handling for noisy signal conditions in production use

The simulator version omits SoC initialization which is provided by the hardware counterpart in the parent firmware workspace.