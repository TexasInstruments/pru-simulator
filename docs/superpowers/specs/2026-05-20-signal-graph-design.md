# Signal Graph — Design Spec

**Date:** 2026-05-20
**Status:** Approved

---

## Overview

Add a time-series signal graph to the IO panel, below the existing GPO/GPI pin grids. The graph behaves like a logic analyzer + analog scope: it records GPO/GPI pin states and up to 8 user-specified memory address values over time, displays them on a canvas, and exports the captured trace as a CSV file.

No server changes are required. All new code lives in `ui/static/app.js` and `ui/static/index.html`.

---

## Architecture

### Sampling approach: client-side polling (Approach A)

After each WebSocket state message is received and `updateUI(state)` runs, the graph module:

1. Reads GPO/GPI directly from `state.io.gpo_pins` / `state.io.gpi_pins` — zero extra requests.
2. For each configured memory channel, fires a `read_memory` WebSocket request (`{action:"read_memory", addr, length, tag:"graph-M0"}` … `"graph-M7"`).
3. When a `{type:"memory", tag:"graph-M*"}` response arrives, back-fills the memory value into the most recently pushed sample.

Sampling only occurs while `recording === true`.

### New module: `signalGraph` object in `app.js`

All graph state is held in a single plain object (no new file). Estimated ~300 lines added to `app.js`.

```
signalGraph = {
  recording: bool,
  windowSize: 1024,           // current buffer capacity
  buf: [],                    // circular buffer array
  head: 0,                    // write pointer
  fill: 0,                    // number of valid samples (0..windowSize)
  memChannels: [              // up to 8 entries
    { addr: number, length: number, color: string, label: string }
  ]
}
```

---

## Data Model

### Circular buffer

Fixed-size array of `windowSize` slots. `head` is the next write index (wraps at `windowSize`). `fill` tracks how many slots contain valid data (saturates at `windowSize`).

Each **sample** object:
```js
{
  step: number,           // state.instruction_count (increments by 1 per executed instruction)
  gpo: [20],              // integers 0/1, from state.io.gpo_pins
  gpi: [20],              // integers 0/1, from state.io.gpi_pins
  mem: [v0, ..., vN]      // length = memChannels.length; number or null per channel
}
```

### Window resize behaviour

When the user changes the window size selector:
- If new size < current fill: drop oldest samples (tail of logical buffer), keep newest.
- If new size > current fill: reallocate array with empty slots appended at the tail.
- `head` and `fill` are recomputed accordingly.

### Memory limits

Worst case: 8192 samples × 48 values (20 GPO + 20 GPI + 8 mem) × 8 bytes ≈ 3 MB. Within normal browser limits.

---

## UI — HTML/CSS additions (`index.html`)

Inside `#io-panel .panel-body`, after the GPI pin grid:

```
<hr>  ← thin divider (1px, var(--border))

[Signal Graph]  [1024 ▼]  [● REC]  [Clear]   ← graph-header row

Memory channels:
  ● M1  [0x00020000]  ×  [4]  ×       ← mem-ch-row (repeated up to 8)
  + Add channel

[canvas#signal-graph-canvas]            ← 140px tall, full panel width

● GPO 0  ─  ● GPO 3  ─  ● M1  ─ …     ← legend row

[↓ Export CSV]  "847 samples recorded"  ← export-row (hidden when fill=0)
```

**Window size select options:** `128 | 256 | 512 | 1024 | 2048 | 4096 | 8192` — default `1024`.

**REC button states:**
- Off: normal button style, label `● REC`
- On: red border + glow, label `● REC` (blinking red dot in canvas corner)

**Step counter:** Rendered inside the canvas at bottom-right as `step N / W` text.

---

## Canvas Rendering

Redrawn synchronously on each new sample (no `requestAnimationFrame` loop — only redraws when data arrives, ~100 ms in Run mode).

Canvas is divided into two horizontal zones by a faint separator line:

### Upper zone — digital lanes

- One lane per **active** GPO/GPI pin (a pin is active if it transitioned at least once within the current buffer window). Static pins are hidden.
- Lane height = upper-zone height ÷ active pin count.
- Each lane draws a step waveform: horizontal lines at HIGH/LOW positions, vertical edges on transitions.
- Label (`GPO 0`, `GPI 3`, etc.) drawn in the lane's color at the left edge.
- GPO pin color palette: teal tones (`#4ec9b0`, `#9cdcfe`, `#ce9178`, …).
- GPI pin color palette: blue/green tones (distinct from GPO).

### Lower zone — analog lanes

- One overlaid trace per configured memory channel.
- Y-axis auto-scales to the global min/max of all analog values in the current buffer window.
- `null` slots rendered as gaps (path is broken; no line drawn across missing samples).
- Label drawn at left edge in the channel's assigned color.

### X-axis

Spans the full buffer window. Oldest sample at left, newest at right. No time labels — step counter shown as text overlay only.

---

## CSV Export

Triggered by "Export CSV" button (visible only when `fill > 0`).

**Column layout:**
```
step,gpo0,gpo1,...,gpo19,gpi0,gpi1,...,gpi19,M1_0x00020000,M2_0x00020004,...
```

- All 20 GPO and 20 GPI columns always present regardless of activity.
- Memory column headers use the format `M<n>_0x<addr>` (e.g. `M1_0x00020000`).
- `null` memory values written as empty string (not the word "null").
- Samples ordered oldest-first.

**Mechanics:** Assembled as a JS string in the browser, wrapped in a `Blob("text/csv")`, downloaded via a temporary `<a download>` element. No server request.

**Filename:** `pru-trace-YYYYMMDD-HHmmss.csv` (local time of export).

---

## File Map

| File | Change |
|---|---|
| `ui/static/index.html` | Add graph section HTML + CSS inside `#io-panel` |
| `ui/static/app.js` | Add `signalGraph` object, `graphSample()`, `graphHandleMemory()`, `drawGraph()`, `exportGraphCSV()`, event wiring |

No changes to `ui/server.py`, `simulator.py`, or any core module.

---

## Out of Scope

- Zoom / pan on the time axis
- Cursor/tooltip showing exact value at a hover position
- Saving traces to the server
- Re-loading a saved CSV trace into the graph
- Triggering (auto-start record on signal edge)
