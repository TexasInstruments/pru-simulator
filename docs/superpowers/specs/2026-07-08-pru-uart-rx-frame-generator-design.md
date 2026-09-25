# PRU UART Receiver with Frame Generator - Design Specification

**Date:** 2026-07-08
**Component:** PRU0 UART Receiver + UARTFrameGenerator Peripheral
**Related Specs:**
- 2026-06-05-mcp-uart-design.md (existing UART TX)
- 2026-06-05-uart-decoder-design.md (existing UART decoder)
- 2026-06-07-sigma-delta-filter-design.md (peripheral pattern)

## 1. Overview

This design adds UART reception capability to the PRU simulator, completing the UART transceiver pair (TX already exists). The implementation includes:

1. **PRU0 Assembly UART Receiver**: Bit-bang UART RX on GPI0 pin at configurable baudrate
2. **UARTFrameGenerator Peripheral**: New peripheral class for generating test UART frames
3. **MCP Server Integration**: New `pru_uart_inject` tool for end-to-end testing
4. **Frame-Oriented Design**: Receives 11-byte frames with no idle between bytes, stores via single SBCO operation

## 2. Requirements

- Receive UART frames with 1 start bit, 8 data bits (LSB first), 1 stop bit (8N1 framing)
- Operate at 4.0 Mbaud nominal (configurable for testing)
- PRU core frequency: 200 MHz
- UART RX signal: PRU_GPI0 (R31.b0)
- Receive exactly 11 bytes per frame with no idle between bytes
- Store received frames to DRAM0 using efficient single SBCO operation
- Detect framing errors (invalid stop bit) and re-sync on next start bit
- Maintain error flag at DRAM0+0x0FFE for host notification
- Use MCP server for test control and verification

## 3. Architecture

```
┌─────────────────────────────────────────────────────┐
│                     PRU0 Core                       │
├───────────────┬────────────────────┬────────────────┤
│   UART RX     │    Other           │    XFR         │
│   Assembly    │    Logic           │    Bus         │
└───────────────┴────────────────────┴────────────────┘
          │                           │
          ▼                           ▼
    ┌─────────────┐            ┌──────────────┐
    │ GPI0 Pin    │◄──────────▶│ UARTFrameGen │
    │ (R31.b0)    │            │ Peripheral   │
    └─────────────┘            └──────────────┘
          │                           │
          │ UART Frames (configurable)│
          ▼                           ▼
    ┌─────────────────────────────────────────────┐
    │            Memory Subsystem                 │
    ├─────────────────────┬───────────────────────┤
    │    DRAM0 (8 KB)     │   Other Regions       │
    │ 0x00000000-0x00001FFF│                     │
    └─────────────────────┴───────────────────────┘
          │
          ▼
    ┌─────────────────────┐
    │   Host (MCP client) │
    └─────────────────────┘
```

### 3.1 Data Flow

1. UARTFrameGenerator creates UART frames on GPI0 pin
2. PRU0 samples GPI0 via R31.b0 register
3. PRU0 assembles bits into bytes using shift-accumulate method
4. Bytes stored in register buffer (R2-R12) as received
5. After 11 bytes received: single SBCO stores frame to DRAM0
6. Host reads DRAM0 via MCP to verify reception
7. Error flag at DRAM0+0x0FFE indicates framing errors

## 4. PRU0 UART Receiver Assembly

### 4.1 Algorithm

**Top-Level State Machine:**
```
WAIT_FOR_START_0 →
RECEIVE_BYTE_0 →
WAIT_FOR_START_1 →
RECEIVE_BYTE_1 →
... →
WAIT_FOR_START_10 →
RECEIVE_BYTE_10 →
STORE_FRAME →
WAIT_FOR_START_0 (next frame)
```

On framing error at any byte:
- Set error flag
- Abort current frame
- Immediately return to WAIT_FOR_START_0

### 4.2 Timing Parameters (200 MHz / 4 Mbaud)

- **Bit Period**: 200 MHz ÷ 4,000,000 = 50 cycles
- **Loop Overhead**: SUB + QBNE = 2 cycles per iteration
- **Iterations per Bit**: 50 cycles ÷ 2 cycles/iter = 25 iterations
- **Start Sample Delay**: 1.5 bit periods = 75 cycles = 38 iterations (rounded up)

**Constants:**
```assembly
.set BIT_TIME, 25      ; iterations per full bit (50 cycles)
.set START_DELAY, 38   ; iterations to sample first data bit (75 cycles)
```

### 4.3 Register Usage

| Register | Purpose                                                                 | Special Notes                          |
|----------|-------------------------------------------------------------------------|----------------------------------------|
| R2-R12   | Frame buffer: R2=byte0, R3=byte1, ..., R12=byte10 (11 bytes)           | "Reserve like R2-R4" for temp storage  |
| R13      | Byte accumulator (assembles current byte being received)                |                                        |
| R14      | Bit mask (starts at 1, left-shifted after each bit)                    |                                        |
| R15      | Temporary for GPI0 sampling (isolates GPI0.b0 via `and r15, r31, 1`)   |                                        |
| R16      | Byte index counter (0-10 for buffer register selection)                 |                                        |
| R17      | Bit counter (0-7 for bits received in current byte)                    |                                        |
| R18      | Delay counter (baud rate timing loops)                                 |                                        |
| R19      | Error flags / status (bit 0 = framing error occurred)                  |                                        |
| R20      | Frame state machine (WAIT_START, RECEIVING_BYTE, etc.)                 |                                        |
| R21-R31  | Available for loop counters, temporary values                          |                                        |
| R0       | Available (special - reserved for LBCO/SBCO if needed)                 | Not used in core algorithm             |
| R1       | Offset register for SBCO (holds DRAM0 byte offset for indirect addressing)|                                       |
| c24      | DRAM0 base address (0x00000000)                                        | Constant table entry                   |

**Note:** R0 and R1 are reserved for special instructions (LBCO/SBCO memory operations) and are not used for general computation in the UART RX algorithm, satisfying the requirement to reserve them for special purposes.

### 4.4 Byte Assembly (LSB-First)

Bits are received LSB-first (b0, b1, ..., b7). To assemble the byte value:

```
; Before first bit of byte:
mov  r13, 0          ; byte accumulator = 0
mov  r14, 1          ; bit mask = 00000001 (bit 0 mask)

; For each of 8 bits (sampled via r15.b0 = GPI0.b0):
; if r15.b0 == 1:   or  r13, r13, r14   ; set bit if sampled=1
; shift r14, r14, 1  ; mask = mask << 1 for next bit
; delay 1 bit period (25 iterations via SUB+QBNE)
```

After 8 bits, R13 contains the received byte value in standard bit order (bit 0 = LSB).

### 4.5 Frame Storage (Single SBCO)

After successfully receiving byte 10 (the 11th byte):

```assembly
; R1 contains the byte offset where we want to store the frame
sbco &r2, c24, *r1, 11   ; Store 11 bytes from R2-R12 to DRAM0[offset] through DRAM0[offset+10]
```

**Why this satisfies requirements:**
- **Single SBCO operation**: Stores all 11 bytes with one instruction
- **No byte-by-byte storage**: Bytes accumulated in registers, stored once per frame
- **R0/R1 reserved**: R0 unused in algorithm, R1 used only for offset addressing (special purpose)
- **R2-R4 used**: Part of the 11-byte receive buffer (R2-R12) as temporary data storage

### 4.6 Error Handling

**Framing Error Detection:**
- After receiving 8 data bits, sample STOP bit via GPI0
- If STOP bit == LOW (0): framing error occurred

**Error Response:**
1. Set error flag: `mov r19.b0, 1` (sticky until cleared by host)
2. Abort current frame: do not store buffered bytes
3. Reset reception state: immediately search for START bit of byte 0
4. Continue operation (do not halt)

**Error Flag Location:**
- **Address**: DRAM0 + 0x0FFE (near end of 8KB DRAM0)
- **Format**: Single byte, bit 0 = framing error occurred (sticky)
- **Host Interaction**: Host can read this byte to detect errors, write 0 to clear

### 4.7 Storage Mapping

**Received Frames:**
- Frame 0: stored at DRAM0[0x0000] through DRAM0[0x000A] (11 bytes)
- Frame 1: stored at DRAM0[0x000B] through DRAM0[0x0015]
- Frame N: stored at DRAM0[0x000B × N] through DRAM0[0x000B × N + 10]

**Error Flag:**
- Single byte at DRAM0[0x0FFE]
- Bit 0: framing error detected (sticky, host-cleared)
- Bits 1-7: reserved for future use (currently 0)

## 5. UARTFrameGenerator Peripheral

### 5.1 Location
`pru_io/uart_frame_generator.py` (following sigma-delta.py pattern)

### 5.2 Features
- Generates UART frames on specified GPI pin
- Configurable parameters:
  - `pin`: GPI pin number (0-19, default 0)
  - `payload`: List of byte values to transmit (e.g., [0x41, 0x42, 0x43])
  - `baudrate`: Baud rate in bits/sec (default 4000000)
  - `idle_state`: Pin state when no data (default True for HIGH)
  - `frames`: Number of times to repeat payload (default 1 for single frame, 0 for continuous)
- Pre-computes timeline of (cycle_offset, pin_value) events
- Integrates with simulator core: checked before each instruction step
- Thread-safe for concurrent access

### 5.3 Event Generation
For each byte in payload:
1. START bit: pin = LOW for 1 bit period
2. 8 data bits: LSB first, each bit period = pin = bit_value
3. STOP bit: pin = HIGH for 1 bit period
4. If not last byte: immediately start next byte's START bit (no idle)
5. After last byte: pin = idle_state until next frame or continuous repeat

### 5.4 Integration
- Inherits from `BasePeripheral` class
- `apply(current_cycle)` method checks for events at current cycle
- Updates simulator's IOPort GPI state when events match
- Can be enabled/disabled via simulator configuration

## 6. MCP Server Integration

### 6.1 New Tool: `pru_uart_inject`

**Purpose:** End-to-end test of UART RX frame reception

**Parameters:**
- `pin`: GPI pin number (default 0)
- `payload`: List of byte values to send (e.g., [0x31, 0x32, 0x33])
- `baudrate`: Baud rate in bits/sec (default 4000000)
- `frames`: Number of frames to transmit (default 1)
- `pru_bin`: Path to PRU assembly binary to load and execute
- `dram0_offset`: DRAM0 byte offset for storage (default 0)
- `clear_on_start`: Clear DRAM0 and error flag before start (default True)

**Returns:**
```json
{
  "status": "success|error",
  "frames_received": integer,
  "bytes_received": integer,
  "receieved_data": [list of byte values from DRAM0],
  "error_count": integer,
  "error_flag_value": integer (DRAM0[0x0FFE] contents)
}
```

### 6.2 Usage Examples

**Basic test:**
```python
result = pru_uart_inject(
    payload=[0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x4B],
    pru_bin="uart_rx_test.out"
)
```

**Error injection test:**
```python
result = pru_uart_inject(
    payload=[0x00]*11,
    baudrate=3900000,  # 3.9 Mbaud to provoke errors
    pru_bin="uart_rx_test.out"
)
```

### 6.3 Related Existing Tools
- `pru_set_input`: Set individual GPI pin values
- `pru_run_until`: Run PRU until specific cycle count
- `pru_memory`: Read DRAM0/DRAM1 contents
- `pru_registers`: Read PRU register state

## 7. Error Detection and Recovery

### 7.1 Framing Error Conditions
- STOP bit sampled as LOW (0) instead of HIGH (1)
- Can occur due to:
  - Baudrate mismatch (TX ≠ RX)
  - Noise on transmission line
  - Line stuck LOW

### 7.2 Error Handling Flow
```
[RECEIVING_BYTE_N]
       │
       ▼ Sample STOP bit via GPI0
   ┌─────────────┐
   │ STOP bit == │
   │   LOW?      │
   └───────┬─────┘
           │ Yes
           ▼
    Set error flag (R19.b0 = 1)
    Abort frame (do not store)
    Reset byte index to 0
    Reset bit/multiframe\
   WAIT_FOR_START_0 ◄─────────────────────┐
           │                              │
           ▼                              │
[WAIT_FOR_START_0]                       │
           │ No                           │
           ▼                              │
    Sample GPI0 for falling edge         │
           │                              │
           ▼ No                           │
    Continue sampling                  ┌─┴─┐
           │                            │
           ◄────────────────────────────┘
           │ Yes                        │
           ▼                            │
    Delay 1.5 bits                    │
           │                            │
           ▼                            │
    [RECEIVE_BYTE_0]                  │
           │                            │
           ▼                            │
    Receive 8 data bits               │
           │                            │
           ▼                            │
    ... (continue for byte N)         │
```

### 7.3 Testing Error Detection
- **Nominal Test**: 4.0 Mbaud → 0 errors expected
- **Low Baudrate Test**: 3.9 Mbaud → expect increasing errors
- **High Baudrate Test**: 4.1 Mbaud → expect errors
- **Noise Simulation**: Random bit flips in generated frames

## 8. Implementation Plan

### 8.1 Phase 1: PRU Assembly Development
- Create `source/uart_rx_11frame.asm`
- Implement state machine and byte assembly logic
- Test with manual GPI0 toggling via `set_gpi_pin` + `run_until`

### 8.2 Phase 2: UARTFrameGenerator Peripheral
- Implement `pru_io/uart_frame_generator.py`
- Integrate with simulator core (simulator.py)
- Add to IOPort peripheral list
- Test with known patterns

### 8.3 Phase 3: MCP Server Integration
- Add `pru_uart_inject` tool to `mcp_server/server.py`
- Implement parameter handling and result formatting
- Add safety checks and documentation

### 8.4 Phase 4: Test Suite
- `tests/test_uart_rx.py`: Basic reception tests
- `tests/test_uart_rx_errors.py`: Framing error and recovery tests
- `tests/test_uart_rx_mcp.py`: End-to-end MCP tool tests
- Validate against existing UART TX for loopback testing

### 8.5 Phase 5: Documentation
- Update `/docs/mcp_server.md` with `pru_uart_inject` documentation
- Add usage examples to `/docs/getting_started.md`
- Update release notes

## 9. Performance and Resource Usage

### 9.1 Cycles per Byte (Nominal 4 Mbaud)
- **Start Detection**: Variable (depends on line idle time)
- **Start Sample Delay**: 75 cycles fixed
- **Data Bit Reception**: 8 × 50 = 400 cycles
- **Stop Bit Verification**: 50 cycles
- **Total per Byte**: ~525 cycles + start detection variability
- **Frame Overhead**: Minimal (just state transitions)

### 9.2 Memory Usage
- **PRU Registers**: 21 registers (R2-R20) used consistently
- **DRAM0 Storage**: 11 bytes per frame + 1 byte error flag
- **Code Size**: Estimated < 100 instructions

### 9.3 Scalability
- **Baudrate Range**: Limited by loop overhead (tested 1-5 Mbaud expected)
- **Frame Size**: Easily modified by changing buffer size and SBCO count
- **Pin Selection**: Configurable via peripheral and MCP tool parameters

## 10. Safety and Security Considerations

### 10.1 Deterministic Behavior
- Fixed timing loops ensure predictable execution
- No dynamic memory allocation
- Bounded register usage

### 10.2 Error Containment
- Framing errors affect only current frame
- Error flag provides host notification
- System continues operation after errors

### 10.3 Test Isolation
- UARTFrameGenerator peripheral can be disabled
- MCP tool requires explicit PRU binary specification
- No side effects on unrelated simulator functions

## 11. Open Issues and Future Work

### 11.1 Configuration Flexibility
- Consider making frame size configurable (currently fixed at 11)
- Consider adding parity bit support
- Consider DMA-like continuous streaming mode

### 11.2 Performance Optimization
- Investigate using PRU's shift register features if available
- Consider double-buffering for higher throughput
- Explore interrupt-driven vs polling trade-offs

### 11.3 Integration Testing
- Loopback testing: UART TX → UART RX via GPO0→GPI0 wiring
- Stress testing with varying baudrates and payloads
- Multi-core coordination (PRU0 RX + RTU0 TX)

## 12. Conclusion

This design provides a robust, efficient UART reception capability for the PRU simulator that:
- Meets all specified requirements (11-byte frames, 4 Mbaud, GPI0 RX)
- Uses optimal instruction cycles with tight timing loops
- Stores data efficiently via single SBCO operation
- Provides clear error detection and recovery mechanisms
- Integrates cleanly with existing simulator architecture
- Enables comprehensive testing via MCP server and new peripheral
- Follows established code patterns and conventions

The frame-oriented approach minimizes memory operations while maintaining deterministic timing, making it suitable for both testing and potential real-world applications.
