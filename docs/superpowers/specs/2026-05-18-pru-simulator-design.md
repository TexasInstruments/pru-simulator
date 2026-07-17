# PRU Simulator — Step 1 Design Specification

## Overview

A multi-core PRU assembly simulator that provides exact simulation of PRU instructions on registers and IOs. Written in Python with an API-first architecture supporting AI-driven code generation (MCP), human visualization (HTML dashboard), and future VS Code integration (DAP/LSP).

**Target devices:**
- V3: AM261x, AM263x, AM263Px
- V4 (ICSS_G): AM243x, AM64x

**Cores:** PRU0 + RTU0 with IPC via XFR scratchpad (R2-R9)

---

## Architecture

See `docs/architecture.png` and `docs/development_flows.png` for visual diagrams.

```
Assembler/Parser (standalone)
  → Instruction IR (intermediate representation)

PRUCore (per-core instance: PRU0, RTU0)
  ├── RegisterFile — R0-R31 with sub-register access
  ├── Decoder — IR → decoded instruction objects
  ├── ALU — arithmetic/logic/bit ops
  ├── BranchUnit — PC manipulation, hardware loop
  ├── CycleCounters — cycles, stall_cycles, instruction_count
  └── IOPort — R30/R31 pin abstraction

MemorySubsystem (shared)
  ├── MemoryBus — routes by address range
  ├── DRAM0, DRAM1 — per-core local (0 latency)
  ├── SharedRAM — ICSS shared (0 latency)
  ├── MSRAM — SOC memory (configurable latency + jitter)
  └── ConstantTable — C0-C31 pointer resolution

XFRBus (shared)
  └── Scratchpad — R2-R9 exchange between cores

Simulator (orchestrator)
  ├── cores: [PRUCore, PRUCore]
  ├── memory: MemorySubsystem
  ├── xfr: XFRBus
  └── step(core_id) / run_until(condition)
```

---

## Module Specifications

### 1. Assembly Parser & Preprocessor

**Files:** `core/parser.py`, `core/preprocessor.py`

#### Preprocessor

Runs first pass over source files:
- `.include "file.inc"` — reads and inlines the file
- `.macro NAME` ... `.endm` — stores macro body, expands at call sites with argument substitution
- `.if` / `.else` / `.endif` — conditional assembly (evaluates constant expressions)
- `.set NAME, value` — named constants (numeric or expression)
- `.struct` / `.ends` — creates field offset constants

#### Parser

Two-pass on preprocessed source:
1. **Pass 1:** Collect all labels → instruction addresses
2. **Pass 2:** Parse instructions, resolve label references

Produces ordered list of `Instruction` IR objects:

```python
@dataclass
class Instruction:
    address: int          # word address (PC value)
    opcode: str           # "ADD", "QBGT", "LBBO", etc.
    operands: list        # parsed operand objects
    source_line: int      # original source line number
    source_text: str      # original asm text
```

#### Operand Types

```python
Register(index, offset, width)    # r5.b2 → Register(5, 16, 8)
Immediate(value)                   # 255, 0xFF
Label(name, resolved_addr)         # my_loop → resolved address
BitField(reg, bit)                 # r31.t5 → for QBBS/QBBC
MemRef(base_reg, offset, length)   # for LBBO/SBBO
ConstRef(table_idx, offset)        # for LBCO/SBCO
```

#### Register Notation Parsing

| Notation | Parsed As | offset | width |
|----------|-----------|--------|-------|
| `Rn` | Register(n, 0, 32) | 0 | 32 |
| `Rn.b0` | Register(n, 0, 8) | 0 | 8 |
| `Rn.b1` | Register(n, 8, 8) | 8 | 8 |
| `Rn.b2` | Register(n, 16, 8) | 16 | 8 |
| `Rn.b3` | Register(n, 24, 8) | 24 | 8 |
| `Rn.w0` | Register(n, 0, 16) | 0 | 16 |
| `Rn.w1` | Register(n, 8, 16) | 8 | 16 |
| `Rn.w2` | Register(n, 16, 16) | 16 | 16 |
| `Rn.tx` | BitField(n, x) | — | 1 |

---

### 2. Register File

**File:** `core/registers.py`

```python
class RegisterFile:
    regs: list[int]    # 32 × 32-bit unsigned integers (R0-R31)
    carry: bool        # carry flag for ADC/SUC/RSC

    def read(self, reg: int, offset: int, width: int) -> int
    def write(self, reg: int, offset: int, width: int, value: int)
    def read_full(self, reg: int) -> int          # shortcut for 32-bit read
    def write_full(self, reg: int, value: int)    # shortcut for 32-bit write
```

**Sub-register access:** `read()` extracts bits at `[offset : offset+width]`. `write()` masks and inserts without disturbing other bits.

**Special registers:**
- R30 writes → notify IOPort (GPO pins update)
- R31 reads → pull from IOPort (GPI pin state)

**Carry bit:** Position depends on destination width:
- Byte destination (.b*): carry = bit 8 of result
- Word destination (.w*): carry = bit 16 of result
- Full register: carry = bit 32 of result

**Special R0 byte fields (bn):** Used as dynamic length/count operand in:
- LBBO, SBBO, LBCO, SBCO — transfer length
- XIN, XOUT, XCHG — transfer length
- LOOP — iteration count
- ZERO, FILL — byte count
- Constraint: value of 0 in r0.bn → simulator raises warning (hangs real PRU)

**R1 byte fields:** Required pointer register for MVIx instructions (MVIB, MVIW, MVID).

---

### 3. ALU & Instruction Execution

**Files:** `core/alu.py`, `core/decoder.py`

#### Decoder

Maps `Instruction` IR to executable form:

```python
@dataclass
class DecodedInstruction:
    execute: Callable
    dst: RegisterRef
    src1: RegisterRef
    src2: Operand          # register or immediate
    width: int             # 8, 16, or 32
```

#### ALU Operations

Pure functions, no side effects:

| Category | Functions |
|----------|-----------|
| Arithmetic | `add`, `adc`, `sub`, `suc`, `rsb`, `rsc` |
| Logic | `and_`, `or_`, `xor_`, `not_` |
| Shift | `lsl`, `lsr` |
| Bit | `clr_bit`, `set_bit`, `lmbd` |
| Compare | `min_`, `max_` |

All return `(result, carry)` tuple for arithmetic, or just `result` for logic/shift.

#### Execution Cycle (per step)

1. Fetch instruction at current PC
2. Read source operands from RegisterFile
3. Execute via ALU / BranchUnit / MemoryBus
4. Write result to destination register
5. Advance PC (unless branch taken)
6. Increment cycle counter (+stall cycles if memory op)

---

### 4. Branch Unit

**File:** `core/branch.py`

| Instruction | Behavior |
|-------------|----------|
| QBA | PC += relative offset |
| JMP | PC = absolute address (immediate or register) |
| JAL | Reg = PC + 1; PC = target |
| QBEQ/QBNE | Branch if OP(255) == / != Reg1 |
| QBGT/QBGE | Branch if OP(255) > / >= Reg1 |
| QBLT/QBLE | Branch if OP(255) < / <= Reg1 |
| QBBS/QBBC | Branch if bit set / clear in Reg1 |
| LOOP | Set hardware loop (V3+) |
| WBS/WBC | Spin until bit set/clear (blocks, increments cycles) |

**Important:** Branch comparisons use reversed operand semantics — `QBGT label, Reg1, OP` branches when `OP > Reg1`.

#### Hardware Loop (V3+)

```python
class LoopState:
    count: int          # remaining iterations
    end_address: int    # PC after loop body
    start_address: int  # PC of first loop body instruction
```

When PC reaches `end_address`: decrement count. If count > 0, jump to `start_address`. Loop setup takes 1 cycle.

---

### 5. Cycle & Stall Counters

**File:** `core/counters.py`

```python
class CycleCounters:
    cycles: int              # total cycles (execution + stalls)
    stall_cycles: int        # cycles lost to memory latency only
    instruction_count: int   # instructions retired

    def tick(self, n=1)      # normal execution cycle
    def stall(self, n)       # memory stall cycles

    @property
    def ipc(self) -> float   # instructions / total cycles
```

**Stall sources (LBBO/SBBO/LBCO/SBCO only, all others execute in 1 cycle):**

| Region | Read (32-bit aligned) | Write (32-bit aligned) |
|--------|----------------------|------------------------|
| DRAM0 / DRAM1 | 2 cycles | 1 cycle |
| ICSS_SHARED | 2 cycles | 1 cycle |
| MS_RAM | 40 + random(0, jitter) cycles | 1 cycle |

**Burst transfers (multi-word):** Each additional 32-bit word adds 1 stall cycle.
- Example: `LBBO &r2, r1, 0, 8` (2 words from DRAM) = 2 + 1 = 3 stall cycles
- Example: `LBBO &r2, r1, 0, 12` (3 words from DRAM) = 2 + 1 + 1 = 4 stall cycles
- Example: `SBBO &r2, r1, 0, 8` (2 words to DRAM) = 1 + 1 = 2 stall cycles

**32-bit boundary crossing:** A transfer that crosses a 32-bit boundary counts as two transfers.
- Example: 16-bit read at address 0x03 (crosses 0x04 boundary) = 2 read stalls instead of 1

**Stall calculation formula:**
```
words = ceil(transfer_bytes / 4) + boundary_crossings
read_stalls  = region.read_latency + (words - 1) * 1
write_stalls = region.write_latency + (words - 1) * 1
```

Each core has its own `CycleCounters` instance.

---

### 6. Memory Subsystem

**Files:** `mem/memory_bus.py`, `mem/regions.py`

#### MemoryBus

```python
class MemoryBus:
    regions: list[MemoryRegion]

    def read(self, addr: int, length: int) -> tuple[bytes, int]   # data, latency
    def write(self, addr: int, data: bytes) -> int                 # latency
```

Routes by address range. Returns data + stall cycle count.

#### MemoryRegion

```python
class MemoryRegion:
    name: str
    base_addr: int
    size: int
    read_latency: int    # base stall cycles for first 32-bit read
    write_latency: int   # base stall cycles for first 32-bit write
    jitter: int          # max random additional cycles (reads only)
    data: bytearray

    def calc_read_stalls(self, addr: int, length: int) -> int
    def calc_write_stalls(self, addr: int, length: int) -> int
```

#### Configuration: memory.cfg

```ini
[device]
target = AM243x    # V4: core=V4, shared_ram=64KB
# target = AM263x  # V3: core=V3, shared_ram=32KB

[DRAM0]
base = 0x00000000
size = 0x2000
read_latency = 2
write_latency = 1
jitter = 0

[DRAM1]
base = 0x00002000
size = 0x2000
read_latency = 2
write_latency = 1
jitter = 0

[ICSS_SHARED]
base = 0x00010000
size = 0x10000     # 64KB for V4 (AM243x/AM64x), 32KB for V3 (AM261x/AM263x)
read_latency = 2
write_latency = 1
jitter = 0

[MS_RAM]
base = 0x80000000
size = 0x10000
read_latency = 40
write_latency = 1
jitter = 10        # applies to reads only
```

Device target sets defaults; individual sections override.

#### Constant Table (C0-C31)

Maps constant register IDs to base addresses for LBCO/SBCO. Loaded from `config/constants.cfg`:

```ini
[constants]
c0  = 0x00020000
c1  = 0x48040000
c2  = 0x4802A000
; ... device-specific entries
c24 = 0x00000000   # PRU0 DRAM
c25 = 0x00002000   # PRU1 DRAM
c26 = 0x00010000   # ICSS shared
c28 = 0x00100000   # ICSS CFG
```

Values are device-specific. Simulator loads the constant table matching the `[device] target` in `memory.cfg`.

---

### 7. XFR Bus & Scratchpad

**Files:** `xfr/xfr_bus.py`, `xfr/scratchpad.py`

#### Scratchpad

```python
class Scratchpad:
    data: bytearray    # 32 bytes (R2-R9 = 8 regs × 4 bytes)

    def xin(self, start_reg: int, length: int) -> bytes
    def xout(self, start_reg: int, data: bytes) -> None
    def xchg(self, start_reg: int, data: bytes) -> bytes  # atomic exchange
```

#### XFRBus

Dispatches by broadside device ID. Full AM243x/AM64x (V4) mapping:

| Broadside ID | Hardware Module | Copies / Sharing |
|-------------|-----------------|------------------|
| 0x00 | MPY/MAC | 6: TX_PRU1/0 + RTU_PRU1/0 + PRU1/0 |
| 0x01 | CRC16/32 | 6: TX_PRU1/0 + RTU_PRU1/0 + PRU1/0 |
| 0x08 | STITCH_FIFO64 | 2: PRU1/0 |
| 0x09 | QUEUE_PTR | 2: RTU_PRU1/0 |
| 0x02/0x38/0x39/0x47/0x49 | SUM32 | 4: RTU_PRU1/0 + PRU1/0 (various modes) |
| **0x0A (10)** | **SPAD Bank0** | **shared PRU1/0; shared RTU_PRU1/0; per TX_PRU** |
| **0x0B (11)** | **SPAD Bank1** | **shared PRU1/0; shared RTU_PRU1/0** |
| **0x0C (12)** | **SPAD Bank2** | **shared PRU1/0; shared RTU_PRU1/0** |
| **0x0F (15)** | **IPC SPAD** | **2: 1 per slice** |
| 0x14/0x15 (20/21) | RX L2 | 2: PRU1/0 |
| 0x16 (22) | RX Classifier | 2: 1 per slice |
| 0x23 (35) | RX first 24B + 16B TSN/pre | 2: RTU_PRU1/0 |
| 0x0F (15) | RX last 12B | mapped to upper IPC SPAD (RTU+PRU) |
| 0x1E-0x23 (30-35) | FDB 16KB (2×8KB banks) | 1: shared all cores |
| 0x20/0x21/0x22 (32/33/34) | FDB results | 1: shared RTU+PRU |
| 0x1E/0x26 (30/38) | BS RAM (RTU mode) | 2: RTU_PRU1/0 |
| 0x30/0x31 (48/49) | BS RAM (PRU mode) | 2: PRU1/0 + TX_PRU1/0 |
| 0x28 (40) | TX L2 | 2: PRU1/0 or TX_PRU1/0 |
| 0x50-0x53 | XFR2PSI | 4: RTU_PRU1/0 + PRU1/0 |
| 0x58/0x59 | XFR2PSI Share | 2: RTU access to PRU's PSI |
| 0x60-0x64 | XFR2VBUSP | shared per slice (RD/WD/TX) |
| 0x70-0x72 | XFR2SHORT_DMA | 2: RTU_PRU1/0 |
| 0x90 | XFR2SPIN | 1: per PRU_ICSSG system |
| 0xA0-0xA2 | BSWAP | 6: all cores (byte/4_8/4_16 modes) |
| 0xF0 | QUEUE_EMPTY | shared XIN only, all 6 cores |

**Step 1 scope:** Implement SPAD Bank0 (ID 10) and IPC SPAD (ID 15). Other devices stubbed (return zero / log warning).

#### IPC Pattern (Step 1)

```assembly
; PRU0 writes R2-R9 to SPAD Bank0 (shared with RTU0)
XOUT 10, &r2, 32

; RTU0 reads SPAD Bank0 into R2-R9
XIN  10, &r2, 32

; Or using IPC SPAD (ID 15) for per-slice exchange
XOUT 15, &r2, 32
XIN  15, &r2, 32
```

---

### 8. IO Port

**File:** `pru_io/io_port.py`

```python
class IOPort:
    gpo: int    # 20-bit output state (R30 bits 0-19)
    gpi: int    # 20-bit input state (R31 bits 0-19)

    def write_r30(self, value: int) -> None
    def read_r31(self) -> int
    def set_gpi_pin(self, pin: int, value: bool) -> None
    def set_gpi_word(self, value: int) -> None
```

**Step 1 scope:** Direct IO mode only.
- GPO0-19: mapped to R30 bits 0-19
- GPI0-19: mapped to R31 bits 0-19

**Integration:** RegisterFile intercepts R30 writes and R31 reads, delegates to IOPort.

**External stimulus:** MCP tool or test scripts can set GPI pins to simulate inputs.

---

### 9. MCP Server

**File:** `mcp_server/server.py`

stdio-based MCP server for Claude Code integration.

#### Tools

| Tool | Parameters | Returns |
|------|-----------|---------|
| `pru_load` | `source: str, core: "pru0"\|"rtu0"` | `{success, errors[], line_count}` |
| `pru_step` | `core: str, count: int` | `{pc, cycles, stall_cycles, halted, instruction_text}` |
| `pru_run_until` | `core: str, condition: str` | `{pc, cycles, reason}` |
| `pru_registers` | `core: str` | `{r0-r31 as hex, carry}` |
| `pru_memory` | `addr: int, length: int` | `{hex_dump, ascii}` |
| `pru_io` | `core: str` | `{gpo_pins: [0\|1 × 20], gpi_pins: [0\|1 × 20]}` |
| `pru_set_input` | `core: str, pin: int, value: bool` | `{ok}` |
| `pru_reset` | `core: str` | `{ok}` |
| `pru_breakpoint` | `core: str, address: int` | `{id}` |
| `pru_status` | — | `{cores: [{name, pc, cycles, stall_cycles, ipc, halted}]}` |

#### AI Workflow

```
Claude generates PRU assembly
  → pru_load(source, "pru0")
  → pru_step("pru0", 100)
  → pru_registers("pru0") + pru_io("pru0")
  → verify output matches intent
  → if wrong: regenerate and retry
```

---

### 10. HTML Dashboard

**Files:** `ui/server.py`, `ui/static/index.html`, `ui/static/app.js`

**Backend:** FastAPI + WebSocket
**Frontend:** Single-page HTML/JS (no framework)

#### Panels

| Panel | Content |
|-------|---------|
| Source View | Assembly listing, current PC highlighted |
| Register View | R0-R31 with sub-field breakdown, changed values highlighted |
| IO View | 20 GPO + 20 GPI as colored indicators (green=high, gray=low) |
| Memory View | Hex dump of selected memory region |
| Controls | Step, Run, Reset, core selector (PRU0/RTU0) |
| Counters | Total cycles, stall cycles, instruction count, IPC |

#### Update Flow

Each `step()` → simulator pushes state delta over WebSocket → frontend updates in-place.

---

### 11. Self-Test / Validation Suite

**Files:** `tests/`, `tests/asm/`

#### Structure

One `.asm` file per instruction group:
- `test_arithmetic.asm` — ADD, ADC, SUB, SUC, RSB, RSC
- `test_logic.asm` — AND, OR, XOR, NOT, LSL, LSR
- `test_bit.asm` — CLR, SET, LMBD
- `test_data_move.asm` — MOV, LDI, LDI32, MVIx
- `test_memory.asm` — LBBO, SBBO, LBCO, SBCO
- `test_branches.asm` — all QBxx, JMP, JAL, QBA
- `test_control.asm` — LOOP, WBS, WBC, HALT, SLP
- `test_xfr.asm` — XIN, XOUT, XCHG (IPC)
- `test_corner_cases.asm` — carry propagation, sub-register overlaps, boundary values

#### Validation Method

1. Each test writes expected results to known registers, then HALTs
2. Python test runner: load → execute → compare final registers vs expected
3. Same `.asm` files assembled and run on real PRU silicon for cross-validation
4. Mismatches filed as bugs → simulator fixed to match hardware

#### Corner Cases to Cover

- Carry propagation across sub-register boundaries
- Sub-register write overlap (write .w1 then read .b1, .b2)
- Boundary values: 0, 0xFF, 0xFFFF, 0xFFFFFFFF
- LBBO with bn=0 (should warn, not hang)
- Branch at edge of address space
- LOOP with count from r0.bn

---

### 12. Project Structure

```
pru_simulator/
├── core/
│   ├── __init__.py
│   ├── parser.py          # assembly parser (two-pass)
│   ├── preprocessor.py    # .macro, .include, .if, .set, .struct
│   ├── registers.py       # RegisterFile with sub-register access
│   ├── alu.py             # arithmetic/logic/bit operations
│   ├── decoder.py         # instruction IR → executable form
│   ├── branch.py          # branch unit + hardware loop
│   ├── counters.py        # cycle + stall counters
│   └── pru_core.py        # PRUCore class (ties it all together)
├── mem/
│   ├── __init__.py
│   ├── memory_bus.py      # address routing
│   ├── regions.py         # MemoryRegion base + implementations
│   └── constant_table.py  # C0-C31 resolution
├── pru_io/
│   ├── __init__.py
│   └── io_port.py         # R30/R31 direct IO
├── xfr/
│   ├── __init__.py
│   ├── xfr_bus.py         # XFR dispatch by device ID
│   └── scratchpad.py      # shared scratchpad storage
├── mcp_server/
│   ├── __init__.py
│   └── server.py          # MCP stdio server
├── ui/
│   ├── server.py          # FastAPI + WebSocket backend
│   └── static/
│       ├── index.html
│       └── app.js
├── tests/
│   ├── asm/               # self-test assembly files
│   ├── test_parser.py
│   ├── test_registers.py
│   ├── test_alu.py
│   ├── test_memory.py
│   ├── test_branches.py
│   ├── test_xfr.py
│   └── test_integration.py
├── config/
│   ├── memory_am243x.cfg
│   ├── memory_am263x.cfg
│   └── constants.cfg      # constant table definitions
├── docs/
│   ├── architecture.png
│   ├── development_flows.png
│   └── generate_architecture_diagram.py
├── references/
│   ├── PRU Assembly Instruction Cheat Sheet.md
│   ├── PRU Instruction Set v01.pptx
│   ├── spruhv6b.pdf
│   └── spruij2.pdf
├── simulator.py            # top-level Simulator orchestrator
├── memory.cfg              # default memory configuration
└── requirements.txt        # fastapi, uvicorn, websockets, mcp
```

---

### 13. Future Extensions (Not in Step 1)

- `.out` binary file loading
- Additional IO modes (shift register, PRU-ECAP)
- Hardware multiplier (R25-R29)
- Additional peripherals on XFR bus
- VS Code extension (DAP debugger + LSP)
- TSEN task manager multi-tasking (V4)
- Event-driven cycle-accurate bus contention modeling
- Multi-instance (more than 2 cores)

---

### 14. Dependencies

```
python >= 3.11
fastapi
uvicorn
websockets
mcp              # Model Context Protocol SDK
pytest           # testing
```

---

### 15. Development Flows

Four primary flows (see `docs/development_flows.png`):

1. **Human Developer** — write .asm → load in HTML dashboard → single-step → observe IO/registers → debug
2. **AI Agent (MCP)** — Claude generates PRU code → pru_load → pru_step → pru_registers/pru_io → verify → iterate
3. **VS Code (Future)** — edit .asm → LSP syntax check → DAP debug session → step/breakpoint → IO panel
4. **Validation** — self-test .asm suite → run on simulator → run on real silicon → compare → fix mismatches
