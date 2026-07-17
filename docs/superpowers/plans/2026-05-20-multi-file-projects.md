# Multi-File Projects Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-file project support — `.include` resolution, project subfolders with optional `project.json` manifests, shared `source/lib/`, and a tabbed editor UI.

**Architecture:** Thread `include_paths` through the existing preprocessor API (already accepts it at the instance level); update server endpoints to expose structured file listings and subpath access; replace the single-filename editor with a tab bar backed by an in-memory tab array.

**Tech Stack:** Python/FastAPI backend, vanilla JS frontend, existing `Preprocessor`/`Parser`/`PRUCore`/`Simulator` call chain.

---

## File Structure

| File | Change |
|------|--------|
| `core/preprocessor.py` | Add `include_paths` param to `process_text` |
| `core/parser.py` | Add `include_paths` param to `parse_text`, pass through |
| `core/pru_core.py` | Add `include_paths` param to `load_asm`, pass through |
| `simulator.py` | Add `include_paths` param to `load`, pass through |
| `tests/test_multi_file.py` | New — include_paths integration tests |
| `ui/server.py` | Structured `GET /source`; subpath `GET/PUT /source/{path:path}`; `filename` in `load` handler |
| `ui/static/index.html` | Add tab bar div + CSS; add Open Project button; remove `asm-filename` input |
| `ui/static/app.js` | Tab state, tab functions, updated Open/Save/Load & Assemble, Open Project, startup |

---

## Task 1: Thread `include_paths` through the call chain

**Files:**
- Modify: `core/preprocessor.py:44`
- Modify: `core/parser.py:89,100`
- Modify: `core/pru_core.py:62,68`
- Modify: `simulator.py:82,86`
- Create: `tests/test_multi_file.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_multi_file.py`:

```python
"""Integration tests for multi-file include path threading."""
import tempfile
from pathlib import Path

import pytest

from core.preprocessor import Preprocessor
from core.parser import Parser
from simulator import Simulator


def test_preprocessor_include_paths_resolves_include():
    """process_text resolves .include when include_paths contains the file's dir."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "constants.inc").write_text(".set MY_VAL, 42\n", encoding="utf-8")
        source = '.include "constants.inc"\nldi r0, MY_VAL\nhalt\n'
        pp = Preprocessor()
        lines = pp.process_text(source, include_paths=[tmpdir])
        joined = " ".join(lines).lower()
        assert "ldi" in joined and "42" in joined


def test_preprocessor_include_paths_does_not_mutate_state():
    """Passing include_paths to process_text must not permanently alter self.include_paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pp = Preprocessor()
        original = list(pp.include_paths)
        pp.process_text("ldi r0, 1\nhalt\n", include_paths=[tmpdir])
        assert pp.include_paths == original


def test_parser_include_paths_assembles_included_file():
    """parse_text with include_paths assembles source that .include's a define file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "defs.inc").write_text(".set MAGIC, 0x55\n", encoding="utf-8")
        source = '.include "defs.inc"\nldi r1, MAGIC\nhalt\n'
        parser = Parser()
        instrs = parser.parse_text(source, include_paths=[tmpdir])
        assert len(instrs) == 2
        assert instrs[0].opcode == "LDI"


def test_simulator_load_include_paths_succeeds():
    """Simulator.load passes include_paths so included defines are resolved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "reg_vals.inc").write_text(".set BASE, 10\n", encoding="utf-8")
        source = '.include "reg_vals.inc"\nldi r0, BASE\nhalt\n'
        sim = Simulator()
        errors = sim.load("pru0", source, include_paths=[tmpdir])
        assert errors == []
        assert sim.cores["pru0"].instructions[0].opcode == "LDI"


def test_simulator_load_without_filename_still_works():
    """Omitting include_paths leaves existing single-file behavior unchanged."""
    sim = Simulator()
    errors = sim.load("pru0", "ldi r0, 1\nhalt\n")
    assert errors == []
    assert len(sim.cores["pru0"].instructions) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_multi_file.py -v
```

Expected: 4 failures (TypeError: unexpected keyword argument `include_paths`), 1 pass (`test_simulator_load_without_filename_still_works`).

- [ ] **Step 3: Update `core/preprocessor.py` — add `include_paths` param to `process_text`**

Change line 44 from:
```python
    def process_text(self, text: str) -> list[str]:
        """Process raw assembly text, returning cleaned instruction lines."""
        raw_lines = text.splitlines()
        return self._process_lines(raw_lines)
```
To:
```python
    def process_text(self, text: str, include_paths: list[str] | None = None) -> list[str]:
        """Process raw assembly text, returning cleaned instruction lines."""
        if include_paths is not None:
            old_paths = self.include_paths
            self.include_paths = list(include_paths)
            result = self._process_lines(text.splitlines())
            self.include_paths = old_paths
            return result
        return self._process_lines(text.splitlines())
```

- [ ] **Step 4: Update `core/parser.py` — add `include_paths` param to `parse_text`**

Change line 89 from:
```python
    def parse_text(self, text: str) -> list[Instruction]:
```
To:
```python
    def parse_text(self, text: str, include_paths: list[str] | None = None) -> list[Instruction]:
```

Change line 100 from:
```python
        preprocessed = self.preprocessor.process_text(text)
```
To:
```python
        preprocessed = self.preprocessor.process_text(text, include_paths)
```

- [ ] **Step 5: Update `core/pru_core.py` — add `include_paths` param to `load_asm`**

Change line 62 from:
```python
    def load_asm(self, source: str) -> list[str]:
```
To:
```python
    def load_asm(self, source: str, include_paths: list[str] | None = None) -> list[str]:
```

Change line 68 from:
```python
            self.instructions = self._parser.parse_text(source)
```
To:
```python
            self.instructions = self._parser.parse_text(source, include_paths)
```

- [ ] **Step 6: Update `simulator.py` — add `include_paths` param to `load`**

Change line 82 from:
```python
    def load(self, core: str, source: str) -> list[str]:
        """Parse and load assembly *source* into *core*.

        Returns a list of error strings (empty on success).
        """
        return self._get_core(core).load_asm(source)
```
To:
```python
    def load(self, core: str, source: str, include_paths: list[str] | None = None) -> list[str]:
        """Parse and load assembly *source* into *core*.

        Returns a list of error strings (empty on success).
        """
        return self._get_core(core).load_asm(source, include_paths)
```

- [ ] **Step 7: Run all tests**

```
python -m pytest --tb=short -q
```

Expected: 421 passed (416 original + 5 new).

- [ ] **Step 8: Commit**

```bash
git add core/preprocessor.py core/parser.py core/pru_core.py simulator.py tests/test_multi_file.py
git commit -m "feat: thread include_paths through preprocessor → parser → pru_core → simulator"
```

---

## Task 2: Update server.py endpoints

**Files:**
- Modify: `ui/server.py`

- [ ] **Step 1: Replace `_safe_source_path` with `_safe_source_subpath` and update `GET /source`**

In `ui/server.py`, replace the `_safe_source_path` function (lines 27–35) with:

```python
def _safe_source_subpath(path: str) -> pathlib.Path | None:
    """Return resolved path within SOURCE_DIR, allowing subdirectories. Returns None if unsafe."""
    if not path or len(path) > 300:
        return None
    if path.suffix_lower := pathlib.Path(path).suffix.lower():
        pass
    try:
        candidate = (SOURCE_DIR / path).resolve()
        candidate.relative_to(SOURCE_DIR.resolve())  # raises ValueError if outside SOURCE_DIR
    except (ValueError, OSError):
        return None
    if candidate.suffix.lower() not in ('.asm', '.s', '.inc', '.json'):
        return None
    return candidate
```

Wait — that has a syntax error. Use this instead:

```python
def _safe_source_subpath(path: str) -> pathlib.Path | None:
    """Return resolved path within SOURCE_DIR, allowing subdirectories. Returns None if unsafe."""
    if not path or len(path) > 300:
        return None
    try:
        candidate = (SOURCE_DIR / path).resolve()
        candidate.relative_to(SOURCE_DIR.resolve())  # raises ValueError if outside SOURCE_DIR
    except (ValueError, OSError):
        return None
    if candidate.suffix.lower() not in ('.asm', '.s', '.inc', '.json'):
        return None
    return candidate
```

- [ ] **Step 2: Replace `GET /source` to return structured response**

Replace lines 98–102:
```python
@app.get("/source")
async def list_source():
    SOURCE_DIR.mkdir(exist_ok=True)
    files = sorted(p.name for p in SOURCE_DIR.iterdir()
                   if p.suffix.lower() in ('.asm', '.s', '.inc'))
    return files
```
With:
```python
@app.get("/source")
async def list_source():
    SOURCE_DIR.mkdir(exist_ok=True)
    files = sorted(
        p.name for p in SOURCE_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in ('.asm', '.s', '.inc')
    )
    lib_dir = SOURCE_DIR / "lib"
    lib = sorted(
        p.name for p in lib_dir.iterdir()
        if p.is_file() and p.suffix.lower() in ('.asm', '.s', '.inc')
    ) if lib_dir.exists() else []
    projects = {}
    for subdir in sorted(SOURCE_DIR.iterdir()):
        if subdir.is_dir() and subdir.name != "lib":
            proj_files = sorted(
                p.name for p in subdir.iterdir()
                if p.is_file() and p.suffix.lower() in ('.asm', '.s', '.inc', '.json')
            )
            if any(f.endswith(('.asm', '.s')) for f in proj_files):
                projects[subdir.name] = proj_files
    return {"files": files, "projects": projects, "lib": lib}
```

- [ ] **Step 3: Replace single-file GET/PUT routes with subpath-aware versions**

Replace lines 105–120 (the old `GET /source/{filename}` and `PUT /source/{filename}`):

```python
@app.get("/source/{path:path}")
async def get_source_file(path: str):
    resolved = _safe_source_subpath(path)
    if resolved is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    if not resolved.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    return PlainTextResponse(resolved.read_text(encoding="utf-8"))


@app.put("/source/{path:path}")
async def put_source_file(path: str, request: Request):
    resolved = _safe_source_subpath(path)
    if resolved is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    text = (await request.body()).decode("utf-8")
    resolved.write_text(text, encoding="utf-8")
    return {"ok": True}
```

- [ ] **Step 4: Update `load` WebSocket handler to compute `include_paths` from `filename`**

In the WebSocket handler, replace lines 145–150:
```python
        if action == "load":
            _history[core].clear()
            errors = sim.load(core, msg["source"])
            if errors:
                await websocket.send_json({"type": "error", "errors": errors})
            await _send_state(websocket, core)
```
With:
```python
        if action == "load":
            _history[core].clear()
            filename = msg.get("filename")
            include_paths = None
            if filename:
                file_dir = SOURCE_DIR / pathlib.Path(filename).parent
                lib_dir = SOURCE_DIR / "lib"
                include_paths = [str(file_dir)]
                if lib_dir.exists():
                    include_paths.append(str(lib_dir))
            errors = sim.load(core, msg["source"], include_paths)
            if errors:
                await websocket.send_json({"type": "error", "errors": errors})
            await _send_state(websocket, core)
```

- [ ] **Step 5: Verify server starts without errors**

```
python ui/server.py
```

Expected: Server starts on port 8080 with no import errors.

- [ ] **Step 6: Run full test suite**

```
python -m pytest --tb=short -q
```

Expected: 421 passed.

- [ ] **Step 7: Commit**

```bash
git add ui/server.py
git commit -m "feat: structured GET /source, subpath file access, include_paths in load handler"
```

---

## Task 3: Tab bar HTML/CSS and tab state infrastructure

**Files:**
- Modify: `ui/static/index.html`
- Modify: `ui/static/app.js`

- [ ] **Step 1: Add tab bar CSS to `index.html`**

Inside the `<style>` block, after the `/* ---- Source editor panel */` section (after the `#asm-source` rule), add:

```css
    /* ---- Editor tab bar ----------------------------------------------------- */
    #editor-tab-bar {
      display: flex;
      flex-shrink: 0;
      overflow-x: auto;
      background: #1e1e1e;
      border-bottom: 1px solid var(--border);
      scrollbar-width: thin;
    }

    .editor-tab {
      display: flex;
      align-items: center;
      gap: 5px;
      padding: 3px 10px;
      font-size: 11px;
      color: var(--text-dim);
      cursor: pointer;
      border-right: 1px solid var(--border);
      white-space: nowrap;
      user-select: none;
      flex-shrink: 0;
    }
    .editor-tab:hover { background: #2a2a2a; color: var(--text); }
    .editor-tab.active {
      background: var(--panel);
      color: var(--text);
      border-top: 1px solid var(--accent);
    }
    .tab-close {
      font-size: 14px;
      line-height: 1;
      opacity: 0.5;
      flex-shrink: 0;
    }
    .tab-close:hover { opacity: 1; color: var(--halted); }
```

- [ ] **Step 2: Add tab bar div + Open Project button; remove `asm-filename` input from `index.html`**

In the `#editor-panel` section, replace the entire panel markup (lines 803–823):

```html
      <!-- Assembly editor panel -->
      <div class="panel" id="editor-panel">
        <div class="panel-title" data-panel-id="editor">
          <span>Assembly Editor</span>
          <select id="mc-load-core" style="display:none;background:var(--btn);color:var(--text);border:1px solid var(--border);padding:2px 4px;border-radius:3px;font-family:inherit;font-size:12px;" title="Load target core">
            <option value="pru0">→ PRU0</option>
            <option value="rtu0">→ RTU0</option>
          </select>
          <input type="file" id="file-input" accept=".asm,.inc,.s" style="display:none" />
          <button id="btn-file" class="btn-load">Open</button>
          <button id="btn-open-project" class="btn-load">Project</button>
          <button id="btn-save-asm" class="btn-load">Save</button>
          <button id="btn-load" class="btn-load">Load &amp; Assemble</button>
        </div>
        <div id="editor-tab-bar"></div>
        <textarea id="asm-source" spellcheck="false" placeholder="Enter PRU assembly here...
Example:
    LDI r0, 0x42
    LDI r1, 0x10
    ADD r2, r0, r1
    HALT"></textarea>
      </div>
```

- [ ] **Step 3: Add tab state variables to `app.js`**

After the `// ---- Multi-core state` block (after line 29), add:

```js
// ---- Editor tab state ------------------------------------------------------
let tabs = [];              // [{path: string, content: string, dirty: boolean}]
let activeTab = -1;         // index into tabs[], -1 = no open tabs
let activeProjectManifest = null;  // {project: string, files: string[]} or null
```

- [ ] **Step 4: Add tab DOM ref and tab functions to `app.js`**

In the `// ---- DOM references` block, remove the `asmFilenameInput` declaration and add:

```js
const btnOpenProject  = document.getElementById("btn-open-project");
```

(Remove the line: `const asmFilenameInput = document.getElementById("asm-filename");`)

After the `initSpadState` function, add:

```js
// ---- Tab management --------------------------------------------------------

function renderTabs() {
  const bar = document.getElementById('editor-tab-bar');
  if (!bar) return;
  bar.innerHTML = '';
  tabs.forEach((tab, i) => {
    const name = tab.path.split('/').pop();
    const el = document.createElement('div');
    el.className = 'editor-tab' + (i === activeTab ? ' active' : '');

    const label = document.createElement('span');
    label.className = 'tab-label';
    label.textContent = (i === activeTab ? '▶ ' : '') + name + (tab.dirty ? '*' : '');

    const close = document.createElement('span');
    close.className = 'tab-close';
    close.textContent = '×';
    close.addEventListener('click', e => { e.stopPropagation(); closeTab(i); });

    el.appendChild(label);
    el.appendChild(close);
    el.addEventListener('click', () => switchTab(i));
    bar.appendChild(el);
  });
}

function switchTab(i) {
  if (i === activeTab) return;
  if (activeTab >= 0 && tabs[activeTab]) {
    tabs[activeTab].content = asmSource.value;
  }
  activeTab = i;
  asmSource.value = tabs[i] ? tabs[i].content : '';
  renderTabs();
}

function closeTab(i) {
  if (tabs[i] && tabs[i].dirty) {
    if (!confirm(`Close ${tabs[i].path.split('/').pop()} without saving?`)) return;
  }
  // If this was the only tab in a project, clear manifest
  if (activeProjectManifest) {
    const proj = activeProjectManifest.project;
    const remaining = tabs.filter((t, idx) => idx !== i && t.path.startsWith(proj + '/'));
    if (remaining.length === 0) activeProjectManifest = null;
  }
  tabs.splice(i, 1);
  if (activeTab >= tabs.length) activeTab = tabs.length - 1;
  asmSource.value = activeTab >= 0 && tabs[activeTab] ? tabs[activeTab].content : '';
  renderTabs();
}

function openFileAsTab(path, content) {
  const existing = tabs.findIndex(t => t.path === path);
  if (existing >= 0) {
    switchTab(existing);
    return;
  }
  if (activeTab >= 0 && tabs[activeTab]) {
    tabs[activeTab].content = asmSource.value;
  }
  tabs.push({ path, content, dirty: false });
  activeTab = tabs.length - 1;
  asmSource.value = content;
  renderTabs();
}

async function initEditorTabs() {
  try {
    const res = await fetch('/source/program.asm');
    const text = res.ok ? await res.text() : '';
    openFileAsTab('program.asm', text);
  } catch (_) {
    tabs.push({ path: 'program.asm', content: '', dirty: false });
    activeTab = 0;
    renderTabs();
  }
}
```

- [ ] **Step 5: Add textarea dirty-tracking listener to `app.js`**

After the `asmSource` DOM reference declaration, add:

```js
asmSource.addEventListener('input', () => {
  if (activeTab >= 0 && tabs[activeTab] && !tabs[activeTab].dirty) {
    tabs[activeTab].dirty = true;
    renderTabs();
  }
});
```

- [ ] **Step 6: Call `initEditorTabs` from `initUI`**

In `initUI()` (around line 179), add `initEditorTabs()` at the end:

```js
function initUI() {
  initSpadState();
  buildRegTable();
  buildPinGrid(gpoGrid, 20, "gpo", null);
  buildPinGrid(gpiGrid, 20, "gpi", handleGpiClick);
  initLayout('sc');
  initEditorTabs();
}
```

- [ ] **Step 7: Verify in browser**

Start server (`python ui/server.py`), open `http://localhost:8080`. Confirm:
- Tab bar appears below editor title
- One tab `▶ program.asm` visible
- Editing textarea adds `*` to tab label
- Clicking `×` on the tab closes it (with confirm if dirty)

- [ ] **Step 8: Commit**

```bash
git add ui/static/index.html ui/static/app.js
git commit -m "feat: add editor tab bar with tab state management"
```

---

## Task 4: Open, Open Project, Save, Load & Assemble integration

**Files:**
- Modify: `ui/static/app.js`

- [ ] **Step 1: Update "Open" button handler to use structured `/source` response**

Replace the existing `btnFile.addEventListener("click", ...)` handler (lines ~510–544) with:

```js
btnFile.addEventListener("click", async () => {
  document.getElementById("_src-menu")?.remove();
  let sourceData = { files: [], lib: [], projects: {} };
  try { sourceData = await fetch("/source").then(r => r.json()); } catch (_) {}

  const allFiles = [
    ...sourceData.files,
    ...sourceData.lib.map(f => 'lib/' + f),
  ];
  if (allFiles.length === 0) { fileInput.click(); return; }

  const menu = document.createElement("div");
  menu.id = "_src-menu";
  menu.style.cssText = "position:fixed;background:#252526;border:1px solid #3c3c3c;border-radius:4px;z-index:2000;min-width:200px;max-height:300px;overflow-y:auto;box-shadow:0 4px 12px rgba(0,0,0,.6);font-size:12px;";
  const rect = btnFile.getBoundingClientRect();
  menu.style.left = rect.left + "px";
  menu.style.top  = (rect.bottom + 4) + "px";

  allFiles.forEach(path => {
    const item = document.createElement("div");
    item.style.cssText = "padding:6px 12px;cursor:pointer;color:#d4d4d4;";
    item.textContent = path;
    item.addEventListener("mouseover", () => item.style.background = "#094771");
    item.addEventListener("mouseout",  () => item.style.background = "");
    item.addEventListener("click", async () => {
      menu.remove();
      activeProjectManifest = null;
      try {
        const text = await fetch(`/source/${encodeURIComponent(path)}`).then(r => r.text());
        openFileAsTab(path, text);
      } catch (e) { console.error(e); }
    });
    menu.appendChild(item);
  });
  document.body.appendChild(menu);
  const close = (e) => {
    if (!menu.contains(e.target) && e.target !== btnFile) {
      menu.remove(); document.removeEventListener("click", close);
    }
  };
  setTimeout(() => document.addEventListener("click", close), 0);
});
```

- [ ] **Step 2: Update `fileInput` change handler to open as tab**

Replace the existing `fileInput.addEventListener("change", ...)` (lines ~557–566):

```js
fileInput.addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (ev) => {
    activeProjectManifest = null;
    openFileAsTab(file.name, ev.target.result);
  };
  reader.readAsText(file);
  fileInput.value = "";
});
```

- [ ] **Step 3: Add "Open Project" button handler**

After the `fileInput` change handler, add:

```js
// ---- Open Project ----------------------------------------------------------

async function openProject(projName, projFiles) {
  let manifest = null;
  try {
    const res = await fetch(`/source/${projName}/project.json`);
    if (res.ok) {
      const m = await res.json();
      if (Array.isArray(m.files)) manifest = m;
    }
  } catch (_) {}

  const asmFiles = manifest
    ? manifest.files
    : projFiles.filter(f => f.endsWith('.asm') || f.endsWith('.s')).sort();
  const incFiles = projFiles.filter(
    f => !asmFiles.includes(f) && f !== 'project.json'
  );
  const orderedFiles = [...asmFiles, ...incFiles];

  for (const fname of orderedFiles) {
    const path = `${projName}/${fname}`;
    try {
      const text = await fetch(`/source/${path}`).then(r => r.text());
      openFileAsTab(path, text);
    } catch (e) { console.error(`Failed to open ${path}:`, e); }
  }

  const firstAsm = tabs.findIndex(
    t => t.path.startsWith(projName + '/') &&
         (t.path.endsWith('.asm') || t.path.endsWith('.s'))
  );
  if (firstAsm >= 0) switchTab(firstAsm);

  activeProjectManifest = manifest ? { project: projName, files: manifest.files } : null;
}

btnOpenProject.addEventListener("click", async () => {
  document.getElementById("_proj-menu")?.remove();
  let sourceData = { files: [], lib: [], projects: {} };
  try { sourceData = await fetch("/source").then(r => r.json()); } catch (_) {}

  const projectNames = Object.keys(sourceData.projects);
  if (projectNames.length === 0) { alert("No projects found in source/"); return; }

  const menu = document.createElement("div");
  menu.id = "_proj-menu";
  menu.style.cssText = "position:fixed;background:#252526;border:1px solid #3c3c3c;border-radius:4px;z-index:2000;min-width:200px;max-height:300px;overflow-y:auto;box-shadow:0 4px 12px rgba(0,0,0,.6);font-size:12px;";
  const rect = btnOpenProject.getBoundingClientRect();
  menu.style.left = rect.left + "px";
  menu.style.top  = (rect.bottom + 4) + "px";

  projectNames.forEach(projName => {
    const item = document.createElement("div");
    item.style.cssText = "padding:6px 12px;cursor:pointer;color:#d4d4d4;";
    item.textContent = projName;
    item.addEventListener("mouseover", () => item.style.background = "#094771");
    item.addEventListener("mouseout",  () => item.style.background = "");
    item.addEventListener("click", async () => {
      menu.remove();
      await openProject(projName, sourceData.projects[projName]);
    });
    menu.appendChild(item);
  });
  document.body.appendChild(menu);
  const close = (e) => {
    if (!menu.contains(e.target) && e.target !== btnOpenProject) {
      menu.remove(); document.removeEventListener("click", close);
    }
  };
  setTimeout(() => document.addEventListener("click", close), 0);
});
```

- [ ] **Step 4: Update "Save" button handler**

Replace the existing `btnSaveAsm.addEventListener("click", ...)` (lines ~546–555):

```js
btnSaveAsm.addEventListener("click", async () => {
  if (activeTab < 0 || !tabs[activeTab]) return;
  tabs[activeTab].content = asmSource.value;
  const path = tabs[activeTab].path;
  try {
    const res = await fetch(`/source/${path}`, { method: "PUT", body: asmSource.value });
    if (res.ok) {
      tabs[activeTab].dirty = false;
      renderTabs();
      flashStatus("SAVED", "reloaded");
    } else {
      const d = await res.json();
      console.error("Save failed:", d.error);
    }
  } catch (e) { console.error(e); }
});
```

- [ ] **Step 5: Update "Load & Assemble" handler to auto-save, send `filename`, handle project manifest**

Replace the existing `btnLoad.addEventListener("click", ...)` (lines ~486–502):

```js
btnLoad.addEventListener("click", async () => {
  stopRun(); stopSim();
  clearErrors();

  // Sync active textarea content into tab buffer
  if (activeTab >= 0 && tabs[activeTab]) {
    tabs[activeTab].content = asmSource.value;
  }

  // Auto-save all dirty tabs
  for (let i = 0; i < tabs.length; i++) {
    const tab = tabs[i];
    if (tab.dirty) {
      try {
        await fetch(`/source/${tab.path}`, { method: "PUT", body: tab.content });
        tab.dirty = false;
      } catch (e) { console.error(`Auto-save failed for ${tab.path}:`, e); }
    }
  }
  renderTabs();

  // Determine source text and filename for include path resolution
  let source = asmSource.value;
  let filename;
  if (activeTab >= 0 && tabs[activeTab]) {
    const activeTabPath = tabs[activeTab].path;
    const projName = activeTabPath.includes('/') ? activeTabPath.split('/')[0] : null;

    if (projName && activeProjectManifest &&
        activeProjectManifest.project === projName &&
        Array.isArray(activeProjectManifest.files)) {
      // Concatenate tabs in manifest order
      const parts = activeProjectManifest.files.map(fname => {
        const path = `${projName}/${fname}`;
        const t = tabs.find(tab => tab.path === path);
        return t ? t.content : '';
      });
      source = parts.join('\n');
      filename = `${projName}/${activeProjectManifest.files[0]}`;
    } else {
      source = asmSource.value;
      filename = activeTabPath;
    }
  }

  if (multiCoreMode) {
    const targetCore = mcLoadCore.value || "pru0";
    mcPrevRegs[targetCore] = new Array(32).fill("0x00000000");
    mcLastSourceKey[targetCore] = '';
    const srcList = document.getElementById(`mc-${targetCore}-source-list`);
    if (srcList) srcList.innerHTML = "";
    sendAction({ action: "load", core: targetCore, source, filename });
  } else {
    prevRegisters = new Array(32).fill("0x00000000");
    sourceList.innerHTML = "";
    sendAction({ action: "load", core: currentCore, source, filename });
  }
});
```

- [ ] **Step 6: Run full test suite**

```
python -m pytest --tb=short -q
```

Expected: 421 passed.

- [ ] **Step 7: Smoke test in browser**

Start server (`python ui/server.py`) and verify:

1. **Single file with `.include`**: Create `source/lib/constants.inc` containing `.set LED_PIN, 5`. Create `source/test_include.asm` containing `.include "constants.inc"\nldi r0, LED_PIN\nhalt`. Open `test_include.asm` via Open button, click Load & Assemble. Confirm no errors and source panel shows `ldi r0, 5`.

2. **Project with manifest**: Create `source/my_proj/` with `main.asm` (`ldi r0, 1\nhalt`) and `project.json` (`{"files":["main.asm"]}`). Click Project button, select `my_proj`, confirm two tabs open. Click Load & Assemble. Confirm source panel shows the instruction.

3. **Dirty indicator**: Edit a tab's content — confirm `*` appears on tab label. Save — confirm `*` disappears.

4. **Tab close**: Open two files, close one — confirm the other remains active.

- [ ] **Step 8: Commit**

```bash
git add ui/static/app.js
git commit -m "feat: Open Project button, tab-aware Save and Load & Assemble with filename"
```
