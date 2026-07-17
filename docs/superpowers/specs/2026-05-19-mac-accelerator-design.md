# MAC/MPY Broadside Accelerator — Design Spec

**Date:** 2026-05-19
**Feature:** PRU ICSSG MPY/MAC hardware accelerator (device_id=0), with an extensible accelerator registry that will support 12+ future accelerators.

---

## Background

The PRU ICSSG supports broadside accelerators accessed via XIN/XOUT/XCHG instructions using non-SPAD device IDs. Currently the simulator silently discards any XFR operation with an unknown device ID. This spec adds:

1. An `Accelerator` ABC that all broadside accelerators implement.
2. `MACAccelerator` — the first concrete accelerator (device_id=0).
3. An accelerator registry on `PRUCore` so future accelerators plug in with one line.
4. A minimal MAC mode indicator in the dashboard UI.

**Reference:** AM64x/AM243x TRM §6.4.6.2.1 (PRU Multiplier with Accumulation)
**Examples:** `C:/ti/GitHub/open-pru/academy/mac/`

---

## Architecture

### New files

| File | Purpose |
|------|---------|
| `xfr/accelerator.py` | `Accelerator` ABC — interface all broadside accelerators implement |
| `xfr/mac_accelerator.py` | `MACAccelerator(Accelerator)` — device_id=0, MPY/MAC logic |
| `tests/test_mac_accelerator.py` | Unit tests for MACAccelerator in isolation |

### Modified files

| File | Change |
|------|--------|
| `core/pru_core.py` | Add `self.accelerators: dict[int, Accelerator]`; route device_id in accelerators before SPAD in XIN/XOUT/XCHG; reset accelerators in `reset()` |
| `ui/server.py` | Include `mac` state in `_send_state` payload |
| `ui/static/app.js` | Read `mac` from state and update indicator |
| `ui/static/index.html` | MAC mode indicator element near R25-R29 in register panel |

No changes to `Simulator`, `XFRBus`, `mem/`, or `pru_io/`.

---

## Accelerator ABC (`xfr/accelerator.py`)

```python
from abc import ABC, abstractmethod

class Accelerator(ABC):
    DEVICE_ID: int  # override in each subclass

    @abstractmethod
    def xout(self, start_reg: int, data: bytes) -> None: ...

    @abstractmethod
    def xin(self, start_reg: int, length: int) -> bytes: ...

    @abstractmethod
    def xchg(self, start_reg: int, data: bytes) -> bytes: ...

    @abstractmethod
    def reset(self) -> None: ...
```

---

## MACAccelerator (`xfr/mac_accelerator.py`)

### Hardware model

| Register | Direction | Description |
|----------|-----------|-------------|
| R25 | XOUT write / XIN read | MAC_CTRL_STATUS: bit 0=MAC_MODE, bit 1=ACC_CARRY |
| R26 | XIN read / XOUT seed | Lower 32 bits of 64-bit result |
| R27 | XIN read / XOUT seed | Upper 32 bits of 64-bit result |
| R28 | auto-sampled | Operand A (read directly from RegisterFile) |
| R29 | auto-sampled | Operand B (read directly from RegisterFile) |

Device ID: **0**

### State

```python
mac_mode: bool        # False = multiply-only (default), True = accumulate
_accumulator: int     # 64-bit internal accumulator
acc_carry: bool       # sticky overflow flag
_regs: RegisterFile   # reference for auto-sampling R28/R29
```

### `xout(start_reg, data)` behavior

**start_reg == 25, len ≥ 1:**
```
ctrl = data[0]
bit0 = ctrl & 0x01   # MAC_MODE
bit1 = ctrl & 0x02   # clear ACC_CARRY request

if bit0 == 1:
    # Trigger accumulation: sample R28*R29 and add to accumulator
    product = R28 * R29   (both read as unsigned 32-bit)
    _accumulator += product
    if _accumulator > 0xFFFFFFFF_FFFFFFFF:
        _accumulator &= 0xFFFFFFFF_FFFFFFFF
        acc_carry = True
    mac_mode = True
else:
    # Multiply-only mode: clear accumulator
    _accumulator = 0
    mac_mode = False

if bit1:
    acc_carry = False
```

**start_reg == 26, len ≥ 8:**
```
# Seed accumulator (XOUT R26:R27)
_accumulator = low32(data[0:4]) | high32(data[4:8]) << 32
acc_carry = False
```

### `xin(start_reg, length)` behavior

**start_reg == 25:** Returns `bytes([bit0=mac_mode | bit1=acc_carry]) + zeros(length-1)`

**start_reg == 26:**
- Multiply-only mode: `product = R28 * R29`; return `product[0:32]` low bytes
- MAC mode: return `_accumulator[0:32]` low bytes

**start_reg == 27:**
- Multiply-only mode: `product = R28 * R29`; return `product[32:64]` high bytes
- MAC mode: return `_accumulator[32:64]` high bytes

Length is respected (caller may request 4 or 8 bytes starting at R26).

**All other start_reg:** return `bytes(length)` (zeros).

### `xchg` — not meaningful for MAC; returns `bytes(length)` zeros.

### `reset()`
```
mac_mode = False
_accumulator = 0
acc_carry = False
```

---

## PRUCore Changes (`core/pru_core.py`)

### `__init__`
```python
from xfr.mac_accelerator import MACAccelerator

self.accelerators: dict[int, Accelerator] = {
    MACAccelerator.DEVICE_ID: MACAccelerator(self.registers),
}
```

### XIN/XOUT/XCHG dispatch (all three handlers get the same pattern)
```python
if device_id in self.accelerators:
    # Route to broadside accelerator
    data = self._read_registers_to_bytes(start_reg, length)  # XOUT only
    result = self.accelerators[device_id].xout(start_reg, data)
elif self.xfr.xfr_shift_en and device_id in (SPAD_BANK0, ...):
    self._xout_shifted(...)
else:
    self.xfr.xout(device_id, ...)
```

XIN writes result back to registers via `_write_registers_from_bytes`.

### `reset()`
Add after existing reset logic:
```python
for acc in self.accelerators.values():
    acc.reset()
```

---

## WebSocket State (`ui/server.py`)

Add to `_send_state` payload:
```python
"mac": {
    "mode": c.accelerators[0].mac_mode,
    "acc_carry": c.accelerators[0].acc_carry,
}
```
Guard with `if 0 in c.accelerators` to avoid KeyError if MAC is not registered.

---

## UI Changes

### `index.html`
Add a one-line MAC status bar below the register table, inside the registers panel:
```html
<div id="mac-status">MAC: <span id="mac-mode-label">OFF</span> <span id="mac-carry-label"></span></div>
```

### `app.js`
In the state update handler:
```javascript
if (state.mac) {
  document.getElementById("mac-mode-label").textContent = state.mac.mode ? "ACC" : "MPY";
  document.getElementById("mac-carry-label").textContent = state.mac.acc_carry ? "CARRY" : "";
}
```

---

## Tests

### `tests/test_mac_accelerator.py` (unit, no PRUCore)

| Test | Verifies |
|------|---------|
| `test_multiply_only_basic` | R28=50, R29=25 → XIN R26=1250, R27=0 |
| `test_multiply_only_large` | R28=0xFFFFFFFF, R29=0xFFFFFFFF → correct 64-bit result split across R26/R27 |
| `test_mac_mode_accumulate` | Three XOUT R25 in MAC mode → acc = sum of three products |
| `test_mac_clear_accumulator` | XOUT R25[0]=0 after accumulation → acc=0, mac_mode=False |
| `test_acc_carry_set` | Overflow accumulator → acc_carry=True |
| `test_acc_carry_clear` | Write R25[1]=1 after carry → acc_carry=False |
| `test_seed_accumulator` | XOUT R26:R27 → accumulator seeded, carry cleared |
| `test_xin_status` | XIN R25 reflects mac_mode and acc_carry |
| `test_reset` | reset() clears all state |

### Integration tests added to `tests/test_pru_core.py` (or `test_integration.py`)

| Test | Source |
|------|--------|
| `test_mac_multiply_only_program` | Academy `mac_multiply/main.asm` — 50×25=1250 |
| `test_mac_dot_product_program` | Academy `mac/main.asm` — dot product (1,2,3)·(4,5,6)=32 |

---

## Spec Self-Review

- **Placeholders:** None.
- **Consistency:** XIN start_reg=26 in multiply-only mode computes R28×R29 on demand — consistent with TRM "sampled every clock cycle" (our single-step model is equivalent).
- **Scope:** Single accelerator + registry. No other accelerators added here.
- **Ambiguity resolved:** `xout(start_reg=25)` with bit0=1 always triggers one accumulation, including the first XOUT that enables MAC mode. This matches the academy example behavior (first two XOUTs with R28=R29=0 add zeros to acc, which is harmless).
- **XCHG for MAC:** Returns zeros — no meaningful exchange semantic for MAC hardware.
