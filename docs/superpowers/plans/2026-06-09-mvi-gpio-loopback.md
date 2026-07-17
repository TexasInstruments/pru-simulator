# MVI GPIO Loopback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GPIO loopback control (UI toggle strip, backend mask, combinatorial loopback in io_port), fix MVIB R31 reads to go through io_port, and add a walking-bit firmware example.

**Architecture:** `IOPort` gains a `loopback_mask` (5 groups × 4 bits each) applied combinatorially in `write_r30()`; `pru_core._mvi_read_direct` is patched to call `io_port.read_r31()` for register 31; a WebSocket action `set_loopback` propagates the toggle state; the IO panel renders a compact strip between GPO and GPI rows.

**Tech Stack:** Python (pytest), FastAPI/WebSockets, Vanilla JS, PRU assembly (pru_simulator parser)

---

### Task 1: IOPort loopback — tests

**Files:**
- Create: `tests/test_io_loopback.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_io_loopback.py
"""Tests for IOPort GPIO loopback: set_loopback_group() and write_r30() with mask."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from pru_io.io_port import IOPort


class TestSetLoopbackGroup:
    def test_group0_sets_low_nibble(self):
        io = IOPort()
        io.set_loopback_group(0, True)
        assert io.loopback_mask == 0xF

    def test_group1_sets_second_nibble(self):
        io = IOPort()
        io.set_loopback_group(1, True)
        assert io.loopback_mask == 0xF0

    def test_group4_sets_bits_19_to_16(self):
        io = IOPort()
        io.set_loopback_group(4, True)
        assert io.loopback_mask == 0xF0000

    def test_multiple_groups_accumulate(self):
        io = IOPort()
        io.set_loopback_group(0, True)
        io.set_loopback_group(2, True)
        assert io.loopback_mask == 0xF0F

    def test_disable_clears_bits(self):
        io = IOPort()
        io.set_loopback_group(0, True)
        io.set_loopback_group(0, False)
        assert io.loopback_mask == 0x0

    def test_disable_does_not_affect_other_groups(self):
        io = IOPort()
        io.set_loopback_group(0, True)
        io.set_loopback_group(1, True)
        io.set_loopback_group(0, False)
        assert io.loopback_mask == 0xF0


class TestLoopbackApplied:
    def test_write_r30_propagates_gpo_to_gpi_for_looped_group(self):
        io = IOPort()
        io.set_loopback_group(0, True)   # bits 3:0
        io.write_r30(0x5)
        assert io.gpi & 0xF == 0x5

    def test_write_r30_does_not_clobber_nonlooped_gpi_bits(self):
        io = IOPort()
        io.set_gpi_pin(7, True)          # set bit 7 in GPI manually
        io.set_loopback_group(0, True)   # only loopback bits 3:0
        io.write_r30(0x3)
        assert io.gpi & 0x80 == 0x80    # bit 7 unchanged
        assert io.gpi & 0x0F == 0x3     # bits 3:0 looped

    def test_no_loopback_mask_gpi_unchanged(self):
        io = IOPort()
        io.gpi = 0
        io.write_r30(0xFF)
        assert io.gpi == 0              # no groups enabled → GPI untouched

    def test_loopback_not_applied_when_sd_en_set(self):
        """Loopback must not fire when R30 bit 25 (sd_en) is set."""
        io = IOPort()
        io.set_loopback_group(0, True)
        io.write_r30((1 << 25) | 0x7)   # sd_en=1, GPO low bits=7
        assert io.gpi == 0              # sd_en → no loopback

    def test_loopback_partial_mask(self):
        """Only the nibble with loopback enabled echoes GPO."""
        io = IOPort()
        io.set_loopback_group(1, True)  # bits 7:4
        io.write_r30(0xAB)
        assert io.gpi & 0xF0 == 0xA0   # bits 7:4 = 0xA (from 0xAB)
        assert io.gpi & 0x0F == 0x00   # bits 3:0 not looped
```

- [ ] **Step 2: Run tests — verify they all FAIL**

```
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
pytest tests/test_io_loopback.py -v
```

Expected: `AttributeError: 'IOPort' object has no attribute 'loopback_mask'` (or similar) for all 11 tests.

---

### Task 2: IOPort loopback — implementation

**Files:**
- Modify: `pru_io/io_port.py`

- [ ] **Step 1: Add `loopback_mask` to `__init__`**

In `pru_io/io_port.py` line 21–22, change:
```python
    def __init__(self) -> None:
        self.gpo: int = 0   # driven by writes to R30
        self.gpi: int = 0   # sampled by reads from R31
        self.sd_filter: SigmaDeltaFilter | None = None
```
to:
```python
    def __init__(self) -> None:
        self.gpo: int = 0             # driven by writes to R30
        self.gpi: int = 0             # sampled by reads from R31
        self.sd_filter: SigmaDeltaFilter | None = None
        self.loopback_mask: int = 0   # which GPO bits feed back to GPI (5 groups × 4 bits)
```

- [ ] **Step 2: Add `set_loopback_group()` method**

After `write_r30()` (after line 33), add:
```python
    def set_loopback_group(self, group: int, enabled: bool) -> None:
        """Enable/disable GPO→GPI loopback for a 4-bit group (0–4).

        group 0 → bits 3:0, group 1 → bits 7:4, ..., group 4 → bits 19:16.
        """
        mask = 0xF << (group * 4)
        if enabled:
            self.loopback_mask |= mask
        else:
            self.loopback_mask &= ~mask
```

- [ ] **Step 3: Apply loopback in `write_r30()`**

Change `write_r30()` from:
```python
    def write_r30(self, value: int) -> None:
        """Set GPO state from *value*. If SD filter attached, also process R30."""
        self.gpo = value & _MASK_20
        if self.sd_filter is not None:
            self.sd_filter.process_r30(value)
```
to:
```python
    def write_r30(self, value: int) -> None:
        """Set GPO state from *value*. If SD filter attached, also process R30.

        When sd_en (R30 bit 25) is clear, applies loopback_mask: masked GPO bits
        are immediately reflected into GPI (combinatorial, matching hardware).
        """
        self.gpo = value & _MASK_20
        if self.sd_filter is not None:
            self.sd_filter.process_r30(value)
        if not (value & (1 << 25)) and self.loopback_mask:
            self.gpi = (self.gpi & ~self.loopback_mask) | (self.gpo & self.loopback_mask)
```

- [ ] **Step 4: Run tests — verify all 11 pass**

```
pytest tests/test_io_loopback.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Run full suite — verify no regressions**

```
pytest tests/ -q
```

Expected: all tests pass (657 + 11 = 668).

- [ ] **Step 6: Commit**

```bash
git add pru_io/io_port.py tests/test_io_loopback.py
git commit -m "feat(io): add GPIO loopback mask to IOPort"
```

---

### Task 3: Fix MVIB R31 reads — tests

**Files:**
- Modify: `tests/test_mvi.py` (append new class at bottom)

- [ ] **Step 1: Write the failing test**

Append at the end of `tests/test_mvi.py`:

```python
# ---------------------------------------------------------------------------
# R31 fix: _mvi_read_direct must route R31 through io_port.read_r31()
# ---------------------------------------------------------------------------

class TestMVIR31IOPort:
    def test_mvib_direct_r31_reads_gpi_not_raw_regfile(self):
        """MVIB *r1.b0++, r31.b0 must return io_port.gpi, not raw register file."""
        # r1.b0=8 → stores into R2.b0; io_port.gpi=0x42 → expect R2.b0 = 0x42
        core = make_core(
            "ldi r1.b0, 8\n"          # TX ptr → R2.b0 (byte addr 8)
            "mvib *r1.b0++, r31.b0\n" # regfile[8] = R31.b0; r1.b0++
            "halt\n"
        )
        core.io_port.gpi = 0x42       # set GPI value (NOT raw register file)
        core.run(1000)
        assert reg(core, 2) & 0xFF == 0x42

    def test_mvib_direct_r31_uses_updated_gpi(self):
        """Second consecutive MVIB reflects a GPI change between steps."""
        core = make_core(
            "ldi r1.b0, 8\n"
            "mvib *r1.b0++, r31.b0\n"
            "mvib *r1.b0++, r31.b0\n"
            "halt\n"
        )
        # Start with gpi=0x11; after first mvib set gpi=0x22
        core.io_port.gpi = 0x11
        core.step()   # ldi
        core.step()   # first mvib → R2.b0 should get 0x11
        core.io_port.gpi = 0x22
        core.step()   # second mvib → R2.b1 should get 0x22
        core.step()   # halt
        assert reg(core, 2) & 0x00FF == 0x11
        assert (reg(core, 2) >> 8) & 0xFF == 0x22
```

- [ ] **Step 2: Run tests — verify they FAIL**

```
pytest tests/test_mvi.py::TestMVIR31IOPort -v
```

Expected: FAIL — both tests read 0x00 (raw register file R31 = 0), not 0x42.

---

### Task 4: Fix `_mvi_read_direct` for R31

**Files:**
- Modify: `core/pru_core.py`

- [ ] **Step 1: Patch `_mvi_read_direct`**

Find `_mvi_read_direct` in `core/pru_core.py` (~line 679):
```python
    def _mvi_read_direct(self, op: MVIOperand, size: int) -> bytes:
        """Read *size* bytes from the direct register operand."""
        start_byte = self._MVI_SEL_BIT_OFFSET[op.sel] >> 3
        return self._read_registers_to_bytes(op.reg, size, start_byte)
```

Replace with:
```python
    def _mvi_read_direct(self, op: MVIOperand, size: int) -> bytes:
        """Read *size* bytes from the direct register operand.

        R31 reads go through io_port.read_r31() so the loopback-updated GPI
        value (and SD status in SD mode) is returned, not the stale register file.
        """
        start_byte = self._MVI_SEL_BIT_OFFSET[op.sel] >> 3
        if op.reg == 31:
            word = self.io_port.read_r31()
            return bytes((word >> ((start_byte + i) * 8)) & 0xFF for i in range(size))
        return self._read_registers_to_bytes(op.reg, size, start_byte)
```

- [ ] **Step 2: Run R31 tests — verify they pass**

```
pytest tests/test_mvi.py::TestMVIR31IOPort -v
```

Expected: 2 passed.

- [ ] **Step 3: Run full suite — verify no regressions**

```
pytest tests/ -q
```

Expected: all tests pass (668 + 2 = 670).

- [ ] **Step 4: Commit**

```bash
git add core/pru_core.py tests/test_mvi.py
git commit -m "fix(mvi): route MVIB R31 reads through io_port.read_r31()"
```

---

### Task 5: Simulator API + WebSocket — tests

**Files:**
- Modify: `tests/test_io_loopback.py` (append)

- [ ] **Step 1: Write failing tests for `Simulator.set_loopback`**

Append at the end of `tests/test_io_loopback.py`:

```python
# ---------------------------------------------------------------------------
# Simulator.set_loopback integration
# ---------------------------------------------------------------------------

from simulator import Simulator


class TestSimulatorSetLoopback:
    def _make_sim(self):
        sim = Simulator(config_path="nonexistent.cfg")
        return sim

    def test_set_loopback_updates_pru0_mask(self):
        sim = self._make_sim()
        sim.set_loopback("pru0", 0, True)
        assert sim.cores["pru0"].io_port.loopback_mask == 0xF

    def test_set_loopback_updates_rtu0_mask(self):
        sim = self._make_sim()
        sim.set_loopback("rtu0", 1, True)
        assert sim.cores["rtu0"].io_port.loopback_mask == 0xF0

    def test_set_loopback_disable_clears_mask(self):
        sim = self._make_sim()
        sim.set_loopback("pru0", 0, True)
        sim.set_loopback("pru0", 0, False)
        assert sim.cores["pru0"].io_port.loopback_mask == 0x0

    def test_set_loopback_cores_independent(self):
        sim = self._make_sim()
        sim.set_loopback("pru0", 2, True)
        assert sim.cores["rtu0"].io_port.loopback_mask == 0x0
```

- [ ] **Step 2: Run tests — verify they FAIL**

```
pytest tests/test_io_loopback.py::TestSimulatorSetLoopback -v
```

Expected: `AttributeError: 'Simulator' object has no attribute 'set_loopback'`

---

### Task 6: Simulator API + WebSocket — implementation

**Files:**
- Modify: `simulator.py`
- Modify: `ui/server.py`

- [ ] **Step 1: Add `set_loopback` to `simulator.py`**

In `simulator.py`, after `set_input()` (~line 212), add:

```python
    def set_loopback(self, core: str, group: int, enabled: bool) -> None:
        """Enable/disable GPO→GPI loopback for a 4-bit *group* (0–4) on *core*."""
        self._get_core(core).io_port.set_loopback_group(group, enabled)
```

- [ ] **Step 2: Run Simulator tests — verify they pass**

```
pytest tests/test_io_loopback.py::TestSimulatorSetLoopback -v
```

Expected: 4 passed.

- [ ] **Step 3: Add `set_loopback` action to `ui/server.py`**

In `ui/server.py`, after the `elif action == "write_sd_register":` block (~line 412), add before the `except` clause:

```python
            elif action == "set_loopback":
                sim.set_loopback(core, int(msg["group"]), bool(msg["enabled"]))
```

Note: no `await _send_state` — this is configuration only; state doesn't change visibly from loopback toggling.

- [ ] **Step 4: Run full suite — no regressions**

```
pytest tests/ -q
```

Expected: all tests pass (670 + 4 = 674).

- [ ] **Step 5: Commit**

```bash
git add simulator.py ui/server.py tests/test_io_loopback.py
git commit -m "feat(loopback): add set_loopback API and WebSocket action"
```

---

### Task 7: Frontend CSS + HTML

**Files:**
- Modify: `ui/static/index.html`

- [ ] **Step 1: Add CSS for loopback strip**

In `ui/static/index.html`, find the `/* ---- IO panel ---- */` CSS block (~line 354). Add these rules after `.io-section-title { ... }`:

```css
    /* ---- IO loopback strip -------------------------------------------- */
    .lb-strip {
      display: flex;
      align-items: center;
      gap: 6px;
      margin: 6px 0;
      padding: 4px 8px;
      background: #252535;
      border-radius: 4px;
    }
    .lb-label {
      font-size: 10px;
      color: #888;
      margin-right: 2px;
      white-space: nowrap;
    }
    .lb-btn {
      padding: 2px 8px;
      border-radius: 3px;
      font-size: 10px;
      background: #313145;
      border: 1px solid #555;
      color: #888;
      cursor: pointer;
    }
    .lb-btn.active {
      background: #f9e2af22;
      border-color: #f9e2af;
      color: #f9e2af;
    }
```

- [ ] **Step 2: Add loopback strip HTML to IO panel**

In `ui/static/index.html`, find the IO panel section (~line 1293–1299):

```html
          <div class="io-section">
            <div class="io-section-title">GPO (R30) — Output</div>
            <div class="pin-grid" id="gpo-grid"></div>
          </div>
          <div class="io-section">
            <div class="io-section-title">GPI (R31) — Input (click to toggle)</div>
            <div class="pin-grid" id="gpi-grid"></div>
          </div>
```

Replace with:

```html
          <div class="io-section">
            <div class="io-section-title">GPO (R30) — Output</div>
            <div class="pin-grid" id="gpo-grid"></div>
          </div>
          <div class="lb-strip" id="loopback-strip">
            <span class="lb-label">&#x21A9; Loopback</span>
            <button class="lb-btn" data-group="0">3:0</button>
            <button class="lb-btn" data-group="1">7:4</button>
            <button class="lb-btn" data-group="2">11:8</button>
            <button class="lb-btn" data-group="3">15:12</button>
            <button class="lb-btn" data-group="4">19:16</button>
          </div>
          <div class="io-section">
            <div class="io-section-title">GPI (R31) — Input (click to toggle)</div>
            <div class="pin-grid" id="gpi-grid"></div>
          </div>
```

- [ ] **Step 3: Commit**

```bash
git add ui/static/index.html
git commit -m "feat(ui): add loopback strip HTML and CSS to IO panel"
```

---

### Task 8: Frontend JS — loopback toggle handler

**Files:**
- Modify: `ui/static/app.js`

- [ ] **Step 1: Add `onLoopbackToggle` handler and attach click listeners**

In `app.js`, find `handleGpiClick` (~line 1854). Directly after that function's closing `}`, add:

```js
// ---- Loopback strip -------------------------------------------------------

function onLoopbackToggle(btn) {
  const group = parseInt(btn.dataset.group, 10);
  const enabled = !btn.classList.contains('active');
  btn.classList.toggle('active', enabled);
  sendAction({ action: 'set_loopback', core: currentCore, group, enabled });
}

document.querySelectorAll('#loopback-strip .lb-btn').forEach(btn => {
  btn.addEventListener('click', () => onLoopbackToggle(btn));
});
```

- [ ] **Step 2: Reset loopback buttons on core switch**

In `app.js`, find the `coreSelect.addEventListener("change", ...)` block (~line 1279). After `prevRegisters = new Array(32).fill("0x00000000");`, add:

```js
  document.querySelectorAll('#loopback-strip .lb-btn').forEach(b => b.classList.remove('active'));
```

The full block should look like:

```js
coreSelect.addEventListener("change", () => {
  stopRun(); stopSim();
  currentCore = coreSelect.value;
  prevRegisters = new Array(32).fill("0x00000000");
  document.querySelectorAll('#loopback-strip .lb-btn').forEach(b => b.classList.remove('active'));
  initSpadState();
  sourceList.innerHTML = "";
  sendAction({ action: "get_state", core: currentCore });
  updateCtableForCore();
});
```

- [ ] **Step 3: Commit**

```bash
git add ui/static/app.js
git commit -m "feat(ui): wire loopback toggle buttons to set_loopback WebSocket action"
```

---

### Task 9: Firmware example

**Files:**
- Create: `source/mvi_gpio_loopback.asm`

- [ ] **Step 1: Write the firmware example**

```asm
; mvi_gpio_loopback.asm
; Walking-bit GPO → GPI loopback demo using MVIB register-file indirect.
;
; Hardware setup (simulator):
;   1. Load this file.
;   2. In the IO panel, enable "Loopback" groups 3:0 and 7:4.
;   3. Step or Run.
;   4. After 16 steps of the inner loop, R10-R13 mirror R2-R5.
;
; Register layout:
;   R0          iteration counter (0-15)
;   R1.b0       TX pointer — byte address into register file (starts at R2.b0 = 8)
;   R1.b1       RX pointer — byte address into register file (starts at R10.b0 = 40)
;   R2-R5       TX pattern: 16-byte walking-bit sequence (loaded at init)
;   R10-R13     RX capture buffer (filled by MVIB from GPI/R31.b0)
;   R30.b0      GPO byte output (driven by MVIB)
;   R31.b0      GPI byte input  (read by MVIB)

; ---- Initialise TX pattern (walking bit: 0x01 0x02 0x04 0x08 ... 0x80 × 2) ----
START:
    ldi  r2.w0,  0x0201     ; r2.b0=0x01  r2.b1=0x02
    ldi  r2.w2,  0x0804     ; r2.b2=0x04  r2.b3=0x08
    ldi  r3.w0,  0x2010     ; r3.b0=0x10  r3.b1=0x20
    ldi  r3.w2,  0x8040     ; r3.b2=0x40  r3.b3=0x80
    ldi  r4.w0,  0x0201     ; repeat cycle
    ldi  r4.w2,  0x0804
    ldi  r5.w0,  0x2010
    ldi  r5.w2,  0x8040

; ---- Initialise pointers and counter ----
    ldi  r1.b0,  8          ; TX pointer → R2.b0 (byte 8 in register file)
    ldi  r1.b1,  40         ; RX pointer → R10.b0 (byte 40 in register file)
    ldi  r0,     0          ; iteration counter

; ---- Main loop ----
LOOP:
    mvib r30.b0, *r1.b0++   ; GPO byte ← TX pattern[r1.b0]; advance TX ptr
    mvib *r1.b1++, r31.b0   ; RX buf[r1.b1] ← GPI byte (R31.b0); advance RX ptr

    add  r0, r0, 1
    qbge RELOAD, r0, 16     ; if r0 >= 16: reload pointers
    qba  LOOP

RELOAD:
    ldi  r0,    0
    ldi  r1.b0, 8           ; reset TX → R2
    ldi  r1.b1, 40          ; reset RX → R10
    qba  LOOP
```

- [ ] **Step 2: Commit**

```bash
git add source/mvi_gpio_loopback.asm
git commit -m "feat(demo): add mvi_gpio_loopback.asm walking-bit example"
```

---

### Task 10: Test firmware assembles + end-to-end loopback

**Files:**
- Modify: `tests/test_io_loopback.py` (append)

- [ ] **Step 1: Write the tests**

Append at the end of `tests/test_io_loopback.py`:

```python
# ---------------------------------------------------------------------------
# Firmware: source/mvi_gpio_loopback.asm assembly + loopback e2e
# ---------------------------------------------------------------------------

import pathlib


class TestFirmwareAssembly:
    def test_mvi_gpio_loopback_assembles(self):
        """source/mvi_gpio_loopback.asm assembles without errors."""
        src_path = pathlib.Path(__file__).parent.parent / "source" / "mvi_gpio_loopback.asm"
        source = src_path.read_text(encoding="utf-8")
        sim = Simulator(config_path="nonexistent.cfg")
        errors = sim.load("pru0", source)
        assert errors == [], f"Assembly errors: {errors}"

    def test_mvi_gpio_loopback_runs_16_iterations(self):
        """After 16+setup steps, RX buffer mirrors TX pattern when loopback enabled."""
        src_path = pathlib.Path(__file__).parent.parent / "source" / "mvi_gpio_loopback.asm"
        source = src_path.read_text(encoding="utf-8")
        sim = Simulator(config_path="nonexistent.cfg")
        errors = sim.load("pru0", source)
        assert errors == [], f"Assembly errors: {errors}"

        # Enable loopback for all bits 7:0 (groups 0 and 1)
        sim.set_loopback("pru0", 0, True)
        sim.set_loopback("pru0", 1, True)

        # Run enough steps for init (9) + 16 loop iterations (3 steps each = 48) + margin
        sim.step("pru0", 80)

        regs = sim.registers("pru0")
        # TX pattern: R2=0x08040201, R3=0x80402010, R4=0x08040201, R5=0x80402010
        # RX buffer:  R10–R13 should mirror R2–R5 (bits 7:0 looped)
        # Only bits 7:0 are looped, so only b0 and b1 echo; b2/b3 of each reg = 0
        assert regs[10] & 0xFFFF == 0x0201, f"R10={regs[10]:#010x}"
        assert regs[11] & 0xFFFF == 0x2010, f"R11={regs[11]:#010x}"
        assert regs[12] & 0xFFFF == 0x0201, f"R12={regs[12]:#010x}"
        assert regs[13] & 0xFFFF == 0x2010, f"R13={regs[13]:#010x}"
```

- [ ] **Step 2: Run tests — verify they FAIL (firmware file doesn't exist yet)**

```
pytest tests/test_io_loopback.py::TestFirmwareAssembly -v
```

Expected: `FileNotFoundError` for `mvi_gpio_loopback.asm` (file not created yet in this order, OR assembly error if file has a bug).

> **Note:** If Task 9 was done first and the file exists, both tests should already pass. Verify each test's FAIL reason is as expected before implementing.

- [ ] **Step 3: Verify tests pass after firmware file is in place (Task 9)**

```
pytest tests/test_io_loopback.py::TestFirmwareAssembly -v
```

Expected: 2 passed.

- [ ] **Step 4: Run full suite**

```
pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_io_loopback.py
git commit -m "test(loopback): add firmware assembly and e2e loopback tests"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|-----------------|------|
| `loopback_mask` + `set_loopback_group()` in `io_port.py` | Task 2 |
| `write_r30()` applies loopback, guarded by sd_en bit 25 | Task 2 |
| `_mvi_read_direct` R31 fix | Task 4 |
| `simulator.set_loopback()` | Task 6 |
| `server.py` `set_loopback` action | Task 6 |
| Loopback strip HTML (Option A) | Task 7 |
| CSS `.lb-strip`, `.lb-btn`, `.lb-btn.active` | Task 7 |
| `onLoopbackToggle` JS handler | Task 8 |
| Core-switch resets loopback buttons | Task 8 |
| `source/mvi_gpio_loopback.asm` firmware | Task 9 |
| TDD tests for all backend changes | Tasks 1, 3, 5, 10 |

**Type consistency check:**

- `set_loopback_group(group, enabled)` in IOPort → `set_loopback(core, group, enabled)` in Simulator → server `msg["group"]`, `msg["enabled"]` → JS `{action:'set_loopback', core, group, enabled}` — consistent throughout.
- `loopback_mask` attribute — referenced in Task 1 tests and set in Task 2 `__init__`. Consistent.
- `onLoopbackToggle(btn)` defined in Task 8 — no other task references it. Self-contained.

**Placeholder scan:** No TBD or TODO present. All code steps are complete.

**Ambiguity:** The firmware e2e test (Task 10) checks only the lower 2 bytes of R10–R13 because only groups 0 and 1 (bits 7:0) are enabled. Upper bytes (groups 2–4) would require additional loopback groups to be enabled, which would interfere with the bitfield-specific check. The test comment makes this explicit.
