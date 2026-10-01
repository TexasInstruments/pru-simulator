# MAC/MPY Broadside Accelerator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an extensible broadside accelerator registry to PRUCore and implement the MPY/MAC accelerator (device_id=0), so XIN/XOUT 0 instructions execute correctly and a MAC mode indicator appears in the UI.

**Architecture:** An `Accelerator` ABC defines the interface; `MACAccelerator` implements it using a direct `RegisterFile` reference for auto-sampling R28/R29. `PRUCore` holds `self.accelerators: dict[int, Accelerator]` and checks it before SPAD in all three XFR handlers. The server's `_send_state` adds a `mac` key; the UI renders it as a one-line indicator below the carry row.

**Tech Stack:** Python, pytest, FastAPI WebSocket, vanilla JS

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `xfr/accelerator.py` | Create | `Accelerator` ABC — interface contract for all broadside accelerators |
| `xfr/mac_accelerator.py` | Create | `MACAccelerator(Accelerator)` — device_id=0, MPY/MAC logic |
| `tests/test_mac_accelerator.py` | Create | Unit tests for MACAccelerator in isolation (no PRUCore) |
| `core/pru_core.py` | Modify | Add `self.accelerators` dict; route device_id in accelerators in XIN/XOUT/XCHG; reset in `reset()` |
| `tests/test_integration.py` | Modify | Add integration tests: multiply-only program and MAC dot-product program |
| `ui/server.py` | Modify | Add `mac` key to `_send_state` payload |
| `ui/static/index.html` | Modify | Add `#mac-row` indicator below `#carry-row` |
| `ui/static/app.js` | Modify | Update MAC indicator from incoming state |

---

## Task 1: Accelerator ABC

**Files:**
- Create: `xfr/accelerator.py`

- [ ] **Step 1: Create `xfr/accelerator.py`**

```python
"""Abstract base class for PRU ICSSG broadside accelerators.

Each accelerator has a unique XFR device ID and is accessed via
XIN, XOUT, and XCHG instructions from PRUCore.
"""

from abc import ABC, abstractmethod


class Accelerator(ABC):
    """Interface all broadside accelerators must implement."""

    DEVICE_ID: int  # Override in each subclass

    @abstractmethod
    def xout(self, start_reg: int, data: bytes) -> None:
        """Handle an XOUT instruction targeting this accelerator.

        Args:
            start_reg: The PRU register index the XOUT started from.
            data: The raw bytes read from PRU registers (length as specified in instruction).
        """

    @abstractmethod
    def xin(self, start_reg: int, length: int) -> bytes:
        """Handle an XIN instruction targeting this accelerator.

        Args:
            start_reg: The PRU register index to write into.
            length: Number of bytes requested.

        Returns:
            Exactly `length` bytes to write into PRU registers starting at start_reg.
        """

    @abstractmethod
    def xchg(self, start_reg: int, data: bytes) -> bytes:
        """Handle an XCHG instruction targeting this accelerator.

        Args:
            start_reg: Starting PRU register index.
            data: Bytes from PRU registers (same as xout).

        Returns:
            Bytes to write back into PRU registers (same as xin).
        """

    @abstractmethod
    def reset(self) -> None:
        """Reset accelerator state to power-on defaults."""
```

- [ ] **Step 2: Commit**

```bash
cd /path/to/pru_simulator
git add xfr/accelerator.py
git commit -m "feat: add Accelerator ABC for broadside accelerator registry"
```

---

## Task 2: MACAccelerator — unit tests then implementation

**Files:**
- Create: `tests/test_mac_accelerator.py`
- Create: `xfr/mac_accelerator.py`

- [ ] **Step 1: Write failing unit tests**

Create `tests/test_mac_accelerator.py`:

```python
"""Unit tests for MACAccelerator (device_id=0, MPY/MAC broadside accelerator)."""

import struct
import pytest

from core.registers import RegisterFile
from xfr.mac_accelerator import MACAccelerator


@pytest.fixture
def mac():
    """Fresh MACAccelerator wired to a fresh RegisterFile (R28=R29=0)."""
    regs = RegisterFile()
    return MACAccelerator(regs), regs


# ---------------------------------------------------------------------------
# Multiply-only mode (default, mac_mode=False)
# ---------------------------------------------------------------------------

class TestMultiplyOnly:
    def test_basic_product(self, mac):
        m, regs = mac
        regs.write_full(28, 50)
        regs.write_full(29, 25)
        result = m.xin(26, 8)
        low  = struct.unpack_from("<I", result, 0)[0]
        high = struct.unpack_from("<I", result, 4)[0]
        assert low == 1250
        assert high == 0

    def test_large_product_64bit_split(self, mac):
        m, regs = mac
        regs.write_full(28, 0xFFFFFFFF)
        regs.write_full(29, 0xFFFFFFFF)
        product = 0xFFFFFFFF * 0xFFFFFFFF  # = 0xFFFFFFFE_00000001
        result = m.xin(26, 8)
        low  = struct.unpack_from("<I", result, 0)[0]
        high = struct.unpack_from("<I", result, 4)[0]
        assert low  == product & 0xFFFFFFFF          # 0x00000001
        assert high == (product >> 32) & 0xFFFFFFFF  # 0xFFFFFFFE

    def test_xin_r27_returns_high_word(self, mac):
        m, regs = mac
        regs.write_full(28, 0xFFFFFFFF)
        regs.write_full(29, 0xFFFFFFFF)
        product = 0xFFFFFFFF * 0xFFFFFFFF
        result = m.xin(27, 4)
        high = struct.unpack_from("<I", result, 0)[0]
        assert high == (product >> 32) & 0xFFFFFFFF

    def test_xin_r26_length_4(self, mac):
        """XIN &R26, 4 returns only the low 4 bytes."""
        m, regs = mac
        regs.write_full(28, 7)
        regs.write_full(29, 6)
        result = m.xin(26, 4)
        assert len(result) == 4
        low = struct.unpack_from("<I", result, 0)[0]
        assert low == 42

    def test_mode_is_multiply_only_by_default(self, mac):
        m, _ = mac
        assert m.mac_mode is False

    def test_zero_operands(self, mac):
        m, regs = mac
        # R28=R29=0 by default
        result = m.xin(26, 8)
        assert result == bytes(8)


# ---------------------------------------------------------------------------
# xout R25 — mode control
# ---------------------------------------------------------------------------

class TestXoutControl:
    def test_xout_bit0_sets_mac_mode(self, mac):
        m, _ = mac
        m.xout(25, bytes([0x01]))
        assert m.mac_mode is True

    def test_xout_bit0_clear_disables_mac_and_clears_accumulator(self, mac):
        m, regs = mac
        regs.write_full(28, 10)
        regs.write_full(29, 10)
        m.xout(25, bytes([0x01]))          # enable + accumulate 100
        assert m._accumulator == 100
        m.xout(25, bytes([0x00]))          # disable → clears accumulator
        assert m._accumulator == 0
        assert m.mac_mode is False

    def test_xout_bit1_clears_acc_carry(self, mac):
        """Writing bit 1=1 to R25 clears ACC_CARRY."""
        m, regs = mac
        # Seed near max and overflow
        near_max = 0xFFFFFFFF_FFFFFFFE
        m.xout(26, (near_max & 0xFFFFFFFF).to_bytes(4, 'little') +
                   ((near_max >> 32) & 0xFFFFFFFF).to_bytes(4, 'little'))
        regs.write_full(28, 1)
        regs.write_full(29, 2)
        m.xout(25, bytes([0x01]))          # overflow → acc_carry=True
        assert m.acc_carry is True
        m.xout(25, bytes([0x03]))          # bit1=1 → clear carry; bit0=1 → stay MAC
        assert m.acc_carry is False
        assert m.mac_mode is True          # mac_mode preserved


# ---------------------------------------------------------------------------
# Multiply-and-accumulate mode
# ---------------------------------------------------------------------------

class TestMACMode:
    def test_each_xout_r25_triggers_accumulation(self, mac):
        m, regs = mac
        m.xout(25, bytes([0x01]))           # enable; acc += 0*0 = 0
        regs.write_full(28, 3)
        regs.write_full(29, 4)
        m.xout(25, bytes([0x01]))           # acc += 12
        regs.write_full(28, 5)
        regs.write_full(29, 6)
        m.xout(25, bytes([0x01]))           # acc += 30; total=42
        result = m.xin(26, 8)
        low  = struct.unpack_from("<I", result, 0)[0]
        high = struct.unpack_from("<I", result, 4)[0]
        assert low == 42
        assert high == 0

    def test_dot_product_1_2_3_dot_4_5_6(self, mac):
        """(1,2,3)·(4,5,6) = 4+10+18 = 32."""
        m, regs = mac
        m.xout(25, bytes([0x01]))           # enable; acc += 0
        for a, b in [(1, 4), (2, 5), (3, 6)]:
            regs.write_full(28, a)
            regs.write_full(29, b)
            m.xout(25, bytes([0x01]))
        result = m.xin(26, 8)
        low = struct.unpack_from("<I", result, 0)[0]
        assert low == 32

    def test_overflow_sets_acc_carry(self, mac):
        m, regs = mac
        near_max = 0xFFFFFFFF_FFFFFFFE
        m.xout(26, (near_max & 0xFFFFFFFF).to_bytes(4, 'little') +
                   ((near_max >> 32) & 0xFFFFFFFF).to_bytes(4, 'little'))
        regs.write_full(28, 1)
        regs.write_full(29, 2)              # product=2; near_max+2 > 64-bit max
        m.xout(25, bytes([0x01]))
        assert m.acc_carry is True

    def test_accumulator_wraps_on_overflow(self, mac):
        m, regs = mac
        near_max = 0xFFFFFFFF_FFFFFFFF
        m.xout(26, (near_max & 0xFFFFFFFF).to_bytes(4, 'little') +
                   ((near_max >> 32) & 0xFFFFFFFF).to_bytes(4, 'little'))
        regs.write_full(28, 1)
        regs.write_full(29, 1)              # product=1; max+1 wraps to 0
        m.xout(25, bytes([0x01]))
        assert m._accumulator == 0


# ---------------------------------------------------------------------------
# Seed accumulator via XOUT R26:R27
# ---------------------------------------------------------------------------

class TestSeedAccumulator:
    def test_seed_low_and_high(self, mac):
        m, regs = mac
        low_seed  = (0xDEADBEEF).to_bytes(4, 'little')
        high_seed = (0x00000001).to_bytes(4, 'little')
        m.xout(26, low_seed + high_seed)
        assert m._accumulator == 0x1_DEADBEEF

    def test_seed_clears_acc_carry(self, mac):
        m, regs = mac
        near_max = 0xFFFFFFFF_FFFFFFFE
        m.xout(26, (near_max & 0xFFFFFFFF).to_bytes(4, 'little') +
                   ((near_max >> 32) & 0xFFFFFFFF).to_bytes(4, 'little'))
        regs.write_full(28, 1)
        regs.write_full(29, 2)
        m.xout(25, bytes([0x01]))           # trigger overflow → carry=True
        assert m.acc_carry is True
        m.xout(26, bytes(8))               # seed with 0 → carry cleared
        assert m.acc_carry is False


# ---------------------------------------------------------------------------
# XIN R25 — status readback
# ---------------------------------------------------------------------------

class TestXinStatus:
    def test_initial_status_is_zero(self, mac):
        m, _ = mac
        status = m.xin(25, 1)
        assert status[0] == 0x00

    def test_status_reflects_mac_mode(self, mac):
        m, _ = mac
        m.xout(25, bytes([0x01]))
        status = m.xin(25, 1)
        assert status[0] & 0x01            # bit0 set

    def test_status_reflects_acc_carry(self, mac):
        m, regs = mac
        near_max = 0xFFFFFFFF_FFFFFFFE
        m.xout(26, (near_max & 0xFFFFFFFF).to_bytes(4, 'little') +
                   ((near_max >> 32) & 0xFFFFFFFF).to_bytes(4, 'little'))
        regs.write_full(28, 1)
        regs.write_full(29, 2)
        m.xout(25, bytes([0x01]))
        status = m.xin(25, 1)
        assert status[0] & 0x02            # bit1 set

    def test_xin_status_length_respected(self, mac):
        m, _ = mac
        result = m.xin(25, 4)
        assert len(result) == 4
        assert result[1:] == bytes(3)      # padding zeros


# ---------------------------------------------------------------------------
# Unknown start_reg and xchg
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_xin_unknown_start_reg_returns_zeros(self, mac):
        m, _ = mac
        assert m.xin(10, 4) == bytes(4)

    def test_xchg_returns_zeros(self, mac):
        m, regs = mac
        regs.write_full(28, 5)
        regs.write_full(29, 5)
        result = m.xchg(26, bytes(8))
        assert result == bytes(8)

    def test_xout_short_data_ignored_for_seed(self, mac):
        """XOUT R26 with fewer than 8 bytes does not trigger seeding."""
        m, _ = mac
        m._accumulator = 99
        m.xout(26, bytes(4))               # only 4 bytes — no seed
        assert m._accumulator == 99


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_clears_all_state(self, mac):
        m, regs = mac
        regs.write_full(28, 10)
        regs.write_full(29, 10)
        m.xout(25, bytes([0x01]))          # accumulate 100
        m.reset()
        assert m.mac_mode is False
        assert m._accumulator == 0
        assert m.acc_carry is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /path/to/pru_simulator
python -m pytest tests/test_mac_accelerator.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'xfr.mac_accelerator'`

- [ ] **Step 3: Create `xfr/mac_accelerator.py`**

```python
"""PRU ICSSG MPY/MAC broadside accelerator (device_id=0).

Hardware reference: AM64x/AM243x TRM §6.4.6.2.1

Register mapping:
  R25  MAC_CTRL_STATUS  bit0=MAC_MODE  bit1=ACC_CARRY (write-1-to-clear)
  R26  Lower 32 bits of 64-bit result (XIN read / XOUT seed low word)
  R27  Upper 32 bits of 64-bit result (XIN read / XOUT seed high word)
  R28  Operand A — auto-sampled directly from PRU register file
  R29  Operand B — auto-sampled directly from PRU register file
"""

import struct

from core.registers import RegisterFile
from xfr.accelerator import Accelerator

_MASK64 = 0xFFFF_FFFF_FFFF_FFFF


class MACAccelerator(Accelerator):
    """Multiply-only and multiply-accumulate accelerator for PRU ICSSG."""

    DEVICE_ID = 0

    def __init__(self, register_file: RegisterFile) -> None:
        self._regs = register_file
        self.mac_mode: bool = False
        self._accumulator: int = 0
        self.acc_carry: bool = False

    # ------------------------------------------------------------------
    # Accelerator interface
    # ------------------------------------------------------------------

    def xout(self, start_reg: int, data: bytes) -> None:
        if start_reg == 25 and len(data) >= 1:
            ctrl = data[0]
            if ctrl & 0x01:
                # MAC_MODE=1: trigger one accumulation of R28*R29
                a = self._regs.read_full(28)
                b = self._regs.read_full(29)
                product = a * b
                self._accumulator += product
                if self._accumulator > _MASK64:
                    self._accumulator &= _MASK64
                    self.acc_carry = True
                self.mac_mode = True
            else:
                # MAC_MODE=0: clear accumulator and enter multiply-only
                self._accumulator = 0
                self.mac_mode = False
            if ctrl & 0x02:
                self.acc_carry = False

        elif start_reg == 26 and len(data) >= 8:
            # Seed the accumulator from R26:R27 bytes; clear carry
            low  = struct.unpack_from("<I", data, 0)[0]
            high = struct.unpack_from("<I", data, 4)[0]
            self._accumulator = (high << 32) | low
            self.acc_carry = False

    def xin(self, start_reg: int, length: int) -> bytes:
        if start_reg == 25:
            ctrl = (0x01 if self.mac_mode else 0) | (0x02 if self.acc_carry else 0)
            return bytes([ctrl]) + bytes(max(0, length - 1))

        if start_reg in (26, 27):
            if self.mac_mode:
                result64 = self._accumulator
            else:
                a = self._regs.read_full(28)
                b = self._regs.read_full(29)
                result64 = a * b

            low  = result64 & 0xFFFF_FFFF
            high = (result64 >> 32) & 0xFFFF_FFFF
            full8 = struct.pack("<II", low, high)

            if start_reg == 26:
                return full8[:length]
            else:  # start_reg == 27
                return full8[4:4 + length]

        return bytes(length)

    def xchg(self, start_reg: int, data: bytes) -> bytes:
        # XCHG has no meaningful semantic for MAC hardware
        return bytes(len(data))

    def reset(self) -> None:
        self.mac_mode = False
        self._accumulator = 0
        self.acc_carry = False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_mac_accelerator.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add xfr/mac_accelerator.py tests/test_mac_accelerator.py
git commit -m "feat: add MACAccelerator (device_id=0) with full unit test suite"
```

---

## Task 3: Wire accelerator registry into PRUCore

**Files:**
- Modify: `core/pru_core.py`
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Write failing integration tests**

Open `tests/test_integration.py` and append these tests at the end of the file:

```python
# ===========================================================================
# MAC accelerator integration tests
# ===========================================================================

class TestMACIntegration:
    """Run assembly programs through PRUCore using the MAC accelerator."""

    def _make_core(self):
        from mem.memory_bus import MemoryBus
        from mem.regions import MemoryRegion
        from xfr.xfr_bus import XFRBus
        from pru_io.io_port import IOPort
        from mem.constant_table import ConstantTable
        bus = MemoryBus()
        bus.add_region(MemoryRegion("DRAM0", 0x00000000, 0x2000, 2, 1, 0))
        return PRUCore("test", bus, XFRBus(), IOPort(), ConstantTable())

    def test_multiply_only_50_times_25(self):
        """Academy mac_multiply example: 50*25=1250 in R26, R27=0."""
        core = self._make_core()
        src = """
            zero  &r0, 120
            LDI   R25, 0
            XOUT  0, &R25, 1
            LDI   R28, 50
            LDI   R29, 25
            NOP
            XIN   0, &R25, 1
            XIN   0, &R26, 4
            XIN   0, &R27, 4
            HALT
        """
        errors = core.load_asm(src)
        assert errors == []
        core.run()
        assert core.registers.read_full(26) == 1250
        assert core.registers.read_full(27) == 0

    def test_mac_dot_product_1_2_3_dot_4_5_6(self):
        """Academy mac example: (1,2,3)·(4,5,6) = 32 in R26."""
        core = self._make_core()
        src = """
            zero  &r0, 120
            LDI   R10, 1
            LDI   R11, 2
            LDI   R12, 3
            LDI   R13, 4
            LDI   R14, 5
            LDI   R15, 6

            LDI   R25, 1
            XOUT  0, &R25, 1
            LDI   R25, 3
            XOUT  0, &R25, 1
            LDI   R25, 1

            MOV   R28, R10
            MOV   R29, R13
            XOUT  0, &R25, 1

            MOV   R28, R11
            MOV   R29, R14
            XOUT  0, &R25, 1

            MOV   R28, R12
            MOV   R29, R15
            XOUT  0, &R25, 1

            XIN   0, &R25, 1
            XIN   0, &R26, 4
            XIN   0, &R27, 4
            HALT
        """
        errors = core.load_asm(src)
        assert errors == []
        core.run()
        assert core.registers.read_full(26) == 32
        assert core.registers.read_full(27) == 0

    def test_mac_reset_clears_accelerator(self):
        """After core.reset(), MAC accumulator and mode are cleared."""
        core = self._make_core()
        src = """
            LDI   R25, 1
            XOUT  0, &R25, 1
            LDI   R28, 100
            LDI   R29, 100
            XOUT  0, &R25, 1
            HALT
        """
        core.load_asm(src)
        core.run()
        assert core.accelerators[0].mac_mode is True
        assert core.accelerators[0]._accumulator > 0
        core.reset()
        assert core.accelerators[0].mac_mode is False
        assert core.accelerators[0]._accumulator == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_integration.py::TestMACIntegration -v
```

Expected: `AttributeError: 'PRUCore' object has no attribute 'accelerators'`

- [ ] **Step 3: Add accelerator imports and registry to `core/pru_core.py`**

Add these two imports after the existing imports at the top of `core/pru_core.py` (after the `from pru_io.io_port import IOPort` line):

```python
from xfr.accelerator import Accelerator
from xfr.mac_accelerator import MACAccelerator
```

In `PRUCore.__init__`, add after `self._branch = BranchUnit()`:

```python
        self.accelerators: dict[int, Accelerator] = {
            MACAccelerator.DEVICE_ID: MACAccelerator(self.registers),
        }
```

- [ ] **Step 4: Add accelerator reset to `PRUCore.reset()`**

The current `reset()` method ends with `self.loop_state = None`. Add one line after it:

Find in `core/pru_core.py`:
```python
    def reset(self) -> None:
        """Reset all state to initial conditions."""
        self.registers = RegisterFile()
        self.counters.reset()
        self.pc = 0
        self.halted = False
        self.loop_state = None
```

Replace with:
```python
    def reset(self) -> None:
        """Reset all state to initial conditions."""
        self.registers = RegisterFile()
        self.counters.reset()
        self.pc = 0
        self.halted = False
        self.loop_state = None
        for acc in self.accelerators.values():
            acc.reset()
```

- [ ] **Step 5: Replace the XIN handler in `PRUCore.step()`**

Find in `core/pru_core.py`:
```python
        elif op == "XIN":
            # XIN device_id, &reg, length
            device_id_op, reg_op, length_op = instr.operands
            device_id = self._read_operand(device_id_op)
            length = self._read_operand(length_op)
            start_reg = reg_op.index if isinstance(reg_op, Register) else 0
            if self.xfr.xfr_shift_en and device_id in (SPAD_BANK0, SPAD_BANK1, SPAD_BANK2):
                self._xin_shifted(device_id, start_reg, length)
            else:
                xfr_offset = (start_reg - 2) * 4 if device_id == IPC_SPAD else start_reg * 4
                data = self.xfr.xin(device_id, xfr_offset, length)
                self._write_registers_from_bytes(start_reg, data)
```

Replace with:
```python
        elif op == "XIN":
            # XIN device_id, &reg, length
            device_id_op, reg_op, length_op = instr.operands
            device_id = self._read_operand(device_id_op)
            length = self._read_operand(length_op)
            start_reg = reg_op.index if isinstance(reg_op, Register) else 0
            if device_id in self.accelerators:
                data = self.accelerators[device_id].xin(start_reg, length)
                self._write_registers_from_bytes(start_reg, data)
            elif self.xfr.xfr_shift_en and device_id in (SPAD_BANK0, SPAD_BANK1, SPAD_BANK2):
                self._xin_shifted(device_id, start_reg, length)
            else:
                xfr_offset = (start_reg - 2) * 4 if device_id == IPC_SPAD else start_reg * 4
                data = self.xfr.xin(device_id, xfr_offset, length)
                self._write_registers_from_bytes(start_reg, data)
```

- [ ] **Step 6: Replace the XOUT handler in `PRUCore.step()`**

Find in `core/pru_core.py`:
```python
        elif op == "XOUT":
            # XOUT device_id, &reg, length
            device_id_op, reg_op, length_op = instr.operands
            device_id = self._read_operand(device_id_op)
            length = self._read_operand(length_op)
            start_reg = reg_op.index if isinstance(reg_op, Register) else 0
            if self.xfr.xfr_shift_en and device_id in (SPAD_BANK0, SPAD_BANK1, SPAD_BANK2):
                self._xout_shifted(device_id, start_reg, length)
            else:
                xfr_offset = (start_reg - 2) * 4 if device_id == IPC_SPAD else start_reg * 4
                data = self._read_registers_to_bytes(start_reg, length)
                self.xfr.xout(device_id, xfr_offset, data)
```

Replace with:
```python
        elif op == "XOUT":
            # XOUT device_id, &reg, length
            device_id_op, reg_op, length_op = instr.operands
            device_id = self._read_operand(device_id_op)
            length = self._read_operand(length_op)
            start_reg = reg_op.index if isinstance(reg_op, Register) else 0
            if device_id in self.accelerators:
                data = self._read_registers_to_bytes(start_reg, length)
                self.accelerators[device_id].xout(start_reg, data)
            elif self.xfr.xfr_shift_en and device_id in (SPAD_BANK0, SPAD_BANK1, SPAD_BANK2):
                self._xout_shifted(device_id, start_reg, length)
            else:
                xfr_offset = (start_reg - 2) * 4 if device_id == IPC_SPAD else start_reg * 4
                data = self._read_registers_to_bytes(start_reg, length)
                self.xfr.xout(device_id, xfr_offset, data)
```

- [ ] **Step 7: Replace the XCHG handler in `PRUCore.step()`**

Find in `core/pru_core.py`:
```python
        elif op == "XCHG":
            # XCHG device_id, &reg, length
            device_id_op, reg_op, length_op = instr.operands
            device_id = self._read_operand(device_id_op)
            length = self._read_operand(length_op)
            start_reg = reg_op.index if isinstance(reg_op, Register) else 0
            if self.xfr.xfr_shift_en and device_id in (SPAD_BANK0, SPAD_BANK1, SPAD_BANK2):
                self._xchg_shifted(device_id, start_reg, length)
            else:
                xfr_offset = (start_reg - 2) * 4 if device_id == IPC_SPAD else start_reg * 4
                data = self._read_registers_to_bytes(start_reg, length)
                old_data = self.xfr.xchg(device_id, xfr_offset, data)
                self._write_registers_from_bytes(start_reg, old_data)
```

Replace with:
```python
        elif op == "XCHG":
            # XCHG device_id, &reg, length
            device_id_op, reg_op, length_op = instr.operands
            device_id = self._read_operand(device_id_op)
            length = self._read_operand(length_op)
            start_reg = reg_op.index if isinstance(reg_op, Register) else 0
            if device_id in self.accelerators:
                data = self._read_registers_to_bytes(start_reg, length)
                old_data = self.accelerators[device_id].xchg(start_reg, data)
                self._write_registers_from_bytes(start_reg, old_data)
            elif self.xfr.xfr_shift_en and device_id in (SPAD_BANK0, SPAD_BANK1, SPAD_BANK2):
                self._xchg_shifted(device_id, start_reg, length)
            else:
                xfr_offset = (start_reg - 2) * 4 if device_id == IPC_SPAD else start_reg * 4
                data = self._read_registers_to_bytes(start_reg, length)
                old_data = self.xfr.xchg(device_id, xfr_offset, data)
                self._write_registers_from_bytes(start_reg, old_data)
```

- [ ] **Step 8: Run integration tests**

```bash
python -m pytest tests/test_integration.py::TestMACIntegration -v
```

Expected: all 3 tests pass.

- [ ] **Step 9: Run full test suite**

```bash
python -m pytest --tb=short -q
```

Expected: `388 passed` (all existing) + 3 new MAC integration tests = 391 passed.

- [ ] **Step 10: Commit**

```bash
git add core/pru_core.py tests/test_integration.py
git commit -m "feat: wire accelerator registry into PRUCore; route XIN/XOUT/XCHG device_id=0 to MAC"
```

---

## Task 4: UI — MAC mode indicator

**Files:**
- Modify: `ui/server.py`
- Modify: `ui/static/index.html`
- Modify: `ui/static/app.js`

- [ ] **Step 1: Add `mac` key to `_send_state` in `ui/server.py`**

Find in `ui/server.py`:
```python
        "xfr_shift_en": sim.xfr.xfr_shift_en,
    }
    await ws.send_json(state)
```

Replace with:
```python
        "xfr_shift_en": sim.xfr.xfr_shift_en,
        "mac": _read_mac(c),
    }
    await ws.send_json(state)
```

Then add the helper function immediately above `_send_state` (after `_read_spad`):

```python
def _read_mac(core) -> dict:
    """Return MAC accelerator status for the given PRUCore."""
    acc = core.accelerators.get(0)
    if acc is None:
        return {"mode": False, "acc_carry": False}
    return {"mode": acc.mac_mode, "acc_carry": acc.acc_carry}
```

- [ ] **Step 2: Add MAC status row to `ui/static/index.html`**

Find in `ui/static/index.html`:
```html
      <div id="carry-row">Carry: <span class="val" id="reg-carry">0</span></div>
```

Replace with:
```html
      <div id="carry-row">Carry: <span class="val" id="reg-carry">0</span></div>
      <div id="mac-row">MAC: <span id="mac-mode-label">MPY</span><span id="mac-carry-label"></span></div>
```

Also add a CSS rule for `#mac-row`. Find the `#carry-row` CSS block in `index.html`:

```css
    #carry-row {
```

Add immediately before that line:

```css
    #mac-row {
      font-size: 11px;
      color: var(--text-dim);
      padding: 1px 4px 2px;
    }
    #mac-row #mac-mode-label { color: var(--text); font-weight: bold; margin: 0 4px; }
    #mac-row #mac-carry-label { color: var(--changed); font-weight: bold; margin-left: 4px; }

```

- [ ] **Step 3: Update MAC indicator in `ui/static/app.js`**

Find in `app.js` (around line 292–295, the XFR shift button state update):
```javascript
  // XFR shift button state
  if (state.xfr_shift_en !== undefined) {
    btnXfrShift.classList.toggle("active", state.xfr_shift_en);
  }
```

Add immediately after that block:
```javascript
  // MAC mode indicator
  if (state.mac) {
    document.getElementById("mac-mode-label").textContent = state.mac.mode ? "ACC" : "MPY";
    const carryEl = document.getElementById("mac-carry-label");
    carryEl.textContent = state.mac.acc_carry ? " CARRY" : "";
  }
```

- [ ] **Step 4: Verify in browser**

Start the server:
```bash
cd /path/to/pru_simulator
python ui/server.py
```

Open `http://localhost:8080`. Verify:
- MAC indicator shows `MAC: MPY` by default (dim text, no CARRY)
- Load and run `academy/mac_multiply` or paste the multiply-only assembly from Task 3; after HALT, R26=1250
- Load and run the dot-product assembly from Task 3; after HALT, MAC row shows `MAC: ACC`

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest --tb=short -q
```

Expected: 391 passed (no regressions).

- [ ] **Step 6: Commit**

```bash
git add ui/server.py ui/static/index.html ui/static/app.js
git commit -m "feat: add MAC mode indicator to dashboard UI"
```

---

## Self-Review

**Spec coverage:**
- ✅ `Accelerator` ABC (`xfr/accelerator.py`): Task 1
- ✅ `MACAccelerator` device_id=0, R25/R26/R27 xin/xout/seed: Task 2
- ✅ Multiply-only mode (MAC_MODE=0): Task 2 `TestMultiplyOnly`
- ✅ MAC mode accumulate on each XOUT R25 when bit0=1: Task 2 `TestMACMode`
- ✅ ACC_CARRY set on overflow, cleared via bit1: Task 2 `TestXoutControl`
- ✅ Seed accumulator via XOUT R26:R27: Task 2 `TestSeedAccumulator`
- ✅ XIN R25 status readback: Task 2 `TestXinStatus`
- ✅ PRUCore registry + reset: Task 3 Steps 3–4
- ✅ XIN/XOUT/XCHG routing: Task 3 Steps 5–7
- ✅ Integration tests (academy programs): Task 3 Step 1
- ✅ `_send_state` mac key: Task 4 Step 1
- ✅ UI indicator: Task 4 Steps 2–3

**Placeholder scan:** None found.

**Type consistency:**
- `MACAccelerator.DEVICE_ID` (int, =0) used in `PRUCore.__init__` as dict key — matches `device_id in self.accelerators` check
- `_read_mac(core)` defined Task 4 Step 1, called in `_send_state` — same function name
- `acc.mac_mode`, `acc.acc_carry`, `acc._accumulator` — all defined in Task 2 Step 3, read in Task 3 integration tests and `_read_mac`
- `core.accelerators.get(0)` in `_read_mac` — `.get()` handles absent key, returns None safely
