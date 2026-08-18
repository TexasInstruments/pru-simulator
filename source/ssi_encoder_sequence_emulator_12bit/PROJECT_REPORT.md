# ssi_encoder_sequence_emulator_12bit — Project Report

12-bit cycling sequence SSI encoder emulator implementation in the pru-simulator repository.

## Overview

This project provides PRU1 assembly firmware that emulates a 12-bit absolute encoder cycling through a predefined test sequence for SSI communication validation. The firmware responds to an external SSI clock, drives sequentially changing 12-bit position values MSB-first on the data line, and maintains observable state in simulator DRAM1 for regression testing purposes.

## Design and Implementation

### Reactive Clock Handling

Like the fixed-value emulator, this firmware is purely reactive to an external clock source:
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

### Sequence Generation

The firmware cycles through a fixed 5-value test sequence:
- Sequence: 0xABC → 0xAAA → 0xBCA → 0x12A → 0xCC2 → repeat
- Each value is held for exactly one complete frame
- Values change only between frames, ensuring stable transmission during each frame
- The sequence provides visually distinct test values for easy verification in memory views

Sequence implementation uses a frame counter/index that increments after each completed frame, with a jump table to select the next value:
- Frame 0: 0xABC
- Frame 1: 0xAAA
- Frame 2: 0xBCA
- Frame 3: 0x12A
- Frame 4: 0xCC2
- Frame 5+: Wraps to frame 0 (0xABC)

### Memory Interface

Observable state is maintained in the simulator's DRAM1 (accessed via C24 constant table for PRU1):
- Offset 0x08: 32-bit current value being emitted (read-only at runtime)
- Offset 0x14: 32-bit frame counter increments after each complete frame (0-indexed)

The frame counter serves dual purpose as sequence index, ensuring synchronized advancement.

### Synchronization and Frame Handling

To prevent false triggering:
- Implements a sync-detection loop requiring PAUSE_THRESH consecutive high clock samples
- PAUSE_THRESH=1 is set low here because the SSI reader's initial settle period (60 cycles) provides ample qualification time for the first frame
- After last bit sampled on rising edge, waits for final falling edge
- Returns data line to idle high and increments frame counter/sequence index
- Loops back to sync-detection for next frame

## Files

| File | Purpose |
|------|---------|
| `ssi_encoder_sequence_emulator_12bit.asm` | PRU1 firmware implementing cycling sequence SSI encoder |
| `README.md` | This documentation |

## Running in the Simulator

### Regression Test Validation (with PRU0 Reader)

1. Start the simulator dashboard: `python ui/server.py`
2. Load this firmware on **PRU1**
3. Load the SSI reader on **PRU0**
4. Configure GPIO loopback in the IO panel:
   - PRU0 GPO0 (clock out) → PRU1 GPI16 (clock in)
   - PRU1 GPO0 (data out) → PRU0 GPI8 (data in)
5. Enable Signal Graph recording and run in multi-core mode
6. Observe PRU0 capturing the sequence values in order at DRAM0 offset 0x10:
   - Frame 0: 0xABC
   - Frame 1: 0xAAA
   - Frame 2: 0xBCA
   - Frame 3: 0x12A
   - Frame 4: 0xCC2
   - Frame 5: 0xABC (repeats)
7. Verify both frame counters advance correctly

### Automated Regression Test

Execute the validation test:
```bash
python -m pytest tests/test_ssi_encoder_sequence_emulator.py -v
```
This test automates the multi-core setup, GPIO wiring, execution, and verification of all five sequence values.

### Single-core Validation

1. Load this firmware on **PRU1** only
2. Configure internal loopback in IO panel: Enable loopback on PRU1
3. Manually toggle GPI16 (clock input) to simulate clock pulses
4. Observe GPO0 (data output) for correct bit sequence of current value
5. Check DRAM1 offset 0x08 for stored value and 0x14 for frame counter

### MCP Server Validation

```python
from mcp_server.simulator_mcp import PRUSimulatorMCP
mcp = PRUSimulatorMCP()
result = mcp.pru_ssi_inject(
    source=open("source/ssi_reader_4mhz_12bit/ssi_reader_4mhz_12bit.asm").read(),
    value=0xABC,  # First value in sequence
    clk_pin="GPO0",
    data_pin="GPI8",
)
# Returns match=true when reader correctly captures current sequence value
```

## Notes for Hardware Adaptation

When adapting this firmware for real hardware execution:
- Pin assignments must match hardware schematic (clock input, data output)
- The firmware assumes clean clock edges - consider adding input filtering for noisy environments
- Frame counter/sequence index may be accessed differently depending on host interface requirements
- For production use, consider adding error detection for missing or malformed clock signals
- The reactive nature makes this emulator robust to clock frequency variations
- In hardware, the sequence would typically represent test positions or known encoder states for validation

The simulator version relies on the external clock source which in hardware would come from the SSI master device under test.