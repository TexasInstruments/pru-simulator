# Register Edit, IO Sync, and Memory Font Design

**Date:** 2026-05-18
**Status:** Approved

---

## Summary

Four coordinated fixes to the HTML dashboard:

1. **Memory font/spacing** — match `#mem-grid` font size and line spacing to the Registers panel
2. **Editable registers** — double-click any register row to edit its value inline
3. **R30 → GPO consistency** — GPO pin display always derived from R30 register value
4. **R31 ↔ GPI consistency** — R31 register display always reflects current GPI pin state

---

## 1. Memory Font/Spacing

**Problem:** `#mem-grid` uses `font-size: 11px` and `line-height: 1.8`. The Registers panel inherits the body font (13px, default line-height ~1.6 from the `line-height` in `.panel-body`).

**Fix:** In `ui/static/index.html`, change the `#mem-grid` CSS:
- `font-size: 13px` (was 11px)
- Remove the explicit `line-height: 1.8` override (inherits body default)

---

## 2. Editable Registers

### Trigger
Double-click on any register value cell (`#reg-val-N`) opens an inline hex editor.

### UX (matches existing `editWord()` pattern in memory panel)
- Replace cell content with a text `<input>` pre-filled with the current hex value
- Enter commits; Escape cancels
- Blur commits
- On commit: send `{action: "set_register", core, index: N, value: parsedInt}` over WebSocket
- Server responds with a full state update

### Server (WebSocket handler in `ui/server.py`)
New `action == "set_register"` branch:
```python
elif action == "set_register":
    idx = int(msg.get("index", 0))
    val = int(msg.get("value", 0)) & 0xFFFFFFFF
    core_obj = sim.cores[core]
    core_obj.registers.write_full(idx, val)
    if idx == 30:
        core_obj.io_port.write_r30(val)
    elif idx == 31:
        core_obj.io_port.set_gpi_word(val)
    await _send_state(websocket, core)
```

### JS (in `ui/static/app.js`)
New `editRegister(rowEl, index)` function called from a `dblclick` listener on each register row. Pattern mirrors `editWord()`:
- Swap value cell content for `<input>`
- On commit: parse hex, send `set_register`, restore cell text from next state update

### No enable/disable gating
The register editor is always available (even when halted or idle). The user asked for it "during single step" but there is no mechanical reason to restrict it.

---

## 3. R30 → GPO Consistency

**Problem:** `io_port.gpo` can diverge from `registers.regs[30]` after `reset()` (which creates a new `RegisterFile` but does not reset `io_port`). GPO pins are derived from `io_port.gpo`, so they show stale state after reset.

**Fix:** In `_send_state` in `ui/server.py`, derive GPO pins directly from R30:

```python
r30 = c.registers.read_full(30)
gpo_pins = [(r30 >> i) & 1 for i in range(20)]
```

Replace the `sim.io(core)` call in `_send_state` with this inline derivation for GPO, keeping GPI from `io_port`.

**Alternative considered:** Fix `PRUCore.reset()` to also call `io_port.write_r30(0)`. Rejected — the server-side derivation is simpler and removes the possibility of future drift from any other code path.

---

## 4. R31 ↔ GPI Consistency

**Problem:** `_send_state` sends `c.registers.regs[31]` for R31 display, but `registers.regs[31]` is never written when GPI pins change (only `io_port.gpi` is updated by `set_gpi_pin`). So the R31 register display is always 0 regardless of pin state.

**Fix:** In `_send_state`, override R31 in the register list:

```python
regs = list(c.registers.regs)
regs[31] = c.io_port.read_r31()
state["registers"] = [f"0x{r:08X}" for r in regs]
```

This keeps all other register values from `registers.regs` and substitutes only R31 with the live GPI word.

---

## 5. Data Flow After Fixes

| Event | R30 display | GPO pins | R31 display | GPI pins |
|---|---|---|---|---|
| Instruction writes R30 | ✅ from regs[30] | ✅ derived from regs[30] | — | — |
| Reset | ✅ 0 | ✅ derived from regs[30]=0 | — | — |
| User edits R30 | ✅ from regs[30] | ✅ derived from regs[30] | — | — |
| User toggles GPI pin | — | — | ✅ from io_port.read_r31() | ✅ shown |
| User edits R31 | — | — | ✅ from io_port.read_r31() | ✅ updates |

---

## 6. Files Changed

| File | Change |
|---|---|
| `ui/static/index.html` | CSS: font-size 13px, remove line-height override on `#mem-grid` |
| `ui/server.py` | `_send_state`: derive GPO from R30, substitute R31 from io_port; add `set_register` WS action |
| `ui/static/app.js` | Add `editRegister()`, attach `dblclick` to register rows in `buildRegTable()` |

No changes to simulator core.

---

## 7. Testing

- Existing 383 tests must still pass
- New server test: `set_register` action updates register and returns updated state
- New server test: R30 set via `set_register` updates GPO pins in state response
- New server test: R31 display in state reflects GPI pin state set via `set_input`
