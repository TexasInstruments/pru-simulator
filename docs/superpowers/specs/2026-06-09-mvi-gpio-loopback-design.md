# MVI GPIO Loopback — Design Spec

**Date:** 2026-06-09
**Feature:** Walking-bit MVI firmware example + UI GPIO loopback control

---

## Overview

Two related deliverables:

1. **`source/mvi_gpio_loopback.asm`** — a firmware example that exercises the new MVIB instruction by streaming a walking-bit pattern from a register buffer to GPO (R30.b0) and collecting GPI (R31.b0) back into a second buffer using register-file indirect addressing.

2. **GPIO loopback control** — a compact toggle strip in the IO panel that routes GPO bit groups back to GPI, enabling self-contained loopback testing without external hardware. Also includes a bug fix: MVI reads of R31 must go through `io_port.read_r31()` (not the raw register file) to pick up the looped value.

---

## Section 1 — Architecture

### Components

| Component | Change |
|-----------|--------|
| `source/mvi_gpio_loopback.asm` | New file — walking-bit demo firmware |
| `pru_io/io_port.py` | Add `loopback_mask`, `set_loopback_group()`, apply loopback in `write_r30()` |
| `core/pru_core.py` | Fix `_mvi_read_direct` for R31 → route through `io_port.read_r31()` |
| `simulator.py` | Add `set_loopback(core, group, enabled)` API |
| `ui/server.py` | Handle `set_loopback` WebSocket action |
| `ui/static/app.js` | Add loopback toggle strip to IO panel, `onLoopbackToggle()` handler |
| `ui/static/index.html` | CSS for `.lb-strip`, `.lb-btn`, `.lb-btn.active` |

### Data flow

```
UI toggle click
  → ws.send({action:"set_loopback", core, group, enabled})
  → server.py dispatcher
  → sim.set_loopback(core, group, enabled)
  → io_port.set_loopback_group(group, enabled)   [sets loopback_mask]

PRU executes: MVIB r30.b0, *r1.b0++
  → pru_core: _mvi_write_direct(30, ...)
  → io_port.write_r30(value)
  → gpo updated; gpi = (gpi & ~loopback_mask) | (gpo & loopback_mask)

PRU executes: MVIB *r1.b1++, r31.b0
  → pru_core: _mvi_read_direct(31, ...)  [FIXED: calls io_port.read_r31()]
  → returns current gpi value
  → written into RX buffer (R10–R13)
```

---

## Section 2 — Firmware (`source/mvi_gpio_loopback.asm`)

### Pattern

R2–R5 hold 16 bytes of a walking-bit sequence (2 full cycles of the 8-bit walking bit):

```
R2: b0=0x01  b1=0x02  b2=0x04  b3=0x08
R3: b0=0x10  b1=0x20  b2=0x40  b3=0x80
R4: b0=0x01  b1=0x02  b2=0x04  b3=0x08   (repeat)
R5: b0=0x10  b1=0x20  b2=0x40  b3=0x80
```

Loaded with 8× `LDI rN.wH, imm16` pairs (two half-words per register).

### Pointer layout

| Register field | Value | Points to |
|----------------|-------|-----------|
| `r1.b0` | 8 | R2.b0 — TX buffer start |
| `r1.b1` | 40 | R10.b0 — RX buffer start |

Register-file byte addresses: R0 = 0–3, R1 = 4–7, R2 = 8–11, ..., R10 = 40–43, R13 = 52–55.

TX range: bytes 8–23 (R2–R5, 16 bytes).
RX range: bytes 40–55 (R10–R13, 16 bytes).

### Loop structure

```asm
    ldi r0, 0               ; iteration counter

LOOP:
    mvib r30.b0, *r1.b0++   ; GPO ← pattern byte; advance TX ptr
    mvib *r1.b1++, r31.b0   ; RX buf ← GPI byte;  advance RX ptr

    add  r0, r0, 1
    qbge RELOAD, r0, 16     ; if r0 >= 16, wrap pointers
    jmp  LOOP

RELOAD:
    ldi r0, 0
    ldi r1.b0, 8            ; reset TX → R2
    ldi r1.b1, 40           ; reset RX → R10
    jmp LOOP
```

### Expected behaviour

| Loopback state | R10–R13 after one full sweep |
|----------------|------------------------------|
| Off | All zeros (no external signal) |
| Groups 3:0 and 7:4 on (bits 7:0) | Mirror of R2–R5 (walking-bit echoed back) |
| Group 3:0 only (bits 3:0) | Lower nibble echoed; upper nibble zeros |

---

## Section 3 — Backend

### `pru_io/io_port.py`

Add `loopback_mask` (default 0) and `set_loopback_group()`:

```python
self.loopback_mask = 0          # which of 20 GPO bits feed back to GPI

def set_loopback_group(self, group: int, enabled: bool):
    """group 0–4 controls bits [group*4+3 : group*4]"""
    mask = 0xF << (group * 4)
    if enabled:
        self.loopback_mask |= mask
    else:
        self.loopback_mask &= ~mask
```

Modify `write_r30()` to apply loopback immediately (combinatorial, matching hardware). Guard with `sd_en` check — in SD mode `gpi` is unused for GPIO, so loopback must not clobber it:

```python
def write_r30(self, value: int):
    self.gpo = value & 0xFFFFF
    if not self.sd_en:   # bit 25 of R30; only apply loopback in GPIO mode
        self.gpi = (self.gpi & ~self.loopback_mask) | (self.gpo & self.loopback_mask)
```

### `core/pru_core.py`

Fix `_mvi_read_direct` — R31 must go through `io_port.read_r31()`, not the raw register file:

```python
def _mvi_read_direct(self, reg_idx, bit_offset, width):
    if reg_idx == 31:
        word = self.io_port.read_r31()
    else:
        # existing register-file read
        ...
```

### `simulator.py`

```python
def set_loopback(self, core: str, group: int, enabled: bool):
    self._get_core(core).io_port.set_loopback_group(group, enabled)
```

### `ui/server.py`

Add to the action dispatcher:

```python
elif action == "set_loopback":
    sim.set_loopback(data["core"], data["group"], data["enabled"])
```

### Snapshot/restore

No changes needed. `loopback_mask` is UI configuration; stepping back does not change it.

---

## Section 4 — Frontend

### HTML (IO panel, between GPO and GPI rows)

```html
<div id="loopback-strip" class="lb-strip">
  <span class="lb-label">↩ Loopback</span>
  <button class="lb-btn" data-group="0">3:0</button>
  <button class="lb-btn" data-group="1">7:4</button>
  <button class="lb-btn" data-group="2">11:8</button>
  <button class="lb-btn" data-group="3">15:12</button>
  <button class="lb-btn" data-group="4">19:16</button>
</div>
```

### CSS

```css
.lb-strip { display:flex; align-items:center; gap:6px; margin:6px 0; padding:4px 8px;
            background:#252535; border-radius:4px; }
.lb-label { font-size:10px; color:#888; margin-right:2px; }
.lb-btn   { padding:2px 8px; border-radius:3px; font-size:10px;
            background:#313145; border:1px solid #555; color:#888; cursor:pointer; }
.lb-btn.active { background:#f9e2af22; border-color:#f9e2af; color:#f9e2af; }
```

### JS (`app.js`, co-located with IO panel render)

```js
function onLoopbackToggle(btn) {
  const group = parseInt(btn.dataset.group);
  const enabled = !btn.classList.contains('active');
  btn.classList.toggle('active', enabled);
  ws.send(JSON.stringify({ action: 'set_loopback', core: currentCore, group, enabled }));
}
```

### Lifecycle

- Strip is rendered once at panel init; toggle state is managed via `classList` (not re-rendered on step).
- On core switch: all buttons reset to inactive (loopback is per-core; new core starts with `loopback_mask = 0`).

---

## Testing

New tests to add (TDD — write before implementing):

| Test | File |
|------|------|
| `set_loopback_group` sets/clears correct mask bits | `tests/test_io_loopback.py` |
| `write_r30` with loopback propagates GPO bits to GPI | `tests/test_io_loopback.py` |
| `write_r30` with loopback does not clobber non-masked GPI bits | `tests/test_io_loopback.py` |
| MVIB `r30.b0, *ptr++` then MVIB `*ptr++, r31.b0` reads looped value | `tests/test_io_loopback.py` |
| `simulator.set_loopback()` propagates to correct core's io_port | `tests/test_io_loopback.py` |
| `_mvi_read_direct` for R31 calls `io_port.read_r31()` not raw regfile | `tests/test_mvi.py` (extend) |
| Firmware file assembles without errors | `tests/test_io_loopback.py` |

---

## Out of Scope

- Loopback state is not persisted across page reload (UI-only configuration).
- No loopback for SD mode (loopback_mask only applies when `sd_en = 0`).
- No loopback snapshot/restore for step-back (configuration, not execution state).
