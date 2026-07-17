# Multi-File Projects — Design Spec

**Date:** 2026-05-20
**Status:** Approved

## Overview

Add multi-file project support to the PRU simulator. Users can write one main `.asm` that `.include`s shared constants/macros from `.inc` files, split large programs across multiple `.asm` files via a project manifest, and maintain a shared library of reusable `.inc` files. The editor gains a tab bar for managing open files. The assembler's existing `.include` resolution is wired up end-to-end.

---

## 1. Disk & Project Structure

```
source/
├── lib/                    # Shared library — always on include path
│   └── constants.inc
├── my_project/             # Project subfolder
│   ├── project.json        # Optional manifest (only needed for multi-.asm concat)
│   ├── main.asm            # Entry file
│   ├── isr.asm             # Additional .asm files
│   └── local_macros.inc    # Project-local includes
└── program.asm             # Flat single files work unchanged
```

### `project.json` schema

Only required when multiple `.asm` files must be concatenated in a specific order:

```json
{
  "name": "My Project",
  "files": ["init.asm", "main.asm", "isr.asm"]
}
```

- `files` — assembly files concatenated in this order before assembling.
- If `project.json` is absent or has no `files` key, any `.asm` in the folder is assembled independently using `.include` directives.
- `source/lib/` is always appended to the include path for every assembly operation, if the directory exists.
- Flat `source/*.asm` files are unchanged — include path is `[source/, source/lib/]`.

---

## 2. Backend Changes

### 2a. Include path threading

The `load` WebSocket message gains an optional `filename` field:

```json
{ "action": "load", "core": "pru0", "source": "...", "filename": "my_project/main.asm" }
```

Server derives include paths:

```python
SOURCE_ROOT = Path("source")
file_dir = SOURCE_ROOT / Path(filename).parent   # e.g. source/my_project/
lib_dir  = SOURCE_ROOT / "lib"
include_paths = [str(file_dir)] + ([str(lib_dir)] if lib_dir.exists() else [])
```

| File location | `include_paths` |
|---|---|
| `source/program.asm` | `[source/, source/lib/]` |
| `source/my_project/main.asm` | `[source/my_project/, source/lib/]` |
| No `filename` sent (backward compat) | `[source/]` |

`include_paths` is threaded through:

```
server.py load handler
  → Simulator.load(core, source, include_paths)
  → PRUCore.load_asm(source, include_paths)
  → Parser.parse_text(text, include_paths)
  → Preprocessor.process_text(text, include_paths)   ← already accepts this
```

Only the call-site signatures change; the preprocessor implementation is unchanged.

### 2b. Extended `GET /source`

Response changes from a flat list to a structured object:

```json
{
  "files":    ["program.asm", "mac_example.asm"],
  "projects": { "my_project": ["main.asm", "isr.asm", "local_macros.inc"] },
  "lib":      ["constants.inc"]
}
```

- `files` — `.asm`/`.s`/`.inc` files directly under `source/` root.
- `projects` — subdirectories (excluding `lib/`) that contain at least one `.asm` file, mapped to their file lists.
- `lib` — files under `source/lib/`.

### 2c. `PUT /source/{path:path}`

Extended to `mkdir -p` the parent directory before writing, so saving `my_project/new_file.asm` works even when the subfolder does not yet exist.

No new endpoints. Server does not parse `project.json` — the client owns that entirely.

---

## 3. Editor UI Changes

### Tab bar

Added between the editor title bar and the textarea:

```
┌─────────────────────────────────────────────────────────────────┐
│ Assembly Editor  [→PRU0▾]  [Open] [Open Project] [Save] [Load & Assemble] │
├─────────────────────────────────────────────────────────────────┤
│ ▶ main.asm ×  │  isr.asm ×  │  local_macros.inc ×  │           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  <textarea>                                                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

- **▶** on the active tab — marks it as the assembly entry point.
- **×** — closes tab (prompts if unsaved changes).
- **`*` suffix** on tab label — dirty indicator (content modified since last save).
- Switching tabs: saves textarea content to the outgoing tab's in-memory buffer, loads incoming tab's content into textarea.
- The `asm-filename` input is removed; each tab owns its path.

### Tab state model

Each tab:
```js
{ path: 'my_project/main.asm', content: '...', dirty: false }
```

Active tab index tracked separately. Textarea always reflects `tabs[activeIdx].content`.

### Buttons

**"Open"** — unchanged behavior. File list sourced from `files` + `lib` arrays in the structured `GET /source` response.

**"Open Project"** — new button. Shows a dropdown of project folder names from the `projects` key. Selecting a project:
1. Fetches `GET /source/my_project/project.json` (if exists) to get tab order.
2. Fetches each file's content via `GET /source/my_project/{file}`.
3. Opens each as a tab (manifest order if `files` present, else alphabetical).
4. Makes the first `.asm` tab active.

**"Save"** — saves active tab content via `PUT /source/{path}`, clears dirty flag.

**"Load & Assemble"**:
1. Auto-saves all dirty tabs (`PUT /source/{path}` for each).
2. Derive project folder from active tab's path: if path contains `/` (e.g. `my_project/main.asm`), project folder = `my_project`; otherwise no project folder (flat file).
3. If a project folder exists and that folder's `project.json` (already in memory from Open Project) has a `files` list:
   - Concatenate tab contents in `files` order → `source`; `filename` = first file in list (e.g. `my_project/init.asm`).
4. Otherwise: `source` = active tab content; `filename` = active tab path.
5. Sends `{ action: "load", core, source, filename }` WebSocket message.

### Startup

Editor initializes with one tab: `program.asm` loaded from server, same as today.

---

## 4. Assembly Flow & Edge Cases

### Normal flow

1. User clicks Load & Assemble.
2. All dirty tabs auto-saved to disk.
3. WebSocket `load` sent with `source` + `filename`.
4. Server derives `include_paths`, threads to preprocessor.
5. Preprocessor resolves `.include` directives from disk.
6. Assembled result (or errors) displayed as today.

### Concatenated project

- Client joins tab contents in `files` order with `\n`.
- `filename` set to the first file for include path derivation.
- **Known limitation:** line numbers in error messages refer to the concatenated blob and do not map back to individual source files.

### `source/lib/` absent

Silently omitted from include paths — no error, no warning.

### Backward compatibility

If `filename` is omitted from the `load` message, `include_paths` defaults to `[source/]` — existing single-file behavior is unchanged.

---

## File Structure Changes

| File | Change |
|------|--------|
| `core/preprocessor.py` | No change |
| `core/parser.py` | Accept optional `include_paths`, pass to preprocessor |
| `core/pru_core.py` | Accept optional `include_paths`, pass to parser |
| `simulator.py` | Accept optional `include_paths`, pass to `PRUCore.load_asm` |
| `ui/server.py` | Derive `include_paths` from `filename`; extend `GET /source`; `mkdir -p` on PUT |
| `ui/static/app.js` | Tab bar state + rendering; Open Project button; updated Load & Assemble; remove `asm-filename` input |
| `ui/static/index.html` | Add tab bar markup + CSS; replace `asm-filename` input with Open Project button |

---

## Testing

- Single flat file with no `.include` — assembles unchanged.
- Single flat file with `.include "lib/constants.inc"` — resolves from `source/lib/`.
- Project folder, `.include`-based — entry file includes project-local `.inc`; assembles correctly.
- Project folder, `files` manifest — tabs concatenated in order; assembly runs as one unit.
- Open Project button — opens all project files as tabs in correct order.
- Auto-save before assemble — unsaved tab edits reach disk before preprocessor reads includes.
- Dirty indicator — appears on edit, clears on save.
- Close tab with unsaved changes — prompt shown.
- Backward compat — `filename` omitted → include path falls back to `source/`.
