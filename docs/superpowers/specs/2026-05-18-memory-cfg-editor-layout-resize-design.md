# PRU Simulator Dashboard — Layout Redesign + memory.cfg Editor

**Date:** 2026-05-18
**Status:** Approved

---

## Summary

Three coordinated changes to the HTML dashboard:

1. **Layout**: Restructure from 3-column to 4-column top row (Source | Registers | Memory | IO)
2. **Resizable panels**: Drag handles between columns so the user can resize panels with the mouse
3. **memory.cfg editor**: "Config" button opens a modal to view/edit the config file; saving auto-reloads the simulator

---

## 1. Layout

### Current
```
grid-template-columns: 1fr 260px 320px
grid-template-rows: 1fr 200px 180px

Row 1: Source | Registers | IO
Row 2: Memory (spans all 3 cols)
Row 3: Editor (spans all 3 cols)
```

### New
```
Row 1: Source | Registers | Memory | IO   (flex, resizable)
Row 2: Editor (full width)
```

- The top section switches from CSS Grid to **CSS Flex** with `flex-direction: row`
- Initial flex-basis: Source 35%, Registers 15%, Memory 30%, IO 20%
- The Memory panel renders identically to today (hex grid, address input, Refresh button)
- The Editor panel stays below, full width, unchanged

---

## 2. Resizable Panels

### Mechanism
- Between each adjacent column pair: a `<div class="divider">` element (4px wide, cursor: col-resize)
- On `mousedown` on a divider: record start X, left panel width, right panel width
- On `mousemove`: compute delta, clamp each panel to min 120px, update `flex-basis` in px
- On `mouseup`: stop tracking
- No external libraries — ~60 lines of vanilla JS

### Divider styling
- 4px wide, `background: var(--border)`, `cursor: col-resize`
- On hover: `background: var(--accent)` to signal draggability
- Transitions off during drag to avoid lag

---

## 3. memory.cfg Editor

### Button
- "Config" button added to the controls bar, styled neutral (no color class)
- Positioned after the Reset button, before the status badge

### Modal
- Full-screen semi-transparent overlay (`rgba(0,0,0,0.6)`)
- Centered card (~600px wide, ~500px tall)
- Title bar: "memory.cfg"
- `<textarea>` pre-filled with raw file content (monospace, full height)
- Buttons: "Save & Reload" (accent blue) | "Cancel"
- Error area below textarea: hidden by default, shows red error text on save failure

### Data flow
1. Button click → `GET /config` → populate textarea → show modal
2. "Save & Reload" → `PUT /config` with textarea body
3. Server: write file → `sim = Simulator(config_path=...)` → return `{ok: true}` or `{error: "..."}`
4. On success: close modal → status badge flashes "RELOADED" for 2s → memory panel refreshes
5. On error: keep modal open → show error message in red below textarea

### Server changes (`ui/server.py`)
- `sim` becomes a module-level reference inside a mutable container so `PUT /config` can replace it
- `GET /config` reads `config_path` file, returns `PlainTextResponse`
- `PUT /config` reads request body, writes to `config_path`, rebuilds `sim`, returns JSON

---

## 4. Error Handling

| Scenario | Behavior |
|---|---|
| Config file not found on GET | Return empty string, textarea shows blank |
| Invalid config on PUT | Simulator constructor raises; return `{error: str(e)}`; sim unchanged |
| WebSocket step/reset after reload | Fine — new sim has no loaded program; user must re-assemble |

---

## 5. Files Changed

| File | Change |
|---|---|
| `ui/server.py` | Add `GET /config`, `PUT /config`; make `sim` replaceable |
| `ui/static/index.html` | 4-column flex layout, dividers, modal HTML, Config button |
| `ui/static/app.js` | Drag-resize logic, modal open/save/cancel handlers, status flash |

No new files. No changes to core simulator code.

---

## 6. Out of Scope

- Row resize (only column resize in this iteration)
- Persisting panel widths across page reload
- Syntax validation of config before save (server-side error is sufficient)
