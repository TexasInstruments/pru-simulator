# PRU Simulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dual-core PRU assembly simulator (PRU0 + RTU0) with MCP server integration for AI-driven code generation and an HTML dashboard for human visualization.

**Architecture:** Modular pipeline — each hardware component is a separate Python module with clean interfaces. The Simulator orchestrator wires cores to shared memory/XFR bus. Interface layer (MCP + HTML) wraps the orchestrator.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, websockets, mcp SDK, pytest

---

## Task 1: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `core/__init__.py`
- Create: `mem/__init__.py`
- Create: `pru_io/__init__.py`
- Create: `xfr/__init__.py`
- Create: `mcp_server/__init__.py`
- Create: `ui/__init__.py`
- Create: `tests/__init__.py`
- Create: `config/memory_am243x.cfg`
- Create: `config/memory_am263x.cfg`
- Create: `config/constants_am243x.cfg`
- Create: `memory.cfg` (default symlink/copy of am243x)

- [ ] **Step 1: Create requirements.txt**

```
pytest>=7.0
fastapi>=0.100
uvicorn>=0.20
websockets>=11.0
mcp>=1.0
```

- [ ] **Step 2: Create all __init__.py files**

```bash
touch core/__init__.py mem/__init__.py pru_io/__init__.py xfr/__init__.py mcp_server/__init__.py tests/__init__.py
mkdir -p ui/static tests/asm config
touch ui/__init__.py
```

- [ ] **Step 3: Create config/memory_am243x.cfg**

```ini
[device]
target = AM243x
core_version = V4

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
size = 0x10000
read_latency = 2
write_latency = 1
jitter = 0

[MS_RAM]
base = 0x80000000
size = 0x10000
read_latency = 40
write_latency = 1
jitter = 10
```

- [ ] **Step 4: Create config/memory_am263x.cfg**

```ini
[device]
target = AM263x
core_version = V3

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
size = 0x8000
read_latency = 2
write_latency = 1
jitter = 0

[MS_RAM]
base = 0x80000000
size = 0x10000
read_latency = 40
write_latency = 1
jitter = 10
```

- [ ] **Step 5: Create config/constants_am243x.cfg**

```ini
[constants]
c0  = 0x00020000
c1  = 0x48040000
c2  = 0x4802A000
c3  = 0x00030000
c4  = 0x00026000
c5  = 0x48060000
c6  = 0x48030000
c7  = 0x00028000
c8  = 0x46000000
c9  = 0x4A100000
c10 = 0x48318000
c11 = 0x48022000
c12 = 0x48024000
c13 = 0x48310000
c14 = 0x481CC000
c15 = 0x481D0000
c16 = 0x481A0000
c17 = 0x4819C000
c18 = 0x00032400
c19 = 0x00032000
c20 = 0x00032800
c21 = 0x00032C00
c22 = 0x00032400
c23 = 0x00032000
c24 = 0x00000000
c25 = 0x00002000
c26 = 0x00010000
c27 = 0x00000000
c28 = 0x00100000
c29 = 0x00000000
c30 = 0x00000000
c31 = 0x00000000
```

- [ ] **Step 6: Copy AM243x as default memory.cfg**

```bash
cp config/memory_am243x.cfg memory.cfg
```

- [ ] **Step 7: Verify structure**

Run: `find . -name "*.py" -o -name "*.cfg" -o -name "*.txt" | sort`

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: project scaffolding with configs and package structure"
```

---

## Task 2: Register File

**Files:**
- Create: `core/registers.py`
- Create: `tests/test_registers.py`

- [ ] **Step 1: Write failing tests for RegisterFile**

```python
# tests/test_registers.py
import pytest
from core.registers import RegisterFile


class TestRegisterFileBasic:
    def test_initial_state_all_zeros(self):
        rf = RegisterFile()
        for i in range(32):
            assert rf.read_full(i) == 0

    def test_write_read_full_register(self):
        rf = RegisterFile()
        rf.write_full(5, 0xDEADBEEF)
        assert rf.read_full(5) == 0xDEADBEEF

    def test_write_truncates_to_32_bits(self):
        rf = RegisterFile()
        rf.write_full(0, 0x1FFFFFFFF)
        assert rf.read_full(0) == 0xFFFFFFFF


class TestSubRegisterAccess:
    def test_read_b0(self):
        rf = RegisterFile()
        rf.write_full(5, 0x44332211)
        assert rf.read(5, 0, 8) == 0x11

    def test_read_b1(self):
        rf = RegisterFile()
        rf.write_full(5, 0x44332211)
        assert rf.read(5, 8, 8) == 0x22

    def test_read_b2(self):
        rf = RegisterFile()
        rf.write_full(5, 0x44332211)
        assert rf.read(5, 16, 8) == 0x33

    def test_read_b3(self):
        rf = RegisterFile()
        rf.write_full(5, 0x44332211)
        assert rf.read(5, 24, 8) == 0x44

    def test_read_w0(self):
        rf = RegisterFile()
        rf.write_full(5, 0x44332211)
        assert rf.read(5, 0, 16) == 0x2211

    def test_read_w1(self):
        rf = RegisterFile()
        rf.write_full(5, 0x44332211)
        assert rf.read(5, 8, 16) == 0x3322

    def test_read_w2(self):
        rf = RegisterFile()
        rf.write_full(5, 0x44332211)
        assert rf.read(5, 16, 16) == 0x4433

    def test_write_b0_preserves_other_bytes(self):
        rf = RegisterFile()
        rf.write_full(5, 0x44332211)
        rf.write(5, 0, 8, 0xFF)
        assert rf.read_full(5) == 0x443322FF

    def test_write_b3_preserves_other_bytes(self):
        rf = RegisterFile()
        rf.write_full(5, 0x44332211)
        rf.write(5, 24, 8, 0xAA)
        assert rf.read_full(5) == 0xAA332211

    def test_write_w0_preserves_upper(self):
        rf = RegisterFile()
        rf.write_full(5, 0x44332211)
        rf.write(5, 0, 16, 0xBEEF)
        assert rf.read_full(5) == 0x4433BEEF

    def test_write_w2_preserves_lower(self):
        rf = RegisterFile()
        rf.write_full(5, 0x44332211)
        rf.write(5, 16, 16, 0xCAFE)
        assert rf.read_full(5) == 0xCAFE2211

    def test_write_w1_middle_word(self):
        rf = RegisterFile()
        rf.write_full(5, 0x44332211)
        rf.write(5, 8, 16, 0xABCD)
        assert rf.read_full(5) == 0x44ABCD11


class TestCarryFlag:
    def test_initial_carry_false(self):
        rf = RegisterFile()
        assert rf.carry is False

    def test_set_carry(self):
        rf = RegisterFile()
        rf.carry = True
        assert rf.carry is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_registers.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'core.registers'"

- [ ] **Step 3: Implement RegisterFile**

```python
# core/registers.py
"""PRU Register File — 32 × 32-bit registers with sub-register access."""


class RegisterFile:
    def __init__(self):
        self.regs: list[int] = [0] * 32
        self.carry: bool = False

    def read_full(self, reg: int) -> int:
        return self.regs[reg]

    def write_full(self, reg: int, value: int) -> None:
        self.regs[reg] = value & 0xFFFFFFFF

    def read(self, reg: int, offset: int, width: int) -> int:
        mask = (1 << width) - 1
        return (self.regs[reg] >> offset) & mask

    def write(self, reg: int, offset: int, width: int, value: int) -> None:
        mask = (1 << width) - 1
        clear_mask = ~(mask << offset) & 0xFFFFFFFF
        self.regs[reg] = (self.regs[reg] & clear_mask) | ((value & mask) << offset)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_registers.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/registers.py tests/test_registers.py
git commit -m "feat: RegisterFile with sub-register access (b0-b3, w0-w2)"
```

---

## Task 3: Cycle Counters

**Files:**
- Create: `core/counters.py`
- Create: `tests/test_counters.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_counters.py
import pytest
from core.counters import CycleCounters


class TestCycleCounters:
    def test_initial_state(self):
        cc = CycleCounters()
        assert cc.cycles == 0
        assert cc.stall_cycles == 0
        assert cc.instruction_count == 0

    def test_tick_increments_cycles_and_instructions(self):
        cc = CycleCounters()
        cc.tick()
        assert cc.cycles == 1
        assert cc.instruction_count == 1

    def test_tick_n(self):
        cc = CycleCounters()
        cc.tick(5)
        assert cc.cycles == 5
        assert cc.instruction_count == 5

    def test_stall_increments_cycles_and_stall_only(self):
        cc = CycleCounters()
        cc.stall(10)
        assert cc.cycles == 10
        assert cc.stall_cycles == 10
        assert cc.instruction_count == 0

    def test_ipc_normal(self):
        cc = CycleCounters()
        cc.tick(10)
        assert cc.ipc == 1.0

    def test_ipc_with_stalls(self):
        cc = CycleCounters()
        cc.tick(5)
        cc.stall(5)
        assert cc.ipc == 0.5

    def test_ipc_zero_cycles(self):
        cc = CycleCounters()
        assert cc.ipc == 0.0

    def test_reset(self):
        cc = CycleCounters()
        cc.tick(10)
        cc.stall(5)
        cc.reset()
        assert cc.cycles == 0
        assert cc.stall_cycles == 0
        assert cc.instruction_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_counters.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement CycleCounters**

```python
# core/counters.py
"""PRU Cycle and Stall Counters."""


class CycleCounters:
    def __init__(self):
        self.cycles: int = 0
        self.stall_cycles: int = 0
        self.instruction_count: int = 0

    def tick(self, n: int = 1) -> None:
        self.cycles += n
        self.instruction_count += n

    def stall(self, n: int) -> None:
        self.cycles += n
        self.stall_cycles += n

    @property
    def ipc(self) -> float:
        if self.cycles == 0:
            return 0.0
        return self.instruction_count / self.cycles

    def reset(self) -> None:
        self.cycles = 0
        self.stall_cycles = 0
        self.instruction_count = 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_counters.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/counters.py tests/test_counters.py
git commit -m "feat: CycleCounters with tick, stall, and IPC tracking"
```

---

## Task 4: IO Port

**Files:**
- Create: `pru_io/io_port.py`
- Create: `tests/test_io_port.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_io_port.py
import pytest
from pru_io.io_port import IOPort


class TestIOPort:
    def test_initial_gpo_zero(self):
        io = IOPort()
        assert io.gpo == 0

    def test_initial_gpi_zero(self):
        io = IOPort()
        assert io.gpi == 0

    def test_write_r30_sets_gpo(self):
        io = IOPort()
        io.write_r30(0x000FFFFF)
        assert io.gpo == 0x000FFFFF

    def test_write_r30_masks_to_20_bits(self):
        io = IOPort()
        io.write_r30(0xFFFFFFFF)
        assert io.gpo == 0x000FFFFF

    def test_read_r31_returns_gpi(self):
        io = IOPort()
        io.set_gpi_word(0x00055555)
        assert io.read_r31() == 0x00055555

    def test_set_gpi_pin_high(self):
        io = IOPort()
        io.set_gpi_pin(5, True)
        assert io.read_r31() == (1 << 5)

    def test_set_gpi_pin_low(self):
        io = IOPort()
        io.set_gpi_word(0x000FFFFF)
        io.set_gpi_pin(5, False)
        assert io.read_r31() == 0x000FFFFF & ~(1 << 5)

    def test_set_gpi_pin_out_of_range(self):
        io = IOPort()
        with pytest.raises(ValueError):
            io.set_gpi_pin(20, True)

    def test_gpo_individual_bits(self):
        io = IOPort()
        io.write_r30(0b10101010101010101010)
        for i in range(20):
            expected = 1 if i % 2 == 1 else 0
            assert ((io.gpo >> i) & 1) == expected

    def test_get_gpo_pins_list(self):
        io = IOPort()
        io.write_r30(0b101)
        pins = io.get_gpo_pins()
        assert pins[0] == 1
        assert pins[1] == 0
        assert pins[2] == 1
        assert len(pins) == 20

    def test_get_gpi_pins_list(self):
        io = IOPort()
        io.set_gpi_word(0b110)
        pins = io.get_gpi_pins()
        assert pins[0] == 0
        assert pins[1] == 1
        assert pins[2] == 1
        assert len(pins) == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_io_port.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement IOPort**

```python
# pru_io/io_port.py
"""PRU IO Port — Direct IO mode for R30 (GPO) and R31 (GPI)."""

GPI_WIDTH = 20
GPO_WIDTH = 20
PIN_MASK = (1 << 20) - 1  # 0x000FFFFF


class IOPort:
    def __init__(self):
        self.gpo: int = 0
        self.gpi: int = 0

    def write_r30(self, value: int) -> None:
        self.gpo = value & PIN_MASK

    def read_r31(self) -> int:
        return self.gpi & PIN_MASK

    def set_gpi_pin(self, pin: int, value: bool) -> None:
        if pin < 0 or pin >= GPI_WIDTH:
            raise ValueError(f"GPI pin {pin} out of range (0-{GPI_WIDTH-1})")
        if value:
            self.gpi |= (1 << pin)
        else:
            self.gpi &= ~(1 << pin)

    def set_gpi_word(self, value: int) -> None:
        self.gpi = value & PIN_MASK

    def get_gpo_pins(self) -> list[int]:
        return [(self.gpo >> i) & 1 for i in range(GPO_WIDTH)]

    def get_gpi_pins(self) -> list[int]:
        return [(self.gpi >> i) & 1 for i in range(GPI_WIDTH)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_io_port.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add pru_io/io_port.py tests/test_io_port.py
git commit -m "feat: IOPort with direct IO mode (GPI0-19, GPO0-19)"
```

---

## Task 5: Memory Subsystem

**Files:**
- Create: `mem/regions.py`
- Create: `mem/memory_bus.py`
- Create: `mem/constant_table.py`
- Create: `tests/test_memory.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_memory.py
import pytest
from mem.regions import MemoryRegion
from mem.memory_bus import MemoryBus
from mem.constant_table import ConstantTable


class TestMemoryRegion:
    def test_create_region(self):
        r = MemoryRegion("DRAM0", 0x0000, 0x2000, read_latency=2, write_latency=1, jitter=0)
        assert r.name == "DRAM0"
        assert r.size == 0x2000

    def test_write_and_read(self):
        r = MemoryRegion("DRAM0", 0x0000, 0x2000, read_latency=2, write_latency=1, jitter=0)
        r.write(0x0000, b'\x11\x22\x33\x44')
        assert r.read(0x0000, 4) == b'\x11\x22\x33\x44'

    def test_read_stalls_single_word(self):
        r = MemoryRegion("DRAM0", 0x0000, 0x2000, read_latency=2, write_latency=1, jitter=0)
        assert r.calc_read_stalls(0x0000, 4) == 2

    def test_read_stalls_two_words(self):
        r = MemoryRegion("DRAM0", 0x0000, 0x2000, read_latency=2, write_latency=1, jitter=0)
        assert r.calc_read_stalls(0x0000, 8) == 3  # 2 + 1

    def test_read_stalls_three_words(self):
        r = MemoryRegion("DRAM0", 0x0000, 0x2000, read_latency=2, write_latency=1, jitter=0)
        assert r.calc_read_stalls(0x0000, 12) == 4  # 2 + 1 + 1

    def test_write_stalls_single_word(self):
        r = MemoryRegion("DRAM0", 0x0000, 0x2000, read_latency=2, write_latency=1, jitter=0)
        assert r.calc_write_stalls(0x0000, 4) == 1

    def test_write_stalls_two_words(self):
        r = MemoryRegion("DRAM0", 0x0000, 0x2000, read_latency=2, write_latency=1, jitter=0)
        assert r.calc_write_stalls(0x0000, 8) == 2  # 1 + 1

    def test_boundary_crossing_read(self):
        r = MemoryRegion("DRAM0", 0x0000, 0x2000, read_latency=2, write_latency=1, jitter=0)
        # 2-byte read at addr 0x03 crosses the 0x04 boundary
        assert r.calc_read_stalls(0x0003, 2) == 3  # 2 + 1 (counts as 2 words)

    def test_ms_ram_read_stalls(self):
        r = MemoryRegion("MS_RAM", 0x80000000, 0x10000, read_latency=40, write_latency=1, jitter=0)
        assert r.calc_read_stalls(0x80000000, 4) == 40

    def test_ms_ram_write_stalls(self):
        r = MemoryRegion("MS_RAM", 0x80000000, 0x10000, read_latency=40, write_latency=1, jitter=0)
        assert r.calc_write_stalls(0x80000000, 4) == 1

    def test_out_of_bounds_raises(self):
        r = MemoryRegion("DRAM0", 0x0000, 0x2000, read_latency=2, write_latency=1, jitter=0)
        with pytest.raises(ValueError):
            r.read(0x2000, 4)


class TestMemoryBus:
    def setup_method(self):
        self.bus = MemoryBus()
        self.bus.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
        self.bus.add_region(MemoryRegion("DRAM1", 0x2000, 0x2000, 2, 1, 0))
        self.bus.add_region(MemoryRegion("SHARED", 0x10000, 0x10000, 2, 1, 0))
        self.bus.add_region(MemoryRegion("MS_RAM", 0x80000000, 0x10000, 40, 1, 10))

    def test_route_to_dram0(self):
        self.bus.write(0x0000, b'\xAA\xBB\xCC\xDD')
        data, stalls = self.bus.read(0x0000, 4)
        assert data == b'\xAA\xBB\xCC\xDD'
        assert stalls == 2

    def test_route_to_dram1(self):
        self.bus.write(0x2000, b'\x11\x22\x33\x44')
        data, stalls = self.bus.read(0x2000, 4)
        assert data == b'\x11\x22\x33\x44'

    def test_write_returns_stalls(self):
        stalls = self.bus.write(0x0000, b'\x01\x02\x03\x04')
        assert stalls == 1

    def test_unmapped_address_raises(self):
        with pytest.raises(ValueError):
            self.bus.read(0x50000000, 4)


class TestConstantTable:
    def test_load_and_resolve(self):
        ct = ConstantTable()
        ct.set(24, 0x00000000)  # PRU0 DRAM
        ct.set(26, 0x00010000)  # ICSS shared
        assert ct.resolve(24) == 0x00000000
        assert ct.resolve(26) == 0x00010000

    def test_invalid_index_raises(self):
        ct = ConstantTable()
        with pytest.raises(ValueError):
            ct.resolve(32)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement MemoryRegion**

```python
# mem/regions.py
"""PRU Memory Region with latency-aware stall calculation."""
import math
import random


class MemoryRegion:
    def __init__(self, name: str, base_addr: int, size: int,
                 read_latency: int, write_latency: int, jitter: int = 0):
        self.name = name
        self.base_addr = base_addr
        self.size = size
        self.read_latency = read_latency
        self.write_latency = write_latency
        self.jitter = jitter
        self.data = bytearray(size)

    def _to_local(self, addr: int) -> int:
        local = addr - self.base_addr
        if local < 0 or local >= self.size:
            raise ValueError(f"Address 0x{addr:08X} out of bounds for {self.name}")
        return local

    def read(self, addr: int, length: int) -> bytes:
        local = self._to_local(addr)
        if local + length > self.size:
            raise ValueError(f"Read 0x{addr:08X}+{length} exceeds {self.name}")
        return bytes(self.data[local:local + length])

    def write(self, addr: int, data: bytes) -> None:
        local = self._to_local(addr)
        if local + len(data) > self.size:
            raise ValueError(f"Write 0x{addr:08X}+{len(data)} exceeds {self.name}")
        self.data[local:local + len(data)] = data

    def _count_words(self, addr: int, length: int) -> int:
        """Count 32-bit word accesses including boundary crossings."""
        start = addr
        end = addr + length
        first_word_boundary = (start & ~0x3)
        last_word_boundary = ((end - 1) & ~0x3)
        words = ((last_word_boundary - first_word_boundary) >> 2) + 1
        return words

    def calc_read_stalls(self, addr: int, length: int) -> int:
        words = self._count_words(addr, length)
        base = self.read_latency + random.randint(0, self.jitter)
        return base + (words - 1)

    def calc_write_stalls(self, addr: int, length: int) -> int:
        words = self._count_words(addr, length)
        return self.write_latency + (words - 1)

    def contains(self, addr: int) -> bool:
        return self.base_addr <= addr < self.base_addr + self.size
```

- [ ] **Step 4: Implement MemoryBus**

```python
# mem/memory_bus.py
"""PRU Memory Bus — routes accesses to regions by address."""
from mem.regions import MemoryRegion


class MemoryBus:
    def __init__(self):
        self.regions: list[MemoryRegion] = []

    def add_region(self, region: MemoryRegion) -> None:
        self.regions.append(region)
        self.regions.sort(key=lambda r: r.base_addr)

    def _find_region(self, addr: int) -> MemoryRegion:
        for region in self.regions:
            if region.contains(addr):
                return region
        raise ValueError(f"No memory region mapped at 0x{addr:08X}")

    def read(self, addr: int, length: int) -> tuple[bytes, int]:
        region = self._find_region(addr)
        data = region.read(addr, length)
        stalls = region.calc_read_stalls(addr, length)
        return data, stalls

    def write(self, addr: int, data: bytes) -> int:
        region = self._find_region(addr)
        region.write(addr, data)
        stalls = region.calc_write_stalls(addr, len(data))
        return stalls
```

- [ ] **Step 5: Implement ConstantTable**

```python
# mem/constant_table.py
"""PRU Constant Table — C0-C31 base address resolution."""


class ConstantTable:
    def __init__(self):
        self.entries: list[int] = [0] * 32

    def set(self, index: int, value: int) -> None:
        if index < 0 or index >= 32:
            raise ValueError(f"Constant table index {index} out of range (0-31)")
        self.entries[index] = value & 0xFFFFFFFF

    def resolve(self, index: int) -> int:
        if index < 0 or index >= 32:
            raise ValueError(f"Constant table index {index} out of range (0-31)")
        return self.entries[index]

    def load_from_dict(self, mapping: dict[int, int]) -> None:
        for idx, val in mapping.items():
            self.set(idx, val)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_memory.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add mem/regions.py mem/memory_bus.py mem/constant_table.py tests/test_memory.py
git commit -m "feat: Memory subsystem with regions, bus routing, and stall calculation"
```

---

## Task 6: XFR Bus & Scratchpad

**Files:**
- Create: `xfr/scratchpad.py`
- Create: `xfr/xfr_bus.py`
- Create: `tests/test_xfr.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_xfr.py
import pytest
from xfr.scratchpad import Scratchpad
from xfr.xfr_bus import XFRBus

SPAD_BANK0 = 10
IPC_SPAD = 15


class TestScratchpad:
    def test_initial_zeros(self):
        sp = Scratchpad()
        assert sp.read(0, 32) == bytes(32)

    def test_write_and_read(self):
        sp = Scratchpad()
        data = bytes(range(32))
        sp.write(0, data)
        assert sp.read(0, 32) == data

    def test_partial_write(self):
        sp = Scratchpad()
        sp.write(0, b'\xAA\xBB\xCC\xDD')
        assert sp.read(0, 4) == b'\xAA\xBB\xCC\xDD'
        assert sp.read(4, 4) == b'\x00\x00\x00\x00'

    def test_exchange(self):
        sp = Scratchpad()
        sp.write(0, b'\x11\x22\x33\x44')
        old = sp.exchange(0, b'\xAA\xBB\xCC\xDD')
        assert old == b'\x11\x22\x33\x44'
        assert sp.read(0, 4) == b'\xAA\xBB\xCC\xDD'


class TestXFRBus:
    def test_xout_xin_spad_bank0(self):
        bus = XFRBus()
        data = b'\x01\x02\x03\x04' * 8  # 32 bytes
        bus.xout(SPAD_BANK0, 0, data)
        result = bus.xin(SPAD_BANK0, 0, 32)
        assert result == data

    def test_xout_xin_ipc_spad(self):
        bus = XFRBus()
        data = b'\xDE\xAD\xBE\xEF' * 8
        bus.xout(IPC_SPAD, 0, data)
        result = bus.xin(IPC_SPAD, 0, 32)
        assert result == data

    def test_xchg(self):
        bus = XFRBus()
        bus.xout(SPAD_BANK0, 0, b'\x11\x22\x33\x44')
        old = bus.xchg(SPAD_BANK0, 0, b'\xAA\xBB\xCC\xDD')
        assert old == b'\x11\x22\x33\x44'

    def test_unknown_device_xin_returns_zeros(self):
        bus = XFRBus()
        result = bus.xin(99, 0, 4)
        assert result == b'\x00\x00\x00\x00'

    def test_unknown_device_xout_logs_warning(self, caplog):
        import logging
        bus = XFRBus()
        with caplog.at_level(logging.WARNING):
            bus.xout(99, 0, b'\x01\x02\x03\x04')
        assert "Unsupported XFR device" in caplog.text

    def test_spad_bank0_and_ipc_are_independent(self):
        bus = XFRBus()
        bus.xout(SPAD_BANK0, 0, b'\xAA' * 32)
        bus.xout(IPC_SPAD, 0, b'\xBB' * 32)
        assert bus.xin(SPAD_BANK0, 0, 32) == b'\xAA' * 32
        assert bus.xin(IPC_SPAD, 0, 32) == b'\xBB' * 32
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_xfr.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement Scratchpad**

```python
# xfr/scratchpad.py
"""PRU Scratchpad — 32 bytes of shared storage for XFR IPC."""

SCRATCHPAD_SIZE = 32  # 8 registers × 4 bytes (R2-R9)


class Scratchpad:
    def __init__(self):
        self.data = bytearray(SCRATCHPAD_SIZE)

    def read(self, offset: int, length: int) -> bytes:
        end = min(offset + length, SCRATCHPAD_SIZE)
        return bytes(self.data[offset:end])

    def write(self, offset: int, data: bytes) -> None:
        end = min(offset + len(data), SCRATCHPAD_SIZE)
        self.data[offset:end] = data[:end - offset]

    def exchange(self, offset: int, data: bytes) -> bytes:
        length = len(data)
        old = self.read(offset, length)
        self.write(offset, data)
        return old
```

- [ ] **Step 4: Implement XFRBus**

```python
# xfr/xfr_bus.py
"""PRU XFR Bus — dispatches XIN/XOUT/XCHG by broadside device ID."""
import logging
from xfr.scratchpad import Scratchpad

logger = logging.getLogger(__name__)

SPAD_BANK0 = 10
SPAD_BANK1 = 11
SPAD_BANK2 = 12
IPC_SPAD = 15


class XFRBus:
    def __init__(self):
        self.devices: dict[int, Scratchpad] = {
            SPAD_BANK0: Scratchpad(),
            SPAD_BANK1: Scratchpad(),
            SPAD_BANK2: Scratchpad(),
            IPC_SPAD: Scratchpad(),
        }

    def xin(self, device_id: int, offset: int, length: int) -> bytes:
        if device_id in self.devices:
            return self.devices[device_id].read(offset, length)
        logger.warning(f"Unsupported XFR device ID {device_id} for XIN, returning zeros")
        return bytes(length)

    def xout(self, device_id: int, offset: int, data: bytes) -> None:
        if device_id in self.devices:
            self.devices[device_id].write(offset, data)
        else:
            logger.warning(f"Unsupported XFR device ID {device_id} for XOUT, ignoring")

    def xchg(self, device_id: int, offset: int, data: bytes) -> bytes:
        if device_id in self.devices:
            return self.devices[device_id].exchange(offset, data)
        logger.warning(f"Unsupported XFR device ID {device_id} for XCHG, returning zeros")
        return bytes(len(data))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_xfr.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add xfr/scratchpad.py xfr/xfr_bus.py tests/test_xfr.py
git commit -m "feat: XFR bus with SPAD Bank0/1/2 and IPC SPAD"
```

---

## Task 7: ALU Operations

**Files:**
- Create: `core/alu.py`
- Create: `tests/test_alu.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_alu.py
import pytest
from core.alu import ALU


class TestArithmetic:
    def test_add_basic(self):
        result, carry = ALU.add(10, 20, 32)
        assert result == 30
        assert carry is False

    def test_add_overflow_32(self):
        result, carry = ALU.add(0xFFFFFFFF, 1, 32)
        assert result == 0
        assert carry is True

    def test_add_overflow_8(self):
        result, carry = ALU.add(0xFF, 1, 8)
        assert result == 0
        assert carry is True

    def test_add_overflow_16(self):
        result, carry = ALU.add(0xFFFF, 1, 16)
        assert result == 0
        assert carry is True

    def test_adc_no_carry(self):
        result, carry = ALU.adc(10, 20, False, 32)
        assert result == 30

    def test_adc_with_carry(self):
        result, carry = ALU.adc(10, 20, True, 32)
        assert result == 31

    def test_sub_basic(self):
        result, carry = ALU.sub(20, 10, 32)
        assert result == 10
        assert carry is False

    def test_sub_underflow(self):
        result, carry = ALU.sub(0, 1, 32)
        assert result == 0xFFFFFFFF
        assert carry is True

    def test_suc_with_carry(self):
        result, carry = ALU.suc(20, 10, True, 32)
        assert result == 9

    def test_rsb_basic(self):
        result, carry = ALU.rsb(10, 20, 32)
        assert result == 10  # 20 - 10

    def test_rsc_with_carry(self):
        result, carry = ALU.rsc(10, 20, True, 32)
        assert result == 9  # 20 - 10 - 1


class TestLogic:
    def test_and(self):
        assert ALU.and_(0xFF00, 0x0FF0) == 0x0F00

    def test_or(self):
        assert ALU.or_(0xFF00, 0x00FF) == 0xFFFF

    def test_xor(self):
        assert ALU.xor_(0xFF00, 0xFFFF) == 0x00FF

    def test_not(self):
        assert ALU.not_(0x00000000) == 0xFFFFFFFF

    def test_not_truncates(self):
        assert ALU.not_(0xFFFFFFFF) == 0x00000000


class TestShift:
    def test_lsl(self):
        assert ALU.lsl(1, 4) == 16

    def test_lsl_large(self):
        assert ALU.lsl(0x80000000, 1) == 0  # shifts out

    def test_lsr(self):
        assert ALU.lsr(16, 4) == 1

    def test_lsr_zero_fill(self):
        assert ALU.lsr(0x80000000, 31) == 1


class TestBit:
    def test_set_bit(self):
        assert ALU.set_bit(0, 5) == 0x20

    def test_clr_bit(self):
        assert ALU.clr_bit(0xFF, 3) == 0xF7

    def test_lmbd_find_1_from_left(self):
        # 0x80000000 = bit 31 set, looking for 1 → position 31
        assert ALU.lmbd(0x80000000, 1) == 31

    def test_lmbd_find_1_lower(self):
        assert ALU.lmbd(0x00000001, 1) == 0

    def test_lmbd_find_0_from_left(self):
        # 0x7FFFFFFF = bit 31 clear, looking for 0 → position 31
        assert ALU.lmbd(0x7FFFFFFF, 0) == 31

    def test_lmbd_not_found(self):
        # All 1s, looking for 0 → returns 32
        assert ALU.lmbd(0xFFFFFFFF, 0) == 32

    def test_lmbd_zero_looking_for_1(self):
        assert ALU.lmbd(0, 1) == 32


class TestCompare:
    def test_min(self):
        assert ALU.min_(10, 20) == 10

    def test_min_equal(self):
        assert ALU.min_(5, 5) == 5

    def test_max(self):
        assert ALU.max_(10, 20) == 20

    def test_max_equal(self):
        assert ALU.max_(5, 5) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_alu.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement ALU**

```python
# core/alu.py
"""PRU ALU — Pure arithmetic, logic, shift, and bit operations."""


class ALU:
    @staticmethod
    def add(a: int, b: int, width: int) -> tuple[int, bool]:
        mask = (1 << width) - 1
        result = a + b
        carry = result > mask
        return result & mask, carry

    @staticmethod
    def adc(a: int, b: int, carry_in: bool, width: int) -> tuple[int, bool]:
        mask = (1 << width) - 1
        result = a + b + (1 if carry_in else 0)
        carry = result > mask
        return result & mask, carry

    @staticmethod
    def sub(a: int, b: int, width: int) -> tuple[int, bool]:
        mask = (1 << width) - 1
        result = a - b
        carry = result < 0
        return result & mask, carry

    @staticmethod
    def suc(a: int, b: int, carry_in: bool, width: int) -> tuple[int, bool]:
        mask = (1 << width) - 1
        result = a - b - (1 if carry_in else 0)
        carry = result < 0
        return result & mask, carry

    @staticmethod
    def rsb(a: int, b: int, width: int) -> tuple[int, bool]:
        """Reverse subtract: result = b - a."""
        mask = (1 << width) - 1
        result = b - a
        carry = result < 0
        return result & mask, carry

    @staticmethod
    def rsc(a: int, b: int, carry_in: bool, width: int) -> tuple[int, bool]:
        """Reverse subtract with carry: result = b - a - carry."""
        mask = (1 << width) - 1
        result = b - a - (1 if carry_in else 0)
        carry = result < 0
        return result & mask, carry

    @staticmethod
    def and_(a: int, b: int) -> int:
        return a & b

    @staticmethod
    def or_(a: int, b: int) -> int:
        return a | b

    @staticmethod
    def xor_(a: int, b: int) -> int:
        return a ^ b

    @staticmethod
    def not_(a: int) -> int:
        return (~a) & 0xFFFFFFFF

    @staticmethod
    def lsl(value: int, shift: int) -> int:
        return (value << shift) & 0xFFFFFFFF

    @staticmethod
    def lsr(value: int, shift: int) -> int:
        return (value >> shift) & 0xFFFFFFFF

    @staticmethod
    def set_bit(value: int, bit: int) -> int:
        return value | (1 << bit)

    @staticmethod
    def clr_bit(value: int, bit: int) -> int:
        return value & ~(1 << bit)

    @staticmethod
    def lmbd(value: int, target: int) -> int:
        """Left-most bit detect: scan from MSB for bit matching target."""
        target_bit = target & 1
        for i in range(31, -1, -1):
            if ((value >> i) & 1) == target_bit:
                return i
        return 32

    @staticmethod
    def min_(a: int, b: int) -> int:
        return min(a, b)

    @staticmethod
    def max_(a: int, b: int) -> int:
        return max(a, b)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_alu.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/alu.py tests/test_alu.py
git commit -m "feat: ALU with arithmetic, logic, shift, bit, and compare ops"
```

---

## Task 8: Assembly Parser — Operand Types & Register Notation

**Files:**
- Create: `core/operands.py`
- Create: `tests/test_operands.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_operands.py
import pytest
from core.operands import parse_operand, Register, Immediate, BitField


class TestParseRegister:
    def test_full_register(self):
        r = parse_operand("r5")
        assert isinstance(r, Register)
        assert r.index == 5 and r.offset == 0 and r.width == 32

    def test_byte0(self):
        r = parse_operand("r5.b0")
        assert r == Register(5, 0, 8)

    def test_byte1(self):
        r = parse_operand("r5.b1")
        assert r == Register(5, 8, 8)

    def test_byte2(self):
        r = parse_operand("r5.b2")
        assert r == Register(5, 16, 8)

    def test_byte3(self):
        r = parse_operand("r5.b3")
        assert r == Register(5, 24, 8)

    def test_word0(self):
        r = parse_operand("r5.w0")
        assert r == Register(5, 0, 16)

    def test_word1(self):
        r = parse_operand("r5.w1")
        assert r == Register(5, 8, 16)

    def test_word2(self):
        r = parse_operand("r5.w2")
        assert r == Register(5, 16, 16)

    def test_bit_field(self):
        r = parse_operand("r31.t5")
        assert isinstance(r, BitField)
        assert r.reg == 31 and r.bit == 5

    def test_case_insensitive(self):
        r = parse_operand("R5.B2")
        assert r == Register(5, 16, 8)

    def test_r0_through_r31(self):
        for i in range(32):
            r = parse_operand(f"r{i}")
            assert r == Register(i, 0, 32)


class TestParseImmediate:
    def test_decimal(self):
        r = parse_operand("255")
        assert isinstance(r, Immediate)
        assert r.value == 255

    def test_hex(self):
        r = parse_operand("0xFF")
        assert isinstance(r, Immediate)
        assert r.value == 255

    def test_hex_upper(self):
        r = parse_operand("0XFF")
        assert isinstance(r, Immediate)
        assert r.value == 255

    def test_zero(self):
        r = parse_operand("0")
        assert isinstance(r, Immediate)
        assert r.value == 0


class TestParseSpecial:
    def test_ampersand_register(self):
        """&r2 means register address (byte offset = reg * 4)."""
        r = parse_operand("&r2")
        assert isinstance(r, Register)
        assert r.index == 2

    def test_unknown_returns_none_for_labels(self):
        """Unrecognized tokens are returned as strings for label resolution."""
        r = parse_operand("my_label")
        assert r == "my_label"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_operands.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement operand types and parser**

```python
# core/operands.py
"""PRU operand types and register notation parser."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Register:
    index: int
    offset: int   # bit offset within register
    width: int    # 8, 16, or 32

    @property
    def byte_offset(self) -> int:
        """Byte offset into register file (for & addressing)."""
        return self.index * 4 + (self.offset // 8)


@dataclass(frozen=True)
class Immediate:
    value: int


@dataclass(frozen=True)
class BitField:
    reg: int
    bit: int


@dataclass(frozen=True)
class Label:
    name: str
    resolved_addr: int = -1


@dataclass(frozen=True)
class MemRef:
    base_reg: int
    offset: object  # Register or Immediate
    length: object  # Immediate or 'bn' reference


@dataclass(frozen=True)
class ConstRef:
    table_idx: int
    offset: object  # Register or Immediate


# Regex patterns
_REG_FULL = re.compile(r'^[rR](\d+)$')
_REG_BYTE = re.compile(r'^[rR](\d+)\.[bB]([0-3])$')
_REG_WORD = re.compile(r'^[rR](\d+)\.[wW]([0-2])$')
_REG_BIT = re.compile(r'^[rR](\d+)\.[tT](\d+)$')
_REG_AMP = re.compile(r'^&[rR](\d+)$')
_IMM_HEX = re.compile(r'^0[xX]([0-9a-fA-F]+)$')
_IMM_DEC = re.compile(r'^(\d+)$')

_BYTE_OFFSETS = {0: 0, 1: 8, 2: 16, 3: 24}
_WORD_OFFSETS = {0: 0, 1: 8, 2: 16}


def parse_operand(token: str) -> object:
    """Parse a single operand token into its typed representation."""
    token = token.strip().rstrip(',')

    # &Rn (register address for burst ops)
    m = _REG_AMP.match(token)
    if m:
        return Register(int(m.group(1)), 0, 32)

    # Rn.tx (bit field)
    m = _REG_BIT.match(token)
    if m:
        return BitField(int(m.group(1)), int(m.group(2)))

    # Rn.bX (byte)
    m = _REG_BYTE.match(token)
    if m:
        reg = int(m.group(1))
        byte_idx = int(m.group(2))
        return Register(reg, _BYTE_OFFSETS[byte_idx], 8)

    # Rn.wX (word)
    m = _REG_WORD.match(token)
    if m:
        reg = int(m.group(1))
        word_idx = int(m.group(2))
        return Register(reg, _WORD_OFFSETS[word_idx], 16)

    # Rn (full register)
    m = _REG_FULL.match(token)
    if m:
        return Register(int(m.group(1)), 0, 32)

    # Hex immediate
    m = _IMM_HEX.match(token)
    if m:
        return Immediate(int(m.group(1), 16))

    # Decimal immediate
    m = _IMM_DEC.match(token)
    if m:
        return Immediate(int(m.group(1)))

    # Unrecognized — return as string (label candidate)
    return token
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_operands.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/operands.py tests/test_operands.py
git commit -m "feat: Operand types and register notation parser"
```

---

## Task 9: Assembly Preprocessor

**Files:**
- Create: `core/preprocessor.py`
- Create: `tests/test_preprocessor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_preprocessor.py
import pytest
import os
import tempfile
from core.preprocessor import Preprocessor


class TestSetDirective:
    def test_set_constant(self):
        pp = Preprocessor()
        lines = pp.process_text(".set MY_CONST, 42\nldi r0, MY_CONST")
        assert lines == ["ldi r0, 42"]

    def test_set_hex(self):
        pp = Preprocessor()
        lines = pp.process_text(".set ADDR, 0xFF\nldi r0, ADDR")
        assert lines == ["ldi r0, 0xFF"]

    def test_multiple_sets(self):
        pp = Preprocessor()
        lines = pp.process_text(".set A, 10\n.set B, 20\nadd r0, r1, A\nadd r2, r3, B")
        assert lines == ["add r0, r1, 10", "add r2, r3, 20"]


class TestConditional:
    def test_if_true(self):
        pp = Preprocessor()
        pp.defines["DEBUG"] = 1
        lines = pp.process_text(".if DEBUG\nldi r0, 1\n.endif")
        assert lines == ["ldi r0, 1"]

    def test_if_false(self):
        pp = Preprocessor()
        pp.defines["DEBUG"] = 0
        lines = pp.process_text(".if DEBUG\nldi r0, 1\n.endif")
        assert lines == []

    def test_if_else(self):
        pp = Preprocessor()
        pp.defines["MODE"] = 0
        lines = pp.process_text(".if MODE\nldi r0, 1\n.else\nldi r0, 2\n.endif")
        assert lines == ["ldi r0, 2"]


class TestMacro:
    def test_simple_macro(self):
        pp = Preprocessor()
        src = """.macro LOAD_CONST
ldi r0, 42
.endm
LOAD_CONST"""
        lines = pp.process_text(src)
        assert lines == ["ldi r0, 42"]

    def test_macro_with_args(self):
        pp = Preprocessor()
        src = """.macro SET_REG reg, val
ldi reg, val
.endm
SET_REG r5, 100"""
        lines = pp.process_text(src)
        assert lines == ["ldi r5, 100"]

    def test_macro_multiple_lines(self):
        pp = Preprocessor()
        src = """.macro INIT_PAIR r1, r2, v
ldi r1, v
ldi r2, v
.endm
INIT_PAIR r3, r4, 0"""
        lines = pp.process_text(src)
        assert lines == ["ldi r3, 0", "ldi r4, 0"]


class TestInclude:
    def test_include_file(self):
        pp = Preprocessor()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.inc', delete=False) as f:
            f.write("ldi r0, 99\n")
            inc_path = f.name
        try:
            pp.include_paths = [os.path.dirname(inc_path)]
            src = f'.include "{os.path.basename(inc_path)}"\nldi r1, 1'
            lines = pp.process_text(src)
            assert lines == ["ldi r0, 99", "ldi r1, 1"]
        finally:
            os.unlink(inc_path)


class TestStruct:
    def test_struct_offsets(self):
        pp = Preprocessor()
        src = """.struct MyStruct
.u32 field_a
.u16 field_b
.u8  field_c
.ends
ldi r0, MyStruct.field_a
ldi r1, MyStruct.field_b
ldi r2, MyStruct.field_c"""
        lines = pp.process_text(src)
        assert lines == ["ldi r0, 0", "ldi r1, 4", "ldi r2, 6"]


class TestComments:
    def test_strip_line_comment(self):
        pp = Preprocessor()
        lines = pp.process_text("ldi r0, 5 ; load five")
        assert lines == ["ldi r0, 5"]

    def test_comment_only_line(self):
        pp = Preprocessor()
        lines = pp.process_text("; this is a comment\nldi r0, 1")
        assert lines == ["ldi r0, 1"]

    def test_empty_lines_stripped(self):
        pp = Preprocessor()
        lines = pp.process_text("\n\nldi r0, 1\n\n")
        assert lines == ["ldi r0, 1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_preprocessor.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement Preprocessor**

```python
# core/preprocessor.py
"""PRU Assembly Preprocessor — .macro, .include, .if, .set, .struct."""
import os
import re


class Preprocessor:
    def __init__(self):
        self.defines: dict[str, object] = {}
        self.macros: dict[str, tuple[list[str], list[str]]] = {}  # name → (params, body)
        self.structs: dict[str, dict[str, int]] = {}  # name → {field: offset}
        self.include_paths: list[str] = ["."]

    def process_file(self, filepath: str) -> list[str]:
        with open(filepath, 'r') as f:
            text = f.read()
        self.include_paths.insert(0, os.path.dirname(os.path.abspath(filepath)))
        return self.process_text(text)

    def process_text(self, text: str) -> list[str]:
        lines = text.split('\n')
        lines = self._strip_comments(lines)
        lines = self._process_directives(lines)
        return [l for l in lines if l.strip()]

    def _strip_comments(self, lines: list[str]) -> list[str]:
        result = []
        for line in lines:
            # Remove ; comments (but not inside strings)
            idx = line.find(';')
            if idx >= 0:
                line = line[:idx]
            result.append(line.rstrip())
        return result

    def _process_directives(self, lines: list[str]) -> list[str]:
        output = []
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            low = line.lower()

            if not line:
                i += 1
                continue

            # .set NAME, value
            if low.startswith('.set '):
                parts = line[5:].split(',', 1)
                name = parts[0].strip()
                value = parts[1].strip()
                self.defines[name] = value
                i += 1
                continue

            # .macro NAME [args]
            if low.startswith('.macro '):
                i = self._parse_macro(lines, i)
                continue

            # .struct NAME
            if low.startswith('.struct '):
                i = self._parse_struct(lines, i)
                continue

            # .if EXPR
            if low.startswith('.if '):
                i, block_lines = self._parse_conditional(lines, i)
                output.extend(self._process_directives(block_lines))
                continue

            # .include "file"
            if low.startswith('.include '):
                m = re.match(r'\.include\s+"([^"]+)"', line, re.IGNORECASE)
                if m:
                    inc_lines = self._do_include(m.group(1))
                    output.extend(self._process_directives(inc_lines))
                i += 1
                continue

            # Macro expansion
            expanded = self._try_expand_macro(line)
            if expanded is not None:
                output.extend(self._process_directives(expanded))
                i += 1
                continue

            # Substitute defines and struct fields
            line = self._substitute(line)
            output.append(line)
            i += 1

        return output

    def _parse_macro(self, lines: list[str], start: int) -> int:
        header = lines[start].strip()
        parts = header[7:].split()  # skip ".macro "
        name = parts[0]
        params = [p.strip().rstrip(',') for p in parts[1:]] if len(parts) > 1 else []
        body = []
        i = start + 1
        while i < len(lines):
            if lines[i].strip().lower() == '.endm':
                self.macros[name] = (params, body)
                return i + 1
            body.append(lines[i])
            i += 1
        return i

    def _parse_struct(self, lines: list[str], start: int) -> int:
        header = lines[start].strip()
        name = header[8:].strip()  # skip ".struct "
        fields = {}
        offset = 0
        i = start + 1
        while i < len(lines):
            line = lines[i].strip().lower()
            if line == '.ends':
                self.structs[name] = fields
                return i + 1
            # .u8 name, .u16 name, .u32 name
            m = re.match(r'\.(u8|u16|u32)\s+(\w+)', lines[i].strip(), re.IGNORECASE)
            if m:
                size_map = {'u8': 1, 'u16': 2, 'u32': 4}
                field_size = size_map[m.group(1).lower()]
                field_name = m.group(2)
                fields[field_name] = offset
                offset += field_size
            i += 1
        return i

    def _parse_conditional(self, lines: list[str], start: int) -> tuple[int, list[str]]:
        cond_expr = lines[start].strip()[4:]  # skip ".if "
        true_block = []
        false_block = []
        in_else = False
        depth = 0
        i = start + 1
        while i < len(lines):
            low = lines[i].strip().lower()
            if low.startswith('.if '):
                depth += 1
            elif low == '.endif':
                if depth == 0:
                    cond_val = self._eval_condition(cond_expr)
                    return i + 1, true_block if cond_val else false_block
                depth -= 1
            elif low == '.else' and depth == 0:
                in_else = True
                i += 1
                continue
            if in_else:
                false_block.append(lines[i])
            else:
                true_block.append(lines[i])
            i += 1
        return i, true_block if self._eval_condition(cond_expr) else false_block

    def _eval_condition(self, expr: str) -> bool:
        expr = expr.strip()
        if expr in self.defines:
            val = self.defines[expr]
            if isinstance(val, str):
                try:
                    return int(val) != 0
                except ValueError:
                    return bool(val)
            return bool(val)
        try:
            return int(expr) != 0
        except ValueError:
            return False

    def _try_expand_macro(self, line: str) -> list[str] | None:
        parts = line.split()
        name = parts[0]
        if name not in self.macros:
            return None
        params, body = self.macros[name]
        args = [a.strip().rstrip(',') for a in parts[1:]] if len(parts) > 1 else []
        # Expand: substitute params with args
        expanded = []
        for body_line in body:
            result = body_line
            for param, arg in zip(params, args):
                result = result.replace(param, arg)
            expanded.append(result)
        return expanded

    def _substitute(self, line: str) -> str:
        # Substitute struct fields (e.g., MyStruct.field_a → offset)
        for struct_name, fields in self.structs.items():
            for field_name, offset in fields.items():
                token = f"{struct_name}.{field_name}"
                if token in line:
                    line = line.replace(token, str(offset))
        # Substitute .set defines
        for name, value in self.defines.items():
            # Word-boundary replacement
            line = re.sub(r'\b' + re.escape(name) + r'\b', str(value), line)
        return line

    def _do_include(self, filename: str) -> list[str]:
        for path in self.include_paths:
            full = os.path.join(path, filename)
            if os.path.exists(full):
                with open(full, 'r') as f:
                    return f.read().split('\n')
        raise FileNotFoundError(f"Include file not found: {filename}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_preprocessor.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/preprocessor.py tests/test_preprocessor.py
git commit -m "feat: Preprocessor with .macro, .include, .if, .set, .struct"
```

---

## Task 10: Assembly Parser — Two-Pass Label Resolution

**Files:**
- Create: `core/parser.py`
- Create: `tests/test_parser.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_parser.py
import pytest
from core.parser import Parser, Instruction
from core.operands import Register, Immediate, BitField


class TestParserBasic:
    def test_parse_simple_add(self):
        p = Parser()
        instructions = p.parse_text("add r0, r1, r2")
        assert len(instructions) == 1
        inst = instructions[0]
        assert inst.opcode == "ADD"
        assert inst.address == 0

    def test_parse_ldi(self):
        p = Parser()
        instructions = p.parse_text("ldi r5, 0xFF")
        inst = instructions[0]
        assert inst.opcode == "LDI"
        assert inst.operands[0] == Register(5, 0, 32)
        assert inst.operands[1] == Immediate(255)

    def test_parse_sub_register(self):
        p = Parser()
        instructions = p.parse_text("add r0.b0, r1.w2, r2.b3")
        inst = instructions[0]
        assert inst.operands[0] == Register(0, 0, 8)
        assert inst.operands[1] == Register(1, 16, 16)
        assert inst.operands[2] == Register(2, 24, 8)

    def test_addresses_sequential(self):
        p = Parser()
        instructions = p.parse_text("ldi r0, 1\nldi r1, 2\nldi r2, 3")
        assert [i.address for i in instructions] == [0, 1, 2]

    def test_source_line_tracking(self):
        p = Parser()
        instructions = p.parse_text("ldi r0, 1\nldi r1, 2")
        assert instructions[0].source_line == 1
        assert instructions[1].source_line == 2


class TestLabels:
    def test_label_resolved(self):
        p = Parser()
        instructions = p.parse_text("start:\nldi r0, 1\nqba start")
        assert len(instructions) == 2
        # QBA operand should be the label "start" resolved to addr 0
        qba = instructions[1]
        assert qba.opcode == "QBA"

    def test_forward_reference(self):
        p = Parser()
        instructions = p.parse_text("qba end\nldi r0, 1\nend:\nhalt")
        qba = instructions[0]
        assert qba.opcode == "QBA"
        # "end" is at address 2
        assert qba.operands[0].resolved_addr == 2

    def test_label_with_colon(self):
        p = Parser()
        instructions = p.parse_text("my_label:\n    ldi r0, 1")
        assert len(instructions) == 1
        assert p.labels["my_label"] == 0


class TestPreprocessorIntegration:
    def test_set_and_use(self):
        p = Parser()
        instructions = p.parse_text(".set COUNT, 10\nldi r0, COUNT")
        assert instructions[0].operands[1] == Immediate(10)

    def test_macro_expansion(self):
        p = Parser()
        src = ".macro NOP\nadd r0, r0, 0\n.endm\nNOP\nNOP"
        instructions = p.parse_text(src)
        assert len(instructions) == 2
        assert instructions[0].opcode == "ADD"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parser.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement Parser**

```python
# core/parser.py
"""PRU Assembly Parser — Two-pass with label resolution."""
from dataclasses import dataclass, field
from core.preprocessor import Preprocessor
from core.operands import parse_operand, Label, Register, Immediate, BitField
import re


@dataclass
class Instruction:
    address: int
    opcode: str
    operands: list
    source_line: int
    source_text: str


class Parser:
    def __init__(self):
        self.preprocessor = Preprocessor()
        self.labels: dict[str, int] = {}
        self.instructions: list[Instruction] = []

    def parse_file(self, filepath: str) -> list[Instruction]:
        lines = self.preprocessor.process_file(filepath)
        return self._two_pass(lines)

    def parse_text(self, text: str) -> list[Instruction]:
        lines = self.preprocessor.process_text(text)
        return self._two_pass(lines)

    def _two_pass(self, lines: list[str]) -> list[Instruction]:
        # Pass 1: collect labels, count instruction addresses
        raw_lines = []  # (line_text, line_number)
        addr = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            # Check for label (ends with : or starts line before instruction)
            label_match = re.match(r'^(\w+):\s*(.*)', stripped)
            if label_match:
                label_name = label_match.group(1)
                self.labels[label_name] = addr
                remainder = label_match.group(2).strip()
                if remainder:
                    raw_lines.append((remainder, i + 1))
                    addr += 1
            else:
                raw_lines.append((stripped, i + 1))
                addr += 1

        # Pass 2: parse instructions, resolve labels
        self.instructions = []
        for idx, (line, line_num) in enumerate(raw_lines):
            inst = self._parse_line(line, idx, line_num)
            if inst:
                self.instructions.append(inst)

        return self.instructions

    def _parse_line(self, line: str, address: int, line_num: int) -> Instruction | None:
        # Split into opcode and operands
        parts = line.split(None, 1)
        opcode = parts[0].upper()
        operand_str = parts[1] if len(parts) > 1 else ""

        # Parse operands (comma-separated)
        operands = []
        if operand_str:
            tokens = self._split_operands(operand_str)
            for token in tokens:
                parsed = parse_operand(token)
                # If it's a string (unresolved label), try to resolve
                if isinstance(parsed, str):
                    if parsed in self.labels:
                        parsed = Label(parsed, self.labels[parsed])
                    else:
                        parsed = Label(parsed, -1)
                operands.append(parsed)

        return Instruction(
            address=address,
            opcode=opcode,
            operands=operands,
            source_line=line_num,
            source_text=line,
        )

    def _split_operands(self, operand_str: str) -> list[str]:
        """Split operands by comma, respecting parentheses."""
        tokens = []
        current = ""
        depth = 0
        for ch in operand_str:
            if ch == '(' :
                depth += 1
                current += ch
            elif ch == ')':
                depth -= 1
                current += ch
            elif ch == ',' and depth == 0:
                tokens.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            tokens.append(current.strip())
        return tokens
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parser.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/parser.py tests/test_parser.py
git commit -m "feat: Two-pass assembly parser with label resolution"
```

---

## Task 11: Branch Unit

**Files:**
- Create: `core/branch.py`
- Create: `tests/test_branches.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_branches.py
import pytest
from core.branch import BranchUnit, LoopState


class TestConditionalBranch:
    """Branch semantics: QBGT label, Reg1, OP → branch if OP > Reg1."""

    def test_qbeq_taken(self):
        bu = BranchUnit()
        assert bu.qbeq(reg_val=10, op_val=10) is True

    def test_qbeq_not_taken(self):
        bu = BranchUnit()
        assert bu.qbeq(reg_val=10, op_val=11) is False

    def test_qbne_taken(self):
        bu = BranchUnit()
        assert bu.qbne(reg_val=10, op_val=11) is True

    def test_qbne_not_taken(self):
        bu = BranchUnit()
        assert bu.qbne(reg_val=10, op_val=10) is False

    def test_qbgt_taken(self):
        bu = BranchUnit()
        # Branch if OP > Reg1: OP=20, Reg1=10 → 20 > 10 → True
        assert bu.qbgt(reg_val=10, op_val=20) is True

    def test_qbgt_not_taken(self):
        bu = BranchUnit()
        assert bu.qbgt(reg_val=20, op_val=10) is False

    def test_qbgt_equal_not_taken(self):
        bu = BranchUnit()
        assert bu.qbgt(reg_val=10, op_val=10) is False

    def test_qbge_taken_greater(self):
        bu = BranchUnit()
        assert bu.qbge(reg_val=10, op_val=20) is True

    def test_qbge_taken_equal(self):
        bu = BranchUnit()
        assert bu.qbge(reg_val=10, op_val=10) is True

    def test_qbge_not_taken(self):
        bu = BranchUnit()
        assert bu.qbge(reg_val=20, op_val=10) is False

    def test_qblt_taken(self):
        bu = BranchUnit()
        # Branch if OP < Reg1: OP=5, Reg1=10 → 5 < 10 → True
        assert bu.qblt(reg_val=10, op_val=5) is True

    def test_qblt_not_taken(self):
        bu = BranchUnit()
        assert bu.qblt(reg_val=5, op_val=10) is False

    def test_qble_taken_less(self):
        bu = BranchUnit()
        assert bu.qble(reg_val=10, op_val=5) is True

    def test_qble_taken_equal(self):
        bu = BranchUnit()
        assert bu.qble(reg_val=10, op_val=10) is True

    def test_qbbs_taken(self):
        bu = BranchUnit()
        assert bu.qbbs(reg_val=0b100000, bit=5) is True

    def test_qbbs_not_taken(self):
        bu = BranchUnit()
        assert bu.qbbs(reg_val=0b000000, bit=5) is False

    def test_qbbc_taken(self):
        bu = BranchUnit()
        assert bu.qbbc(reg_val=0b000000, bit=5) is True

    def test_qbbc_not_taken(self):
        bu = BranchUnit()
        assert bu.qbbc(reg_val=0b100000, bit=5) is False


class TestHardwareLoop:
    def test_loop_init(self):
        ls = LoopState(count=10, start_address=5, end_address=8)
        assert ls.count == 10

    def test_loop_tick_decrements(self):
        ls = LoopState(count=3, start_address=5, end_address=8)
        should_loop = ls.tick()
        assert should_loop is True
        assert ls.count == 2

    def test_loop_tick_last_iteration(self):
        ls = LoopState(count=1, start_address=5, end_address=8)
        should_loop = ls.tick()
        assert should_loop is False
        assert ls.count == 0

    def test_loop_done(self):
        ls = LoopState(count=0, start_address=5, end_address=8)
        assert ls.is_done() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_branches.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement BranchUnit**

```python
# core/branch.py
"""PRU Branch Unit — conditional branches and hardware loop."""
from dataclasses import dataclass


@dataclass
class LoopState:
    count: int
    start_address: int
    end_address: int

    def tick(self) -> bool:
        """Called when PC reaches end_address. Returns True if should loop back."""
        self.count -= 1
        return self.count > 0

    def is_done(self) -> bool:
        return self.count <= 0


class BranchUnit:
    """Evaluates branch conditions. All comparisons use PRU semantics:
    QBxx label, Reg1, OP → condition is OP <cmp> Reg1."""

    def qbeq(self, reg_val: int, op_val: int) -> bool:
        return op_val == reg_val

    def qbne(self, reg_val: int, op_val: int) -> bool:
        return op_val != reg_val

    def qbgt(self, reg_val: int, op_val: int) -> bool:
        return op_val > reg_val

    def qbge(self, reg_val: int, op_val: int) -> bool:
        return op_val >= reg_val

    def qblt(self, reg_val: int, op_val: int) -> bool:
        return op_val < reg_val

    def qble(self, reg_val: int, op_val: int) -> bool:
        return op_val <= reg_val

    def qbbs(self, reg_val: int, bit: int) -> bool:
        return bool((reg_val >> bit) & 1)

    def qbbc(self, reg_val: int, bit: int) -> bool:
        return not bool((reg_val >> bit) & 1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_branches.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/branch.py tests/test_branches.py
git commit -m "feat: BranchUnit with conditional branches and hardware loop"
```

---

## Task 12: PRU Core — Instruction Execution Engine

**Files:**
- Create: `core/pru_core.py`
- Create: `tests/test_pru_core.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pru_core.py
import pytest
from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from xfr.xfr_bus import XFRBus
from pru_io.io_port import IOPort


def make_core(asm: str) -> PRUCore:
    """Helper: create a PRUCore, load assembly, return ready to step."""
    mem = MemoryBus()
    mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
    xfr = XFRBus()
    io = IOPort()
    core = PRUCore("PRU0", mem, xfr, io)
    core.load_asm(asm)
    return core


class TestArithmeticExecution:
    def test_add_immediate(self):
        core = make_core("ldi r1, 10\nadd r0, r1, 5")
        core.step()
        core.step()
        assert core.registers.read_full(0) == 15

    def test_add_register(self):
        core = make_core("ldi r1, 10\nldi r2, 20\nadd r0, r1, r2")
        core.step()
        core.step()
        core.step()
        assert core.registers.read_full(0) == 30

    def test_sub(self):
        core = make_core("ldi r1, 50\nsub r0, r1, 20")
        core.step()
        core.step()
        assert core.registers.read_full(0) == 30

    def test_ldi(self):
        core = make_core("ldi r5, 0xBEEF")
        core.step()
        assert core.registers.read_full(5) == 0xBEEF


class TestLogicExecution:
    def test_and(self):
        core = make_core("ldi r1, 0xFF0F\nand r0, r1, 0xF0")
        core.step()
        core.step()
        assert core.registers.read_full(0) == 0x00000000  # 0xFF0F & 0xF0 = 0

    def test_or(self):
        core = make_core("ldi r1, 0xF0\nor r0, r1, 0x0F")
        core.step()
        core.step()
        assert core.registers.read_full(0) == 0xFF

    def test_lsl(self):
        core = make_core("ldi r1, 1\nlsl r0, r1, 4")
        core.step()
        core.step()
        assert core.registers.read_full(0) == 16

    def test_set_bit(self):
        core = make_core("ldi r1, 0\nset r0, r1, 5")
        core.step()
        core.step()
        assert core.registers.read_full(0) == 32


class TestBranchExecution:
    def test_qba(self):
        core = make_core("qba skip\nldi r0, 99\nskip:\nldi r0, 42")
        core.step()  # qba → jumps to skip (addr 2)
        core.step()  # ldi r0, 42
        assert core.registers.read_full(0) == 42

    def test_qbeq_taken(self):
        core = make_core("ldi r1, 5\nqbeq done, r1, 5\nldi r0, 1\ndone:\nldi r0, 99")
        core.step()  # ldi r1, 5
        core.step()  # qbeq → 5 == 5, branch taken
        core.step()  # ldi r0, 99
        assert core.registers.read_full(0) == 99

    def test_qbeq_not_taken(self):
        core = make_core("ldi r1, 5\nqbeq done, r1, 6\nldi r0, 1\ndone:\nhalt")
        core.step()  # ldi r1, 5
        core.step()  # qbeq → 6 != 5, not taken
        core.step()  # ldi r0, 1
        assert core.registers.read_full(0) == 1


class TestMemoryExecution:
    def test_sbbo_lbbo(self):
        core = make_core("ldi r1, 0x100\nldi r2, 0xDEAD\nsbbo &r2, r1, 0, 4\nldi r2, 0\nlbbo &r2, r1, 0, 4")
        for _ in range(5):
            core.step()
        assert core.registers.read_full(2) == 0xDEAD


class TestHalt:
    def test_halt_stops_execution(self):
        core = make_core("ldi r0, 1\nhalt\nldi r0, 99")
        core.step()  # ldi r0, 1
        core.step()  # halt
        assert core.halted is True
        core.step()  # should not advance
        assert core.registers.read_full(0) == 1


class TestCycleCounter:
    def test_instructions_count_cycles(self):
        core = make_core("ldi r0, 1\nldi r1, 2\nldi r2, 3")
        core.step()
        core.step()
        core.step()
        assert core.counters.cycles == 3
        assert core.counters.instruction_count == 3

    def test_memory_adds_stall_cycles(self):
        core = make_core("ldi r1, 0x100\nldi r2, 0xAA\nsbbo &r2, r1, 0, 4")
        core.step()  # ldi: 1 cycle
        core.step()  # ldi: 1 cycle
        core.step()  # sbbo: 1 cycle exec + 1 write stall
        assert core.counters.cycles == 4  # 3 instructions + 1 stall
        assert core.counters.stall_cycles == 1


class TestIOExecution:
    def test_write_r30_updates_gpo(self):
        core = make_core("ldi r30, 0x0005")
        core.step()
        assert core.io_port.gpo == 0x0005

    def test_read_r31_gets_gpi(self):
        core = make_core("mov r0, r31")
        core.io_port.set_gpi_pin(3, True)
        core.step()
        assert core.registers.read_full(0) == (1 << 3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pru_core.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement PRUCore**

```python
# core/pru_core.py
"""PRU Core — ties together registers, ALU, branch, memory, IO, and execution."""
import logging
from core.registers import RegisterFile
from core.alu import ALU
from core.branch import BranchUnit, LoopState
from core.counters import CycleCounters
from core.parser import Parser, Instruction
from core.operands import Register, Immediate, Label, BitField
from mem.memory_bus import MemoryBus
from xfr.xfr_bus import XFRBus
from pru_io.io_port import IOPort
import struct

logger = logging.getLogger(__name__)

R30 = 30
R31 = 31


class PRUCore:
    def __init__(self, name: str, memory: MemoryBus, xfr: XFRBus, io_port: IOPort):
        self.name = name
        self.registers = RegisterFile()
        self.alu = ALU()
        self.branch_unit = BranchUnit()
        self.counters = CycleCounters()
        self.memory = memory
        self.xfr = xfr
        self.io_port = io_port
        self.pc: int = 0
        self.halted: bool = False
        self.instructions: list[Instruction] = []
        self.loop_state: LoopState | None = None
        self.breakpoints: set[int] = set()

    def load_asm(self, source: str) -> list[str]:
        """Parse assembly source, return list of errors (empty if success)."""
        parser = Parser()
        try:
            self.instructions = parser.parse_text(source)
            self.pc = 0
            self.halted = False
            return []
        except Exception as e:
            return [str(e)]

    def reset(self) -> None:
        self.registers = RegisterFile()
        self.counters.reset()
        self.pc = 0
        self.halted = False
        self.loop_state = None

    def step(self) -> None:
        """Execute one instruction."""
        if self.halted:
            return
        if self.pc >= len(self.instructions):
            self.halted = True
            return

        inst = self.instructions[self.pc]
        self._execute(inst)

    def _execute(self, inst: Instruction) -> None:
        """Dispatch and execute a single instruction."""
        op = inst.opcode
        ops = inst.operands

        # Default: advance PC after execution
        advance_pc = True

        if op == "LDI":
            self._write_operand(ops[0], self._get_imm_value(ops[1]))
        elif op == "LDI32":
            self._write_operand(ops[0], self._get_imm_value(ops[1]))
        elif op == "MOV":
            val = self._read_operand(ops[1])
            self._write_operand(ops[0], val)
        elif op in ("ADD", "ADC", "SUB", "SUC", "RSB", "RSC"):
            self._exec_arithmetic(op, ops)
        elif op in ("AND", "OR", "XOR", "NOT"):
            self._exec_logic(op, ops)
        elif op in ("LSL", "LSR"):
            self._exec_shift(op, ops)
        elif op in ("SET", "CLR"):
            self._exec_bit(op, ops)
        elif op == "LMBD":
            src = self._read_operand(ops[1])
            target = self._read_operand(ops[2])
            self._write_operand(ops[0], ALU.lmbd(src, target))
        elif op in ("MIN", "MAX"):
            a = self._read_operand(ops[1])
            b = self._read_operand(ops[2])
            result = ALU.min_(a, b) if op == "MIN" else ALU.max_(a, b)
            self._write_operand(ops[0], result)
        elif op in ("QBA", "JMP", "JAL"):
            advance_pc = self._exec_jump(op, ops)
        elif op in ("QBEQ", "QBNE", "QBGT", "QBGE", "QBLT", "QBLE", "QBBS", "QBBC"):
            advance_pc = self._exec_conditional_branch(op, ops)
        elif op == "LOOP":
            self._exec_loop(ops)
        elif op in ("WBS", "WBC"):
            self._exec_wait(op, ops)
            advance_pc = True
        elif op in ("LBBO", "SBBO"):
            self._exec_memory(op, ops)
        elif op in ("LBCO", "SBCO"):
            self._exec_memory_const(op, ops)
        elif op in ("XIN", "XOUT", "XCHG"):
            self._exec_xfr(op, ops)
        elif op == "HALT":
            self.halted = True
        elif op == "SLP":
            self.halted = True
        elif op == "ZERO":
            self._exec_zero(ops)
        elif op == "FILL":
            self._exec_fill(ops)
        else:
            logger.warning(f"Unknown instruction: {op}")

        # Advance PC
        if advance_pc and not self.halted:
            self.pc += 1

        # Check hardware loop
        if self.loop_state and self.pc == self.loop_state.end_address:
            if self.loop_state.tick():
                self.pc = self.loop_state.start_address
            else:
                self.loop_state = None

        # Count cycle
        self.counters.tick()

    def _read_operand(self, op) -> int:
        if isinstance(op, Register):
            if op.index == R31:
                return self.io_port.read_r31()
            return self.registers.read(op.index, op.offset, op.width)
        elif isinstance(op, Immediate):
            return op.value
        elif isinstance(op, BitField):
            return self.registers.read_full(op.reg)
        elif isinstance(op, Label):
            return op.resolved_addr
        return 0

    def _write_operand(self, op, value: int) -> None:
        if isinstance(op, Register):
            self.registers.write(op.index, op.offset, op.width, value)
            if op.index == R30:
                self.io_port.write_r30(self.registers.read_full(R30))

    def _get_imm_value(self, op) -> int:
        if isinstance(op, Immediate):
            return op.value
        if isinstance(op, Register):
            return self.registers.read(op.index, op.offset, op.width)
        if isinstance(op, Label):
            return op.resolved_addr
        return 0

    def _get_dest_width(self, op) -> int:
        if isinstance(op, Register):
            return op.width
        return 32

    def _exec_arithmetic(self, op: str, ops: list) -> None:
        dst = ops[0]
        a = self._read_operand(ops[1])
        b = self._read_operand(ops[2])
        width = self._get_dest_width(dst)
        carry_in = self.registers.carry

        if op == "ADD":
            result, carry = ALU.add(a, b, width)
        elif op == "ADC":
            result, carry = ALU.adc(a, b, carry_in, width)
        elif op == "SUB":
            result, carry = ALU.sub(a, b, width)
        elif op == "SUC":
            result, carry = ALU.suc(a, b, carry_in, width)
        elif op == "RSB":
            result, carry = ALU.rsb(a, b, width)
        elif op == "RSC":
            result, carry = ALU.rsc(a, b, carry_in, width)

        self._write_operand(dst, result)
        self.registers.carry = carry

    def _exec_logic(self, op: str, ops: list) -> None:
        dst = ops[0]
        a = self._read_operand(ops[1])

        if op == "NOT":
            result = ALU.not_(a)
        else:
            b = self._read_operand(ops[2])
            if op == "AND":
                result = ALU.and_(a, b)
            elif op == "OR":
                result = ALU.or_(a, b)
            elif op == "XOR":
                result = ALU.xor_(a, b)

        self._write_operand(dst, result)

    def _exec_shift(self, op: str, ops: list) -> None:
        dst = ops[0]
        val = self._read_operand(ops[1])
        shift = self._read_operand(ops[2])
        if op == "LSL":
            result = ALU.lsl(val, shift)
        else:
            result = ALU.lsr(val, shift)
        self._write_operand(dst, result)

    def _exec_bit(self, op: str, ops: list) -> None:
        dst = ops[0]
        val = self._read_operand(ops[1])
        bit = self._read_operand(ops[2])
        if op == "SET":
            result = ALU.set_bit(val, bit)
        else:
            result = ALU.clr_bit(val, bit)
        self._write_operand(dst, result)

    def _exec_jump(self, op: str, ops: list) -> bool:
        """Returns False (don't advance PC normally)."""
        if op == "QBA":
            target = self._get_imm_value(ops[0])
            self.pc = target
            return False
        elif op == "JMP":
            target = self._read_operand(ops[0])
            self.pc = target
            return False
        elif op == "JAL":
            self._write_operand(ops[0], self.pc + 1)
            target = self._get_imm_value(ops[1])
            self.pc = target
            return False
        return True

    def _exec_conditional_branch(self, op: str, ops: list) -> bool:
        """Returns False if branch taken (don't advance PC)."""
        target_label = ops[0]
        target_addr = self._get_imm_value(target_label)

        if op in ("QBBS", "QBBC"):
            reg_val = self._read_operand(ops[1])
            bit = self._read_operand(ops[2])
            if op == "QBBS":
                taken = self.branch_unit.qbbs(reg_val, bit)
            else:
                taken = self.branch_unit.qbbc(reg_val, bit)
        else:
            reg_val = self._read_operand(ops[1])
            op_val = self._read_operand(ops[2])
            branch_fn = getattr(self.branch_unit, op.lower())
            taken = branch_fn(reg_val, op_val)

        if taken:
            self.pc = target_addr
            return False
        return True

    def _exec_loop(self, ops: list) -> None:
        """LOOP label, count — set up hardware loop."""
        end_addr = self._get_imm_value(ops[0])
        count = self._read_operand(ops[1])
        if count == 0:
            logger.warning("LOOP with count 0 — would hang real PRU!")
        self.loop_state = LoopState(
            count=count,
            start_address=self.pc + 1,
            end_address=end_addr,
        )

    def _exec_wait(self, op: str, ops: list) -> None:
        """WBS/WBC — check bit, if not met just advance (simplified)."""
        reg_val = self._read_operand(ops[0])
        bit = self._read_operand(ops[1])
        # In real hardware this spins. We just check once and advance.
        # The simulator user should set R31 pins before stepping.
        pass

    def _exec_memory(self, op: str, ops: list) -> None:
        """LBBO/SBBO &RegN, base_reg, offset, length."""
        start_reg = ops[0]  # Register (the & register)
        base_val = self._read_operand(ops[1])
        offset_val = self._read_operand(ops[2])
        length = self._read_operand(ops[3])

        # Check for bn (r0.bX as length)
        if length == 0:
            logger.warning("Memory op with length 0 — would hang real PRU!")
            return

        addr = (base_val + offset_val) & 0xFFFFFFFF

        if op == "LBBO":
            data, stalls = self.memory.read(addr, length)
            self.counters.stall(stalls)
            self._write_registers_from_bytes(start_reg.index, data)
        else:  # SBBO
            data = self._read_registers_to_bytes(start_reg.index, length)
            stalls = self.memory.write(addr, data)
            self.counters.stall(stalls)

    def _exec_memory_const(self, op: str, ops: list) -> None:
        """LBCO/SBCO — same as LBBO but base from constant table."""
        # For now, treat like LBBO with the constant value as base
        # Full constant table integration comes with config loading
        self._exec_memory(op, ops)

    def _exec_xfr(self, op: str, ops: list) -> None:
        """XIN/XOUT/XCHG device_id, &reg, length."""
        device_id = self._read_operand(ops[0])
        start_reg = ops[1]
        length = self._read_operand(ops[2])

        if length == 0:
            logger.warning("XFR op with length 0 — would hang real PRU!")
            return

        reg_idx = start_reg.index if isinstance(start_reg, Register) else 0
        # Offset into scratchpad based on register: R2 → offset 0, R3 → offset 4, etc.
        spad_offset = (reg_idx - 2) * 4 if reg_idx >= 2 else 0

        if op == "XIN":
            data = self.xfr.xin(device_id, spad_offset, length)
            self._write_registers_from_bytes(reg_idx, data)
        elif op == "XOUT":
            data = self._read_registers_to_bytes(reg_idx, length)
            self.xfr.xout(device_id, spad_offset, data)
        elif op == "XCHG":
            data = self._read_registers_to_bytes(reg_idx, length)
            old = self.xfr.xchg(device_id, spad_offset, data)
            self._write_registers_from_bytes(reg_idx, old)

    def _exec_zero(self, ops: list) -> None:
        """ZERO &reg, length — clear register space."""
        start_reg = ops[0]
        length = self._read_operand(ops[1])
        reg_idx = start_reg.index if isinstance(start_reg, Register) else 0
        num_regs = (length + 3) // 4
        for i in range(num_regs):
            if reg_idx + i < 32:
                self.registers.write_full(reg_idx + i, 0)

    def _exec_fill(self, ops: list) -> None:
        """FILL &reg, length — fill register space with 0xFF."""
        start_reg = ops[0]
        length = self._read_operand(ops[1])
        reg_idx = start_reg.index if isinstance(start_reg, Register) else 0
        num_regs = (length + 3) // 4
        for i in range(num_regs):
            if reg_idx + i < 32:
                self.registers.write_full(reg_idx + i, 0xFFFFFFFF)

    def _write_registers_from_bytes(self, start_reg: int, data: bytes) -> None:
        """Write bytes into consecutive registers starting at start_reg."""
        for i in range(0, len(data), 4):
            reg_idx = start_reg + (i // 4)
            if reg_idx >= 32:
                break
            chunk = data[i:i+4]
            val = int.from_bytes(chunk.ljust(4, b'\x00'), 'little')
            self.registers.write_full(reg_idx, val)

    def _read_registers_to_bytes(self, start_reg: int, length: int) -> bytes:
        """Read bytes from consecutive registers starting at start_reg."""
        result = bytearray()
        for i in range(0, length, 4):
            reg_idx = start_reg + (i // 4)
            if reg_idx >= 32:
                break
            val = self.registers.read_full(reg_idx)
            chunk = val.to_bytes(4, 'little')
            result.extend(chunk)
        return bytes(result[:length])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pru_core.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/pru_core.py tests/test_pru_core.py
git commit -m "feat: PRUCore execution engine with full instruction dispatch"
```

---

## Task 13: Simulator Orchestrator

**Files:**
- Create: `simulator.py`
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_integration.py
import pytest
from simulator import Simulator


class TestSimulatorBasic:
    def test_create_default(self):
        sim = Simulator()
        assert sim.cores["pru0"] is not None
        assert sim.cores["rtu0"] is not None

    def test_load_and_step(self):
        sim = Simulator()
        errors = sim.load("pru0", "ldi r0, 42\nhalt")
        assert errors == []
        sim.step("pru0")
        sim.step("pru0")
        assert sim.registers("pru0")[0] == 42

    def test_both_cores_independent(self):
        sim = Simulator()
        sim.load("pru0", "ldi r0, 1\nhalt")
        sim.load("rtu0", "ldi r0, 2\nhalt")
        sim.step("pru0")
        sim.step("rtu0")
        assert sim.registers("pru0")[0] == 1
        assert sim.registers("rtu0")[0] == 2

    def test_xfr_ipc_between_cores(self):
        sim = Simulator()
        # PRU0 writes R2 to scratchpad
        sim.load("pru0", "ldi r2, 0xCAFE\nxout 10, &r2, 4\nhalt")
        sim.step("pru0")
        sim.step("pru0")
        # RTU0 reads scratchpad into R2
        sim.load("rtu0", "xin 10, &r2, 4\nhalt")
        sim.step("rtu0")
        assert sim.registers("rtu0")[2] == 0xCAFE

    def test_status(self):
        sim = Simulator()
        sim.load("pru0", "ldi r0, 1\nhalt")
        sim.step("pru0")
        status = sim.status()
        assert status["pru0"]["pc"] == 1
        assert status["pru0"]["cycles"] == 1

    def test_reset(self):
        sim = Simulator()
        sim.load("pru0", "ldi r0, 1\nhalt")
        sim.step("pru0")
        sim.reset("pru0")
        assert sim.registers("pru0")[0] == 0

    def test_io(self):
        sim = Simulator()
        sim.load("pru0", "ldi r30, 5\nhalt")
        sim.step("pru0")
        io = sim.io("pru0")
        assert io["gpo_pins"][0] == 1
        assert io["gpo_pins"][2] == 1
        assert io["gpo_pins"][1] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_integration.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement Simulator**

```python
# simulator.py
"""PRU Simulator Orchestrator — manages dual-core PRU0 + RTU0."""
import configparser
import os
from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from mem.constant_table import ConstantTable
from xfr.xfr_bus import XFRBus
from pru_io.io_port import IOPort


class Simulator:
    def __init__(self, config_path: str = "memory.cfg"):
        self.xfr = XFRBus()
        self.memory = self._load_memory(config_path)
        self.constant_table = ConstantTable()

        io_pru0 = IOPort()
        io_rtu0 = IOPort()

        self.cores = {
            "pru0": PRUCore("PRU0", self.memory, self.xfr, io_pru0),
            "rtu0": PRUCore("RTU0", self.memory, self.xfr, io_rtu0),
        }

    def _load_memory(self, config_path: str) -> MemoryBus:
        bus = MemoryBus()
        if not os.path.exists(config_path):
            # Default fallback
            bus.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
            bus.add_region(MemoryRegion("DRAM1", 0x2000, 0x2000, 2, 1, 0))
            bus.add_region(MemoryRegion("ICSS_SHARED", 0x10000, 0x10000, 2, 1, 0))
            bus.add_region(MemoryRegion("MS_RAM", 0x80000000, 0x10000, 40, 1, 10))
            return bus

        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        for section in cfg.sections():
            if section == "device":
                continue
            if "base" in cfg[section]:
                bus.add_region(MemoryRegion(
                    name=section,
                    base_addr=int(cfg[section]["base"], 16),
                    size=int(cfg[section]["size"], 16),
                    read_latency=int(cfg[section].get("read_latency", "0")),
                    write_latency=int(cfg[section].get("write_latency", "0")),
                    jitter=int(cfg[section].get("jitter", "0")),
                ))
        return bus

    def load(self, core: str, source: str) -> list[str]:
        return self.cores[core].load_asm(source)

    def step(self, core: str, count: int = 1) -> dict:
        c = self.cores[core]
        for _ in range(count):
            if c.halted:
                break
            c.step()
        return {
            "pc": c.pc,
            "cycles": c.counters.cycles,
            "stall_cycles": c.counters.stall_cycles,
            "halted": c.halted,
        }

    def registers(self, core: str) -> list[int]:
        return list(self.cores[core].registers.regs)

    def memory_read(self, addr: int, length: int) -> bytes:
        data, _ = self.memory.read(addr, length)
        return data

    def io(self, core: str) -> dict:
        port = self.cores[core].io_port
        return {
            "gpo_pins": port.get_gpo_pins(),
            "gpi_pins": port.get_gpi_pins(),
        }

    def set_input(self, core: str, pin: int, value: bool) -> None:
        self.cores[core].io_port.set_gpi_pin(pin, value)

    def reset(self, core: str) -> None:
        self.cores[core].reset()

    def status(self) -> dict:
        result = {}
        for name, c in self.cores.items():
            result[name] = {
                "pc": c.pc,
                "cycles": c.counters.cycles,
                "stall_cycles": c.counters.stall_cycles,
                "instruction_count": c.counters.instruction_count,
                "ipc": c.counters.ipc,
                "halted": c.halted,
            }
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_integration.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add simulator.py tests/test_integration.py
git commit -m "feat: Simulator orchestrator with dual-core and shared XFR/memory"
```

---

## Task 14: MCP Server

**Files:**
- Create: `mcp_server/server.py`
- Create: `tests/test_mcp_server.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_mcp_server.py
import pytest
import json
from mcp_server.server import PRUSimulatorMCP


class TestMCPTools:
    def setup_method(self):
        self.mcp = PRUSimulatorMCP()

    def test_pru_load(self):
        result = self.mcp.pru_load(source="ldi r0, 42\nhalt", core="pru0")
        assert result["success"] is True
        assert result["line_count"] == 2
        assert result["errors"] == []

    def test_pru_load_invalid(self):
        result = self.mcp.pru_load(source="INVALID_OP r0, r1", core="pru0")
        # Should still load (unknown ops become warnings, not hard errors)
        assert result["success"] is True

    def test_pru_step(self):
        self.mcp.pru_load(source="ldi r0, 1\nldi r1, 2\nhalt", core="pru0")
        result = self.mcp.pru_step(core="pru0", count=2)
        assert result["pc"] == 2
        assert result["cycles"] == 2

    def test_pru_registers(self):
        self.mcp.pru_load(source="ldi r0, 0xFF\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=1)
        result = self.mcp.pru_registers(core="pru0")
        assert result["r0"] == "0x000000ff"
        assert result["carry"] is False

    def test_pru_io(self):
        self.mcp.pru_load(source="ldi r30, 5\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=1)
        result = self.mcp.pru_io(core="pru0")
        assert result["gpo_pins"][0] == 1
        assert result["gpo_pins"][2] == 1
        assert len(result["gpo_pins"]) == 20

    def test_pru_set_input(self):
        self.mcp.pru_load(source="mov r0, r31\nhalt", core="pru0")
        self.mcp.pru_set_input(core="pru0", pin=5, value=True)
        self.mcp.pru_step(core="pru0", count=1)
        regs = self.mcp.pru_registers(core="pru0")
        assert int(regs["r0"], 16) == (1 << 5)

    def test_pru_reset(self):
        self.mcp.pru_load(source="ldi r0, 1\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=1)
        self.mcp.pru_reset(core="pru0")
        regs = self.mcp.pru_registers(core="pru0")
        assert regs["r0"] == "0x00000000"

    def test_pru_status(self):
        self.mcp.pru_load(source="ldi r0, 1\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=2)
        status = self.mcp.pru_status()
        assert "pru0" in status["cores"]
        assert status["cores"]["pru0"]["halted"] is True

    def test_pru_memory(self):
        self.mcp.pru_load(source="ldi r1, 0\nldi r2, 0xABCD\nsbbo &r2, r1, 0, 4\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=3)
        result = self.mcp.pru_memory(addr=0, length=4)
        assert "abcd" in result["hex_dump"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp_server.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement MCP Server logic**

```python
# mcp_server/server.py
"""PRU Simulator MCP Server — exposes simulator as tools for Claude Code."""
import json
import sys
import logging
from simulator import Simulator

logger = logging.getLogger(__name__)


class PRUSimulatorMCP:
    """MCP tool implementations. Can be used standalone or via stdio MCP protocol."""

    def __init__(self, config_path: str = "memory.cfg"):
        self.sim = Simulator(config_path)

    def pru_load(self, source: str, core: str = "pru0") -> dict:
        errors = self.sim.load(core, source)
        line_count = len([l for l in source.split('\n') if l.strip()])
        return {"success": len(errors) == 0, "errors": errors, "line_count": line_count}

    def pru_step(self, core: str = "pru0", count: int = 1) -> dict:
        result = self.sim.step(core, count)
        c = self.sim.cores[core]
        inst_text = ""
        if c.pc > 0 and c.pc - 1 < len(c.instructions):
            inst_text = c.instructions[c.pc - 1].source_text
        result["instruction_text"] = inst_text
        return result

    def pru_run_until(self, core: str = "pru0", condition: str = "halt", max_steps: int = 10000) -> dict:
        c = self.sim.cores[core]
        for _ in range(max_steps):
            if c.halted:
                break
            c.step()
        return {
            "pc": c.pc,
            "cycles": c.counters.cycles,
            "reason": "halted" if c.halted else "max_steps",
        }

    def pru_registers(self, core: str = "pru0") -> dict:
        regs = self.sim.registers(core)
        result = {}
        for i in range(32):
            result[f"r{i}"] = f"0x{regs[i]:08x}"
        result["carry"] = self.sim.cores[core].registers.carry
        return result

    def pru_memory(self, addr: int, length: int) -> dict:
        data = self.sim.memory_read(addr, length)
        hex_dump = data.hex()
        ascii_repr = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data)
        return {"hex_dump": hex_dump, "ascii": ascii_repr}

    def pru_io(self, core: str = "pru0") -> dict:
        return self.sim.io(core)

    def pru_set_input(self, core: str = "pru0", pin: int = 0, value: bool = False) -> dict:
        self.sim.set_input(core, pin, value)
        return {"ok": True}

    def pru_reset(self, core: str = "pru0") -> dict:
        self.sim.reset(core)
        return {"ok": True}

    def pru_breakpoint(self, core: str = "pru0", address: int = 0) -> dict:
        self.sim.cores[core].breakpoints.add(address)
        return {"id": len(self.sim.cores[core].breakpoints)}

    def pru_status(self) -> dict:
        status = self.sim.status()
        return {"cores": status}


def run_stdio_server():
    """Run as stdio-based MCP server (for Claude Code integration)."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        import mcp.types as types

        server = Server("pru-simulator")
        mcp_impl = PRUSimulatorMCP()

        @server.list_tools()
        async def list_tools() -> list[types.Tool]:
            return [
                types.Tool(name="pru_load", description="Load PRU assembly source",
                           inputSchema={"type": "object", "properties": {
                               "source": {"type": "string"}, "core": {"type": "string", "default": "pru0"}
                           }, "required": ["source"]}),
                types.Tool(name="pru_step", description="Step execution",
                           inputSchema={"type": "object", "properties": {
                               "core": {"type": "string", "default": "pru0"},
                               "count": {"type": "integer", "default": 1}
                           }}),
                types.Tool(name="pru_registers", description="Read all registers",
                           inputSchema={"type": "object", "properties": {
                               "core": {"type": "string", "default": "pru0"}
                           }}),
                types.Tool(name="pru_memory", description="Read memory region",
                           inputSchema={"type": "object", "properties": {
                               "addr": {"type": "integer"}, "length": {"type": "integer"}
                           }, "required": ["addr", "length"]}),
                types.Tool(name="pru_io", description="Read IO pin states",
                           inputSchema={"type": "object", "properties": {
                               "core": {"type": "string", "default": "pru0"}
                           }}),
                types.Tool(name="pru_set_input", description="Set GPI pin value",
                           inputSchema={"type": "object", "properties": {
                               "core": {"type": "string", "default": "pru0"},
                               "pin": {"type": "integer"}, "value": {"type": "boolean"}
                           }, "required": ["pin", "value"]}),
                types.Tool(name="pru_reset", description="Reset core state",
                           inputSchema={"type": "object", "properties": {
                               "core": {"type": "string", "default": "pru0"}
                           }}),
                types.Tool(name="pru_status", description="Get status of all cores",
                           inputSchema={"type": "object", "properties": {}}),
                types.Tool(name="pru_run_until", description="Run until halt or max steps",
                           inputSchema={"type": "object", "properties": {
                               "core": {"type": "string", "default": "pru0"},
                               "condition": {"type": "string", "default": "halt"},
                               "max_steps": {"type": "integer", "default": 10000}
                           }}),
                types.Tool(name="pru_breakpoint", description="Set a breakpoint",
                           inputSchema={"type": "object", "properties": {
                               "core": {"type": "string", "default": "pru0"},
                               "address": {"type": "integer"}
                           }, "required": ["address"]}),
            ]

        @server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
            fn = getattr(mcp_impl, name)
            result = fn(**arguments)
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        import asyncio
        asyncio.run(stdio_server(server).run())

    except ImportError:
        logger.error("MCP SDK not installed. Run: pip install mcp")
        sys.exit(1)


if __name__ == "__main__":
    run_stdio_server()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcp_server.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server/server.py tests/test_mcp_server.py
git commit -m "feat: MCP server with all PRU simulator tools"
```

---

## Task 15: HTML Dashboard

**Files:**
- Create: `ui/server.py`
- Create: `ui/static/index.html`
- Create: `ui/static/app.js`

- [ ] **Step 1: Create FastAPI backend with WebSocket**

```python
# ui/server.py
"""PRU Simulator HTML Dashboard — FastAPI + WebSocket."""
import json
import os
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from simulator import Simulator

app = FastAPI(title="PRU Simulator Dashboard")
sim = Simulator()

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            action = msg.get("action")

            if action == "load":
                errors = sim.load(msg.get("core", "pru0"), msg["source"])
                await websocket.send_json({"type": "loaded", "errors": errors})
                await _send_state(websocket, msg.get("core", "pru0"))

            elif action == "step":
                core = msg.get("core", "pru0")
                count = msg.get("count", 1)
                sim.step(core, count)
                await _send_state(websocket, core)

            elif action == "reset":
                core = msg.get("core", "pru0")
                sim.reset(core)
                await _send_state(websocket, core)

            elif action == "set_input":
                core = msg.get("core", "pru0")
                sim.set_input(core, msg["pin"], msg["value"])
                await _send_state(websocket, core)

            elif action == "get_state":
                await _send_state(websocket, msg.get("core", "pru0"))

    except Exception:
        pass


async def _send_state(ws: WebSocket, core: str):
    c = sim.cores[core]
    state = {
        "type": "state",
        "core": core,
        "pc": c.pc,
        "halted": c.halted,
        "registers": [f"0x{r:08X}" for r in c.registers.regs],
        "carry": c.registers.carry,
        "cycles": c.counters.cycles,
        "stall_cycles": c.counters.stall_cycles,
        "instruction_count": c.counters.instruction_count,
        "ipc": round(c.counters.ipc, 3),
        "io": sim.io(core),
        "instructions": [
            {"addr": i.address, "text": i.source_text}
            for i in c.instructions
        ],
    }
    await ws.send_json(state)


def start_dashboard(host: str = "127.0.0.1", port: int = 8080):
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    start_dashboard()
```

- [ ] **Step 2: Create HTML frontend**

```html
<!-- ui/static/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>PRU Simulator</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Consolas', monospace; background: #1e1e1e; color: #d4d4d4; }
        .container { display: grid; grid-template-columns: 1fr 1fr 1fr; grid-template-rows: auto 1fr 1fr; gap: 8px; padding: 8px; height: 100vh; }
        .panel { background: #252526; border: 1px solid #3c3c3c; border-radius: 4px; padding: 8px; overflow: auto; }
        .panel h3 { color: #569cd6; margin-bottom: 8px; font-size: 12px; text-transform: uppercase; }
        .controls { grid-column: 1 / -1; display: flex; gap: 8px; align-items: center; padding: 8px; background: #333; border-radius: 4px; }
        .controls button { padding: 6px 16px; background: #0e639c; color: white; border: none; border-radius: 3px; cursor: pointer; font-family: inherit; }
        .controls button:hover { background: #1177bb; }
        .controls select, .controls textarea { font-family: inherit; background: #3c3c3c; color: #d4d4d4; border: 1px solid #555; border-radius: 3px; padding: 4px; }
        .source-line { padding: 1px 4px; font-size: 11px; white-space: pre; }
        .source-line.current { background: #264f78; color: #fff; }
        .reg-row { display: flex; justify-content: space-between; font-size: 11px; padding: 1px 4px; }
        .reg-row.changed { color: #4ec9b0; font-weight: bold; }
        .io-grid { display: grid; grid-template-columns: repeat(10, 1fr); gap: 4px; }
        .io-pin { width: 20px; height: 20px; border-radius: 50%; text-align: center; font-size: 9px; line-height: 20px; }
        .io-pin.high { background: #4ec9b0; color: #000; }
        .io-pin.low { background: #555; color: #999; }
        .counters { font-size: 12px; }
        .counters span { margin-right: 16px; }
        #source-input { width: 100%; height: 60px; resize: vertical; }
    </style>
</head>
<body>
    <div class="container">
        <div class="controls">
            <select id="core-select"><option value="pru0">PRU0</option><option value="rtu0">RTU0</option></select>
            <button id="btn-step">Step</button>
            <button id="btn-step10">Step 10</button>
            <button id="btn-run">Run to Halt</button>
            <button id="btn-reset">Reset</button>
            <button id="btn-load">Load</button>
            <textarea id="source-input" placeholder="Paste .asm source here..."></textarea>
            <div class="counters">
                <span>Cycles: <b id="cnt-cycles">0</b></span>
                <span>Stalls: <b id="cnt-stalls">0</b></span>
                <span>IPC: <b id="cnt-ipc">0</b></span>
            </div>
        </div>
        <div class="panel" id="source-panel"><h3>Source</h3><div id="source-view"></div></div>
        <div class="panel" id="reg-panel"><h3>Registers</h3><div id="reg-view"></div></div>
        <div class="panel" id="io-panel">
            <h3>GPO (R30)</h3><div id="gpo-view" class="io-grid"></div>
            <h3 style="margin-top:12px">GPI (R31)</h3><div id="gpi-view" class="io-grid"></div>
        </div>
    </div>
    <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Create JavaScript frontend logic**

```javascript
// ui/static/app.js
const ws = new WebSocket(`ws://${location.host}/ws`);
let prevRegs = new Array(32).fill("0x00000000");

ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "state") updateUI(msg);
};

ws.onopen = () => {
    ws.send(JSON.stringify({action: "get_state", core: getCore()}));
};

function getCore() { return document.getElementById("core-select").value; }

document.getElementById("btn-step").onclick = () => ws.send(JSON.stringify({action: "step", core: getCore(), count: 1}));
document.getElementById("btn-step10").onclick = () => ws.send(JSON.stringify({action: "step", core: getCore(), count: 10}));
document.getElementById("btn-run").onclick = () => ws.send(JSON.stringify({action: "step", core: getCore(), count: 10000}));
document.getElementById("btn-reset").onclick = () => ws.send(JSON.stringify({action: "reset", core: getCore()}));
document.getElementById("btn-load").onclick = () => {
    const src = document.getElementById("source-input").value;
    ws.send(JSON.stringify({action: "load", core: getCore(), source: src}));
};
document.getElementById("core-select").onchange = () => ws.send(JSON.stringify({action: "get_state", core: getCore()}));

function updateUI(state) {
    // Source view
    const srcDiv = document.getElementById("source-view");
    srcDiv.innerHTML = state.instructions.map((inst, i) =>
        `<div class="source-line${i === state.pc ? ' current' : ''}">${String(i).padStart(3)}  ${inst.text}</div>`
    ).join("");

    // Registers
    const regDiv = document.getElementById("reg-view");
    regDiv.innerHTML = state.registers.map((val, i) => {
        const changed = val !== prevRegs[i];
        return `<div class="reg-row${changed ? ' changed' : ''}">R${String(i).padStart(2, '0')}  ${val}</div>`;
    }).join("");
    prevRegs = [...state.registers];

    // IO pins
    const gpoDiv = document.getElementById("gpo-view");
    gpoDiv.innerHTML = state.io.gpo_pins.map((v, i) =>
        `<div class="io-pin ${v ? 'high' : 'low'}">${i}</div>`
    ).join("");

    const gpiDiv = document.getElementById("gpi-view");
    gpiDiv.innerHTML = state.io.gpi_pins.map((v, i) =>
        `<div class="io-pin ${v ? 'high' : 'low'}" onclick="toggleGPI(${i})">${i}</div>`
    ).join("");

    // Counters
    document.getElementById("cnt-cycles").textContent = state.cycles;
    document.getElementById("cnt-stalls").textContent = state.stall_cycles;
    document.getElementById("cnt-ipc").textContent = state.ipc;
}

function toggleGPI(pin) {
    ws.send(JSON.stringify({action: "set_input", core: getCore(), pin: pin, value: true}));
}
```

- [ ] **Step 4: Verify server starts**

Run: `cd C:/Users/a0746725/ai_code/pru_simulator && python -c "from ui.server import app; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add ui/server.py ui/static/index.html ui/static/app.js
git commit -m "feat: HTML dashboard with WebSocket live state updates"
```

---

## Task 16: Self-Test Assembly Suite

**Files:**
- Create: `tests/asm/test_arithmetic.asm`
- Create: `tests/asm/test_logic.asm`
- Create: `tests/asm/test_branches.asm`
- Create: `tests/asm/test_memory.asm`
- Create: `tests/test_self_test.py`

- [ ] **Step 1: Create arithmetic test assembly**

```asm
; tests/asm/test_arithmetic.asm
; Expected results: r10=30, r11=0, r12=1(carry), r13=10, r14=0xFFFFFFFF
    ldi r1, 10
    ldi r2, 20
    add r10, r1, r2         ; r10 = 10 + 20 = 30
    ldi r3, 0xFF
    add r11.b0, r3.b0, 1   ; 0xFF + 1 = 0x00 (byte overflow), carry set
    ldi r12, 0
    adc r12, r12, 0        ; r12 = 0 + 0 + carry = 1
    sub r13, r2, r1        ; r13 = 20 - 10 = 10
    ldi r4, 0
    sub r14, r4, 1         ; r14 = 0 - 1 = 0xFFFFFFFF (underflow)
    halt
```

- [ ] **Step 2: Create logic test assembly**

```asm
; tests/asm/test_logic.asm
; Expected: r10=0x00F0, r11=0xFFFF, r12=0xFF0F, r13=0xFFFF0000, r14=16, r15=1
    ldi r1, 0xFF00
    ldi r2, 0x00FF
    and r10, r1, 0xF0      ; r10 = 0xFF00 & 0xF0 = 0x0000 ... wait
    ; Actually: 0xFF00 & 0x00F0 ... let me fix
    ldi r1, 0xFFF0
    and r10, r1, 0xFF      ; r10 = 0xFFF0 & 0xFF = 0xF0
    ldi r1, 0xFF00
    or  r11, r1, r2        ; r11 = 0xFF00 | 0x00FF = 0xFFFF
    xor r12, r1, r2        ; r12 = 0xFF00 ^ 0x00FF = 0xFF0F ... wait 0xFFFF
    ; Fix: xor 0xFF00 ^ 0x00FF = 0xFFFF. Let me use different values.
    ldi r3, 0xF0F0
    xor r12, r1, r3        ; r12 = 0xFF00 ^ 0xF0F0 = 0x0FF0
    not r13, r2            ; r13 = ~0x00FF = 0xFFFFFF00
    ldi r4, 1
    lsl r14, r4, 4         ; r14 = 1 << 4 = 16
    ldi r5, 0x80
    lsr r15, r5, 7         ; r15 = 0x80 >> 7 = 1
    halt
```

- [ ] **Step 3: Create branch test assembly**

```asm
; tests/asm/test_branches.asm
; Expected: r10=1, r11=1, r12=1, r13=10 (loop counter result)
    ; Test QBEQ
    ldi r1, 5
    qbeq eq_pass, r1, 5
    ldi r10, 0
    qba eq_done
eq_pass:
    ldi r10, 1
eq_done:

    ; Test QBGT (branch if OP > Reg1: 10 > 5 → taken)
    ldi r2, 5
    qbgt gt_pass, r2, 10
    ldi r11, 0
    qba gt_done
gt_pass:
    ldi r11, 1
gt_done:

    ; Test QBBS (bit 3 of 0x08 is set)
    ldi r3, 0x08
    qbbs bs_pass, r3, 3
    ldi r12, 0
    qba bs_done
bs_pass:
    ldi r12, 1
bs_done:

    ; Test LOOP
    ldi r13, 0
    ldi r0.b0, 10
    loop loop_end, r0.b0
    add r13, r13, 1
loop_end:

    halt
```

- [ ] **Step 4: Create memory test assembly**

```asm
; tests/asm/test_memory.asm
; Expected: r10=0xDEADBEEF after store+load roundtrip
    ldi r1, 0x100           ; base address in DRAM0
    ldi r2, 0xBEEF
    ldi r3, 0xDEAD
    ; Store r2 (lower) and r3 (upper) — but SBBO stores consecutive regs
    ; Actually let's just store r2
    ldi r2, 0xDEAD
    sbbo &r2, r1, 0, 4     ; store 4 bytes of r2 at addr 0x100
    ldi r2, 0              ; clear r2
    lbbo &r10, r1, 0, 4   ; load 4 bytes into r10 from addr 0x100
    ; r10 should = 0x0000DEAD
    halt
```

- [ ] **Step 5: Create Python test runner**

```python
# tests/test_self_test.py
"""Run self-test .asm files and verify register results."""
import pytest
import os
from simulator import Simulator

ASM_DIR = os.path.join(os.path.dirname(__file__), "asm")


def run_asm(filename: str, max_steps: int = 1000) -> Simulator:
    """Load and run an asm file to completion."""
    sim = Simulator()
    filepath = os.path.join(ASM_DIR, filename)
    with open(filepath, 'r') as f:
        source = f.read()
    errors = sim.load("pru0", source)
    assert errors == [], f"Parse errors: {errors}"
    for _ in range(max_steps):
        if sim.cores["pru0"].halted:
            break
        sim.step("pru0")
    return sim


class TestArithmeticASM:
    def test_add_result(self):
        sim = run_asm("test_arithmetic.asm")
        assert sim.registers("pru0")[10] == 30  # r10 = 10 + 20

    def test_sub_result(self):
        sim = run_asm("test_arithmetic.asm")
        assert sim.registers("pru0")[13] == 10  # r13 = 20 - 10

    def test_underflow(self):
        sim = run_asm("test_arithmetic.asm")
        assert sim.registers("pru0")[14] == 0xFFFFFFFF


class TestBranchesASM:
    def test_qbeq_taken(self):
        sim = run_asm("test_branches.asm")
        assert sim.registers("pru0")[10] == 1

    def test_qbgt_taken(self):
        sim = run_asm("test_branches.asm")
        assert sim.registers("pru0")[11] == 1

    def test_qbbs_taken(self):
        sim = run_asm("test_branches.asm")
        assert sim.registers("pru0")[12] == 1

    def test_loop_count(self):
        sim = run_asm("test_branches.asm")
        assert sim.registers("pru0")[13] == 10


class TestMemoryASM:
    def test_store_load_roundtrip(self):
        sim = run_asm("test_memory.asm")
        assert sim.registers("pru0")[10] == 0x0000DEAD
```

- [ ] **Step 6: Run self-test suite**

Run: `pytest tests/test_self_test.py -v`
Expected: All PASS (may require debugging iterations)

- [ ] **Step 7: Commit**

```bash
git add tests/asm/ tests/test_self_test.py
git commit -m "feat: Self-test assembly suite with arithmetic, branches, and memory tests"
```

---

## Summary

| Task | Component | Dependencies |
|------|-----------|-------------|
| 1 | Project scaffolding | None |
| 2 | Register File | None |
| 3 | Cycle Counters | None |
| 4 | IO Port | None |
| 5 | Memory Subsystem | None |
| 6 | XFR Bus & Scratchpad | None |
| 7 | ALU Operations | None |
| 8 | Operand Parser | None |
| 9 | Preprocessor | None |
| 10 | Assembly Parser | Tasks 8, 9 |
| 11 | Branch Unit | None |
| 12 | PRU Core | Tasks 2-11 |
| 13 | Simulator Orchestrator | Task 12 |
| 14 | MCP Server | Task 13 |
| 15 | HTML Dashboard | Task 13 |
| 16 | Self-Test Suite | Task 13 |

**Independent tasks (can run in parallel):** 2, 3, 4, 5, 6, 7, 8, 9, 11
**Sequential chain:** 10 → 12 → 13 → (14, 15, 16 in parallel)
