# ssi_encoder_emulator_12bit — Project Report

12-bit fixed-value SSI encoder emulator implementation in the pru-simulator repository.

## Overview

This project provides PRU1 assembly firmware that emulates a 12-bit absolute encoder for SSI communication testing. The firmware responds to an external SSI clock, drives a fixed 12-bit position value MSB-first on the data line, and maintains observable state in simulator DRAM1 for validation purposes.

## Design and Implementation

### Reactive Clock Handling

The SSI emulator is purely reactive to an external clock source:
- Uses `wbc` (wait until bit cleared) and `wbs` (wait until bit set) instructions to halt on clock edges
- No clock generation - entirely dependent on the SSI master's clock signal
- This design works correctly with any master clock frequency without timing constant adjustments

### Data Transmission Protocol

The firmware implements standard SSI data transmission:
- Idle state: Clock and data lines high
- Frame start: Detects first falling clock edge (synchronization)
- Data driving: Places each bit on the data line during clock low phase
- Bit order: Most Significant Bit (MSB) first, transmitted on falling edges
- Frame length: 12 clock pulses for 12-bit word
- After transmission: Returns data line to idle high state

Bit extraction uses logical shift right (`lsr`) and bit masking to isolate each position bit in sequence from MSB to LSB.

### Memory Interface

Observable state is maintained in the simulator's DRAM1 (accessed via C24 constant table for PRU1):
- Offset 0x08: 32-bit position value to emit (can be modified at runtime)
- Offset 0x14: 32-bit frame counter increments after each complete frame

The default power-on value is 0xA5A (a recognizable test pattern) stored during initialization via `sbco &r2, c24, 8, 4`.

### Synchronization and Frame Handling

To prevent false triggering:
- Implements a sync-detection loop requiring PAUSE_THRESH consecutive high clock samples
- Default PAUSE_THRESH=300 provides noise immunity for initial frame detection
- After last bit sampled on rising edge, waits for final falling edge
- Returns data line to idle high and increments frame counter
- Loops back to sync-detection for next frame

## Files

| File | Purpose |
|------|---------|
| `ssi_encoder_emulator_12bit.asm` | PRU1 firmware implementing fixed-value SSI encoder |
| `README.md` | This documentation |

## Running in the Simulator

### Multi-core Validation (with PRU0 Reader)

1. Start the simulator dashboard: `python ui/server.py`
2. Load this firmware on **PRU1**
3. Load a compatible SSI reader on **PRU0**
4. Configure GPIO loopback in the IO panel:
   - PRU0 GPO0 (clock out) → PRU1 GPI16 (clock in)
   - PRU1 GPO0 (data out) → PRU0 GPI8 (data in)
5. Enable Signal Graph recording and run in multi-core mode
6. Observe waveform alignment: clock edges on both cores should align
7. Verify PRU0 captures the expected fixed value at DRAM0 offset 0x10

### Single-core Validation (via GPIO Loopback)

1. Load this firmware on **PRU1** only
2. Configure internal loopback in IO panel: Enable loopback on PRU1
3. Manually toggle GPI16 (clock input) to simulate clock pulses
4. Observe GPO0 (data output) for correct bit sequence
5. Check DRAM1 offset 0x08 for stored value and 0x14 for frame counter

### MCP Server Validation

```python
from mcp_server.simulator_mcp import PRUSimulatorMCP
mcp = PRUSimulatorMCP()
result = mcp.pru_ssi_inject(
    source=open("source/ssi_reader_4mhz_12bit/ssi_reader_4mhz_12bit.asm").read(),
    value=0xABC,  # Test value the reader should capture
    clk_pin="GPO0",
    data_pin="GPI8",
)
# Returns match=true when reader correctly captures emulator's driven value
```

## Notes for Hardware Adaptation

When adapting this firmware for real hardware execution:
- Pin assignments must match hardware schematic (clock input, data output)
- The firmware assumes clean clock edges - consider adding input filtering for noisy environments
- Frame counter may be accessed differently depending on host interface requirements
- For production use, consider adding error detection for missing or malformed clock signals
- The reactive nature makes this emulator robust to clock frequency variations

The simulator version relies on the external clock source which in hardware would come from the SSI master device.