# MCP Server Documentation + UART Print Example — Design Spec

**Date:** 2026-06-05
**Status:** Approved

---

## Goal

Produce:
1. `docs/mcp_server.md` — user-facing guide covering setup, all MCP tools, and two worked examples
2. `source/uart_tx.asm` — PRU assembly: transmit one byte via R30.t0 at 115200 baud
3. `source/uart_print.asm` — PRU assembly: transmit a null-terminated string from DRAM0
4. Claude Code `settings.json` entry that registers the PRU simulator as an MCP server

---

## Section 1 — MCP Server Setup

### What it covers
- One paragraph explaining what Model Context Protocol is and why the PRU simulator exposes it
- Install step: `pip install mcp` (already in requirements.txt)
- Claude Code config snippet — `mcpServers` entry in `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "pru-simulator": {
      "command": "python",
      "args": ["C:/ti/industrial-automation-lab/Projects/pru_simulator/mcp_server/server.py"],
      "cwd": "C:/ti/industrial-automation-lab/Projects/pru_simulator"
    }
  }
}
```

- Python direct-use quick start (3 lines, no MCP SDK required):

```python
from mcp_server.server import PRUSimulatorMCP
mcp = PRUSimulatorMCP()
mcp.pru_load("ldi r0, 42\nhalt")
```

### MCP tool reference table

All 10 tools documented inline — name, parameters, return shape, one-line description. No separate reference file; the table lives in the same doc.

---

## Section 2 — UART Bit-Bang: Single Character

### UART framing

Standard 8N1 (8 data bits, no parity, 1 stop bit). Idle line is HIGH.

```
  IDLE  START  b0  b1  b2  b3  b4  b5  b6  b7  STOP  IDLE
   1      0    ..  ..  ..  ..  ..  ..  ..  ..    1     1
         <---------  1 / baud  per cell  -------->
```

### Timing calculation

| Parameter      | Value                     |
|----------------|---------------------------|
| PRU clock      | 200 MHz                   |
| Cycle time     | 5 ns                      |
| Baud rate      | 115200                    |
| Bit period     | 8.68 µs = 1736 cycles     |
| Delay loop     | `SUB + QBNE` = 2 cyc/iter |
| BAUD_COUNT     | 868 iterations            |

The 4–6 extra cycles from the surrounding bit-set instructions are negligible at 115200 baud (< 0.5% timing error). For higher baud rates, subtract the surrounding instruction count from BAUD_COUNT.

### `source/uart_tx.asm` — transmit one byte

Structure:
1. Load character into `r0`, bit counter (8) into `r1`
2. START bit: clear R30.t0, delay BAUD_COUNT
3. Bit loop (8 iterations, LSB first):
   - Isolate LSB of `r0` → set/clear R30.t0 only
   - Delay BAUD_COUNT
   - Shift `r0` right, decrement `r1`, loop
4. STOP bit: set R30.t0 (line HIGH), delay BAUD_COUNT
5. `halt`

R30 bits other than t0 are preserved using `SET`/`CLR` bit instructions rather than overwriting the full register, so other GPO pins are not disturbed.

### Python MCP driver — single character

```python
from mcp_server.server import PRUSimulatorMCP

mcp = PRUSimulatorMCP()
mcp.pru_load(open("source/uart_tx.asm").read(), core="pru0")

bits = []
for _ in range(10000):           # enough steps to run past all BAUD_COUNT loops
    mcp.pru_step(core="pru0")
    pin0 = mcp.pru_io(core="pru0")["gpo_pins"][0]
    bits.append(pin0)
    if mcp.pru_status()["cores"]["pru0"]["halted"]:
        break

# Decode: find START edge, sample at bit-centre offsets
print("Raw GPO pin-0 trace:", bits[:20], "...")
```

The doc explains how to identify the START bit falling edge in the bit list and reconstruct the 8 data bits.

---

## Section 3 — UART Bit-Bang: String ("Hello PRU\r\n")

### `source/uart_print.asm` — transmit a string from DRAM0

Structure:
1. Caller pre-loads the string bytes into DRAM0 starting at offset 0 (done by the Python driver via `sbco` sequence or MCP memory write — see note below)
2. `r4` = DRAM0 pointer (starts at 0); `r5` = byte loaded each iteration
3. Outer loop:
   - LBCO one byte from DRAM0[r4] into `r5`
   - `QBEQ done, r5, 0` — stop on null terminator
   - Call the uart_send_byte subroutine (same logic as uart_tx.asm, inline)
   - Increment `r4`, loop
4. `halt`

**Note on pre-loading the string:** The Python driver writes the ASCII bytes for `"Hello PRU\r\n\0"` into DRAM0 via `mcp.sim.memory.write(addr, data)` — the memory bus's low-level write method (`Simulator.memory` is the `MemoryBus`). `PRUSimulatorMCP` exposes `pru_memory` for reads only; writes go through the underlying `Simulator.memory` directly.

### Python MCP driver — string

```python
mcp = PRUSimulatorMCP()

# Write "Hello PRU\r\n\0" into DRAM0 at offset 0
msg = b"Hello PRU\r\n\0"
mcp.sim.memory.write(0x00000000, msg)

mcp.pru_load(open("source/uart_print.asm").read(), core="pru0")
result = mcp.pru_run_until(core="pru0", max_steps=500000)
print("Stopped:", result["reason"], "at cycle", result["cycles"])
```

The doc then shows how to replay the GPO trace (step-mode) and decode each character.

---

## Files to Create / Modify

| Path | Action |
|------|--------|
| `docs/mcp_server.md` | Create — user-facing guide |
| `source/uart_tx.asm` | Create — single-byte UART TX |
| `source/uart_print.asm` | Create — string UART TX |
| `~/.claude/settings.json` | Modify — add `mcpServers` entry |

---

## Out of Scope

- Receive (RX) via GPI — not part of this task
- Hardware UART peripheral stub — not part of this task
- `pru_memory_write` MCP tool — not part of this task (driver uses `mcp.sim.memory_write` directly)
- Baud rate other than 115200
