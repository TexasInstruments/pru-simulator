# UART RX Inject UI Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a UART RX Inject panel to the dashboard IO panel that lets users configure and inject UART frames into the running firmware via WebSocket.

**Architecture:**
- HTML section added below UART Decoder in the IO panel with hex/ASCII toggle, payload input, pin/baud/frames settings, and Inject button
- JavaScript handler validates input, sends `uart_inject` WebSocket action, and displays status
- Server handler calls existing `Simulator.uart_inject()` with `trigger_cycle = current_cycle + 5`

**Tech Stack:** HTML, vanilla JavaScript, Python (FastAPI WebSocket)

---

## File Structure

| File | Responsibility |
|------|---------------|
| **Modify:** `ui/static/index.html` (~line 1370) | Add UART RX Inject section HTML after UART Decoder div |
| **Modify:** `ui/static/app.js` (~line 3097) | Add inject button handler, hex/ASCII toggle, validation, WS message handling |
| **Modify:** `ui/server.py` (~line 414) | Add `uart_inject` action case in WebSocket dispatch |

---

## Task 1: WebSocket Handler — Backend `uart_inject` Action

**Files:**
- Modify: `ui/server.py:414`

- [ ] **Step 1: Add the `uart_inject` action handler**

Insert after the `set_loopback` handler (line 414) and before the `except Exception` (line 415):

```python
            elif action == "set_loopback":
                sim.set_loopback(core, int(msg["group"]), bool(msg["enabled"]))
            elif action == "uart_inject":
                pin = int(msg.get("pin", 0))
                payload = msg.get("payload", [])
                baudrate = int(msg.get("baudrate", 4_000_000))
                frames = int(msg.get("frames", 1))
                pru = sim._get_core(core)
                trigger_cycle = pru.counters.cycles + 5
                sim.uart_inject(
                    core=core,
                    pin=pin,
                    payload=payload,
                    baudrate=baudrate,
                    trigger_cycle=trigger_cycle,
                    frames=frames,
                )
                await websocket.send_text(json.dumps({
                    "type": "uart_inject_ok",
                    "trigger_cycle": trigger_cycle,
                    "payload_len": len(payload),
                    "frames": frames,
                }))
```

- [ ] **Step 2: Verify server starts without errors**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -c "import ui.server; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add ui/server.py
git commit -m "feat(ui): add uart_inject WebSocket action handler"
```

---

## Task 2: HTML Section — UART RX Inject UI

**Files:**
- Modify: `ui/static/index.html:1370`

- [ ] **Step 1: Add the UART RX Inject section HTML**

Insert after line 1370 (closing `</div>` of `#uart-decoder`) and before line 1372 (closing `</div>` of panel-body):

```html
          <hr class="uart-divider" />
          <div class="io-section" id="uart-rx-inject">
            <div class="io-section-title">UART RX Inject</div>
            <!-- Row 1: Mode toggle + payload input -->
            <div class="uart-controls" style="margin-bottom:5px;">
              <div class="uart-inject-mode-toggle">
                <button id="uart-inj-mode-hex" class="uart-inj-mode active">Hex</button>
                <button id="uart-inj-mode-ascii" class="uart-inj-mode">ASCII</button>
              </div>
              <input type="text" id="uart-inj-payload" class="uart-inj-input" value="48 65 6C 6C 6F 57 6F 72 6C 64 21" spellcheck="false" />
              <span id="uart-inj-byte-count" class="uart-label" style="display:none;"></span>
            </div>
            <!-- Row 2: Pin, Baud, Frames, Inject button -->
            <div class="uart-controls">
              <span class="uart-label">Pin:</span>
              <select id="uart-inj-pin" class="uart-inj-select">
                <option value="0">GPI0</option>
                <option value="1">GPI1</option>
                <option value="2">GPI2</option>
                <option value="3">GPI3</option>
              </select>
              <span class="uart-label">Baud:</span>
              <input type="text" id="uart-inj-baud" class="uart-inj-num" value="4.00" />
              <span class="uart-label" style="color:#666;">Mb</span>
              <span class="uart-label">Frames:</span>
              <input type="text" id="uart-inj-frames" class="uart-inj-num" style="width:28px;text-align:center;" value="1" />
              <button id="uart-inj-btn" class="graph-btn" style="color:var(--accent);border-color:#3a5a8a;margin-left:auto;">&#9654; Inject</button>
            </div>
            <!-- Status line -->
            <div id="uart-inj-status" class="uart-hint" style="display:none;"></div>
          </div>
```

- [ ] **Step 2: Add CSS styles for the inject section**

Insert in the `<style>` block (after existing `.uart-hint` styles, around line 600-650 area — search for `.uart-hint`):

```css
.uart-inject-mode-toggle {
  display: inline-flex;
  border: 1px solid #444;
  border-radius: 3px;
  overflow: hidden;
}
.uart-inj-mode {
  background: transparent;
  border: none;
  color: #888;
  padding: 2px 6px;
  font-size: 9px;
  cursor: pointer;
  font-family: inherit;
}
.uart-inj-mode.active {
  background: #3a5a8a;
  color: var(--accent);
}
.uart-inj-input {
  flex: 1;
  background: var(--btn);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 3px 6px;
  font-size: 11px;
  font-family: monospace;
  border-radius: 2px;
  min-width: 120px;
}
.uart-inj-select {
  background: var(--btn);
  border: 1px solid var(--border);
  color: var(--accent);
  padding: 1px 4px;
  font-size: 10px;
  border-radius: 2px;
}
.uart-inj-num {
  background: var(--btn);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 1px 4px;
  font-size: 10px;
  width: 40px;
  text-align: right;
  border-radius: 2px;
  font-family: monospace;
}
```

- [ ] **Step 3: Verify HTML is valid by opening the page**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -c "from html.parser import HTMLParser; HTMLParser().feed(open('ui/static/index.html').read()); print('HTML OK')"`
Expected: `HTML OK`

- [ ] **Step 4: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add ui/static/index.html
git commit -m "feat(ui): add UART RX Inject section HTML and CSS"
```

---

## Task 3: JavaScript — Hex/ASCII Toggle and Payload Parsing

**Files:**
- Modify: `ui/static/app.js` (append after UART decoder handlers, ~line 3097)

- [ ] **Step 1: Add hex/ASCII toggle logic and payload parsing helpers**

Append after the existing `uart-clear-btn` handler (around line 3097):

```javascript
/* ───────────── UART RX Inject ───────────── */

(function() {
  const modeHex   = document.getElementById("uart-inj-mode-hex");
  const modeAscii = document.getElementById("uart-inj-mode-ascii");
  const payloadEl = document.getElementById("uart-inj-payload");
  const countEl   = document.getElementById("uart-inj-byte-count");
  let currentMode = "hex"; // "hex" or "ascii"

  // Internal byte array (source of truth)
  let _bytes = parseHexInput(payloadEl.value);

  function parseHexInput(str) {
    const tokens = str.trim().split(/\s+/).filter(t => t.length > 0);
    const bytes = [];
    for (const t of tokens) {
      if (!/^[0-9a-fA-F]{1,2}$/.test(t)) return null; // invalid
      bytes.push(parseInt(t, 16));
    }
    return bytes;
  }

  function parseAsciiInput(str) {
    const bytes = [];
    for (let i = 0; i < str.length; i++) {
      bytes.push(str.charCodeAt(i) & 0xFF);
    }
    return bytes;
  }

  function bytesToHex(bytes) {
    return bytes.map(b => b.toString(16).padStart(2, "0").toUpperCase()).join(" ");
  }

  function bytesToAscii(bytes) {
    return bytes.map(b => (b >= 32 && b < 127) ? String.fromCharCode(b) : "\u00B7").join("");
  }

  function updateByteCount() {
    if (currentMode === "ascii") {
      countEl.textContent = `(${_bytes ? _bytes.length : 0} bytes)`;
      countEl.style.display = "";
    } else {
      countEl.style.display = "none";
    }
  }

  function switchMode(mode) {
    if (mode === currentMode) return;
    // Sync _bytes from current input before switching
    if (currentMode === "hex") {
      const parsed = parseHexInput(payloadEl.value);
      if (parsed) _bytes = parsed;
    } else {
      _bytes = parseAsciiInput(payloadEl.value);
    }
    currentMode = mode;
    modeHex.classList.toggle("active", mode === "hex");
    modeAscii.classList.toggle("active", mode === "ascii");
    if (_bytes) {
      payloadEl.value = (mode === "hex") ? bytesToHex(_bytes) : bytesToAscii(_bytes);
    }
    updateByteCount();
  }

  modeHex.addEventListener("click", () => switchMode("hex"));
  modeAscii.addEventListener("click", () => switchMode("ascii"));
  payloadEl.addEventListener("input", () => {
    if (currentMode === "hex") {
      _bytes = parseHexInput(payloadEl.value);
    } else {
      _bytes = parseAsciiInput(payloadEl.value);
    }
    updateByteCount();
  });

  // Expose getPayloadBytes for the inject handler
  window._uartInjectGetBytes = function() {
    if (currentMode === "hex") {
      return parseHexInput(payloadEl.value);
    } else {
      return parseAsciiInput(payloadEl.value);
    }
  };
})();
```

- [ ] **Step 2: Verify no syntax errors**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && node -c "$(cat ui/static/app.js)" && echo "JS OK"`
Expected: `JS OK`

- [ ] **Step 3: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add ui/static/app.js
git commit -m "feat(ui): add hex/ASCII toggle and payload parsing for UART inject"
```

---

## Task 4: JavaScript — Inject Button Handler and Status Display

**Files:**
- Modify: `ui/static/app.js` (append after Task 3 code)

- [ ] **Step 1: Add inject button click handler and WS response handler**

Append after the IIFE from Task 3:

```javascript
document.getElementById("uart-inj-btn").addEventListener("click", () => {
  const statusEl = document.getElementById("uart-inj-status");
  const bytes = window._uartInjectGetBytes();

  // Validate payload
  if (!bytes || bytes.length === 0) {
    statusEl.textContent = "\u2717 Invalid payload \u2014 enter space-separated hex bytes or ASCII text";
    statusEl.style.color = "#f38ba8";
    statusEl.style.display = "";
    return;
  }

  // Parse baud
  const baudStr = document.getElementById("uart-inj-baud").value.trim();
  const baudMb = parseFloat(baudStr);
  if (isNaN(baudMb) || baudMb <= 0 || baudMb > 10) {
    statusEl.textContent = "\u2717 Baud must be between 0.01 and 10.00 Mb";
    statusEl.style.color = "#f38ba8";
    statusEl.style.display = "";
    return;
  }
  const baudrate = Math.round(baudMb * 1_000_000);

  // Parse frames
  const framesVal = parseInt(document.getElementById("uart-inj-frames").value, 10);
  if (isNaN(framesVal) || framesVal < 1 || framesVal > 100) {
    statusEl.textContent = "\u2717 Frames must be between 1 and 100";
    statusEl.style.color = "#f38ba8";
    statusEl.style.display = "";
    return;
  }

  // Parse pin
  const pin = parseInt(document.getElementById("uart-inj-pin").value, 10);

  // Send WebSocket action
  sendAction({
    action: "uart_inject",
    core: currentCore,
    pin: pin,
    payload: bytes,
    baudrate: baudrate,
    frames: framesVal,
  });

  // Optimistic status (will be updated by server response)
  statusEl.textContent = "\u231B Arming...";
  statusEl.style.color = "#888";
  statusEl.style.display = "";
});
```

- [ ] **Step 2: Add handler for `uart_inject_ok` response in the WebSocket onmessage handler**

Find the `ws.onmessage` handler (around line 349-365 in app.js). Inside the `try` block, after the existing `msg.type` checks, add a new case. Locate the pattern:

```javascript
      } else if (msg.type === "error") {
```

Insert before that line:

```javascript
      } else if (msg.type === "uart_inject_ok") {
        const st = document.getElementById("uart-inj-status");
        if (st) {
          st.textContent = "\u2713 Armed: " + msg.payload_len + " bytes \u00D7 " +
            msg.frames + " frame" + (msg.frames > 1 ? "s" : "") +
            " \u00B7 trigger cycle " + msg.trigger_cycle + " \u00B7 run to receive";
          st.style.color = "#6a9955";
          st.style.display = "";
        }
```

- [ ] **Step 3: Verify no syntax errors**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && node -c "$(cat ui/static/app.js)" && echo "JS OK"`
Expected: `JS OK`

- [ ] **Step 4: Commit**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git add ui/static/app.js
git commit -m "feat(ui): add UART inject button handler with validation and status display"
```

---

## Task 5: Integration Test — End-to-End Verification

**Files:**
- No new files — manual verification

- [ ] **Step 1: Start the server and verify the panel renders**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python ui/server.py &`
Open: `http://localhost:8080`
Expected: IO panel shows "UART RX Inject" section below "UART Decoder" with hex input, mode toggle, pin/baud/frames controls, and Inject button.

- [ ] **Step 2: Run the existing test suite to confirm no regressions**

Run: `cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -m pytest tests/ --tb=short -q`
Expected: All tests pass (the UI changes don't affect Python tests, but confirms server imports still work).

- [ ] **Step 3: Test the WebSocket handler programmatically**

Run:
```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator && python -c "
import asyncio, json
from simulator import Simulator

sim = Simulator()
src = open('source/uart_rx_11frame.asm').read()
sim.load('pru0', src)

# Simulate what the WS handler does
pru = sim._get_core('pru0')
trigger_cycle = pru.counters.cycles + 5
sim.uart_inject(
    core='pru0', pin=0,
    payload=[0x48, 0x65, 0x6C, 0x6C, 0x6F, 0x57, 0x6F, 0x72, 0x6C, 0x64, 0x21],
    baudrate=4_000_000, trigger_cycle=trigger_cycle, frames=1,
)
print(f'Armed at cycle {trigger_cycle}')

# Run firmware
sim.step('pru0', count=8000)
data = sim.memory_read(0x0000, 11)
print(f'Received: {bytes(data)}')
assert list(data) == [0x48, 0x65, 0x6C, 0x6C, 0x6F, 0x57, 0x6F, 0x72, 0x6C, 0x64, 0x21]
print('Integration OK')
"
```
Expected: `Armed at cycle 5` / `Received: b'HelloWorld!'` / `Integration OK`

- [ ] **Step 4: Final commit (if any fixups needed)**

```bash
cd C:\ti\industrial-automation-lab\Projects\pru_simulator
git status
# If changes exist:
git add -A
git commit -m "fix(ui): UART inject UI integration fixups"
```

---

## Notes for Implementers

### Existing Patterns to Follow
- `sendAction()` (app.js line ~372) sends JSON over WebSocket — reuse it directly
- `currentCore` variable (app.js) tracks selected PRU core — use it for the `core` field
- `.graph-btn` class for styled action buttons (same as Decode button)
- `.uart-controls` flex row with 6px gap for control layout
- `.uart-label` for dim 10px label text
- `.uart-hint` for status text below controls

### Key Implementation Details
- The baudrate input stores Mbaud as a string (e.g., "4.00"). Convert to Hz: `parseFloat(val) * 1_000_000`.
- The `trigger_cycle` is computed server-side as `pru.counters.cycles + 5` — the client does not need to know the current cycle.
- The `uart_inject_ok` response provides `trigger_cycle` for display in the status line.
- The server does NOT send a state update after `uart_inject` — it only sends the confirmation message. This is intentional: arming doesn't change visible pin state until the user steps.

### SD Mode Visibility
The UART RX Inject section should remain visible even when SD mode is active (it uses GPI pins independently of the SD interface). The existing SD mode hides `.io-section:not(.sd-interface)` — to keep the inject section visible, it needs the `.sd-interface` class OR the SD-hide rule needs adjustment. Check the CSS rule and add `.sd-interface` class to the section if needed.
