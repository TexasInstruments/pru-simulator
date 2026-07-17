# Signal Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a time-series signal graph to the IO panel that records GPO/GPI pin states and up to 8 user-specified memory address values, displays them on a canvas, and exports the captured trace as a CSV file.

**Architecture:** A `signalGraph` plain object in `app.js` owns all graph state: a circular buffer of samples, recording flag, window size, and memory channel config. GPO/GPI are sampled directly from each state message; memory values are fetched via existing `read_memory` WebSocket requests whose responses back-fill the most recent sample. Canvas redraws synchronously on each new sample. No server changes.

**Tech Stack:** Vanilla JS, HTML5 Canvas 2D API, existing WebSocket infrastructure (`sendAction`, `ws.onmessage`), CSS variables already defined in `index.html`.

---

## File Map

| File | Change |
|---|---|
| `ui/static/index.html` | Add graph section CSS (after IO panel CSS block) and HTML (inside `#io-panel .panel-body`, after GPI section) |
| `ui/static/app.js` | Add `signalGraph` state at top; add `graphPushSample`, `graphResizeWindow`, `graphGetSamples`, `graphSample`, `graphHandleMemory`, `drawGraph`, `renderMemChannelRows`, `addMemChannel`, `removeMemChannel`, `exportGraphCSV` functions; wire into `updateUI` and `ws.onmessage` |

---

## Task 1: HTML + CSS

**Files:**
- Modify: `ui/static/index.html`

- [ ] **Step 1: Add signal graph CSS to `index.html`**

In `ui/static/index.html`, find the line:

```css
    /* ---- Memory panel --------------------------------------------------- */
```

Insert the following block **immediately before** that line:

```css
    /* ---- Signal graph --------------------------------------------------- */

    .graph-divider {
      border: none;
      border-top: 1px solid var(--border);
      margin: 6px 0 2px;
    }

    .graph-header {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-shrink: 0;
    }

    .graph-section-title {
      font-size: 11px;
      font-weight: 600;
      color: var(--text-dim);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      flex: 1;
    }

    .graph-win-sel {
      font-size: 10px;
      background: var(--btn);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 3px;
      padding: 2px 4px;
      cursor: pointer;
      font-family: inherit;
    }

    .graph-btn {
      font-size: 10px;
      padding: 2px 7px;
      background: var(--btn);
      border: 1px solid var(--border);
      border-radius: 3px;
      color: var(--text);
      cursor: pointer;
      white-space: nowrap;
      font-family: inherit;
    }

    .graph-btn:hover { background: var(--btn-hover); }

    .graph-btn.rec-on {
      border-color: #c44;
      color: #ff7070;
      box-shadow: 0 0 5px rgba(200, 50, 50, 0.35);
    }

    .graph-btn.export-csv { border-color: #2a6; color: #5d5; }

    .graph-mem-section-title {
      font-size: 10px;
      text-transform: uppercase;
      color: var(--text-dim);
      letter-spacing: 0.05em;
      margin-bottom: 3px;
    }

    .graph-mem-channels { display: flex; flex-direction: column; gap: 3px; }

    .graph-mem-row { display: flex; align-items: center; gap: 4px; }

    .graph-ch-swatch { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }

    .graph-ch-lbl { font-size: 10px; color: var(--text-dim); width: 22px; flex-shrink: 0; }

    .graph-ch-addr, .graph-ch-len {
      font-size: 10px;
      font-family: inherit;
      background: #1e1e1e;
      border: 1px solid var(--border);
      border-radius: 3px;
      color: var(--text);
      padding: 2px 4px;
    }

    .graph-ch-addr { width: 88px; }
    .graph-ch-len  { width: 36px; }

    .graph-ch-addr:focus, .graph-ch-len:focus {
      outline: none;
      border-color: var(--accent);
    }

    .graph-ch-remove {
      font-size: 13px;
      color: var(--text-dim);
      cursor: pointer;
      padding: 0 2px;
      line-height: 1;
    }

    .graph-ch-remove:hover { color: var(--halted); }

    .graph-add-ch {
      font-size: 10px;
      color: var(--accent);
      background: none;
      border: 1px dashed #2a3a4a;
      border-radius: 3px;
      padding: 2px 8px;
      cursor: pointer;
      align-self: flex-start;
      margin-top: 2px;
      font-family: inherit;
    }

    .graph-add-ch:hover { background: #1a2530; }

    .graph-canvas-wrap {
      position: relative;
      background: #141414;
      border: 1px solid var(--border);
      border-radius: 3px;
      height: 140px;
      overflow: hidden;
      flex-shrink: 0;
    }

    #signal-graph-canvas { width: 100%; height: 100%; display: block; }

    .graph-rec-dot {
      position: absolute;
      top: 5px;
      right: 6px;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #f44;
      animation: recBlink 1s step-start infinite;
      display: none;
    }

    .graph-rec-dot.active { display: block; }

    @keyframes recBlink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

    .graph-step-label {
      position: absolute;
      bottom: 4px;
      right: 6px;
      font-size: 9px;
      color: #555;
      font-family: monospace;
      pointer-events: none;
    }

    .graph-legend {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      font-size: 10px;
      color: var(--text-dim);
      min-height: 14px;
    }

    .graph-legend-item { display: flex; align-items: center; gap: 3px; }

    .graph-legend-swatch { width: 16px; height: 2px; border-radius: 1px; }

    .graph-export-row { display: flex; align-items: center; gap: 8px; }

    .graph-sample-count { font-size: 10px; color: var(--text-dim); }

```

- [ ] **Step 2: Add graph HTML to the IO panel in `index.html`**

Find this exact block in `ui/static/index.html`:

```html
          <div class="io-section">
            <div class="io-section-title">GPI (R31) — Input (click to toggle)</div>
            <div class="pin-grid" id="gpi-grid"></div>
          </div>
        </div>
      </div>

      <!-- MC PRU0 source panel -->
```

Replace with:

```html
          <div class="io-section">
            <div class="io-section-title">GPI (R31) — Input (click to toggle)</div>
            <div class="pin-grid" id="gpi-grid"></div>
          </div>

          <!-- Signal graph -->
          <hr class="graph-divider">
          <div class="graph-header">
            <span class="graph-section-title">Signal Graph</span>
            <select id="graph-win-sel" class="graph-win-sel">
              <option value="128">128</option>
              <option value="256">256</option>
              <option value="512">512</option>
              <option value="1024" selected>1024</option>
              <option value="2048">2048</option>
              <option value="4096">4096</option>
              <option value="8192">8192</option>
            </select>
            <button id="graph-rec-btn" class="graph-btn">● REC</button>
            <button id="graph-clear-btn" class="graph-btn">Clear</button>
          </div>
          <div>
            <div class="graph-mem-section-title">Memory channels</div>
            <div class="graph-mem-channels" id="graph-mem-channels"></div>
            <button id="graph-add-ch-btn" class="graph-add-ch">+ Add channel</button>
          </div>
          <div class="graph-canvas-wrap">
            <div class="graph-rec-dot" id="graph-rec-dot"></div>
            <span class="graph-step-label" id="graph-step-label"></span>
            <canvas id="signal-graph-canvas"></canvas>
          </div>
          <div class="graph-legend" id="graph-legend"></div>
          <div class="graph-export-row" id="graph-export-row" style="display:none">
            <button id="graph-export-btn" class="graph-btn export-csv">↓ Export CSV</button>
            <span class="graph-sample-count" id="graph-sample-count"></span>
          </div>
        </div>
      </div>

      <!-- MC PRU0 source panel -->
```

- [ ] **Step 3: Verify elements exist in browser**

Start the server (`python ui/server.py`), open `http://localhost:8080`. Open DevTools console and run:

```js
['graph-win-sel','graph-rec-btn','graph-clear-btn','graph-mem-channels',
 'graph-add-ch-btn','graph-rec-dot','signal-graph-canvas','graph-legend',
 'graph-export-row','graph-export-btn'].forEach(id => {
  const el = document.getElementById(id);
  console.log(id, el ? 'OK' : 'MISSING');
});
```

Expected: all 10 IDs print `OK`.

- [ ] **Step 4: Commit**

```bash
git add ui/static/index.html
git commit -m "feat: add signal graph HTML and CSS to IO panel"
```

---

## Task 2: `signalGraph` state object and buffer functions

**Files:**
- Modify: `ui/static/app.js`

- [ ] **Step 1: Add `signalGraph` state and color palettes to `app.js`**

After the `// ---- Editor tab state` block (after line 35), add:

```js
// ---- Signal graph state ----------------------------------------------------

const GRAPH_GPO_COLORS = [
  '#4ec9b0','#9cdcfe','#ce9178','#d7ba7d','#b5cea8',
  '#73c991','#50b8a8','#4ec994','#9dcfe0','#7ecfc4',
  '#4db8a8','#66c19c','#5cc9bc','#8ed4b8','#6bc4a0',
  '#45b89c','#5dc9a0','#7ecfb0','#4cc4a4','#65b8a0',
];
const GRAPH_GPI_COLORS = [
  '#569cd6','#c586c0','#9b9bff','#7b99ee','#82aaff',
  '#89b8e0','#7fa8d0','#a080c0','#8090d0','#6880c8',
  '#7090d8','#6878c8','#7888d8','#8898e8','#7898d0',
  '#5878c8','#6888d8','#7898d0','#6888c0','#5868b8',
];
const GRAPH_MEM_COLORS = [
  '#c586c0','#dcdcaa','#4fc1ff','#f44747',
  '#d7ba7d','#b5cea8','#ce9178','#f48771',
];

const signalGraph = {
  recording: false,
  windowSize: 1024,
  buf: [],        // circular buffer array, length === windowSize
  head: 0,        // next write index
  fill: 0,        // number of valid samples (0..windowSize)
  memChannels: [], // [{addr, length, color, label}], up to 8
};
```

- [ ] **Step 2: Add `graphPushSample`, `graphResizeWindow`, `graphGetSamples` to `app.js`**

After the `updatePins` function (after its closing `}` at around line 524), add:

```js
// ---- Signal graph — buffer -------------------------------------------------

/**
 * Push one sample into the circular buffer.
 * sample = { step, gpo: [20], gpi: [20], mem: [number|null, ...] }
 */
function graphPushSample(sample) {
  signalGraph.buf[signalGraph.head] = sample;
  signalGraph.head = (signalGraph.head + 1) % signalGraph.windowSize;
  if (signalGraph.fill < signalGraph.windowSize) signalGraph.fill++;
}

/**
 * Return samples in chronological order (oldest first).
 */
function graphGetSamples() {
  const { buf, head, fill, windowSize } = signalGraph;
  if (fill === 0) return [];
  const start = fill < windowSize ? 0 : head;
  const out = [];
  for (let i = 0; i < fill; i++) {
    out.push(buf[(start + i) % windowSize]);
  }
  return out;
}

/**
 * Resize the circular buffer to newSize, keeping the most recent samples.
 */
function graphResizeWindow(newSize) {
  const samples = graphGetSamples(); // oldest → newest
  signalGraph.windowSize = newSize;
  signalGraph.buf = new Array(newSize);
  signalGraph.head = 0;
  signalGraph.fill = 0;
  // Re-insert keeping at most the last newSize samples
  const keep = samples.slice(-newSize);
  keep.forEach(s => graphPushSample(s));
}
```

- [ ] **Step 3: Verify buffer logic in browser console**

In browser DevTools console:

```js
// Clear any stale state
signalGraph.buf = []; signalGraph.head = 0; signalGraph.fill = 0; signalGraph.windowSize = 3;
graphPushSample({step:1,gpo:[],gpi:[],mem:[]});
graphPushSample({step:2,gpo:[],gpi:[],mem:[]});
graphPushSample({step:3,gpo:[],gpi:[],mem:[]});
graphPushSample({step:4,gpo:[],gpi:[],mem:[]}); // overwrites oldest
const s = graphGetSamples();
console.assert(s.length === 3, 'fill capped at 3');
console.assert(s[0].step === 2, 'oldest is step 2');
console.assert(s[2].step === 4, 'newest is step 4');
graphResizeWindow(2);
const s2 = graphGetSamples();
console.assert(s2.length === 2, 'resize to 2 keeps newest 2');
console.assert(s2[0].step === 3, 'after resize oldest is step 3');
console.log('buffer tests passed');
```

Expected: `buffer tests passed` with no assertion errors.

- [ ] **Step 4: Commit**

```bash
git add ui/static/app.js
git commit -m "feat: add signalGraph state object and circular buffer functions"
```

---

## Task 3: Sampling — `graphSample` + `graphHandleMemory` + wiring

**Files:**
- Modify: `ui/static/app.js`

- [ ] **Step 1: Add `graphSample` and `graphHandleMemory` to `app.js`**

After the `graphResizeWindow` function (end of Task 2 additions), add:

```js
// ---- Signal graph — sampling -----------------------------------------------

/**
 * Called on every state message while recording === true.
 * Pushes a new sample and fires read_memory for each memory channel.
 */
function graphSample(state) {
  if (!signalGraph.recording) return;
  const sample = {
    step: state.instruction_count,
    gpo: (state.io.gpo_pins || []).slice(0, 20),
    gpi: (state.io.gpi_pins || []).slice(0, 20),
    mem: signalGraph.memChannels.map(() => null),
  };
  graphPushSample(sample);
  signalGraph.memChannels.forEach((ch, i) => {
    sendAction({
      action: "read_memory",
      addr: ch.addr,
      length: ch.length,
      tag: `graph-M${i}`,
    });
  });
}

/**
 * Called when a read_memory response with tag "graph-M*" arrives.
 * Back-fills the value into the most recently pushed sample.
 */
function graphHandleMemory(msg) {
  const idx = parseInt(msg.tag.slice(7), 10); // "graph-M0" → 0
  if (isNaN(idx) || idx >= signalGraph.memChannels.length) return;
  // Find the most recent sample (one slot behind head)
  const { buf, head, fill, windowSize } = signalGraph;
  if (fill === 0) return;
  const lastIdx = (head - 1 + windowSize) % windowSize;
  if (buf[lastIdx] && buf[lastIdx].mem) {
    const raw = msg.data || msg.bytes || msg.values;
    let value = null;
    if (Array.isArray(raw) && raw.length >= 1) {
      // Combine up to 4 bytes into a little-endian uint32
      value = 0;
      for (let b = 0; b < Math.min(raw.length, 4); b++) {
        value |= (raw[b] & 0xff) << (b * 8);
      }
      value = value >>> 0; // unsigned
    }
    buf[lastIdx].mem[idx] = value;
  }
}
```

- [ ] **Step 2: Wire `graphSample` into `updateUI`**

In `updateUI(state)`, find the last line:

```js
  // IO pins
  updatePins(state.io);
}
```

Replace with:

```js
  // IO pins
  updatePins(state.io);

  // Signal graph sample
  graphSample(state);
  drawGraph();
}
```

- [ ] **Step 3: Wire `graphHandleMemory` into `ws.onmessage`**

In the `ws.onmessage` handler, find:

```js
      } else if (msg.type === "memory") {
        if (msg.tag === "mem2") renderMemory2(msg);
        else renderMemory(msg);
```

Replace with:

```js
      } else if (msg.type === "memory") {
        if (msg.tag === "mem2") renderMemory2(msg);
        else if (msg.tag && msg.tag.startsWith("graph-")) { graphHandleMemory(msg); drawGraph(); }
        else renderMemory(msg);
```

- [ ] **Step 4: Verify sampling in browser**

Load a program (e.g. `source/mac_example.asm`), click **● REC**, then **Run**. Open DevTools console:

```js
const s = graphGetSamples();
console.log('samples:', s.length);
console.log('first step:', s[0]?.step);
console.log('gpo length:', s[0]?.gpo?.length);
console.log('mem length:', s[0]?.mem?.length); // 0 if no mem channels added
```

Expected: `samples` > 0, `gpo length` = 20, `mem length` = 0 (no channels configured yet).

- [ ] **Step 5: Commit**

```bash
git add ui/static/app.js
git commit -m "feat: add graphSample and graphHandleMemory, wire into updateUI and ws.onmessage"
```

---

## Task 4: Canvas rendering — `drawGraph`

**Files:**
- Modify: `ui/static/app.js`

- [ ] **Step 1: Add `drawGraph` to `app.js`**

After `graphHandleMemory`, add:

```js
// ---- Signal graph — rendering ----------------------------------------------

function drawGraph() {
  const canvas = document.getElementById("signal-graph-canvas");
  if (!canvas) return;
  const W = canvas.offsetWidth;
  const H = canvas.offsetHeight;
  if (W === 0 || H === 0) return;
  canvas.width  = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");

  // Background
  ctx.fillStyle = "#141414";
  ctx.fillRect(0, 0, W, H);

  const samples = graphGetSamples();

  // Faint vertical grid lines (8 divisions)
  ctx.strokeStyle = "#222";
  ctx.lineWidth = 1;
  for (let gx = 1; gx < 8; gx++) {
    const x = (gx / 8) * W;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
  }

  if (samples.length < 2) {
    // Step counter even when empty
    _graphUpdateStepLabel(samples);
    _graphUpdateLegend([]);
    return;
  }

  const N = samples.length;

  // ---- Determine active digital channels (pin transitioned at least once) ----
  const activeDig = []; // [{label, color, data: [0/1]}]
  for (let i = 0; i < 20; i++) {
    const vals = samples.map(s => s.gpo[i] || 0);
    if (vals.some(v => v !== vals[0])) {
      activeDig.push({ label: `GPO ${i}`, color: GRAPH_GPO_COLORS[i], data: vals });
    }
  }
  for (let i = 0; i < 20; i++) {
    const vals = samples.map(s => s.gpi[i] || 0);
    if (vals.some(v => v !== vals[0])) {
      activeDig.push({ label: `GPI ${i}`, color: GRAPH_GPI_COLORS[i], data: vals });
    }
  }

  // ---- Analog channels (memory) --------------------------------------------
  const activeAna = signalGraph.memChannels.map((ch, i) => ({
    label: ch.label,
    color: ch.color,
    data: samples.map(s => s.mem[i]),
  }));

  // ---- Zone split ----------------------------------------------------------
  const hasAna = activeAna.length > 0;
  const hasDig = activeDig.length > 0;
  let digH, anaTop, anaH;
  if (hasDig && hasAna) {
    digH   = Math.floor(H * 0.47);
    anaTop = digH + 2;
    anaH   = H - anaTop - 2;
    // Separator
    ctx.strokeStyle = "#2a2a2a"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, digH + 1); ctx.lineTo(W, digH + 1); ctx.stroke();
  } else if (hasDig) {
    digH = H;
    anaTop = H; anaH = 0;
  } else {
    digH = 0;
    anaTop = 0; anaH = H;
  }

  // ---- Draw digital lanes --------------------------------------------------
  if (hasDig && digH > 0) {
    const rowH = digH / activeDig.length;
    activeDig.forEach((ch, ri) => {
      const yBase = ri * rowH;
      const yHigh = yBase + rowH * 0.12;
      const yLow  = yBase + rowH * 0.84;
      ctx.strokeStyle = ch.color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ch.data.forEach((v, i) => {
        const x = (i / (N - 1)) * W;
        const y = v ? yHigh : yLow;
        if (i === 0) { ctx.moveTo(x, y); return; }
        if (v !== ch.data[i - 1]) {
          const xm = ((i - 0.5) / (N - 1)) * W;
          ctx.lineTo(xm, ch.data[i - 1] ? yHigh : yLow);
          ctx.lineTo(xm, y);
        }
        ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.fillStyle = ch.color;
      ctx.font = "8px Consolas, monospace";
      ctx.fillText(ch.label, 3, yBase + 9);
    });
  }

  // ---- Draw analog lanes ---------------------------------------------------
  if (hasAna && anaH > 0) {
    // Global min/max across all non-null values
    const allVals = activeAna.flatMap(ch => ch.data.filter(v => v !== null));
    const vMin = allVals.length ? Math.min(...allVals) : 0;
    const vMax = allVals.length ? Math.max(...allVals) : 1;
    const vRange = vMax - vMin || 1;

    activeAna.forEach(ch => {
      ctx.strokeStyle = ch.color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      let penDown = false;
      ch.data.forEach((v, i) => {
        if (v === null) { penDown = false; return; }
        const x = (i / (N - 1)) * W;
        const y = anaTop + anaH - ((v - vMin) / vRange) * anaH;
        if (!penDown) { ctx.moveTo(x, y); penDown = true; }
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.fillStyle = ch.color;
      ctx.font = "8px Consolas, monospace";
      ctx.fillText(ch.label, 3, anaTop + 9 + activeAna.indexOf(ch) * 11);
    });
  }

  // ---- Step label and legend -----------------------------------------------
  _graphUpdateStepLabel(samples);
  _graphUpdateLegend([...activeDig, ...activeAna]);
}

function _graphUpdateStepLabel(samples) {
  const el = document.getElementById("graph-step-label");
  if (!el) return;
  if (samples.length === 0) { el.textContent = ""; return; }
  el.textContent = `step ${samples[samples.length - 1].step} / ${signalGraph.windowSize}`;
}

function _graphUpdateLegend(channels) {
  const el = document.getElementById("graph-legend");
  if (!el) return;
  el.innerHTML = channels.map(ch =>
    `<div class="graph-legend-item">` +
    `<div class="graph-legend-swatch" style="background:${ch.color}"></div>` +
    `<span>${ch.label}</span></div>`
  ).join("");
}
```

- [ ] **Step 2: Verify rendering in browser**

Load any program, click **● REC** (after wiring in Task 5), run it, then check the IO panel. As a quick smoke test without Task 5 wiring, temporarily force recording on in the console:

```js
signalGraph.recording = true;
```

Then step through a few instructions (`ArrowRight` key). The canvas in the IO panel should show digital step waveforms for any GPO pins that changed.

- [ ] **Step 3: Commit**

```bash
git add ui/static/app.js
git commit -m "feat: add drawGraph canvas renderer (digital lanes + analog lanes + legend)"
```

---

## Task 5: Controls and CSV export

**Files:**
- Modify: `ui/static/app.js`

- [ ] **Step 1: Add `renderMemChannelRows`, `addMemChannel`, `removeMemChannel` to `app.js`**

After `_graphUpdateLegend`, add:

```js
// ---- Signal graph — memory channel management ------------------------------

function renderMemChannelRows() {
  const container = document.getElementById("graph-mem-channels");
  if (!container) return;
  container.innerHTML = "";
  signalGraph.memChannels.forEach((ch, i) => {
    const row = document.createElement("div");
    row.className = "graph-mem-row";
    row.dataset.index = i;

    const swatch = document.createElement("div");
    swatch.className = "graph-ch-swatch";
    swatch.style.background = ch.color;

    const lbl = document.createElement("span");
    lbl.className = "graph-ch-lbl";
    lbl.textContent = `M${i + 1}`;

    const addrInput = document.createElement("input");
    addrInput.type = "text";
    addrInput.className = "graph-ch-addr";
    addrInput.value = "0x" + ch.addr.toString(16).toUpperCase().padStart(8, "0");
    addrInput.addEventListener("change", () => {
      const v = parseInt(addrInput.value, 16);
      if (!isNaN(v)) signalGraph.memChannels[i].addr = v >>> 0;
    });

    const lenLbl = document.createElement("span");
    lenLbl.style.cssText = "font-size:10px;color:var(--text-dim)";
    lenLbl.textContent = "×";

    const lenInput = document.createElement("input");
    lenInput.type = "text";
    lenInput.className = "graph-ch-len";
    lenInput.value = ch.length;
    lenInput.addEventListener("change", () => {
      const v = parseInt(lenInput.value, 10);
      if (!isNaN(v) && v >= 1 && v <= 64) signalGraph.memChannels[i].length = v;
    });

    const rm = document.createElement("span");
    rm.className = "graph-ch-remove";
    rm.textContent = "×";
    rm.addEventListener("click", () => removeMemChannel(i));

    row.append(swatch, lbl, addrInput, lenLbl, lenInput, rm);
    container.appendChild(row);
  });
}

function addMemChannel() {
  if (signalGraph.memChannels.length >= 8) return;
  const i = signalGraph.memChannels.length;
  signalGraph.memChannels.push({
    addr: 0x00020000,
    length: 4,
    color: GRAPH_MEM_COLORS[i % GRAPH_MEM_COLORS.length],
    label: `M${i + 1}`,
  });
  renderMemChannelRows();
}

function removeMemChannel(index) {
  signalGraph.memChannels.splice(index, 1);
  // Re-label remaining channels
  signalGraph.memChannels.forEach((ch, i) => { ch.label = `M${i + 1}`; });
  renderMemChannelRows();
}
```

- [ ] **Step 2: Add `exportGraphCSV` to `app.js`**

After `removeMemChannel`, add:

```js
// ---- Signal graph — CSV export ---------------------------------------------

function exportGraphCSV() {
  const samples = graphGetSamples();
  if (samples.length === 0) return;

  const gpoHeaders = Array.from({ length: 20 }, (_, i) => `gpo${i}`);
  const gpiHeaders = Array.from({ length: 20 }, (_, i) => `gpi${i}`);
  const memHeaders = signalGraph.memChannels.map(
    (ch, i) => `M${i + 1}_0x${ch.addr.toString(16).toUpperCase().padStart(8, "0")}`
  );
  const header = ["step", ...gpoHeaders, ...gpiHeaders, ...memHeaders].join(",");

  const rows = samples.map(s => {
    const gpo = Array.from({ length: 20 }, (_, i) => s.gpo[i] ?? 0);
    const gpi = Array.from({ length: 20 }, (_, i) => s.gpi[i] ?? 0);
    const mem = s.mem.map(v => (v === null || v === undefined) ? "" : v);
    return [s.step, ...gpo, ...gpi, ...mem].join(",");
  });

  const csv = [header, ...rows].join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const now = new Date();
  const ts = now.getFullYear().toString() +
    String(now.getMonth() + 1).padStart(2, "0") +
    String(now.getDate()).padStart(2, "0") + "-" +
    String(now.getHours()).padStart(2, "0") +
    String(now.getMinutes()).padStart(2, "0") +
    String(now.getSeconds()).padStart(2, "0");
  const a = document.createElement("a");
  a.href = url;
  a.download = `pru-trace-${ts}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
```

- [ ] **Step 3: Wire all controls in `app.js`**

Find the section that wires the "Open" button handler (look for `btnFile.addEventListener`). Just before that section, add a new `// ---- Signal graph controls` section:

```js
// ---- Signal graph controls -------------------------------------------------

document.getElementById("graph-rec-btn").addEventListener("click", () => {
  signalGraph.recording = !signalGraph.recording;
  const btn = document.getElementById("graph-rec-btn");
  const dot = document.getElementById("graph-rec-dot");
  btn.classList.toggle("rec-on", signalGraph.recording);
  dot.classList.toggle("active", signalGraph.recording);
});

document.getElementById("graph-clear-btn").addEventListener("click", () => {
  signalGraph.buf = new Array(signalGraph.windowSize);
  signalGraph.head = 0;
  signalGraph.fill = 0;
  const exportRow = document.getElementById("graph-export-row");
  if (exportRow) exportRow.style.display = "none";
  drawGraph();
});

document.getElementById("graph-win-sel").addEventListener("change", (e) => {
  graphResizeWindow(parseInt(e.target.value, 10));
  drawGraph();
});

document.getElementById("graph-add-ch-btn").addEventListener("click", () => {
  addMemChannel();
});

document.getElementById("graph-export-btn").addEventListener("click", () => {
  exportGraphCSV();
});
```

- [ ] **Step 4: Show/hide Export CSV row when fill changes**

In `graphPushSample`, add export row visibility update. Find:

```js
function graphPushSample(sample) {
  signalGraph.buf[signalGraph.head] = sample;
  signalGraph.head = (signalGraph.head + 1) % signalGraph.windowSize;
  if (signalGraph.fill < signalGraph.windowSize) signalGraph.fill++;
}
```

Replace with:

```js
function graphPushSample(sample) {
  signalGraph.buf[signalGraph.head] = sample;
  signalGraph.head = (signalGraph.head + 1) % signalGraph.windowSize;
  if (signalGraph.fill < signalGraph.windowSize) signalGraph.fill++;
  // Show Export CSV once we have data
  const exportRow = document.getElementById("graph-export-row");
  const countEl   = document.getElementById("graph-sample-count");
  if (exportRow && signalGraph.fill > 0) {
    exportRow.style.display = "";
    if (countEl) countEl.textContent = `${signalGraph.fill} samples recorded`;
  }
}
```

- [ ] **Step 5: Smoke test in browser**

Start server (`python ui/server.py`), open `http://localhost:8080`. Test the following:

1. **REC toggle:** Click `● REC` → button gets red glow. Click again → glow gone.
2. **Record a trace:** Load any program (e.g. `source/mac_example.asm`), click `● REC`, click Run, let it run for a moment, click `● REC` again to stop. Confirm `Export CSV` button appears with a sample count.
3. **Add memory channel:** Click `+ Add channel` → a row appears with address `0x00020000` and length `4`. Add a second → `M2` row appears. Try adding 9 → nothing happens (max 8).
4. **Remove memory channel:** Click `×` on a channel row → it disappears; remaining channels re-label correctly.
5. **Window resize:** With data in the buffer, change the selector from 1024 to 128 → `samples recorded` count drops to ≤ 128.
6. **Canvas:** While recording, step through some instructions — digital lanes appear for any GPO pins that toggled.
7. **Export CSV:** Click `↓ Export CSV` → a `.csv` file downloads. Open it and confirm: header row starts with `step,gpo0,...`, correct number of data rows.
8. **Clear:** Click `Clear` → Export CSV row disappears, canvas goes blank.

- [ ] **Step 6: Commit**

```bash
git add ui/static/app.js
git commit -m "feat: signal graph controls, memory channel add/remove, CSV export"
```

---

## Self-Review

**Spec coverage:**
- ✅ GPO/GPI pins sampled from state message: Task 3 `graphSample`
- ✅ Up to 8 memory channels, user-configurable: Task 5 `addMemChannel` / `removeMemChannel`
- ✅ Client-side polling (Approach A): Task 3 `graphSample` fires `read_memory`
- ✅ Back-fill memory slots: Task 3 `graphHandleMemory`
- ✅ Circular buffer, windowSize 128/256/512/1024/2048/4096/8192 default 1024: Tasks 2 + 5
- ✅ Only active GPO/GPI pins drawn: Task 4 `drawGraph` transition check
- ✅ Analog lanes with null gaps: Task 4 `penDown` logic
- ✅ REC toggle, blinking dot: Task 5 controls
- ✅ Clear button: Task 5 controls
- ✅ Export CSV button visible when fill > 0: Task 5 `graphPushSample`
- ✅ CSV format (step, gpo0..19, gpi0..19, M1_addr…): Task 5 `exportGraphCSV`
- ✅ Filename `pru-trace-YYYYMMDD-HHmmss.csv`: Task 5 `exportGraphCSV`
- ✅ No server changes: confirmed — only `app.js` + `index.html`

**Placeholder scan:** None found.

**Type consistency:**
- `graphPushSample` defined Task 2, called Tasks 3 + 5 — consistent
- `graphGetSamples` defined Task 2, called Tasks 3 + 4 + 5 — consistent
- `graphResizeWindow` defined Task 2, called Task 5 — consistent
- `graphSample` defined Task 3, called from `updateUI` Task 3 — consistent
- `graphHandleMemory` defined Task 3, called from `ws.onmessage` Task 3 — consistent
- `drawGraph` defined Task 4, called Tasks 3 + 5 — consistent
- `renderMemChannelRows` defined Task 5, called from `addMemChannel` + `removeMemChannel` Task 5 — consistent
- `addMemChannel` defined Task 5, called from button listener Task 5 — consistent
- `GRAPH_GPO_COLORS` / `GRAPH_GPI_COLORS` / `GRAPH_MEM_COLORS` defined Task 2, used Tasks 4 + 5 — consistent
- `signalGraph.memChannels[i].label` set in `addMemChannel` Task 5, read in `drawGraph` Task 4 via `activeAna` — consistent
