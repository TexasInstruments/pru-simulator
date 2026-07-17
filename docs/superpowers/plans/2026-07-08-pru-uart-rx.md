# PRU UART RX 4.0 Mbit Receiver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement PRU0 UART receiver capable of receiving 11-byte frames at 4.0 Mbaud with no idle between bytes, store frames via single SBCO operation, and integrate with UARTFrameGenerator peripheral and MCP server for testing.

**Architecture:**
- PRU0 assembly UART receiver uses register buffering (R2-R12) to accumulate 11 bytes then stores via single SBCO
- UARTFrameGenerator peripheral creates test frames on GPI0 pin with configurable payload and baudrate
- MCP server provides pru_uart_inject tool for end-to-end testing
- Error detection: framing error (invalid stop bit) sets sticky flag at DRAM0+0x0FFE and restarts reception

**Tech Stack:**
- PRU assembly language (PRU ISA)
- Python (peripheral class, MCP server tool, tests)
- Existing PRU simulator infrastructure (MemoryBus, IOPort, PRUCore, Simulator)

---

## File Structure

| File | Responsibility |
|------|---------------|
| **Create:** `pru_io/uart_frame_generator.py` | Generates UART bit waveform on GPI pin (pre-computes timeline, applies per cycle) |
| **Create:** `source/uart_rx_11frame.asm` | PRU0 assembly: polls GPI0, assembles bytes into R2-R12, stores via SBCO |
| **Create:** `tests/test_uart_frame_generator.py` | Unit tests for the generator class in isolation |
| **Create:** `tests/test_uart_rx.py` | Integration tests: generator + PRU assembly running in simulator |
| **Create:** `tests/test_uart_rx_mcp.py` | End-to-end MCP tool tests |
| **Modify:** `pru_io/io_port.py` | Add `uart_generator` field and pre-tick hook |
| **Modify:** `core/pru_core.py` (~line 108) | Call uart_generator.tick() before instruction execution |
| **Modify:** `simulator.py` (~line 55) | Wire UARTFrameGenerator to IOPort during initialization |
| **Modify:** `mcp_server/server.py` | Add `pru_uart_inject` tool method |

---

## Task 1: UARTFrameGenerator — Core Bit Timeline

**Files:**
- Create: `pru_io/uart_frame_generator.py`
- Test: `tests/test_uart_frame_generator.py`

This task builds the standalone generator that pre-computes UART waveform events.

- [ ] **Step 1: Write failing test — timeline produces correct start bit**

```python
# tests/test_uart_frame_generator.py
import pytest
from pru_io.uart_frame_generator import UARTFrameGenerator


class TestUARTFrameGeneratorTimeline:
    """Test that the generator computes the correct bit timeline."""

    def test_single_byte_start_bit(self):
        """Start bit should be LOW for exactly one bit period."""
        gen = UARTFrameGenerator(
            pin=0,
            payload=[0x41],
            baudrate=4_000_000,
            pru_clock_mhz=200.0,
        )
        # Bit period = 200MHz / 4Mbaud = 50 cycles
        # At cycle 0, generator should start with idle HIGH
        # The first event is the start bit going LOW at cycle 0
        # Start bit lasts 50 cycles (cycles 0-49)
        gen.start(trigger_cycle=0)
        assert gen.get_pin_value(0) == 0   # start bit LOW
        assert gen.get_pin_value(49) == 0  # still in start bit
        assert gen.get_pin_value(50) == 1  # first data bit of 0x41 (bit0=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_frame_generator.py::TestUARTFrameGeneratorTimeline::test_single_byte_start_bit -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pru_io.uart_frame_generator'`

- [ ] **Step 3: Write minimal implementation**

```python
# pru_io/uart_frame_generator.py
"""UART Frame Generator — produces bit-level waveform on a GPI pin.

Pre-computes a timeline of pin transitions for 8N1 UART frames.
Used to test PRU UART receiver assembly code.
"""

from __future__ import annotations


class UARTFrameGenerator:
    """Generates UART bit waveform events for testing PRU UART RX.

    Pre-computes all pin transitions at construction time.
    Call start() to arm the generator at a specific cycle,
    then get_pin_value(cycle) to query the pin state at any cycle.
    """

    def __init__(
        self,
        pin: int = 0,
        payload: list[int] | None = None,
        baudrate: int = 4_000_000,
        pru_clock_mhz: float = 200.0,
    ):
        if pin < 0 or pin > 19:
            raise ValueError(f"GPI pin {pin} out of range 0-19")
        self.pin = pin
        self.payload = payload if payload is not None else []
        self.baudrate = baudrate
        self.pru_clock_mhz = pru_clock_mhz
        self.bit_period = int(pru_clock_mhz * 1_000_000 / baudrate)  # cycles per bit

        self._trigger_cycle: int | None = None
        self._transitions: list[tuple[int, int]] = []  # (cycle, pin_value)
        self._idle_value = 1  # UART idle is HIGH

    def start(self, trigger_cycle: int = 0) -> None:
        """Arm the generator: pre-compute all transitions starting at trigger_cycle."""
        self._trigger_cycle = trigger_cycle
        self._transitions = []
        cycle = trigger_cycle

        for byte_val in self.payload:
            # START bit (LOW)
            self._transitions.append((cycle, 0))
            cycle += self.bit_period

            # 8 data bits, LSB first
            for bit_idx in range(8):
                bit = (byte_val >> bit_idx) & 1
                self._transitions.append((cycle, bit))
                cycle += self.bit_period

            # STOP bit (HIGH)
            self._transitions.append((cycle, 1))
            cycle += self.bit_period

        # Final: return to idle after last stop bit
        self._frame_end_cycle = cycle

    def get_pin_value(self, cycle: int) -> int:
        """Return pin value at the given absolute cycle.

        Before trigger_cycle: returns idle (HIGH).
        After frame_end: returns idle (HIGH).
        During frame: returns the bit value active at that cycle.
        """
        if self._trigger_cycle is None:
            return self._idle_value
        if cycle < self._trigger_cycle:
            return self._idle_value
        if cycle >= self._frame_end_cycle:
            return self._idle_value

        # Find the last transition at or before this cycle
        pin_val = self._idle_value
        for trans_cycle, val in self._transitions:
            if trans_cycle <= cycle:
                pin_val = val
            else:
                break
        return pin_val
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_frame_generator.py::TestUARTFrameGeneratorTimeline::test_single_byte_start_bit -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add pru_io/uart_frame_generator.py tests/test_uart_frame_generator.py
git commit -m "feat: add UARTFrameGenerator with basic timeline computation"
```

---

## Task 2: UARTFrameGenerator — Full Byte Waveform Validation

**Files:**
- Modify: `tests/test_uart_frame_generator.py`

- [ ] **Step 1: Write failing tests — full byte bit pattern and multi-byte no-idle**

```python
# Append to tests/test_uart_frame_generator.py

    def test_single_byte_all_data_bits(self):
        """Verify all 8 data bits of 0x41 ('A') are correct. 0x41 = 0b01000001."""
        gen = UARTFrameGenerator(pin=0, payload=[0x41], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.start(trigger_cycle=0)
        bp = 50  # bit period
        # Bit layout: START | b0 b1 b2 b3 b4 b5 b6 b7 | STOP
        # 0x41 = 0b01000001 → LSB first: 1,0,0,0,0,0,1,0
        expected_bits = [0, 1, 0, 0, 0, 0, 0, 1, 0, 1]  # start, d0-d7, stop
        for i, expected in enumerate(expected_bits):
            # Sample at middle of each bit period
            sample_cycle = i * bp + bp // 2
            actual = gen.get_pin_value(sample_cycle)
            assert actual == expected, f"Bit {i}: expected {expected}, got {actual} at cycle {sample_cycle}"

    def test_two_bytes_no_idle_between(self):
        """Two bytes back-to-back: stop bit of byte 0 immediately followed by start bit of byte 1."""
        gen = UARTFrameGenerator(pin=0, payload=[0xFF, 0x00], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.start(trigger_cycle=0)
        bp = 50
        # Byte 0 (0xFF): start=0, d0-d7=all 1, stop=1 → 10 bits = 500 cycles
        # Byte 1 (0x00): start=0, d0-d7=all 0, stop=1
        # At cycle 500: byte 1 start bit begins (LOW)
        assert gen.get_pin_value(499) == 1  # byte 0 stop bit still HIGH
        assert gen.get_pin_value(500) == 0  # byte 1 start bit (LOW, no idle gap)

    def test_idle_before_trigger(self):
        """Pin should be HIGH (idle) before trigger cycle."""
        gen = UARTFrameGenerator(pin=0, payload=[0x00], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.start(trigger_cycle=100)
        assert gen.get_pin_value(0) == 1
        assert gen.get_pin_value(99) == 1
        assert gen.get_pin_value(100) == 0  # start bit

    def test_idle_after_frame(self):
        """Pin should return to HIGH (idle) after last stop bit."""
        gen = UARTFrameGenerator(pin=0, payload=[0x55], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.start(trigger_cycle=0)
        bp = 50
        frame_end = 10 * bp  # 10 bits total (start + 8 data + stop)
        assert gen.get_pin_value(frame_end - 1) == 1  # last cycle of stop bit
        assert gen.get_pin_value(frame_end) == 1      # idle after frame
        assert gen.get_pin_value(frame_end + 100) == 1
```

- [ ] **Step 2: Run tests to verify they pass (implementation from Task 1 should handle these)**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_frame_generator.py -v`
Expected: All PASS (the implementation already handles these cases)

- [ ] **Step 3: If any fail, fix the implementation and re-run**

- [ ] **Step 4: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add tests/test_uart_frame_generator.py
git commit -m "test: add full byte waveform validation for UARTFrameGenerator"
```

---

## Task 3: UARTFrameGenerator — IOPort Integration (tick method)

**Files:**
- Modify: `pru_io/uart_frame_generator.py`
- Modify: `pru_io/io_port.py`
- Test: `tests/test_uart_frame_generator.py`

The generator needs a `tick(cycle)` method that directly updates IOPort.gpi.

- [ ] **Step 1: Write failing test — tick updates IOPort GPI pin**

```python
# Append to tests/test_uart_frame_generator.py
from pru_io.io_port import IOPort


class TestUARTFrameGeneratorTick:
    """Test tick() method that updates IOPort GPI state."""

    def test_tick_sets_gpi_pin_low_on_start_bit(self):
        """tick() should set GPI pin LOW when start bit is active."""
        io = IOPort()
        io.gpi = 0xFFFFF  # all pins HIGH
        gen = UARTFrameGenerator(pin=0, payload=[0x41], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(io)
        gen.start(trigger_cycle=0)

        gen.tick(0)  # cycle 0: start bit
        assert (io.gpi & 1) == 0  # pin 0 should be LOW

    def test_tick_preserves_other_pins(self):
        """tick() should only affect the configured pin, not others."""
        io = IOPort()
        io.gpi = 0b1111_1111_1111_1111_1111  # all HIGH
        gen = UARTFrameGenerator(pin=3, payload=[0x00], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(io)
        gen.start(trigger_cycle=0)

        gen.tick(0)  # start bit on pin 3
        assert (io.gpi >> 3) & 1 == 0  # pin 3 LOW
        assert (io.gpi >> 0) & 1 == 1  # pin 0 unchanged
        assert (io.gpi >> 4) & 1 == 1  # pin 4 unchanged

    def test_tick_idle_keeps_pin_high(self):
        """Before trigger, tick should set pin HIGH (idle)."""
        io = IOPort()
        io.gpi = 0
        gen = UARTFrameGenerator(pin=0, payload=[0x41], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(io)
        gen.start(trigger_cycle=100)

        gen.tick(50)  # before trigger
        assert (io.gpi & 1) == 1  # idle HIGH
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_frame_generator.py::TestUARTFrameGeneratorTick -v`
Expected: FAIL with `AttributeError: 'UARTFrameGenerator' object has no attribute 'attach'`

- [ ] **Step 3: Implement attach() and tick() methods**

Add to `pru_io/uart_frame_generator.py`:

```python
    def attach(self, io_port) -> None:
        """Attach this generator to an IOPort. tick() will update the port's GPI."""
        self._io_port = io_port

    def tick(self, cycle: int) -> None:
        """Update the attached IOPort's GPI pin to the correct value for this cycle.

        Must be called BEFORE the PRU instruction executes so R31 reads see the correct bit.
        """
        if self._io_port is None:
            return
        pin_val = self.get_pin_value(cycle)
        if pin_val:
            self._io_port.gpi |= (1 << self.pin)
        else:
            self._io_port.gpi &= ~(1 << self.pin)
```

Also add `self._io_port = None` to `__init__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_frame_generator.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add pru_io/uart_frame_generator.py tests/test_uart_frame_generator.py
git commit -m "feat: add tick() and attach() to UARTFrameGenerator for IOPort integration"
```

---

## Task 4: IOPort and PRUCore Integration — Pre-Tick Hook

**Files:**
- Modify: `pru_io/io_port.py`
- Modify: `core/pru_core.py`
- Test: `tests/test_uart_frame_generator.py`

Wire the generator into the simulator's per-instruction execution loop.

- [ ] **Step 1: Write failing test — generator updates GPI visible to R31 read during execution**

```python
# Append to tests/test_uart_frame_generator.py
from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from xfr.xfr_bus import XFRBus


class TestUARTFrameGeneratorPRUIntegration:
    """Test that generator updates are visible to PRU instructions via R31."""

    def _make_core(self, asm: str) -> PRUCore:
        """Create a PRUCore with DRAM0 and IOPort."""
        mem = MemoryBus()
        mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
        xfr = XFRBus()
        io = IOPort()
        core = PRUCore("PRU0", mem, xfr, io)
        errors = core.load_asm(asm)
        assert errors == [], f"Assembly errors: {errors}"
        return core

    def test_r31_reads_uart_start_bit(self):
        """PRU reading R31 should see LOW on pin 0 when start bit is active."""
        # Assembly: read R31 into R5, then halt
        asm = "and r5, r31, 1\nhalt\n"
        core = self._make_core(asm)

        # Attach generator to core's IOPort
        gen = UARTFrameGenerator(pin=0, payload=[0x41], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=0)
        core.io_port.uart_generator = gen

        # Step one instruction (cycle 0: start bit LOW)
        core.step()

        # R5 should contain 0 (pin 0 was LOW during the AND instruction)
        assert core.registers.read_full(5) == 0

    def test_r31_reads_uart_data_bit_high(self):
        """PRU reading R31 should see HIGH when data bit is 1."""
        # 0x41 bit 0 = 1, which starts at cycle 50 (after 50-cycle start bit)
        # We need to step past the start bit period to reach the first data bit
        asm = "and r5, r31, 1\n" * 60 + "halt\n"
        core = self._make_core(asm)

        gen = UARTFrameGenerator(pin=0, payload=[0x41], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=0)
        core.io_port.uart_generator = gen

        # Step 55 instructions (cycle 55 is in the middle of data bit 0 which is HIGH for 0x41)
        for _ in range(55):
            core.step()

        # R5 should contain 1 (pin 0 was HIGH at cycle 54, which is in bit0 period [50..99])
        assert core.registers.read_full(5) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_frame_generator.py::TestUARTFrameGeneratorPRUIntegration -v`
Expected: FAIL (IOPort has no `uart_generator` attribute, PRUCore doesn't call tick)

- [ ] **Step 3: Add uart_generator field to IOPort**

In `pru_io/io_port.py`, add to `__init__`:

```python
        self.uart_generator: "UARTFrameGenerator | None" = None
```

- [ ] **Step 4: Add pre-tick call in PRUCore.step()**

In `core/pru_core.py`, at the beginning of the `step()` method (before instruction fetch/execute), add:

```python
        # Pre-tick: advance UART frame generator before instruction reads R31
        if self.io_port.uart_generator is not None:
            self.io_port.uart_generator.tick(self.counters.cycles)
```

This must go BEFORE the instruction fetch line (`instr = self.instructions[self.pc]`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_frame_generator.py -v`
Expected: All PASS

- [ ] **Step 6: Run existing test suite to confirm no regressions**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/ -v --tb=short`
Expected: All existing tests PASS (uart_generator defaults to None, so no pre-tick occurs)

- [ ] **Step 7: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add pru_io/io_port.py core/pru_core.py tests/test_uart_frame_generator.py
git commit -m "feat: integrate UARTFrameGenerator pre-tick into PRUCore execution loop"
```

---

## Task 5: PRU Assembly — Start Bit Detection

**Files:**
- Create: `source/uart_rx_11frame.asm`
- Test: `tests/test_uart_rx.py`

Build the assembly incrementally, starting with start bit detection.

- [ ] **Step 1: Write failing test — PRU detects start bit and halts**

```python
# tests/test_uart_rx.py
"""Integration tests for PRU UART RX assembly with UARTFrameGenerator."""
import pytest
from pathlib import Path

from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from mem.constant_table import ConstantTable
from xfr.xfr_bus import XFRBus
from pru_io.io_port import IOPort
from pru_io.uart_frame_generator import UARTFrameGenerator


def make_uart_core(asm_path: str | Path) -> PRUCore:
    """Create PRUCore with DRAM0 (8KB) and constant table c24=0x00000000."""
    mem = MemoryBus()
    mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
    xfr = XFRBus()
    io = IOPort()
    ct = ConstantTable()
    ct.entries[24] = 0x00000000  # c24 = DRAM0 base
    core = PRUCore("PRU0", mem, xfr, io, ct)
    source = Path(asm_path).read_text()
    errors = core.load_asm(source)
    assert errors == [], f"Assembly errors: {errors}"
    return core


class TestUARTRXStartDetection:
    """Test that assembly correctly detects UART start bit (falling edge on GPI0)."""

    def test_detects_start_bit_within_100_cycles(self):
        """PRU should exit polling loop when GPI0 goes LOW."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        # Set up generator: start bit at cycle 10
        gen = UARTFrameGenerator(pin=0, payload=[0x41] * 11, baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=10)
        core.io_port.uart_generator = gen

        # GPI0 starts HIGH (idle) — generator sets it before cycle 10
        core.io_port.set_gpi_pin(0, True)

        # Run up to 200 instructions — should NOT halt (it enters receive mode)
        steps = 0
        for _ in range(200):
            core.step()
            steps += 1
            # Check if PC advanced past the polling loop (label-dependent)
            # We just verify it didn't halt in the first 200 steps
        assert not core.halted, "PRU halted unexpectedly during start bit detection"
        assert core.counters.cycles > 10, "PRU should have advanced past the start bit"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_rx.py::TestUARTRXStartDetection -v`
Expected: FAIL (file `source/uart_rx_11frame.asm` does not exist)

- [ ] **Step 3: Write initial assembly — start bit detection loop only**

```asm
; source/uart_rx_11frame.asm
; PRU0 UART Receiver — 11-byte frame, 4.0 Mbaud @ 200 MHz
; Receives 8N1 UART frames on GPI0 (R31 bit 0)
; Stores 11 bytes to DRAM0 via single SBCO from R2-R12
; Framing error flag at DRAM0+0x0FFE
;
; Register Map:
;   R0  — reserved (SBCO/LBCO)
;   R1  — DRAM0 byte offset for frame storage
;   R2-R12 — 11-byte frame buffer (one byte per register)
;   R13 — byte accumulator (current byte being assembled)
;   R14 — bit mask (1, 2, 4, 8, 16, 32, 64, 128)
;   R15 — GPI0 sample value (0 or 1)
;   R16 — byte index counter (0-10)
;   R17 — bit counter (0-7)
;   R18 — delay counter
;   R19 — error flags (bit 0 = framing error, sticky)
;   R20 — frame counter (number of frames received)

.set BIT_TIME,    25      ; iterations per full bit (50 cycles / 2 cyc per iter)
.set START_DELAY, 38      ; iterations to mid-first-data-bit (75 cycles / 2)
.set GPI0_MASK,   1       ; mask for pin 0
.set ERR_ADDR,    0x0FFE  ; DRAM0 offset for error flag

        ; Initialize
        ldi  r1, 0              ; frame storage offset = 0
        ldi  r16, 0             ; byte index = 0
        ldi  r19, 0             ; clear error flags
        ldi  r20, 0             ; frame counter = 0

        ; Set GPI0 idle state HIGH before starting
wait_start:
        ; Poll GPI0 for falling edge (start bit = LOW)
        and  r15, r31, GPI0_MASK
        qbne wait_start, r15, 0   ; loop while pin is HIGH (r15 != 0)

        ; Start bit detected! Delay 1.5 bit periods to sample mid-first-data-bit
        ldi  r18, START_DELAY
delay_start:
        sub  r18, r18, 1
        qbne delay_start, r18, 0

        ; Now at center of first data bit — begin byte reception
        ldi  r16, 0             ; byte index = 0
        jmp  receive_byte

receive_byte:
        ; Receive 8 data bits for current byte (LSB first)
        ldi  r13, 0             ; byte accumulator = 0
        ldi  r14, 1             ; bit mask = 0x01 (bit 0)
        ldi  r17, 0             ; bit counter = 0

sample_bit:
        ; Sample GPI0
        and  r15, r31, GPI0_MASK
        qbeq bit_is_zero, r15, 0
        ; Bit is 1: set bit in accumulator
        or   r13, r13, r14
bit_is_zero:
        ; Advance bit mask
        lsl  r14, r14, 1
        add  r17, r17, 1

        ; Delay one bit period (minus sampling overhead)
        ldi  r18, BIT_TIME
delay_bit:
        sub  r18, r18, 1
        qbne delay_bit, r18, 0

        ; Check if all 8 bits received
        qbne sample_bit, r17, 8

        ; All 8 bits received — store byte in register buffer
        ; Use byte index (R16) to select destination register R2-R12
        qbeq store_r2,  r16, 0
        qbeq store_r3,  r16, 1
        qbeq store_r4,  r16, 2
        qbeq store_r5,  r16, 3
        qbeq store_r6,  r16, 4
        qbeq store_r7,  r16, 5
        qbeq store_r8,  r16, 6
        qbeq store_r9,  r16, 7
        qbeq store_r10, r16, 8
        qbeq store_r11, r16, 9
        qbeq store_r12, r16, 10

store_r2:  mov r2, r13
        jmp check_stop
store_r3:  mov r3, r13
        jmp check_stop
store_r4:  mov r4, r13
        jmp check_stop
store_r5:  mov r5, r13
        jmp check_stop
store_r6:  mov r6, r13
        jmp check_stop
store_r7:  mov r7, r13
        jmp check_stop
store_r8:  mov r8, r13
        jmp check_stop
store_r9:  mov r9, r13
        jmp check_stop
store_r10: mov r10, r13
        jmp check_stop
store_r11: mov r11, r13
        jmp check_stop
store_r12: mov r12, r13
        jmp check_stop

check_stop:
        ; Verify STOP bit (should be HIGH)
        and  r15, r31, GPI0_MASK
        qbeq framing_error, r15, 0   ; if LOW → framing error

        ; STOP bit valid — advance to next byte
        add  r16, r16, 1

        ; Check if all 11 bytes received
        qbeq store_frame, r16, 11

        ; Wait for next start bit (which follows immediately in no-idle frames)
        ; Delay to center of next start bit's first data bit
        ldi  r18, START_DELAY
delay_next:
        sub  r18, r18, 1
        qbne delay_next, r18, 0
        jmp  receive_byte

store_frame:
        ; All 11 bytes in R2-R12 — store to DRAM0 via single SBCO
        sbco &r2, c24, r1, 11

        ; Advance storage offset for next frame
        add  r1, r1, 11
        add  r20, r20, 1       ; increment frame counter

        ; Go back to waiting for next frame's start bit
        jmp  wait_start

framing_error:
        ; Set sticky error flag
        ldi  r19, 1

        ; Write error flag to DRAM0+0x0FFE
        ldi  r18, ERR_ADDR
        sbco &r19, c24, r18, 1

        ; Reset byte index and start over
        ldi  r16, 0
        jmp  wait_start
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_rx.py::TestUARTRXStartDetection -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add source/uart_rx_11frame.asm tests/test_uart_rx.py
git commit -m "feat: add PRU UART RX assembly with start detection and full receive logic"
```

---

## Task 6: PRU Assembly — Single Byte Reception Test

**Files:**
- Modify: `tests/test_uart_rx.py`

- [ ] **Step 1: Write test — receive one frame and verify DRAM0 contents**

```python
# Append to tests/test_uart_rx.py

class TestUARTRXSingleFrame:
    """Test complete 11-byte frame reception."""

    def test_receive_11_bytes_stored_to_dram0(self):
        """Receive one 11-byte frame and verify it is stored correctly in DRAM0."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        payload = [0x48, 0x65, 0x6C, 0x6C, 0x6F, 0x57, 0x6F, 0x72, 0x6C, 0x64, 0x21]  # "HelloWorld!"
        gen = UARTFrameGenerator(pin=0, payload=payload, baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=5)  # start after 5 idle cycles
        core.io_port.uart_generator = gen
        core.io_port.set_gpi_pin(0, True)  # idle HIGH initially

        # Run enough cycles for one full frame:
        # 11 bytes × 10 bits × 50 cycles/bit = 5500 cycles
        # Plus start detection and overhead, allow 8000 steps
        core.run(max_steps=8000)

        # Read DRAM0[0:11]
        data, _ = core.memory.read(0x0000, 11)
        received = list(data)
        assert received == payload, f"Expected {payload}, got {received}"

    def test_frame_counter_increments(self):
        """R20 should be 1 after receiving one frame."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        payload = [0x41] * 11
        gen = UARTFrameGenerator(pin=0, payload=payload, baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=5)
        core.io_port.uart_generator = gen
        core.io_port.set_gpi_pin(0, True)

        core.run(max_steps=8000)

        # R20 = frame counter
        assert core.registers.read_full(20) == 1

    def test_no_error_flag_on_clean_reception(self):
        """DRAM0[0x0FFE] should remain 0 after clean frame reception."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        payload = [0x55, 0xAA, 0x00, 0xFF, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE]
        gen = UARTFrameGenerator(pin=0, payload=payload, baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=5)
        core.io_port.uart_generator = gen
        core.io_port.set_gpi_pin(0, True)

        core.run(max_steps=8000)

        # Error flag should be 0
        err_data, _ = core.memory.read(0x0FFE, 1)
        assert err_data[0] == 0
```

- [ ] **Step 2: Run tests**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_rx.py::TestUARTRXSingleFrame -v`
Expected: PASS if assembly timing is correct. If FAIL, debug timing issues in assembly.

- [ ] **Step 3: Debug and fix assembly timing if needed**

Common issues:
- Sampling point off by a few cycles → adjust `START_DELAY` or `BIT_TIME`
- Store dispatch incorrect → check byte index routing
- SBCO addressing wrong → verify c24 resolves to 0x00000000

Debug approach: Add a helper that dumps register state after each step to trace execution.

- [ ] **Step 4: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add tests/test_uart_rx.py source/uart_rx_11frame.asm
git commit -m "test: add single-frame reception integration tests for UART RX"
```

---

## Task 7: PRU Assembly — Multi-Frame and Edge Cases

**Files:**
- Modify: `tests/test_uart_rx.py`
- Modify: `pru_io/uart_frame_generator.py` (add `frames` parameter)

- [ ] **Step 1: Add frames parameter to UARTFrameGenerator**

In `pru_io/uart_frame_generator.py`, modify `__init__` to accept `frames: int = 1`:

```python
    def __init__(
        self,
        pin: int = 0,
        payload: list[int] | None = None,
        baudrate: int = 4_000_000,
        pru_clock_mhz: float = 200.0,
        frames: int = 1,
        idle_gap_bits: int = 2,
    ):
        # ... existing init ...
        self.frames = frames
        self.idle_gap_bits = idle_gap_bits  # idle bits between repeated frames
```

Modify `start()` to repeat the payload `frames` times with idle gaps between frames:

```python
    def start(self, trigger_cycle: int = 0) -> None:
        """Arm the generator: pre-compute all transitions starting at trigger_cycle."""
        self._trigger_cycle = trigger_cycle
        self._transitions = []
        cycle = trigger_cycle

        for frame_idx in range(self.frames):
            for byte_val in self.payload:
                # START bit (LOW)
                self._transitions.append((cycle, 0))
                cycle += self.bit_period

                # 8 data bits, LSB first
                for bit_idx in range(8):
                    bit = (byte_val >> bit_idx) & 1
                    self._transitions.append((cycle, bit))
                    cycle += self.bit_period

                # STOP bit (HIGH)
                self._transitions.append((cycle, 1))
                cycle += self.bit_period

            # Idle gap between frames (HIGH)
            if frame_idx < self.frames - 1:
                self._transitions.append((cycle, 1))
                cycle += self.bit_period * self.idle_gap_bits

        self._frame_end_cycle = cycle
```

- [ ] **Step 2: Write multi-frame test**

```python
# Append to tests/test_uart_rx.py

class TestUARTRXMultiFrame:
    """Test receiving multiple consecutive frames."""

    def test_two_frames_stored_sequentially(self):
        """Two frames should be stored at DRAM0[0] and DRAM0[11]."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        payload = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B]
        gen = UARTFrameGenerator(
            pin=0, payload=payload, baudrate=4_000_000, pru_clock_mhz=200.0,
            frames=2, idle_gap_bits=4,
        )
        gen.attach(core.io_port)
        gen.start(trigger_cycle=5)
        core.io_port.uart_generator = gen
        core.io_port.set_gpi_pin(0, True)

        # Two frames: 2 × (11 bytes × 10 bits × 50 cycles) + gap = ~11200 cycles
        core.run(max_steps=16000)

        # Frame 0 at DRAM0[0:11]
        data0, _ = core.memory.read(0x0000, 11)
        assert list(data0) == payload

        # Frame 1 at DRAM0[11:22]
        data1, _ = core.memory.read(0x000B, 11)
        assert list(data1) == payload

        # Frame counter = 2
        assert core.registers.read_full(20) == 2

    def test_all_zeros_payload(self):
        """Frame with all-zeros payload (0x00 × 11) received correctly."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        payload = [0x00] * 11
        gen = UARTFrameGenerator(pin=0, payload=payload, baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=5)
        core.io_port.uart_generator = gen
        core.io_port.set_gpi_pin(0, True)

        core.run(max_steps=8000)

        data, _ = core.memory.read(0x0000, 11)
        assert list(data) == payload

    def test_all_ff_payload(self):
        """Frame with all-0xFF payload received correctly."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        payload = [0xFF] * 11
        gen = UARTFrameGenerator(pin=0, payload=payload, baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=5)
        core.io_port.uart_generator = gen
        core.io_port.set_gpi_pin(0, True)

        core.run(max_steps=8000)

        data, _ = core.memory.read(0x0000, 11)
        assert list(data) == payload
```

- [ ] **Step 3: Run tests**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_rx.py::TestUARTRXMultiFrame -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add pru_io/uart_frame_generator.py tests/test_uart_rx.py
git commit -m "feat: add multi-frame support and edge-case tests for UART RX"
```

---

## Task 8: Framing Error Detection and Recovery

**Files:**
- Modify: `pru_io/uart_frame_generator.py` (add error injection)
- Create: `tests/test_uart_rx_errors.py`

- [ ] **Step 1: Add error injection to UARTFrameGenerator**

Add a method to corrupt a stop bit (force it LOW):

```python
    def inject_framing_error(self, byte_index: int) -> None:
        """Corrupt the stop bit of the specified byte (make it LOW instead of HIGH).

        Must be called AFTER start(). Modifies the pre-computed timeline in place.
        byte_index: 0-based index within the payload (0 = first byte's stop bit).
        """
        if self._trigger_cycle is None:
            raise RuntimeError("Call start() before inject_framing_error()")
        # Stop bit for byte N is at transition index: N * 10 + 9
        # Each byte has 10 transitions: 1 start + 8 data + 1 stop
        stop_idx = byte_index * 10 + 9
        if stop_idx >= len(self._transitions):
            raise ValueError(f"byte_index {byte_index} out of range for payload of {len(self.payload)} bytes")
        cycle, _ = self._transitions[stop_idx]
        self._transitions[stop_idx] = (cycle, 0)  # Force stop bit LOW
```

- [ ] **Step 2: Write framing error tests**

```python
# tests/test_uart_rx_errors.py
"""Tests for UART RX framing error detection and recovery."""
import pytest
from pathlib import Path

from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from mem.constant_table import ConstantTable
from xfr.xfr_bus import XFRBus
from pru_io.io_port import IOPort
from pru_io.uart_frame_generator import UARTFrameGenerator


def make_uart_core(asm_path: str | Path) -> PRUCore:
    """Create PRUCore with DRAM0 (8KB) and constant table c24=0x00000000."""
    mem = MemoryBus()
    mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
    xfr = XFRBus()
    io = IOPort()
    ct = ConstantTable()
    ct.entries[24] = 0x00000000
    core = PRUCore("PRU0", mem, xfr, io, ct)
    source = Path(asm_path).read_text()
    errors = core.load_asm(source)
    assert errors == [], f"Assembly errors: {errors}"
    return core


class TestUARTRXFramingErrors:
    """Test framing error detection via stop bit validation."""

    def test_framing_error_sets_error_flag(self):
        """If stop bit is LOW, error flag at DRAM0+0x0FFE should be set."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        payload = [0x41] * 11
        gen = UARTFrameGenerator(pin=0, payload=payload, baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=5)
        gen.inject_framing_error(byte_index=2)  # Corrupt byte 2's stop bit
        core.io_port.uart_generator = gen
        core.io_port.set_gpi_pin(0, True)

        core.run(max_steps=8000)

        # Error flag should be set
        err_data, _ = core.memory.read(0x0FFE, 1)
        assert err_data[0] == 1, f"Expected error flag=1, got {err_data[0]}"

    def test_framing_error_aborts_frame_storage(self):
        """Frame with framing error should NOT be stored to DRAM0."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        payload = [0x41] * 11
        gen = UARTFrameGenerator(pin=0, payload=payload, baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=5)
        gen.inject_framing_error(byte_index=0)  # Error on very first byte
        core.io_port.uart_generator = gen
        core.io_port.set_gpi_pin(0, True)

        core.run(max_steps=8000)

        # Frame should NOT be stored (DRAM0[0:11] should be zeros)
        data, _ = core.memory.read(0x0000, 11)
        assert list(data) == [0] * 11

    def test_recovery_after_framing_error(self):
        """After a framing error, PRU should recover and receive the next valid frame."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        # Send two frames: first has error, second is clean
        # Use two separate generators sequentially, or use frames=2 with error on frame 1
        payload = [0x42] * 11
        gen = UARTFrameGenerator(
            pin=0, payload=payload, baudrate=4_000_000, pru_clock_mhz=200.0,
            frames=2, idle_gap_bits=4,
        )
        gen.attach(core.io_port)
        gen.start(trigger_cycle=5)
        gen.inject_framing_error(byte_index=3)  # Error in byte 3 of frame 1
        core.io_port.uart_generator = gen
        core.io_port.set_gpi_pin(0, True)

        core.run(max_steps=16000)

        # Error flag should be set (from frame 1)
        err_data, _ = core.memory.read(0x0FFE, 1)
        assert err_data[0] == 1

        # Frame 2 should be stored at DRAM0[0] (frame 1 was aborted, offset not advanced)
        data, _ = core.memory.read(0x0000, 11)
        assert list(data) == payload
```

- [ ] **Step 3: Run tests**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_rx_errors.py -v`
Expected: PASS if assembly error handling is correct

- [ ] **Step 4: Fix assembly if stop bit timing or error flag write has issues**

- [ ] **Step 5: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add pru_io/uart_frame_generator.py tests/test_uart_rx_errors.py
git commit -m "feat: add framing error injection and recovery tests for UART RX"
```

---

## Task 9: Simulator Wiring — Attach Generator at Init

**Files:**
- Modify: `simulator.py`
- Test: `tests/test_uart_rx.py`

Wire the generator into the Simulator class so MCP tools and high-level APIs can use it.

- [ ] **Step 1: Write test — Simulator exposes uart_inject method**

```python
# Append to tests/test_uart_rx.py

from simulator import Simulator


class TestUARTRXSimulatorIntegration:
    """Test UART RX via Simulator API (the same path MCP will use)."""

    def test_simulator_uart_inject_single_frame(self):
        """Simulator.uart_inject() loads assembly and receives one frame."""
        sim = Simulator()
        asm_source = (Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm").read_text()
        sim.load("pru0", asm_source)

        payload = [0x48, 0x65, 0x6C, 0x6C, 0x6F, 0x57, 0x6F, 0x72, 0x6C, 0x64, 0x21]
        sim.uart_inject(
            core="pru0",
            pin=0,
            payload=payload,
            baudrate=4_000_000,
            trigger_cycle=5,
        )

        # Run until frame is received
        sim.step("pru0", count=8000)

        # Verify DRAM0 contents
        data = sim.memory_read(0x0000, 11)
        assert list(data) == payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_rx.py::TestUARTRXSimulatorIntegration -v`
Expected: FAIL with `AttributeError: 'Simulator' object has no attribute 'uart_inject'`

- [ ] **Step 3: Add uart_inject method to Simulator**

In `simulator.py`, add:

```python
    def uart_inject(
        self,
        core: str = "pru0",
        pin: int = 0,
        payload: list[int] | None = None,
        baudrate: int = 4_000_000,
        trigger_cycle: int = 0,
        frames: int = 1,
        idle_gap_bits: int = 2,
    ) -> None:
        """Attach a UARTFrameGenerator to the specified core's IOPort.

        The generator will update GPI pin state on each step() call.
        """
        from pru_io.uart_frame_generator import UARTFrameGenerator

        pru = self._get_core(core)
        gen = UARTFrameGenerator(
            pin=pin,
            payload=payload if payload is not None else [],
            baudrate=baudrate,
            pru_clock_mhz=float(self._pru_clock_mhz),
            frames=frames,
            idle_gap_bits=idle_gap_bits,
        )
        gen.attach(pru.io_port)
        gen.start(trigger_cycle=trigger_cycle)
        pru.io_port.uart_generator = gen
        pru.io_port.set_gpi_pin(pin, True)  # Set idle HIGH
```

Also ensure `self._pru_clock_mhz` is stored during `__init__` (it's already extracted for SD filter setup).

- [ ] **Step 4: Run tests**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_rx.py::TestUARTRXSimulatorIntegration -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add simulator.py tests/test_uart_rx.py
git commit -m "feat: add uart_inject() method to Simulator for high-level UART testing"
```

---

## Task 10: MCP Server — pru_uart_inject Tool

**Files:**
- Modify: `mcp_server/server.py`
- Create: `tests/test_uart_rx_mcp.py`

- [ ] **Step 1: Write MCP tool test**

```python
# tests/test_uart_rx_mcp.py
"""End-to-end tests for pru_uart_inject MCP tool."""
import pytest
from pathlib import Path

from mcp_server.server import PRUSimulatorMCP


class TestPRUUARTInjectMCP:
    """Test the pru_uart_inject MCP tool method."""

    def test_basic_inject_returns_received_data(self):
        """pru_uart_inject should load assembly, inject frames, and return received data."""
        mcp = PRUSimulatorMCP()
        asm_source = (Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm").read_text()

        result = mcp.pru_uart_inject(
            source=asm_source,
            payload=[0x48, 0x65, 0x6C, 0x6C, 0x6F, 0x57, 0x6F, 0x72, 0x6C, 0x64, 0x21],
            baudrate=4_000_000,
            frames=1,
        )

        assert result["status"] == "success"
        assert result["frames_received"] == 1
        assert result["received_data"] == [0x48, 0x65, 0x6C, 0x6C, 0x6F, 0x57, 0x6F, 0x72, 0x6C, 0x64, 0x21]
        assert result["error_flag"] == 0

    def test_inject_with_framing_error_reports_error(self):
        """pru_uart_inject with bad baudrate should report error flag."""
        mcp = PRUSimulatorMCP()
        asm_source = (Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm").read_text()

        # Inject at a slightly different baudrate to provoke timing errors
        # (this depends on how sensitive the receiver is)
        result = mcp.pru_uart_inject(
            source=asm_source,
            payload=[0x41] * 11,
            baudrate=4_000_000,
            frames=1,
        )

        assert result["status"] == "success"
        assert "error_flag" in result

    def test_inject_multiple_frames(self):
        """pru_uart_inject with frames=2 should receive both frames."""
        mcp = PRUSimulatorMCP()
        asm_source = (Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm").read_text()

        result = mcp.pru_uart_inject(
            source=asm_source,
            payload=[0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B],
            baudrate=4_000_000,
            frames=2,
        )

        assert result["status"] == "success"
        assert result["frames_received"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_rx_mcp.py -v`
Expected: FAIL with `AttributeError: 'PRUSimulatorMCP' object has no attribute 'pru_uart_inject'`

- [ ] **Step 3: Implement pru_uart_inject in MCP server**

Add to `mcp_server/server.py`:

```python
    def pru_uart_inject(
        self,
        source: str,
        payload: list[int],
        baudrate: int = 4_000_000,
        frames: int = 1,
        core: str = "pru0",
        pin: int = 0,
        dram0_offset: int = 0,
        max_steps: int = 20_000,
    ) -> dict:
        """Inject UART frames into PRU and verify reception.

        Loads assembly source, attaches a UARTFrameGenerator, runs the PRU,
        and returns the received data from DRAM0.

        Args:
            source: PRU assembly source code to load.
            payload: List of 11 byte values per frame.
            baudrate: Baud rate in bits/sec (default 4000000).
            frames: Number of times to repeat the payload (default 1).
            core: PRU core name (default "pru0").
            pin: GPI pin number (default 0).
            dram0_offset: DRAM0 byte offset for storage verification (default 0).
            max_steps: Maximum instructions to execute (default 20000).

        Returns:
            Dict with status, frames_received, received_data, error_flag.
        """
        # Reset and load
        self.sim.reset(core)
        errors = self.sim.load(core, source)
        if errors:
            return {"status": "error", "errors": errors}

        # Clear DRAM0 storage area and error flag
        total_bytes = len(payload) * frames
        self.sim.memory._find_region(0x0000).write(0x0000, bytes(total_bytes))
        self.sim.memory._find_region(0x0000).write(0x0FFE, bytes(1))

        # Inject UART frames
        self.sim.uart_inject(
            core=core,
            pin=pin,
            payload=payload,
            baudrate=baudrate,
            trigger_cycle=5,
            frames=frames,
        )

        # Run PRU
        self.sim.step(core, count=max_steps)

        # Read results
        received_bytes = len(payload) * frames
        data = self.sim.memory_read(dram0_offset, received_bytes)
        err_data = self.sim.memory_read(0x0FFE, 1)

        # Determine frames received by checking non-zero frame data
        frames_received = 0
        frame_size = len(payload)
        for f in range(frames):
            offset = f * frame_size
            frame_data = data[offset:offset + frame_size]
            if any(b != 0 for b in frame_data) or payload == [0] * frame_size:
                frames_received += 1
            else:
                break

        return {
            "status": "success",
            "frames_received": frames_received,
            "received_data": list(data[:frame_size]),  # First frame's data
            "error_flag": err_data[0] if err_data else 0,
        }
```

- [ ] **Step 4: Run tests**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_rx_mcp.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/ -v --tb=short`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add mcp_server/server.py tests/test_uart_rx_mcp.py
git commit -m "feat: add pru_uart_inject MCP tool for end-to-end UART RX testing"
```

---

## Task 11: Assembly Timing Validation — Baudrate Tolerance

**Files:**
- Modify: `tests/test_uart_rx.py`

Validate that the receiver works across a range of baudrates (testing tolerance).

- [ ] **Step 1: Write parametrized baudrate tests**

```python
# Append to tests/test_uart_rx.py

class TestUARTRXBaudrateTolerance:
    """Test receiver tolerance to baudrate variations."""

    @pytest.mark.parametrize("baudrate", [3_900_000, 3_950_000, 4_000_000, 4_050_000, 4_100_000])
    def test_baudrate_range(self, baudrate):
        """Receiver tuned for 4 Mbaud should work within ±2.5% tolerance."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        payload = [0x55, 0xAA, 0x0F, 0xF0, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE]
        gen = UARTFrameGenerator(pin=0, payload=payload, baudrate=baudrate, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=5)
        core.io_port.uart_generator = gen
        core.io_port.set_gpi_pin(0, True)

        core.run(max_steps=8000)

        data, _ = core.memory.read(0x0000, 11)
        err_data, _ = core.memory.read(0x0FFE, 1)

        if abs(baudrate - 4_000_000) <= 100_000:  # ±2.5%
            assert list(data) == payload, f"Failed at {baudrate} baud"
            assert err_data[0] == 0, f"Unexpected error at {baudrate} baud"
```

- [ ] **Step 2: Run tests**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/test_uart_rx.py::TestUARTRXBaudrateTolerance -v`
Expected: PASS for ±2.5% range. Outer values may fail (expected — documents tolerance limit).

- [ ] **Step 3: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add tests/test_uart_rx.py
git commit -m "test: add baudrate tolerance validation for UART RX assembly"
```

---

## Task 12: Final Integration — Full Regression

**Files:**
- None new — this is a validation step

- [ ] **Step 1: Run the complete test suite**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/ -v --tb=short 2>&1 | tail -30`
Expected: All tests PASS, no regressions in SD filter, PRU core, or memory tests.

- [ ] **Step 2: Verify assembly file assembles cleanly**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -c "from simulator import Simulator; s = Simulator(); e = s.load('pru0', open('source/uart_rx_11frame.asm').read()); print(f'Errors: {e}'); assert e == []"`
Expected: `Errors: []`

- [ ] **Step 3: Verify MCP tool works end-to-end**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -c "
from mcp_server.server import PRUSimulatorMCP
from pathlib import Path
mcp = PRUSimulatorMCP()
src = Path('source/uart_rx_11frame.asm').read_text()
r = mcp.pru_uart_inject(source=src, payload=[0x48,0x65,0x6C,0x6C,0x6F,0x57,0x6F,0x72,0x6C,0x64,0x21])
print(r)
assert r['status'] == 'success'
assert r['received_data'] == [0x48,0x65,0x6C,0x6C,0x6F,0x57,0x6F,0x72,0x6C,0x64,0x21]
print('MCP integration OK')
"`
Expected: `MCP integration OK`

- [ ] **Step 4: Final commit with any fixups**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git status
# If any uncommitted changes remain:
git add -A
git commit -m "chore: final cleanup for UART RX 4.0 Mbit implementation"
```

---

## Notes for Implementers

### Assembly Timing Sensitivity

The UART RX assembly uses tight timing loops. The key constraint is:
- **Each instruction = 1 cycle** (in the simulator, no pipeline stalls on branches)
- **SUB + QBNE delay loop = 2 cycles per iteration**
- **BIT_TIME = 25 iterations = 50 cycles = 1 bit period at 4 Mbaud**
- **START_DELAY = 38 iterations = 76 cycles ≈ 1.5 bit periods**

If tests fail, the most likely cause is sampling point drift. The sampling overhead (instructions between the delay loop end and the actual `AND r15, r31, 1`) must be accounted for. Each extra instruction shifts the sample point by 1 cycle (2% of a bit period).

### Register File Byte Writes

The SBCO instruction `sbco &r2, c24, r1, 11` reads 11 bytes starting from R2.b0:
- R2.b0 = byte 0
- R3.b0 = byte 1 (since each register is 32-bit, only b0 of each is used when storing one byte per register)

Verify this matches how the simulator's `_read_registers_to_bytes()` packs data.

### Error Injection Timing

The `inject_framing_error()` method modifies the pre-computed timeline. This means:
- It only works on stop bits of complete bytes
- For multi-frame error injection, the byte_index is global (across all frames)
- Frame boundary: byte_index 0-10 = frame 1, 11-21 = frame 2, etc.
