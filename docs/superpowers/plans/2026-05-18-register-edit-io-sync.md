# Register Edit, IO Sync, and Memory Font Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix R30/R31 ↔ IO pin synchronisation in state updates, add inline register editing, and match memory panel font to registers panel.

**Architecture:** All changes are in the server WebSocket handler (`ui/server.py`) and the dashboard JS/CSS (`ui/static/app.js`, `ui/static/index.html`). No changes to the simulator core. `_send_state` is updated to derive GPO from R30 and R31 from `io_port`; a new `set_register` WebSocket action allows the UI to write any register.

**Tech Stack:** Python FastAPI, starlette WebSocket TestClient, vanilla JS/CSS

---

## File Map

| File | Change |
|---|---|
| `ui/server.py` | Fix `_send_state` IO derivation; add `set_register` WS action |
| `tests/test_server.py` | New WebSocket tests for `set_register`, R30→GPO, R31→GPI |
| `ui/static/index.html` | CSS: `#mem-grid` font-size 13px, remove line-height override |
| `ui/static/app.js` | Fix `editWord` input font-size; add `editRegister()`; attach dblclick in `buildRegTable()` |

---

## Task 1: Server — fix `_send_state` and add `set_register` action

**Files:**
- Modify: `ui/server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_server.py` after the existing tests:

```python
# ---- WebSocket helpers -------------------------------------------------

import pytest
from simulator import Simulator


@pytest.fixture
def fresh_sim(monkeypatch):
    import ui.server as srv
    fresh = Simulator(config_path=srv.config_path)
    monkeypatch.setattr(srv, "sim", fresh)
    return fresh


# ---- set_register tests ------------------------------------------------

def test_set_register_updates_value(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()  # consume state
        ws.send_json({"action": "set_register", "core": "pru0", "index": 5, "value": 0xABCD1234})
        state = ws.receive_json()
        assert state["type"] == "state"
        assert state["registers"][5] == "0xABCD1234"


def test_set_register_r30_updates_gpo(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "set_register", "core": "pru0", "index": 30, "value": 0b101})
        state = ws.receive_json()
        assert state["io"]["gpo_pins"][0] == 1
        assert state["io"]["gpo_pins"][1] == 0
        assert state["io"]["gpo_pins"][2] == 1


def test_set_register_r31_updates_gpi(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "set_register", "core": "pru0", "index": 31, "value": 0x00000008})
        state = ws.receive_json()
        assert state["registers"][31] == "0x00000008"
        assert state["io"]["gpi_pins"][3] == 1


# ---- IO sync tests -----------------------------------------------------

def test_r31_display_reflects_set_input(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "set_input", "core": "pru0", "pin": 0, "value": 1})
        state = ws.receive_json()
        assert state["registers"][31] == "0x00000001"


def test_gpo_zero_after_reset(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        # Set R30 to non-zero
        ws.send_json({"action": "set_register", "core": "pru0", "index": 30, "value": 0xF})
        ws.receive_json()
        # Reset — R30 becomes 0, GPO must also become 0
        ws.send_json({"action": "reset", "core": "pru0"})
        state = ws.receive_json()
        assert all(p == 0 for p in state["io"]["gpo_pins"])
        assert state["registers"][30] == "0x00000000"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd C:/Users/a0746725/ai_code/pru_simulator
python -m pytest tests/test_server.py -v -k "set_register or r31 or r30 or gpo or gpi"
```

Expected: `FAILED` — `set_register` action not handled; R31/R30 not synced.

- [ ] **Step 3: Fix `_send_state` in `ui/server.py`**

Replace the entire `_send_state` function:

```python
async def _send_state(ws, core):
    c = sim.cores[core]
    # R31 display reflects live GPI state (registers.regs[31] is never updated by set_gpi_pin)
    regs = list(c.registers.regs)
    regs[31] = c.io_port.read_r31()
    # GPO pins derived from R30 register value (avoids drift after reset)
    r30 = c.registers.read_full(30)
    gpo_pins = [(r30 >> i) & 1 for i in range(20)]
    state = {
        "type": "state",
        "core": core,
        "pc": c.pc,
        "halted": c.halted,
        "registers": [f"0x{r:08X}" for r in regs],
        "carry": c.registers.carry,
        "cycles": c.counters.cycles,
        "stall_cycles": c.counters.stall_cycles,
        "instruction_count": c.counters.instruction_count,
        "ipc": round(c.counters.ipc, 3),
        "io": {
            "gpo_pins": gpo_pins,
            "gpi_pins": c.io_port.get_gpi_pins(),
        },
        "instructions": [{"addr": i.address, "text": i.source_text} for i in c.instructions],
    }
    await ws.send_json(state)
```

- [ ] **Step 4: Add `set_register` action in the WebSocket handler**

In `ui/server.py`, inside `websocket_endpoint`, after the `elif action == "write_memory":` block and before the final `except`, add:

```python
            elif action == "set_register":
                idx = int(msg.get("index", 0)) & 0x1F  # clamp to 0-31
                val = int(msg.get("value", 0)) & 0xFFFFFFFF
                c = sim.cores[core]
                c.registers.write_full(idx, val)
                if idx == 30:
                    c.io_port.write_r30(val)
                elif idx == 31:
                    c.io_port.set_gpi_word(val)
                await _send_state(websocket, core)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_server.py -v
```

Expected: all tests pass (existing 4 + new 5 = 9 total in test_server.py)

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest --tb=short -q
```

Expected: `388 passed` (383 existing + 5 new)

- [ ] **Step 7: Commit**

```bash
git add ui/server.py tests/test_server.py
git commit -m "feat: fix R30/R31 IO sync in _send_state, add set_register WS action"
```

---

## Task 2: HTML/CSS — memory panel font/spacing fix

**Files:**
- Modify: `ui/static/index.html`
- Modify: `ui/static/app.js`

- [ ] **Step 1: Fix `#mem-grid` CSS in `index.html`**

Find in `ui/static/index.html`:

```css
    #mem-grid {
      display: grid;
      grid-template-columns: 80px repeat(4, 1fr) 140px;
      gap: 0;
      font-size: 11px;
      line-height: 1.8;
      overflow-y: auto;
      overflow-x: auto;
      flex: 1;
      padding: 4px 6px;
    }
```

Replace with:

```css
    #mem-grid {
      display: grid;
      grid-template-columns: 80px repeat(4, 1fr) 140px;
      gap: 0;
      font-size: 13px;
      overflow-y: auto;
      overflow-x: auto;
      flex: 1;
      padding: 4px 6px;
    }
```

(Changed `font-size: 11px` → `font-size: 13px`; removed `line-height: 1.8`.)

- [ ] **Step 2: Fix inline input font-size in `editWord()` in `app.js`**

Find in `ui/static/app.js` inside `editWord()`:

```javascript
  input.style.cssText = "width:72px;font-size:11px;text-align:center;background:#1a1a1a;color:#fff;border:1px solid var(--accent);padding:0;font-family:inherit;";
```

Replace with:

```javascript
  input.style.cssText = "width:80px;font-size:13px;text-align:center;background:#1a1a1a;color:#fff;border:1px solid var(--accent);padding:0;font-family:inherit;";
```

(Changed `font-size:11px` → `font-size:13px`; widened to `80px` to fit 8 hex digits at 13px.)

- [ ] **Step 3: Verify in browser**

Restart the server: `python ui/server.py` (kill any existing on port 8080 first).
Open `http://localhost:8080`. Verify:
- Memory panel hex values are the same size as register values
- No visual clipping of memory values

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest --tb=short -q
```

Expected: `388 passed` (no regressions)

- [ ] **Step 5: Commit**

```bash
git add ui/static/index.html ui/static/app.js
git commit -m "fix: match memory panel font size to registers panel"
```

---

## Task 3: JS — editable register values (double-click)

**Files:**
- Modify: `ui/static/app.js`

- [ ] **Step 1: Add `cursor: text` hint to register value cells**

In `ui/static/index.html`, find the `#reg-table .reg-val` CSS rule:

```css
    #reg-table .reg-val {
      color: var(--text);
      text-align: right;
    }
```

Replace with:

```css
    #reg-table .reg-val {
      color: var(--text);
      text-align: right;
      cursor: text;
    }
```

- [ ] **Step 2: Add `editRegister()` function in `app.js`**

After the `editWord()` function (around line 540), add:

```javascript
function editRegister(valEl, index) {
  const oldVal = valEl.textContent;
  const input = document.createElement("input");
  input.style.cssText = "width:88px;font-size:13px;text-align:right;background:#1a1a1a;color:#fff;border:1px solid var(--accent);padding:0 2px;font-family:inherit;";
  input.value = oldVal.slice(2);  // strip leading "0x"
  input.maxLength = 8;
  valEl.textContent = "";
  valEl.appendChild(input);
  input.focus();
  input.select();

  let committed = false;
  const commit = () => {
    if (committed) return;
    committed = true;
    const newVal = parseInt(input.value, 16);
    if (!isNaN(newVal) && newVal >= 0 && newVal <= 0xFFFFFFFF) {
      sendAction({ action: "set_register", core: currentCore, index, value: newVal });
      valEl.textContent = "0x" + newVal.toString(16).padStart(8, "0").toUpperCase();
    } else {
      valEl.textContent = oldVal;
    }
  };

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit();
    if (e.key === "Escape") { committed = true; valEl.textContent = oldVal; }
  });
  input.addEventListener("blur", commit);
}
```

Note: `committed` flag prevents the double-fire that occurs when Enter is pressed (keydown fires `commit()`, then removing the input triggers blur which would fire again).

- [ ] **Step 3: Attach dblclick listener in `buildRegTable()`**

Find `buildRegTable()`:

```javascript
function buildRegTable() {
  regTbody.innerHTML = "";
  for (let i = 0; i < 32; i++) {
    const tr = document.createElement("tr");
    tr.id = `reg-row-${i}`;
    tr.innerHTML = `<td class="reg-name">R${i}</td><td class="reg-val" id="reg-val-${i}">0x00000000</td>`;
    regTbody.appendChild(tr);
  }
}
```

Replace with:

```javascript
function buildRegTable() {
  regTbody.innerHTML = "";
  for (let i = 0; i < 32; i++) {
    const tr = document.createElement("tr");
    tr.id = `reg-row-${i}`;
    tr.innerHTML = `<td class="reg-name">R${i}</td><td class="reg-val" id="reg-val-${i}">0x00000000</td>`;
    const valCell = tr.querySelector(".reg-val");
    const regIndex = i;
    valCell.addEventListener("dblclick", () => editRegister(valCell, regIndex));
    regTbody.appendChild(tr);
  }
}
```

- [ ] **Step 4: Manual end-to-end test in browser**

Restart server, open `http://localhost:8080`. Test:

1. **Edit a general register:** Double-click R0 → type `DEADBEEF` → press Enter → R0 shows `0xDEADBEEF`
2. **Edit R30:** Double-click R30 → type `00000005` → press Enter → R30 = `0x00000005`, GPO pins 0 and 2 light up
3. **Edit R31:** Double-click R31 → type `00000003` → press Enter → R31 = `0x00000003`, GPI pins 0 and 1 light up, clicking a GPI pin then updates R31
4. **Cancel:** Double-click a register → type garbage → press Escape → original value restored
5. **GPI pin → R31 sync:** Click any GPI pin → R31 register display updates to match

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest --tb=short -q
```

Expected: `388 passed`

- [ ] **Step 6: Commit**

```bash
git add ui/static/app.js ui/static/index.html
git commit -m "feat: double-click to edit register values inline"
```

---

## Self-Review

**Spec coverage:**
- ✅ Memory font 13px, remove line-height override: Task 2
- ✅ Editable registers (dblclick, set_register action): Tasks 1 + 3
- ✅ R30 → GPO consistency (derive from regs[30]): Task 1
- ✅ R31 ↔ GPI consistency (override with io_port.read_r31()): Task 1
- ✅ set_register handles R30 (io_port.write_r30) and R31 (io_port.set_gpi_word): Task 1
- ✅ Tests for set_register, R30→GPO, R31→GPI, reset drift: Task 1

**Placeholder scan:** None found.

**Type consistency:**
- `editRegister(valEl, index)` defined Task 3 Step 2, called Task 3 Step 3 — matches
- `set_register` action name used in server Task 1 Step 4, in JS Task 3 Step 2 — matches
- `c.io_port.read_r31()` in `_send_state` (Task 1); `c.io_port.get_gpi_pins()` also in `_send_state` — both exist on `IOPort` class
- `c.io_port.set_gpi_word(val)` in `set_register` handler — exists on `IOPort` class
- `c.io_port.write_r30(val)` in `set_register` handler — exists on `IOPort` class
- `c.registers.write_full(idx, val)` — exists on `RegisterFile`; `read_full(30)` — exists on `RegisterFile`
