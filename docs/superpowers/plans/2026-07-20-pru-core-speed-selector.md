# PRU Core Speed Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a controls-bar dropdown letting the user set PRU core clock speed to one of 200/225/250/300/333 MHz, applying to PRU0, RTU0, and PRU1 together (the PRU1 drift-demo offset remains a separate manual step, untouched by this feature).

**Architecture:** A `PUT /config/clock_speed` FastAPI endpoint regex-patches `pru_clock_mhz` and `pru1_clock_mhz` in the on-disk ini config, rebuilds the `Simulator`, and clears per-core undo history — mirroring the existing `PUT /config` handler. A `GET /config/clock_speed` endpoint reports the current value. A new `<select>` in the controls bar calls these on load and on change, reusing the existing config-reload UI refresh sequence.

**Tech Stack:** Python (FastAPI, regex via `re`), vanilla JS/HTML (no framework), pytest + FastAPI `TestClient`.

## Global Constraints

- Allowed speeds are exactly `{200, 225, 250, 300, 333}` MHz — this set must match verbatim between the backend `ALLOWED_CLOCK_MHZ` and the frontend `<option>` list.
- The endpoint must preserve ini file formatting/comments (no `configparser.write()` round-trip) — use regex line patching.
- Selecting a speed always writes the same value to both `pru_clock_mhz` and `pru1_clock_mhz` — no special-casing for the drift demo.
- Reference spec: `docs/superpowers/specs/2026-07-20-pru-core-speed-selector-design.md`.

---

### Task 1: Backend `_set_ini_value` helper + clock_speed endpoints

**Files:**
- Modify: `ui/server.py` (add `import re` near line 8; add helper + endpoints after `put_config` at line 180)
- Test: `tests/test_clock_speed_endpoint.py` (new)

**Interfaces:**
- Produces: `_set_ini_value(text: str, section: str, key: str, value: str) -> str` — module-level function in `ui/server.py`.
- Produces: `ALLOWED_CLOCK_MHZ: set[int]` — module-level constant in `ui/server.py`, value `{200, 225, 250, 300, 333}`.
- Produces: `GET /config/clock_speed` → `{"mhz": <float>}`.
- Produces: `PUT /config/clock_speed` with JSON body `{"mhz": <int>}` → `{"ok": true}` on success (200) or `{"error": <str>}` (400) if `mhz` not in `ALLOWED_CLOCK_MHZ`.
- Consumes: `sim` (module global, `ui/server.py:~90`), `config_path` (module global, `ui/server.py:27`), `_config_lock` (`ui/server.py:46`), `_history` (`ui/server.py:50`), `Simulator` (imported at `ui/server.py:18`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_clock_speed_endpoint.py`:

```python
"""Tests for the PRU core clock speed REST endpoint."""
from fastapi.testclient import TestClient
from ui.server import app

client = TestClient(app)


def test_get_clock_speed_returns_current_value(tmp_path, monkeypatch):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    cfg_file.write_text(
        "[device]\n"
        "target = AM243x\n"
        "pru_clock_mhz = 200\n"
        "\n"
        "[DRAM0]\n"
        "base = 0x00000000\n"
        "size = 0x2000\n"
    )
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    monkeypatch.setattr(srv, "sim", srv.Simulator(config_path=str(cfg_file)))

    response = client.get("/config/clock_speed")
    assert response.status_code == 200
    assert response.json()["mhz"] == 200


def test_put_clock_speed_valid_updates_both_keys(tmp_path, monkeypatch):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    cfg_file.write_text(
        "[device]\n"
        "target = AM243x\n"
        "pru_clock_mhz = 200\n"
        "pru1_clock_mhz = 200.1\n"
        "\n"
        "[DRAM0]\n"
        "base = 0x00000000\n"
        "size = 0x2000\n"
    )
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    monkeypatch.setattr(srv, "sim", srv.Simulator(config_path=str(cfg_file)))

    response = client.put("/config/clock_speed", json={"mhz": 250})
    assert response.status_code == 200
    assert response.json()["ok"] is True

    text = cfg_file.read_text()
    assert "pru_clock_mhz = 250" in text
    assert "pru1_clock_mhz = 250" in text
    assert "200.1" not in text


def test_put_clock_speed_inserts_missing_pru1_key(tmp_path, monkeypatch):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    cfg_file.write_text(
        "[device]\n"
        "target = AM243x\n"
        "pru_clock_mhz = 200\n"
        "\n"
        "[DRAM0]\n"
        "base = 0x00000000\n"
        "size = 0x2000\n"
    )
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    monkeypatch.setattr(srv, "sim", srv.Simulator(config_path=str(cfg_file)))

    response = client.put("/config/clock_speed", json={"mhz": 300})
    assert response.status_code == 200

    text = cfg_file.read_text()
    assert "pru_clock_mhz = 300" in text
    assert "pru1_clock_mhz = 300" in text
    # Key was inserted inside [device], not appended after [DRAM0]
    device_block = text.split("[DRAM0]")[0]
    assert "pru1_clock_mhz = 300" in device_block


def test_put_clock_speed_rejects_disallowed_value(tmp_path, monkeypatch):
    import ui.server as srv
    cfg_file = tmp_path / "memory.cfg"
    original = (
        "[device]\n"
        "target = AM243x\n"
        "pru_clock_mhz = 200\n"
        "\n"
        "[DRAM0]\n"
        "base = 0x00000000\n"
        "size = 0x2000\n"
    )
    cfg_file.write_text(original)
    monkeypatch.setattr(srv, "config_path", str(cfg_file))
    monkeypatch.setattr(srv, "sim", srv.Simulator(config_path=str(cfg_file)))

    response = client.put("/config/clock_speed", json={"mhz": 275})
    assert response.status_code == 400
    assert "error" in response.json()
    assert cfg_file.read_text() == original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_clock_speed_endpoint.py -v`
Expected: FAIL — `404 Not Found` for both routes (endpoints don't exist yet), or `AttributeError` if referencing `srv.Simulator`/`ALLOWED_CLOCK_MHZ` before they exist.

- [ ] **Step 3: Add `import re` to `ui/server.py`**

In `ui/server.py`, change the import block (currently lines 3-8):

```python
import asyncio
import json
import math
import os
import pathlib
import re
import sys
```

- [ ] **Step 4: Add `ALLOWED_CLOCK_MHZ`, `_set_ini_value`, and the two endpoints**

In `ui/server.py`, insert immediately after the `put_config` function (after line 180, before the `@app.websocket("/ws")` line at 183):

```python
ALLOWED_CLOCK_MHZ = {200, 225, 250, 300, 333}


def _set_ini_value(text: str, section: str, key: str, value: str) -> str:
    """Set key = value inside [section] of an ini-format string, preserving
    all other formatting/comments. Replaces the key if present, otherwise
    inserts it as the last line of the section. Section must already exist."""
    section_re = re.compile(
        r"(\[" + re.escape(section) + r"\]\n)(.*?)(?=\n\[|\Z)", re.DOTALL
    )
    match = section_re.search(text)
    if match is None:
        raise ValueError(f"[{section}] section not found in config")
    header, body = match.group(1), match.group(2)

    key_re = re.compile(r"^" + re.escape(key) + r"\s*=.*$", re.MULTILINE)
    if key_re.search(body):
        new_body = key_re.sub(f"{key} = {value}", body)
    else:
        new_body = body.rstrip("\n") + f"\n{key} = {value}"

    return text[:match.start()] + header + new_body + text[match.end():]


@app.get("/config/clock_speed")
async def get_clock_speed():
    return {"mhz": sim._pru_clock_mhz}


@app.put("/config/clock_speed")
async def put_clock_speed(request: Request):
    global sim
    body = await request.json()
    mhz = body.get("mhz")
    if mhz not in ALLOWED_CLOCK_MHZ:
        return JSONResponse(
            {"error": f"mhz must be one of {sorted(ALLOWED_CLOCK_MHZ)}"},
            status_code=400,
        )
    async with _config_lock:
        try:
            with open(config_path, "r") as f:
                text = f.read()
            text = _set_ini_value(text, "device", "pru_clock_mhz", str(mhz))
            text = _set_ini_value(text, "device", "pru1_clock_mhz", str(mhz))
            with open(config_path, "w") as f:
                f.write(text)
            sim = Simulator(config_path=config_path)
            _history["pru0"].clear()
            _history["rtu0"].clear()
            _history["pru1"].clear()
            return {"ok": True}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_clock_speed_endpoint.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the full existing server test suite to check for regressions**

Run: `pytest tests/test_server.py -v`
Expected: PASS (no regressions — nothing in Task 1 modifies existing routes)

- [ ] **Step 7: Commit**

```bash
git add ui/server.py tests/test_clock_speed_endpoint.py
git commit -m "feat: add /config/clock_speed endpoint for PRU core speed selection"
```

---

### Task 2: Frontend dropdown (HTML + CSS)

**Files:**
- Modify: `ui/static/index.html` (CSS rule at lines 59-60; controls bar at lines 1258-1262)

**Interfaces:**
- Produces: `<select id="pru-speed-select">` DOM element with `<option>` values `"200"`, `"225"`, `"250"`, `"300"`, `"333"` — consumed by Task 3's JS.
- Consumes: existing CSS custom properties used by sibling selects (none new needed).

- [ ] **Step 1: Extend the shared select CSS rule**

In `ui/static/index.html`, find (around line 59):

```css
    #core-select,
    #mc-partner-select {
```

Replace with:

```css
    #core-select,
    #mc-partner-select,
    #pru-speed-select {
```

- [ ] **Step 2: Add the dropdown markup**

In `ui/static/index.html`, find the controls bar block (around lines 1258-1263):

```html
    <select id="core-select">
      <option value="pru0">PRU0</option>
      <option value="rtu0">RTU0</option>
      <option value="pru1">PRU1</option>
    </select>
```

Insert immediately after the closing `</select>` of `#core-select`:

```html

    <select id="pru-speed-select" title="PRU core clock speed (applies to PRU0, RTU0, and PRU1)">
      <option value="200">200 MHz</option>
      <option value="225">225 MHz</option>
      <option value="250">250 MHz</option>
      <option value="300">300 MHz</option>
      <option value="333">333 MHz</option>
    </select>
```

- [ ] **Step 3: Manually verify markup renders**

Run: `python3 -c "import xml.dom.minidom, re; t = open('ui/static/index.html').read(); assert t.count('id=\"pru-speed-select\"') == 1; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add ui/static/index.html
git commit -m "feat: add PRU core speed dropdown markup to controls bar"
```

---

### Task 3: Frontend wiring (JS)

**Files:**
- Modify: `ui/static/app.js` (element refs near line 103-108; new logic near the config-modal block at lines 1909-1955)

**Interfaces:**
- Consumes: `GET /config/clock_speed` → `{"mhz": <number>}`, `PUT /config/clock_speed` (Task 1); `flashStatus(text, cssClass)` (`app.js:2869`); `refreshMemory()` (`app.js:2447`); `loadRegions()` (`app.js:2460`); `<select id="pru-speed-select">` (Task 2); existing `btnConfigSave` click handler (`app.js:1933-1954`).
- Produces: `loadClockSpeed()` — async function, no params, no return value; refreshes `#pru-speed-select`'s displayed value from the server. Used by page-load init and by the config-modal save handler.

- [ ] **Step 1: Add the element reference**

In `ui/static/app.js`, find (around line 103-108):

```js
const btnConfig       = document.getElementById("btn-config");
const configModal     = document.getElementById("config-modal");
const configTextarea  = document.getElementById("config-textarea");
```

Add a new line directly after:

```js
const btnConfig       = document.getElementById("btn-config");
const configModal     = document.getElementById("config-modal");
const configTextarea  = document.getElementById("config-textarea");
const pruSpeedSelect  = document.getElementById("pru-speed-select");
```

- [ ] **Step 2: Add `loadClockSpeed()` and the change handler**

In `ui/static/app.js`, find the end of the config-modal block (around lines 1953-1956):

```js
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
      loadRegions();
    } else {
      configError.textContent = data.error || "Unknown error";
    }
  } catch (e) {
    configError.textContent = String(e);
  } finally {
    btnConfigSave.disabled    = false;
    btnConfigSave.textContent = "Save & Reload";
  }
});

// ---- Help modal ------------------------------------------------------------
```

Replace with (adds `loadClockSpeed()` call in the save-success branch, then appends the new block before the Help modal comment):

```js
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
      loadRegions();
      loadClockSpeed();
    } else {
      configError.textContent = data.error || "Unknown error";
    }
  } catch (e) {
    configError.textContent = String(e);
  } finally {
    btnConfigSave.disabled    = false;
    btnConfigSave.textContent = "Save & Reload";
  }
});

// ---- PRU core speed selector ------------------------------------------------

async function loadClockSpeed() {
  try {
    const res  = await fetch("/config/clock_speed");
    const data = await res.json();
    pruSpeedSelect.value = String(Math.round(data.mhz));
  } catch (e) {
    // leave dropdown at its last-known value
  }
}
loadClockSpeed();

pruSpeedSelect.addEventListener("change", async () => {
  pruSpeedSelect.disabled = true;
  try {
    const res = await fetch("/config/clock_speed", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mhz: Number(pruSpeedSelect.value) }),
    });
    const data = await res.json();
    if (data.ok) {
      flashStatus("RELOADED", "reloaded");
      refreshMemory();
      loadRegions();
    } else {
      alert(data.error || "Failed to set clock speed");
      loadClockSpeed();
    }
  } catch (e) {
    alert(String(e));
    loadClockSpeed();
  } finally {
    pruSpeedSelect.disabled = false;
  }
});

// ---- Help modal ------------------------------------------------------------
```

- [ ] **Step 3: Manually verify syntax**

Run: `node --check ui/static/app.js`
Expected: no output (exit code 0) — confirms no JS syntax errors. If `node` is unavailable, run `python3 -c "import subprocess,sys; sys.exit(0)"` is not a substitute; instead visually confirm balanced braces around the new block before committing.

- [ ] **Step 4: Commit**

```bash
git add ui/static/app.js
git commit -m "feat: wire PRU core speed dropdown to /config/clock_speed"
```

---

### Task 4: End-to-end manual verification

**Files:** none (verification only)

- [ ] **Step 1: Start the dashboard server**

Run: `python3 -m uvicorn ui.server:app --reload --port 8000` (run in background or a separate terminal)

- [ ] **Step 2: Open the dashboard and confirm the dropdown**

Navigate to `http://localhost:8000/` in a browser. Confirm `#pru-speed-select` appears in the controls bar right after the core select, showing "200 MHz" selected (matches default `memory.cfg`).

- [ ] **Step 3: Change speed and verify reload + persistence**

Select "300 MHz" from the dropdown. Confirm the status badge flashes "RELOADED". Then run:

```bash
grep -E "pru_clock_mhz|pru1_clock_mhz" memory.cfg
```

Expected: both lines read `300`.

- [ ] **Step 4: Revert `memory.cfg` to the checked-in default**

```bash
git checkout -- memory.cfg
```

- [ ] **Step 5: Stop the server**

Stop the `uvicorn` process started in Step 1.

---

## Self-Review Notes

- **Spec coverage:** Backend endpoints (spec §2) → Task 1. Frontend HTML/CSS (spec §3) → Task 2. Frontend JS wiring (spec §3) → Task 3. Testing (spec §4) → Task 1 Step 1. Manual end-to-end + risk acknowledgment (spec §5) → Task 4.
- **Type consistency:** `ALLOWED_CLOCK_MHZ`, `_set_ini_value`, `loadClockSpeed`, `pruSpeedSelect` names are consistent across all tasks that reference them.
- **No placeholders:** all steps contain complete, runnable code.
