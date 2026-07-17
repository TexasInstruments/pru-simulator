# UART Decoder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a UART decoder section to the IO panel that post-processes a Signal Graph GPO0 capture and displays decoded ASCII text.

**Architecture:** Pure frontend change — `decodeUARTBits(bits)` is a standalone pure function (easy to test); `decodeUART(samples)` wraps it for signal graph samples; button handlers in app.js wire UI to logic. Auto-detects bit period from minimum run-length in the captured GPO0 stream. No server changes.

**Tech Stack:** Vanilla JS (ES2020), HTML/CSS (same stack as rest of UI)

---

## File Map

| File | Change |
|------|--------|
| `ui/static/index.html` | Add CSS for decoder section (inside IO panel block); add HTML section inside `#io-panel .panel-body` after GPI grid |
| `ui/static/app.js` | Add `decodeUARTBits()`, `decodeUART()`, `renderUARTBytes()` after `graphGetSamples()`; add button event listeners at end of file |

---

## Task 1: Add CSS for the decoder section

**Files:**
- Modify: `ui/static/index.html` (IO panel CSS block, around line 398, before `/* ---- Signal graph`)

- [ ] **Step 1: Insert CSS after the last rule in the `/* ---- IO panel` block**

Find the line:
```css
    .pin.gpi:hover {
      border-color: var(--accent);
    }
```

Add immediately after the closing `}`:
```css

    /* ---- UART decoder -------------------------------------------------------- */

    .uart-divider {
      border: none;
      border-top: 1px solid var(--border);
      margin: 10px 0 8px;
    }

    .uart-controls {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
      margin-bottom: 5px;
    }

    .uart-label { font-size: 10px; color: var(--text-dim); }
    .uart-pin   { color: var(--accent2); }

    .uart-status {
      font-size: 10px;
      color: var(--text-dim);
      margin-bottom: 4px;
    }

    .uart-output {
      background: #141414;
      border: 1px solid var(--border);
      border-radius: 3px;
      padding: 5px 8px;
      font-size: 12px;
      color: var(--accent2);
      min-height: 36px;
      white-space: pre-wrap;
      word-break: break-all;
    }

    .uart-esc  { color: var(--accent); opacity: 0.8; }
    .uart-hint { font-size: 10px; color: var(--text-dim); margin-top: 4px; }
```

- [ ] **Step 2: Open the simulator in the browser and confirm no visual regressions**

```bash
# server must already be running; if not:
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
python -m uvicorn ui.server:app --reload --port 8000
```

Open http://localhost:8000 — IO panel should look identical to before (no new elements yet).

---

## Task 2: Add HTML for the decoder section

**Files:**
- Modify: `ui/static/index.html` (inside `#io-panel .panel-body`, after GPI section)

- [ ] **Step 1: Insert HTML after the closing `</div>` of the GPI `io-section`**

Find this block (around line 1228):
```html
          <div class="io-section">
            <div class="io-section-title">GPI (R31) — Input (click to toggle)</div>
            <div class="pin-grid" id="gpi-grid"></div>
          </div>

        </div>
      </div>
```

Replace with:
```html
          <div class="io-section">
            <div class="io-section-title">GPI (R31) — Input (click to toggle)</div>
            <div class="pin-grid" id="gpi-grid"></div>
          </div>

          <hr class="uart-divider" />
          <div class="io-section" id="uart-decoder">
            <div class="io-section-title">UART Decoder</div>
            <div class="uart-controls">
              <span class="uart-label">Pin: <span class="uart-pin">GPO0</span></span>
              <span class="uart-label">Format: <span class="uart-pin">8N1</span></span>
              <button id="uart-decode-btn" class="graph-btn" style="color:var(--accent);border-color:#3a5a8a;">&#9654; Decode</button>
              <button id="uart-clear-btn"  class="graph-btn">Clear</button>
            </div>
            <div id="uart-status" class="uart-status" style="display:none;"></div>
            <div id="uart-output" class="uart-output" style="display:none;"></div>
            <div id="uart-hint"   class="uart-hint">Use Signal Graph &#9679; REC to capture GPO0 first</div>
          </div>

        </div>
      </div>
```

- [ ] **Step 2: Verify in browser**

Reload http://localhost:8000. The IO panel should now show, below the GPI grid:
- A thin horizontal rule
- "UART DECODER" section title
- "▶ Decode" (blue) and "Clear" buttons
- Hint text: "Use Signal Graph ● REC to capture GPO0 first"

The buttons do nothing yet (no JS wired).

---

## Task 3: Implement the pure decode algorithm

**Files:**
- Modify: `ui/static/app.js` (after `graphGetSamples()`, around line 610)

- [ ] **Step 1: Add the three functions after `graphGetSamples()`**

Find the line that ends `graphGetSamples`:
```javascript
  return out;
}

/**
 * Resize the circular buffer to newSize
```

Insert between `}` and the `/**` comment:

```javascript
// ---- UART decoder ----------------------------------------------------------

/**
 * Decode a UART 8N1 bit stream from a flat array of 0/1 values.
 * Each element is one simulator step sample of GPO0.
 * Returns { bytes: number[], tBit: number, error: string|null }
 *   error: 'empty' | 'noisy' | 'idle' | null
 */
function decodeUARTBits(bits) {
  if (!bits || bits.length === 0) return { bytes: [], tBit: 0, error: 'empty' };

  // Run-length encode
  const runs = [];
  let i = 0;
  while (i < bits.length) {
    const bit = bits[i];
    let len = 0;
    while (i < bits.length && bits[i] === bit) { i++; len++; }
    runs.push({ bit, len });
  }

  // Auto-detect bit period = minimum run length
  const tBit = Math.min(...runs.map(r => r.len));
  if (tBit < 2) return { bytes: [], tBit: 0, error: 'noisy' };
  if (runs.every(r => r.bit === 1)) return { bytes: [], tBit: 0, error: 'idle' };

  // Decode UART frames (8N1: 1 start LOW, 8 data LSB-first, 1 stop HIGH)
  const bytes = [];
  let pos = 0;
  while (pos < bits.length) {
    if (bits[pos] === 1) { pos++; continue; }        // idle/stop, skip
    const start = pos;

    // Guard: start-bit run must be >= T/2 to reject glitches
    let startLen = 0;
    let p = pos;
    while (p < bits.length && bits[p] === 0) { p++; startLen++; }
    if (startLen < tBit * 0.5) { pos++; continue; }

    // Sample 8 data bits at midpoints: T*(n+1.5) from start of START bit
    let byteVal = 0;
    let valid = true;
    for (let n = 0; n < 8; n++) {
      const samplePos = start + Math.floor(tBit * (n + 1.5));
      if (samplePos >= bits.length) { valid = false; break; }
      byteVal |= bits[samplePos] << n;
    }
    if (!valid) break;                               // partial frame, stop

    // Framing check: stop bit must be HIGH
    const stopPos = start + Math.floor(tBit * 9.5);
    if (stopPos < bits.length && bits[stopPos] === 0) {
      pos = start + tBit;                            // framing error — skip one T, retry
      continue;
    }

    bytes.push(byteVal);
    pos = start + Math.floor(tBit * 10);             // advance past complete frame
  }

  return { bytes, tBit, error: null };
}

/**
 * Extract GPO0 from signal graph samples and call decodeUARTBits.
 * samples: return value of graphGetSamples().
 */
function decodeUART(samples) {
  const bits = samples.map(s => (s.gpo && s.gpo[0] !== undefined) ? (s.gpo[0] ? 1 : 0) : 1);
  return decodeUARTBits(bits);
}

/**
 * Render decoded bytes as an HTML string.
 * CR → \r, LF → \n in accent colour; non-printable → \xNN dim; else literal.
 */
function renderUARTBytes(bytes) {
  return bytes.map(b => {
    if (b === 0x0D) return '<span class="uart-esc">\\r</span>';
    if (b === 0x0A) return '<span class="uart-esc">\\n</span>';
    if (b >= 0x20 && b <= 0x7E) return String.fromCharCode(b).replace(/&/g, '&amp;').replace(/</g, '&lt;');
    return `<span class="uart-esc">\\x${b.toString(16).padStart(2, '0').toUpperCase()}</span>`;
  }).join('');
}
```

- [ ] **Step 2: Verify the functions exist in the browser console**

Open browser DevTools → Console. Type:
```
typeof decodeUARTBits
```
Expected: `"function"`

- [ ] **Step 3: Run the console unit test for `decodeUARTBits`**

Paste this into the browser console:
```javascript
const T = 10;
const mk = (v, n) => Array(n).fill(v);
// Bit stream for 'H' (0x48 = 0b01001000), T_bit=10
// Bits LSB-first: 0,0,0,1,0,0,1,0
const testBits = [
  ...mk(1,T),                        // idle
  ...mk(0,T),                        // START
  ...mk(0,T), ...mk(0,T), ...mk(0,T), // bit0=0, bit1=0, bit2=0
  ...mk(1,T),                        // bit3=1
  ...mk(0,T), ...mk(0,T),            // bit4=0, bit5=0
  ...mk(1,T),                        // bit6=1
  ...mk(0,T),                        // bit7=0
  ...mk(1,T),                        // STOP
];
const r = decodeUARTBits(testBits);
console.assert(r.error === null,      'error:' + r.error);
console.assert(r.tBit === T,          'tBit:' + r.tBit);
console.assert(r.bytes.length === 1,  'len:' + r.bytes.length);
console.assert(r.bytes[0] === 0x48,   'byte:0x' + r.bytes[0].toString(16));
console.log('PASS —', String.fromCharCode(r.bytes[0]), '(0x' + r.bytes[0].toString(16) + ')');
```

Expected output: `PASS — H (0x48)`

- [ ] **Step 4: Run the idle / empty error path tests**

```javascript
console.assert(decodeUARTBits([]).error         === 'empty', 'empty case');
console.assert(decodeUARTBits([1,1,1,1,1]).error === 'idle',  'idle case');
console.log('Error paths PASS');
```

Expected: `Error paths PASS`

---

## Task 4: Wire the Decode and Clear buttons

**Files:**
- Modify: `ui/static/app.js` (end of file, after the keyboard handler)

- [ ] **Step 1: Add button listeners at the very end of `app.js`**

Append after the final `});` (keyboard listener, line ~2781):

```javascript
// ---- UART decoder controls -------------------------------------------------

document.getElementById("uart-decode-btn").addEventListener("click", () => {
  const samples  = graphGetSamples();
  const statusEl = document.getElementById("uart-status");
  const outputEl = document.getElementById("uart-output");
  const hintEl   = document.getElementById("uart-hint");

  const showHint = (msg) => {
    hintEl.textContent = msg;
    hintEl.style.display = "";
    statusEl.style.display = "none";
    outputEl.style.display = "none";
  };

  if (samples.length === 0) {
    return showHint("Use Signal Graph \u25cf REC to capture GPO0 first");
  }

  const { bytes, tBit, error } = decodeUART(samples);

  if (error === "empty" || error === "noisy") {
    return showHint("Signal too noisy to auto-detect bit width");
  }
  if (error === "idle") {
    return showHint("No UART activity detected on GPO0");
  }

  hintEl.style.display = "none";
  statusEl.textContent = `Auto-detected: ${tBit} steps/bit \u00b7 ${bytes.length} bytes decoded`;
  statusEl.style.display = "";
  outputEl.innerHTML = renderUARTBytes(bytes);
  outputEl.style.display = "";
});

document.getElementById("uart-clear-btn").addEventListener("click", () => {
  document.getElementById("uart-status").style.display = "none";
  document.getElementById("uart-output").style.display = "none";
  document.getElementById("uart-hint").textContent = "Use Signal Graph \u25cf REC to capture GPO0 first";
  document.getElementById("uart-hint").style.display = "";
});
```

- [ ] **Step 2: Verify Clear button works**

Reload http://localhost:8000. Click **Clear** in the UART Decoder section.
Expected: hint resets to "Use Signal Graph ● REC to capture GPO0 first".

---

## Task 5: End-to-end test with `uart_print.asm`

- [ ] **Step 1: Write "Hello PRU\r\n" to DRAM0 and load the program**

In the simulator editor, paste `source/uart_print.asm` and click **Load & Assemble**.

Then open the browser console and run:
```javascript
sendAction({ action: "write_memory", addr: 0, data: [72,101,108,108,111,32,80,82,85,13,10,0] });
```
(That's `Hello PRU\r\n\0` as decimal bytes.)

- [ ] **Step 2: Enable recording and run**

1. In the Signal Graph panel, click **● REC** (button turns red).
2. Click **Run** in the top controls bar.
3. Wait for the status badge to show **HALTED**.
4. Click **Run** again to stop (if it didn't halt automatically).

- [ ] **Step 3: Decode**

In the IO panel, click **▶ Decode**.

Expected result:
- Status line: `Auto-detected: 1736 steps/bit · 11 bytes decoded` (exact tBit may vary ±a few depending on memory latency)
- Output box shows: `Hello PRU` followed by `\r\n` in accent colour

- [ ] **Step 4: Test Clear**

Click **Clear**. Output box and status line disappear; hint text returns.

- [ ] **Step 5: Test empty-buffer hint**

Click **● REC** to stop recording, then click **Clear** on the graph, then click **▶ Decode** in the IO panel.
Expected: hint says "Use Signal Graph ● REC to capture GPO0 first".

---

## Task 6: Commit

- [ ] **Step 1: Stage and commit**

```bash
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
git add ui/static/index.html ui/static/app.js
git commit -m "feat: add UART decoder section to IO panel

Post-processes Signal Graph GPO0 capture, auto-detects bit width
from minimum run-length, decodes 8N1 frames, shows ASCII output.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

Expected: commit succeeds, 2 files changed.

---

## Self-Review Notes

- **Spec coverage:**
  - ✅ Placement — IO panel section after GPI grid
  - ✅ Auto-detect bit timing — `Math.min(...runs.map(r => r.len))`
  - ✅ Manual trigger — `▶ Decode` button
  - ✅ Plain text output with non-printable escapes
  - ✅ 8N1 fixed framing
  - ✅ All error states covered (empty, noisy, idle, partial frame)
  - ✅ Hint line when buffer empty
  - ✅ Clear button
  - ✅ Status line with tBit and byte count

- **No placeholders** — all code blocks are complete and runnable.

- **Type consistency** — `decodeUARTBits` returns `{ bytes, tBit, error }`; `decodeUART` returns the same shape; button handler destructures `{ bytes, tBit, error }`. `renderUARTBytes` takes `bytes: number[]`. Consistent throughout.
