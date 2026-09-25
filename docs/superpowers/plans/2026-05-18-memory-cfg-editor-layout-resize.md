# Layout Redesign + memory.cfg Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 4-column resizable panel layout and a modal editor for `memory.cfg` that auto-reloads the simulator on save.

**Architecture:** The server gains two REST endpoints (`GET /config`, `PUT /config`) alongside the existing WebSocket. The HTML top section switches from CSS Grid to CSS Flex with drag-handle dividers between panels. The memory panel moves into the top row. A modal dialog over the existing UI provides config editing.

**Tech Stack:** Python FastAPI, vanilla JS/CSS (no new dependencies), pytest + FastAPI TestClient

---

## File Map

| File | Change |
|---|---|
| `ui/server.py` | Add `GET /config`, `PUT /config`; import `Request`, `PlainTextResponse`, `JSONResponse` |
| `ui/static/index.html` | CSS: grid→flex, panel sizing, divider, modal styles. HTML: `#top-row` wrapper, dividers, Config button, modal markup |
| `ui/static/app.js` | Drag-resize logic, config modal handlers, `flashStatus()` |
| `tests/test_server.py` | New: FastAPI TestClient tests for config endpoints |

---

## Task 1: Server — REST endpoints for config read/write

**Files:**
- Modify: `ui/server.py`
- Create: `tests/test_server.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_server.py`:

```python
"""Tests for dashboard REST endpoints."""
import pytest
from fastapi.testclient import TestClient
from ui.server import app


client = TestClient(app)


def test_get_config_returns_text():
    response = client.get("/config")
    assert response.status_code == 200
    # Default memory.cfg at project root contains DRAM0
    assert "[DRAM0]" in response.text


def test_get_config_content_type_is_text():
    response = client.get("/config")
    assert "text/plain" in response.headers["content-type"]


def test_put_config_valid_returns_ok():
    # Round-trip: read current config, PUT it back unchanged
    content = client.get("/config").text
    response = client.put("/config", content=content)
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_put_config_writes_to_disk(tmp_path, monkeypatch):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    # Seed with valid config content
    original = client.get("/config").text
    cfg_file.write_text(original)
    monkeypatch.setattr(srv, "config_path", str(cfg_file))

    new_content = original + "\n; patched\n"
    response = client.put("/config", content=new_content)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "; patched" in cfg_file.read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /path/to/pru_simulator
python -m pytest tests/test_server.py -v
```

Expected: `FAILED — ImportError or 404` (endpoints don't exist yet)

- [ ] **Step 3: Implement the endpoints**

Edit `ui/server.py`. Replace the import block at the top:

```python
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from simulator import Simulator
```

After the existing `@app.get("/")` route, add:

```python
@app.get("/config")
async def get_config():
    try:
        with open(config_path, "r") as f:
            return PlainTextResponse(f.read())
    except FileNotFoundError:
        return PlainTextResponse("")


@app.put("/config")
async def put_config(request: Request):
    global sim
    text = (await request.body()).decode("utf-8")
    try:
        with open(config_path, "w") as f:
            f.write(text)
        sim = Simulator(config_path=config_path)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_server.py -v
```

Expected: `4 passed`

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
python -m pytest --tb=short -q
```

Expected: `383 passed` (379 existing + 4 new)

- [ ] **Step 6: Commit**

```bash
git add ui/server.py tests/test_server.py
git commit -m "feat: add GET/PUT /config endpoints for memory.cfg editing"
```

---

## Task 2: HTML — CSS layout restructure (grid → flex)

**Files:**
- Modify: `ui/static/index.html`

- [ ] **Step 1: Replace `#main` grid CSS with flex column**

In `index.html`, find and replace:

```css
    /* ---- Main grid ------------------------------------------------------- */
    #main {
      display: grid;
      grid-template-columns: 1fr 260px 320px;
      grid-template-rows: 1fr 200px 180px;
      gap: 6px;
      padding: 6px;
      flex: 1;
      min-height: 0;
      overflow: hidden;
    }
```

Replace with:

```css
    /* ---- Main layout ----------------------------------------------------- */
    #main {
      display: flex;
      flex-direction: column;
      gap: 6px;
      padding: 6px;
      flex: 1;
      min-height: 0;
      overflow: hidden;
    }

    #top-row {
      display: flex;
      flex-direction: row;
      flex: 1;
      min-height: 0;
      overflow: hidden;
    }

    #source-panel    { flex: 0 0 35%; }
    #registers-panel { flex: 0 0 15%; }
    #memory-panel    { flex: 0 0 30%; }
    #io-panel        { flex: 0 0 20%; }

    .divider {
      flex: 0 0 4px;
      background: var(--border);
      cursor: col-resize;
      transition: background 0.15s;
    }
    .divider:hover { background: var(--accent); }
```

- [ ] **Step 2: Remove old grid-position rules from panel CSS**

Find and remove the `grid-column`/`grid-row` lines from each panel comment block:

Remove `#source-panel { grid-column: 1; grid-row: 1; }` (keep the comment line, delete the rule).

Remove `#registers-panel { grid-column: 2; grid-row: 1; }`.

Remove `#io-panel { grid-column: 3; grid-row: 1; }`.

Replace the entire `#memory-panel` rule block:
```css
    /* ---- Memory panel --------------------------------------------------- */
    #memory-panel {
      grid-column: 1 / 4;
      grid-row: 2;
    }
```
with just the comment (no rule — sizing comes from `flex: 0 0 30%` above).

Replace the `#editor-panel` grid positioning:
```css
    /* ---- Source editor panel --------------------------------------------- */
    #editor-panel {
      grid-column: 1 / 4;
      grid-row: 3;
      display: flex;
      flex-direction: column;
    }
```
with:
```css
    /* ---- Source editor panel --------------------------------------------- */
    #editor-panel {
      flex: 0 0 180px;
      display: flex;
      flex-direction: column;
    }
```

- [ ] **Step 3: Restructure the HTML body — wrap top panels in `#top-row` with dividers**

Find the `<!-- Main grid -->` comment block in the HTML body. Replace the entire `<div id="main">` contents with:

```html
  <!-- Main layout -->
  <div id="main">

    <div id="top-row">

      <!-- Source panel -->
      <div class="panel" id="source-panel">
        <div class="panel-title">Source / Disassembly</div>
        <div class="panel-body">
          <ul id="source-list"></ul>
        </div>
      </div>

      <div class="divider"></div>

      <!-- Registers panel -->
      <div class="panel" id="registers-panel">
        <div class="panel-title">Registers</div>
        <div id="carry-row">Carry: <span class="val" id="reg-carry">0</span></div>
        <div class="panel-body">
          <table id="reg-table">
            <tbody id="reg-tbody"></tbody>
          </table>
        </div>
      </div>

      <div class="divider"></div>

      <!-- Memory panel -->
      <div class="panel" id="memory-panel">
        <div id="memory-controls">
          <label>Address:</label>
          <input type="text" id="mem-addr-input" value="0x00000000" />
          <label>Bytes:</label>
          <input type="text" id="mem-len-input" value="128" style="width:50px" />
          <button id="btn-mem-refresh">Refresh</button>
          <span id="mem-region-label" style="font-size:11px;color:var(--accent);margin-left:auto;"></span>
        </div>
        <div id="mem-grid"></div>
      </div>

      <div class="divider"></div>

      <!-- IO panel -->
      <div class="panel" id="io-panel">
        <div class="panel-title">I/O Pins</div>
        <div class="panel-body">
          <div class="io-section">
            <div class="io-section-title">GPO (R30) — Output</div>
            <div class="pin-grid" id="gpo-grid"></div>
          </div>
          <div class="io-section">
            <div class="io-section-title">GPI (R31) — Input (click to toggle)</div>
            <div class="pin-grid" id="gpi-grid"></div>
          </div>
        </div>
      </div>

    </div><!-- /#top-row -->

    <!-- Source editor panel -->
    <div class="panel" id="editor-panel">
      <div class="panel-title">
        <span>Assembly Editor</span>
        <input type="file" id="file-input" accept=".asm,.inc,.s" style="display:none" />
        <button id="btn-file" class="btn-load">Open File</button>
        <button id="btn-load" class="btn-load">Load &amp; Assemble</button>
      </div>
      <textarea id="asm-source" spellcheck="false" placeholder="Enter PRU assembly here...
Example:
    LDI r0, 0x42
    LDI r1, 0x10
    ADD r2, r0, r1
    HALT"></textarea>
    </div>

  </div>
```

- [ ] **Step 4: Start the server and verify layout in browser**

```bash
cd /path/to/pru_simulator
python ui/server.py
```

Open `http://localhost:8080`. Verify:
- Four panels side by side in one row: Source | Registers | Memory | IO
- Assembly Editor below, full width
- No visual errors or missing panels

- [ ] **Step 5: Commit**

```bash
git add ui/static/index.html
git commit -m "feat: restructure dashboard to 4-column flex layout"
```

---

## Task 3: JS — drag-resize for panel dividers

**Files:**
- Modify: `ui/static/app.js`

- [ ] **Step 1: Add resize state variables after existing state block**

After the line `let runInterval = null;`, add:

```javascript
// ---- Resize state ----------------------------------------------------------
let _resize = null;
```

- [ ] **Step 2: Add the drag-resize functions**

After the `initUI()` function definition, add:

```javascript
// ---- Panel resize ----------------------------------------------------------

function initResize() {
  document.querySelectorAll('.divider').forEach(div => {
    div.addEventListener('mousedown', onDividerDown);
  });
}

function onDividerDown(e) {
  e.preventDefault();
  const left  = e.currentTarget.previousElementSibling;
  const right = e.currentTarget.nextElementSibling;
  _resize = {
    startX:     e.clientX,
    leftStart:  left.getBoundingClientRect().width,
    rightStart: right.getBoundingClientRect().width,
    left,
    right,
  };
  document.body.style.cursor     = 'col-resize';
  document.body.style.userSelect = 'none';
  // Disable transitions on dividers during drag
  document.querySelectorAll('.divider').forEach(d => d.style.transition = 'none');
  document.addEventListener('mousemove', onResizeMove);
  document.addEventListener('mouseup',   onResizeUp);
}

function onResizeMove(e) {
  if (!_resize) return;
  const MIN   = 120;
  const delta = e.clientX - _resize.startX;
  let newLeft  = _resize.leftStart  + delta;
  let newRight = _resize.rightStart - delta;
  if (newLeft  < MIN) { newLeft  = MIN; newRight = _resize.leftStart + _resize.rightStart - MIN; }
  if (newRight < MIN) { newRight = MIN; newLeft  = _resize.leftStart + _resize.rightStart - MIN; }
  _resize.left.style.flex  = `0 0 ${newLeft}px`;
  _resize.right.style.flex = `0 0 ${newRight}px`;
}

function onResizeUp() {
  _resize = null;
  document.body.style.cursor     = '';
  document.body.style.userSelect = '';
  document.querySelectorAll('.divider').forEach(d => d.style.transition = '');
  document.removeEventListener('mousemove', onResizeMove);
  document.removeEventListener('mouseup',   onResizeUp);
}
```

- [ ] **Step 3: Call `initResize()` inside `initUI()`**

Find `initUI()`:
```javascript
function initUI() {
  buildRegTable();
  buildPinGrid(gpoGrid, 20, "gpo", null);
  buildPinGrid(gpiGrid, 20, "gpi", handleGpiClick);
}
```

Replace with:
```javascript
function initUI() {
  buildRegTable();
  buildPinGrid(gpoGrid, 20, "gpo", null);
  buildPinGrid(gpiGrid, 20, "gpi", handleGpiClick);
  initResize();
}
```

- [ ] **Step 4: Manual test in browser**

Restart the server, open `http://localhost:8080`. Drag each of the three dividers. Verify:
- Panels resize smoothly
- Neither adjacent panel shrinks below ~120px
- Cursor changes to `col-resize` during drag
- Divider highlights blue on hover

- [ ] **Step 5: Commit**

```bash
git add ui/static/app.js
git commit -m "feat: drag-to-resize panel dividers"
```

---

## Task 4: HTML + CSS — Config button and modal markup

**Files:**
- Modify: `ui/static/index.html`

- [ ] **Step 1: Add Config button to controls bar**

Find in the controls bar HTML:
```html
    <button id="btn-reset" class="btn-reset">Reset</button>

    <span id="status-badge">IDLE</span>
```

Replace with:
```html
    <button id="btn-reset" class="btn-reset">Reset</button>
    <button id="btn-config">Config</button>

    <span id="status-badge">IDLE</span>
```

- [ ] **Step 2: Add modal HTML before the `<script>` tag**

Find `  <script src="/static/app.js"></script>` and insert before it:

```html
  <!-- Config modal -->
  <div id="config-modal">
    <div id="config-card">
      <div id="config-modal-title">memory.cfg</div>
      <textarea id="config-textarea" spellcheck="false"></textarea>
      <div id="config-error"></div>
      <div id="config-buttons">
        <button id="btn-config-save">Save &amp; Reload</button>
        <button id="btn-config-cancel">Cancel</button>
      </div>
    </div>
  </div>

```

- [ ] **Step 3: Add modal and flash CSS**

Inside the `<style>` block, append before the closing `</style>` tag:

```css
    /* ---- Config modal ---------------------------------------------------- */
    #config-modal {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.6);
      z-index: 1000;
      align-items: center;
      justify-content: center;
    }

    #config-card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 4px;
      width: 600px;
      height: 500px;
      display: flex;
      flex-direction: column;
      padding: 12px;
      gap: 8px;
    }

    #config-modal-title {
      font-size: 13px;
      font-weight: bold;
      color: var(--accent);
      flex-shrink: 0;
    }

    #config-textarea {
      flex: 1;
      background: #1a1a1a;
      color: var(--text);
      border: 1px solid var(--border);
      font-family: inherit;
      font-size: 12px;
      padding: 8px;
      resize: none;
      outline: none;
      line-height: 1.5;
    }

    #config-error {
      color: var(--halted);
      font-size: 12px;
      flex-shrink: 0;
      min-height: 0;
    }

    #config-buttons {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
      flex-shrink: 0;
    }

    #btn-config-save { color: var(--accent); border-color: #3a5a8a; }

    /* ---- Status flash ---------------------------------------------------- */
    #status-badge.reloaded { background: #1a3a4a; color: var(--accent); }
```

- [ ] **Step 4: Verify modal HTML is present**

Open browser DevTools console on `http://localhost:8080` and run:
```javascript
document.getElementById('config-modal') !== null  // should be true
document.getElementById('btn-config') !== null     // should be true
```

- [ ] **Step 5: Commit**

```bash
git add ui/static/index.html
git commit -m "feat: add Config button and modal markup/CSS"
```

---

## Task 5: JS — Config modal handlers and status flash

**Files:**
- Modify: `ui/static/app.js`

- [ ] **Step 1: Add DOM references for modal elements**

After the existing DOM references block (after `const cntPc = ...`), add:

```javascript
const btnConfig       = document.getElementById("btn-config");
const configModal     = document.getElementById("config-modal");
const configTextarea  = document.getElementById("config-textarea");
const configError     = document.getElementById("config-error");
const btnConfigSave   = document.getElementById("btn-config-save");
const btnConfigCancel = document.getElementById("btn-config-cancel");
```

- [ ] **Step 2: Add `flashStatus()` utility**

After the `escapeHtml()` function, add:

```javascript
function flashStatus(text, cssClass) {
  const origText  = statusBadge.textContent;
  const origClass = statusBadge.className;
  statusBadge.textContent = text;
  statusBadge.className   = cssClass;
  setTimeout(() => {
    statusBadge.textContent = origText;
    statusBadge.className   = origClass;
  }, 2000);
}
```

- [ ] **Step 3: Add config modal event handlers**

After the `fileInput.addEventListener(...)` block, add:

```javascript
// ---- Config modal ----------------------------------------------------------

btnConfig.addEventListener("click", async () => {
  configError.textContent = "";
  configTextarea.value    = "";
  try {
    const res = await fetch("/config");
    configTextarea.value = await res.text();
  } catch (e) {
    configError.textContent = "Could not load config: " + String(e);
  }
  configModal.style.display = "flex";
  configTextarea.focus();
});

btnConfigCancel.addEventListener("click", () => {
  configModal.style.display = "none";
});

configModal.addEventListener("click", (e) => {
  if (e.target === configModal) configModal.style.display = "none";
});

btnConfigSave.addEventListener("click", async () => {
  configError.textContent      = "";
  btnConfigSave.disabled       = true;
  btnConfigSave.textContent    = "Saving...";
  try {
    const res  = await fetch("/config", { method: "PUT", body: configTextarea.value });
    const data = await res.json();
    if (data.ok) {
      configModal.style.display = "none";
      flashStatus("RELOADED", "reloaded");
      refreshMemory();
    } else {
      configError.textContent = data.error || "Unknown error";
    }
  } catch (e) {
    configError.textContent = String(e);
  } finally {
    btnConfigSave.disabled    = false;
    btnConfigSave.textContent = "Save \u0026 Reload";
  }
});
```

- [ ] **Step 4: Manual end-to-end test**

Restart the server, open `http://localhost:8080`. Test the following:

1. Click **Config** → modal opens, textarea shows current `memory.cfg` content
2. Click **Cancel** → modal closes, no changes
3. Click **Config** again → click outside the card → modal closes
4. Click **Config**, change `read_latency = 2` to `read_latency = 3` in DRAM0 → click **Save & Reload**
   - Modal closes
   - Status badge briefly shows "RELOADED" in blue
   - Memory panel refreshes
5. Revert the change (set back to `read_latency = 2`) and save again to restore original config

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest --tb=short -q
```

Expected: `383 passed`

- [ ] **Step 6: Commit**

```bash
git add ui/static/app.js
git commit -m "feat: config modal JS handlers and status flash"
```

---

## Self-Review

**Spec coverage:**
- ✅ Layout 4-column flex: Tasks 2
- ✅ Drag-resize with 120px minimum: Task 3
- ✅ Config button after Reset, before status badge: Task 4
- ✅ Modal with textarea, Save & Reload, Cancel: Tasks 4+5
- ✅ GET /config populates textarea: Task 5
- ✅ PUT /config saves file + rebuilds sim: Task 1
- ✅ Error shown in modal on bad config: Task 5
- ✅ Status flash "RELOADED" for 2s: Task 5
- ✅ Memory panel refreshes after save: Task 5
- ✅ Divider hover highlight: Task 2 CSS

**Placeholder scan:** None found.

**Type consistency:**
- `flashStatus(text, cssClass)` defined in Task 5 Step 2, called in Task 5 Step 3 — matches.
- `initResize()` defined in Task 3 Step 2, called in Task 3 Step 3 — matches.
- `configModal.style.display = "flex"` used to open — matches `#config-modal { display: none; align-items: center; justify-content: center; }` in CSS.
