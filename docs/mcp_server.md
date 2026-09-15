# PRU Simulator — MCP Server Guide

## What Is the MCP Server?

The PRU Simulator exposes its simulation API as a
[Model Context Protocol](https://modelcontextprotocol.io) (MCP) server.
Any MCP-compatible AI assistant (including Claude Code) can load PRU assembly,
step through execution, inspect registers and memory, and read I/O pin state
as native tool calls — without leaving the conversation.

---

## Setup

### Step 1 — Install the MCP SDK

The `mcp` package is already listed in `requirements.txt`:

```bash
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
pip install -r requirements.txt
```

### Step 2 — Register with Claude Code

Add a `pru-simulator` entry to `~/.claude/.mcp.json`. If the file already
exists, merge into the `mcpServers` object; if it does not exist, create it:

```json
{
  "mcpServers": {
    "pru-simulator": {
      "command": "python",
      "args": [
        "C:/ti/industrial-automation-lab/Projects/pru_simulator/mcp_server/server.py"
      ],
      "cwd": "C:/ti/industrial-automation-lab/Projects/pru_simulator"
    }
  }
}
```

Restart Claude Code. Claude can now call `pru_load`, `pru_step`, etc. directly
in any conversation where the simulator is relevant.

### Python Direct Access (no MCP SDK required)

```python
from mcp_server.server import PRUSimulatorMCP

mcp = PRUSimulatorMCP()              # loads memory.cfg
mcp.pru_load("ldi r0, 42\nhalt")
mcp.pru_run_until(core="pru0")
print(mcp.pru_registers())           # {'r0': '0x0000002a', 'r1': '0x00000000', ...}
```

### Simple SSI realtime firmware

The repository includes the three-image SSI realtime firmware and its
functional harness under `firmware/ssi_test`. It is self-contained and does
not require the CCS workspace:

```python
result = mcp.pru_ssi_simple_run(iterations=1000)
print(result["published"], result["frames"])
```

The dashboard's **Load actual firmware** action uses the same bundled files.
Edit `firmware/ssi_test/ssi_test/ssi_hardware_config.h` and run its
`tools/generate_config.py` command to test another validated profile.

---

## Tool Reference

| Tool | Parameters | Returns | Description |
|------|-----------|---------|-------------|
| `pru_load` | `source: str`, `core="pru0"` | `{success, errors, line_count}` | Assemble and load source |
| `pru_step` | `core="pru0"`, `count=1` | `{pc, cycles, halted, instruction_text}` | Execute N instructions |
| `pru_run_until` | `core="pru0"`, `condition="halt"`, `max_steps=10000` | `{pc, cycles, reason}` | Run to HALT or limit |
| `pru_registers` | `core="pru0"` | `{r0..r31: hex, carry: bool}` | Read all 32 GPRs + carry |
| `pru_memory` | `addr: int`, `length: int` | `{hex_dump, ascii}` | Read memory (hex + ASCII) |
| `pru_io` | `core="pru0"` | `{gpo_pins: [int×20], gpi_pins: [int×20]}` | I/O pin state |
| `pru_set_input` | `core="pru0"`, `pin: int`, `value: bool` | `{ok}` | Drive a GPI pin |
| `pru_reset` | `core="pru0"` | `{ok}` | Reset core (PC=0, regs=0) |
| `pru_breakpoint` | `core="pru0"`, `address: int` | `{id}` | Add a breakpoint |
| `pru_uart_inject` | `source`, `payload`, `baudrate=4M`, `frames=1`, `core`, `pin`, `dram0_offset`, `max_steps` | `{status, frames_received, received_data, match}` | UART RX end-to-end test |
| `pru_ssi_inject` | `source`, `value=0`, `bits=12`, `clk_pin=0`, `data_pin=8`, `core`, `dram0_offset=16`, `max_steps` | `{status, expected, captured, match, frames_captured, cycles}` | SSI encoder end-to-end test |
| `pru_ssi_simple_run` | `iterations=100000` | SSI realtime result object | Run the bundled PRU0, PRU1, and RTU_PRU1 firmware images |
| `pru_status` | — | `{cores: {name: {pc, cycles, halted}}}` | Snapshot all cores |

---

## Example 1 — UART TX: Single Character (`source/uart_tx.asm`)

Bit-bang UART transmitter using GPO pin 0 (R30.t0) at 115200 baud.

### Frame Format (8N1)

```
 IDLE  START  b0   b1   b2   b3   b4   b5   b6   b7  STOP  IDLE
  1      0   LSB  ...  ...  ...  ...  ...  ...  MSB   1     1
        |<---------  1 bit period = 8.68 µs  -------->|
```

The line is idle HIGH. A START bit (LOW) begins the frame. Eight data bits
follow LSB-first. A STOP bit (HIGH) closes the frame.

### Timing

| Parameter | Value |
|-----------|-------|
| PRU clock | 200 MHz → 5 ns/cycle |
| 115200 baud bit period | 8.68 µs = **1736 cycles** |
| Delay loop (`SUB` + `QBNE`) | 2 cycles/iter → **`BAUD_COUNT` = 868** |

The 3–4 instruction cycles around each `SET`/`CLR` add < 0.3% timing error
and are negligible at 115200 baud.

### Assembly Source

```asm
; source/uart_tx.asm — Bit-bang UART TX at 115200 baud (PRU @ 200 MHz)
; Transmits one byte (ASCII 'A' = 0x41) on GPO pin 0 (R30.t0)
; Frame: 8N1  START(0) | b0..b7 LSB-first | STOP(1)
; Timing: 200 MHz / 115200 baud = 1736 cycles/bit
;         Delay loop: SUB + QBNE = 2 cycles/iter  →  BAUD_COUNT = 868
BAUD_COUNT .set 868
TX_CHAR    .set 0x41           ; ASCII 'A'  (change to send a different byte)
TX_BIT     .set 0              ; R30.t0  =  GPO pin 0

        set  r30, r30, TX_BIT   ; idle line HIGH

        ldi  r0, TX_CHAR        ; r0 = byte to transmit
        ldi  r1, 8              ; r1 = bit counter (8 bits)

        ; ---- START bit (LOW) ----
        clr  r30, r30, TX_BIT
        ldi  r2, BAUD_COUNT
delay_start:
        sub  r2, r2, 1
        qbne delay_start, r2, 0

        ; ---- 8 data bits, LSB first ----
txloop:
        qbbs bit_high, r0, 0    ; jump if bit 0 of r0 is set
        clr  r30, r30, TX_BIT   ; bit = 0  →  drive LOW
        qba  bit_done
bit_high:
        set  r30, r30, TX_BIT   ; bit = 1  →  drive HIGH
bit_done:
        lsr  r0, r0, 1          ; shift right: next bit → position 0
        ldi  r2, BAUD_COUNT
delay_data:
        sub  r2, r2, 1
        qbne delay_data, r2, 0
        sub  r1, r1, 1
        qbne txloop, r1, 0

        ; ---- STOP bit (HIGH) ----
        set  r30, r30, TX_BIT
        ldi  r2, BAUD_COUNT
delay_stop:
        sub  r2, r2, 1
        qbne delay_stop, r2, 0

        halt
```

### Python MCP Driver

```python
# Run from: C:/ti/industrial-automation-lab/Projects/pru_simulator
from mcp_server.server import PRUSimulatorMCP

mcp = PRUSimulatorMCP()

with open("source/uart_tx.asm") as f:
    mcp.pru_load(f.read(), core="pru0")

result = mcp.pru_run_until(core="pru0", max_steps=50000)
print(f"Halted after {result['cycles']} cycles")
# Expected: ~17360 cycles  (10 bit-periods × 1736 cycles/bit)

io = mcp.pru_io(core="pru0")
print(f"GPO pin 0 (TX): {io['gpo_pins'][0]}")  # 1 = idle HIGH after STOP bit
```

### Observing the Bit Stream Step-by-Step

Patch `BAUD_COUNT` to a small value and collect `pru_io` after every step:

```python
import re
from mcp_server.server import PRUSimulatorMCP

mcp = PRUSimulatorMCP()
with open("source/uart_tx.asm") as f:
    src = f.read()

# Shrink BAUD_COUNT for fast step-through (not cycle-accurate)
src = re.sub(r"\.set\s+BAUD_COUNT,\s*\d+", ".set BAUD_COUNT, 5", src)
mcp.pru_load(src, core="pru0")

pin_trace = []
for _ in range(500):
    mcp.pru_step(core="pru0")
    pin_trace.append(mcp.pru_io(core="pru0")["gpo_pins"][0])
    if mcp.pru_status()["cores"]["pru0"]["halted"]:
        break

# Compress to unique transitions
transitions = [pin_trace[0]]
for p in pin_trace[1:]:
    if p != transitions[-1]:
        transitions.append(p)

# For 'A' (0x41 = 01000001, LSB-first: 1,0,0,0,0,0,1,0):
# transitions = [1, 0, 1, 0, 1, 0, 1]
# idle → START → b0=1 → b1-b5=0 → b6=1 → b7=0 → STOP
print("Pin 0 transitions:", transitions)
```

---

## Example 2 — UART TX: String (`source/uart_print.asm`)

Transmits a null-terminated string that the Python driver pre-loads into DRAM0.

### Pre-loading Memory

`PRUSimulatorMCP.pru_memory` reads memory but does not write it.
Writes go directly through the underlying `MemoryBus`:

```python
mcp.sim.memory.write(0x00000000, b"Hello PRU\r\n\x00")
```

`0x00000000` is the DRAM0 base address. `c24` in the assembly references the
same region via the PRU constant table (AM243x: `c24 = 0x00000000`).

### Assembly Source

```asm
; source/uart_print.asm — UART TX of a null-terminated string from DRAM0
; Caller must pre-load the string at DRAM0 offset 0 before execution.
; c24 = DRAM0 base  (PRU constant table entry 24, address 0x00000000)
; r4.b0 = current byte offset  (max string length: 255 bytes)
BAUD_COUNT .set 868
TX_BIT     .set 0              ; R30.t0  =  GPO pin 0

        set  r30, r30, TX_BIT   ; idle HIGH

        ldi  r4, 0              ; r4.b0 = DRAM0 byte offset

next_char:
        lbco &r0, c24, r4.b0, 1 ; r0 = DRAM0[r4.b0]
        qbeq done, r0, 0         ; null terminator → stop
        ldi  r1, 8               ; r1 = bit counter

        ; ---- START bit (LOW) ----
        clr  r30, r30, TX_BIT
        ldi  r2, BAUD_COUNT
delay_start:
        sub  r2, r2, 1
        qbne delay_start, r2, 0

        ; ---- 8 data bits, LSB first ----
txloop:
        qbbs bit_high, r0, 0
        clr  r30, r30, TX_BIT
        qba  bit_done
bit_high:
        set  r30, r30, TX_BIT
bit_done:
        lsr  r0, r0, 1
        ldi  r2, BAUD_COUNT
delay_data:
        sub  r2, r2, 1
        qbne delay_data, r2, 0
        sub  r1, r1, 1
        qbne txloop, r1, 0

        ; ---- STOP bit (HIGH) ----
        set  r30, r30, TX_BIT
        ldi  r2, BAUD_COUNT
delay_stop:
        sub  r2, r2, 1
        qbne delay_stop, r2, 0

        add  r4, r4, 1           ; advance to next byte
        qba  next_char

done:
        halt
```

### Python MCP Driver

```python
# Run from: C:/ti/industrial-automation-lab/Projects/pru_simulator
from mcp_server.server import PRUSimulatorMCP

mcp = PRUSimulatorMCP()

# Pre-load "Hello PRU\r\n" into DRAM0
message = b"Hello PRU\r\n\x00"
mcp.sim.memory.write(0x00000000, message)

with open("source/uart_print.asm") as f:
    mcp.pru_load(f.read(), core="pru0")

result = mcp.pru_run_until(core="pru0", max_steps=500000)
print(f"Stopped: {result['reason']} after {result['cycles']} cycles")
# 11 chars × ~17360 cycles/char  ≈  190 000 cycles

# TX line must be HIGH (idle) at the end
io = mcp.pru_io(core="pru0")
assert io["gpo_pins"][0] == 1, "TX line not idle after STOP bit"

# Verify DRAM0 still holds the original string
dump = mcp.pru_memory(addr=0, length=len(message))
print(f"DRAM0 string: {dump['ascii']}")
```

---

## Tips

- **Change the baud rate:** Recalculate `BAUD_COUNT = (PRU_MHz × 1_000_000) / (baud × 2)`.
  For 9600 baud at 200 MHz: `BAUD_COUNT = 10416`.
- **Change the TX pin:** Update `.set TX_BIT, N` (0–19) to use a different GPO pin.
- **Watch the Signal Graph:** Load the program in the web dashboard, open the
  Signal Graph panel, and click Run — pin 0 will show the UART pulses as a
  digital trace.
- **Longer strings:** `r4.b0` limits offsets to 0–255.  For strings longer than
  255 bytes, replace `lbco &r0, c24, r4.b0, 1` with `lbbo &r0, r4, 0, 1` and
  initialise `r4` to the absolute DRAM0 base address (`0x00000000`).
