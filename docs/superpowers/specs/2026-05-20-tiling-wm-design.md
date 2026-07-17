# Tiling Window Manager — Design Spec

**Date:** 2026-05-20
**Status:** Approved

## Overview

Add a tiling window manager to the PRU simulator dashboard. Users can drag panel title bars to any edge of another panel to split the layout, drag dividers to resize, and reset to the default layout at any time. The layout is fully persistent via `localStorage`. Both SC (single-core) and MC (multi-core) modes have independent, separately-persisted layouts.

---

## 1. Data Model

The layout is a recursive tree of two node types stored as plain JS objects:

```js
// Split node — a flex container with N children separated by dividers
{ type: 'split', dir: 'h' | 'v', sizes: [60, 40], children: [node, node, ...] }

// Leaf node — holds exactly one panel
{ type: 'leaf', panelId: 'source' }
```

- `dir: 'h'` — children sit side-by-side (row direction, vertical divider).
- `dir: 'v'` — children stack top-to-bottom (column direction, horizontal divider).
- `sizes` — array of percentages, always sums to 100, one entry per child.

### Panel Registry

A flat map from `panelId` string to the panel's actual DOM element. Panels not present in the active tree have no parent and are effectively invisible.

**SC mode panels (6):**

| ID | DOM element |
|----|-------------|
| `source` | `#source-panel` |
| `registers` | `#reg-panel` |
| `editor` | `#editor-panel` |
| `memory1` | `#mem-panel-1` |
| `memory2` | `#mem-panel-2` |
| `io` | `#io-panel` |

**MC mode panels (8):**

| ID | DOM element |
|----|-------------|
| `mc-pru0-source` | `#mc-pru0-source-panel` |
| `mc-pru0-registers` | `#mc-pru0-reg-panel` |
| `mc-rtu0-source` | `#mc-rtu0-source-panel` |
| `mc-rtu0-registers` | `#mc-rtu0-reg-panel` |
| `editor` | `#editor-panel` |
| `memory1` | `#mem-panel-1` |
| `memory2` | `#mem-panel-2` |
| `io` | `#io-panel` |

### Default SC Tree

Matches current layout — left column (source + editor + memory1), right column (registers + io):

```
split(h, [35, 65])
├── split(v, [60, 20, 20])
│   ├── leaf(source)
│   ├── leaf(editor)
│   └── leaf(memory1)
└── split(v, [60, 40])
    ├── leaf(registers)
    └── leaf(io)
```

### Default MC Tree

Matches current layout — two rows, each row has source + registers side by side:

```
split(v, [50, 50])
├── split(h, [67, 33])
│   ├── leaf(mc-pru0-source)
│   └── leaf(mc-pru0-registers)
└── split(h, [67, 33])
    ├── leaf(mc-rtu0-source)
    └── leaf(mc-rtu0-registers)
```

---

## 2. Rendering & Resize

### Rendering

A single `renderNode(node)` function returns a DOM element:

- **Split node** → `<div class="tile-split tile-split-h/v">`. For each child: wrap `renderNode(child)` in `<div class="tile-child" style="flex: {size}">`. Insert `<div class="tile-divider">` between each pair of children.
- **Leaf node** → `<div class="tile-leaf">`. Look up `panelId` in registry, `appendChild` the panel element into the leaf.

The entire tree is rendered into `#tile-root`, which occupies the same flex position as the current `#main` div (the existing `#main` is renamed to `#tile-root`). Full re-render on every split/merge: clear `#tile-root`'s children, append `renderNode(tree)`. Panels are DOM elements re-parented on each render — not recreated.

### CSS

```css
#tile-root            { display: flex; flex: 1; min-height: 0; overflow: hidden; }
.tile-split           { display: flex; flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
.tile-split-h         { flex-direction: row; }
.tile-split-v         { flex-direction: column; }
.tile-child           { display: flex; min-width: 0; min-height: 0; overflow: hidden; }
.tile-leaf            { display: flex; flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
.tile-divider         { background: #333; flex-shrink: 0; }
.tile-split-h > .tile-divider { width: 4px; cursor: col-resize; }
.tile-split-v > .tile-divider { height: 4px; cursor: row-resize; }
```

### Resize (Divider Drag)

1. Mousedown on `.tile-divider` → record parent split node, adjacent child divs, mouse position.
2. Mousemove → compute delta as % of parent's bounding size → clamp both sides to min 10% → update `sizes[i]` / `sizes[i+1]` → set `child.style.flex` on both siblings directly. No re-render.
3. Mouseup → save layout to `localStorage`.

Minimum panel size: **10%** (prevents collapse to zero).

---

## 3. Drag-to-Split UX

### Initiating a Drag

Mousedown on a `.panel-header` (title bar). Each header has `data-panel-id` attribute. Drag activates after first mousemove > 5px from mousedown point. A semi-transparent ghost `<div>` follows the cursor during the drag.

### Drop Zones

While dragging, each visible `.tile-leaf` receives four overlay divs covering the outer 25% of each edge (top / right / bottom / left). Hovering a drop zone:
- Highlights it with a blue tint.
- Shows a preview line indicating where the new divider will appear.

### On Drop

1. Find target leaf in tree by its panelId.
2. Remove dragged panel's current leaf from tree. If a split is left with one child, collapse it (replace split with that child).
3. Replace target leaf with a new split node containing dragged + target (or target + dragged, depending on drop side):
   - Left/right drop → `split(h, [50, 50])`
   - Top/bottom drop → `split(v, [50, 50])`
4. Full re-render. Save to `localStorage`.

### Edge Cases

- Drop on own panel → no-op.
- Mouseup anywhere outside a drop zone → cancel, no mutation.
- Escape key during drag → cancel, no mutation.
- Cleanup: remove all overlay divs and ghost on drag end.

---

## 4. SC/MC Mode Integration & Persistence

### Two Independent Trees

| Mode | `localStorage` key | Default tree |
|------|--------------------|--------------|
| SC | `pru-layout-sc` | Default SC tree (Section 1) |
| MC | `pru-layout-mc` | Default MC tree (Section 1) |

### Mode Switching

When user clicks the Multi-Core toggle:

1. Serialize active tree → save to `localStorage` under current mode's key.
2. Load the other mode's tree from `localStorage` (or use its default if none saved).
3. Full re-render with the new tree. Panels not in the new tree have no DOM parent — effectively invisible.

### Reset Layout Button

A "Reset Layout" button in the controls bar. On click: delete current mode's key from `localStorage`, load default tree for current mode, full re-render.

### File Structure Changes

| File | Change |
|------|--------|
| `ui/static/layout.js` | **New file** — ~350 lines: tree model, `renderNode`, resize handlers, drag-to-split, save/load, `initLayout()`, `switchLayoutMode()` |
| `ui/static/index.html` | Add `<script src="layout.js">`, add `#tile-root`, add tile CSS, add Reset Layout button, remove `#sc-view`/`#mc-view` wrapper divs, add `data-panel-id` to all panel headers |
| `ui/static/app.js` | Call `initLayout()` on DOMContentLoaded. In `toggleMultiCore()`: call `switchLayoutMode('mc'/'sc')` instead of manually hiding/showing views and DOM-moving the editor panel. All other logic (WebSocket, updateUI, updateMCUI) unchanged. |

### What Does Not Change

- `ui/server.py` — no changes.
- All WebSocket message handling, `updateUI`, `updateMCUI`, register/source/IO update functions — untouched.
- The layout engine is purely a panel-container concern; it does not know about panel contents.

---

## Testing

- Default layout renders correctly in SC and MC modes on first load (no `localStorage` entry).
- Resize: drag divider, reload page — sizes are restored from `localStorage`.
- Drag-to-split: drag source panel to right edge of registers → panels swap into a new horizontal split.
- Collapse: split a panel, then drag one of the two resulting panels elsewhere → original split collapses cleanly.
- Mode switch: arrange SC layout, switch to MC, arrange MC layout, switch back — both layouts independently preserved.
- Reset Layout: click reset — layout returns to default, `localStorage` entry cleared.
- Escape during drag: no tree mutation.
