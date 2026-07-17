# UART RX Inject UI Panel — Design Specification

**Date:** 2026-07-10
**Component:** Dashboard IO Panel — UART RX Frame Injection
**Related Specs:**
- 2026-07-08-pru-uart-rx-frame-generator-design.md (backend implementation)

## 1. Overview

Adds a UI panel to the PRU simulator dashboard that lets users inject UART RX frames into the running firmware. The panel arms a `UARTFrameGenerator` on a GPI pin; the user then steps/runs the firmware to receive the frame. The backend `Simulator.uart_inject()` method already exists — this spec covers the frontend UI and the WebSocket handler that connects them.

## 2. Placement

New `io-section` placed **below** the existing UART Decoder section in the IO panel, separated by an `<hr class="uart-divider"/>`.

## 3. Layout

Two-row design inside a standard `.io-section`:

```
┌─────────────────────────────────────────────────────────┐
│ UART RX INJECT  (section title, uppercase, 10px, #888)  │
├─────────────────────────────────────────────────────────┤
│ [Hex|ASCII]  [________________________payload_input___] │
│ Pin:[GPI0▾]  Baud:[4.00] Mb  Frames:[1]    [▶ Inject]  │
│ ✓ Armed: 11 bytes × 1 frame on GPI0 @ 4.00 Mb · ...    │
└─────────────────────────────────────────────────────────┘
```

### Row 1: Mode Toggle + Payload Input

- **Hex/ASCII toggle**: Two-button toggle group (styled like `mem-btn-group`). Active button gets `#3a5a8a` background + accent color text.
- **Payload input**: Full-width monospace text field.
  - **Hex mode** (default): Space-separated hex bytes, e.g. `48 65 6C 6C 6F 57 6F 72 6C 64 21`
  - **ASCII mode**: Plain string, e.g. `HelloWorld!`. A `(N bytes)` counter appears to the right.
  - Switching modes auto-converts the current value between representations.

### Row 2: Settings + Inject Button

- **Pin**: Dropdown select (`GPI0` through `GPI3`). Default: `GPI0`.
- **Baud**: Numeric input, 40px wide, right-aligned, monospace. Shows value in Mbaud with 2 fractional digits (e.g., `4.00`, `3.95`). Suffix label `Mb` in dim text.
- **Frames**: Numeric input, 28px wide, centered. Default: `1`.
- **Inject button**: `.graph-btn` style, accent border+text, `▶ Inject` label. Pushed to the right via `margin-left:auto`.

### Status Line

Shown after a successful inject, hidden by default. Green (#6a9955), 9px font:

```
✓ Armed: 11 bytes × 1 frame on GPI0 @ 4.00 Mb · trigger cycle 1204 · run to receive
```

On validation error (e.g., invalid hex), shows red (#f38ba8):

```
✗ Invalid hex byte at position 4
```

## 4. Behavior

### 4.1 Inject Action (Arm Only)

Clicking **Inject**:
1. Validates the payload (the UI does not enforce a fixed length — any 1+ bytes accepted; the firmware determines how many it consumes).
2. Parses baudrate: `parseFloat(input) * 1_000_000` → integer Hz.
3. Sends WebSocket message (see section 5).
4. On success response: shows green status line with trigger cycle.
5. Does **not** auto-run the firmware. User steps/runs manually.

### 4.2 Hex/ASCII Toggle

- **Hex → ASCII**: Parse hex bytes, convert to string via `String.fromCharCode()`. Non-printable bytes become `·` in the display but remain in the internal byte array.
- **ASCII → Hex**: Convert each character to 2-digit hex, space-separated.
- Conversion preserves the internal byte array. If ASCII contains non-printable substitutions, switching back to Hex restores original values.

### 4.3 Validation

- **Hex mode**: Each token must be exactly 2 hex characters (0-9, a-f, A-F). Reject otherwise.
- **ASCII mode**: Each character → 1 byte. No multi-byte encoding.
- **Baud**: Must be > 0 and ≤ 10.00 Mbaud.
- **Frames**: Integer 1–100.
- **Pin**: From dropdown, always valid.

## 5. WebSocket Protocol

### Client → Server

```json
{
  "action": "uart_inject",
  "core": "pru0",
  "pin": 0,
  "payload": [72, 101, 108, 108, 111, 87, 111, 114, 108, 100, 33],
  "baudrate": 4000000,
  "frames": 1
}
```

### Server → Client (success)

```json
{
  "type": "uart_inject_ok",
  "trigger_cycle": 1204,
  "payload_len": 11,
  "frames": 1
}
```

### Server → Client (error)

```json
{
  "type": "error",
  "errors": ["No assembly loaded on pru0"]
}
```

## 6. Backend Handler

In `ui/server.py`, add a case for `action == "uart_inject"`:

```python
elif action == "uart_inject":
    core = data.get("core", "pru0")
    pin = data.get("pin", 0)
    payload = data.get("payload", [])
    baudrate = data.get("baudrate", 4_000_000)
    frames = data.get("frames", 1)
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
    await ws.send_json({
        "type": "uart_inject_ok",
        "trigger_cycle": trigger_cycle,
        "payload_len": len(payload),
        "frames": frames,
    })
```

Key: `trigger_cycle` = current PRU cycle counter + 5, so the frame generator arms just ahead of the current execution point.

## 7. Files to Modify

| File | Change |
|------|--------|
| `ui/static/index.html` | Add UART RX Inject section HTML after UART Decoder |
| `ui/static/app.js` | Add inject button handler, hex/ASCII toggle, payload validation, WebSocket send/receive |
| `ui/server.py` | Add `uart_inject` action handler in WebSocket message dispatch |

## 8. CSS Styling

All styles use existing CSS variables and classes:
- Section: `.io-section` + `.io-section-title`
- Toggle buttons: Inline-flex group with 1px border, active state via class
- Inputs: `background:var(--btn); border:1px solid var(--border); color:var(--text); font-family:monospace`
- Inject button: `.graph-btn` with accent border/color
- Status: Inline `<div>` with conditional color (green success / red error)

No new CSS file needed — all styles follow existing IO panel conventions.

## 9. Edge Cases

- **No firmware loaded**: Inject returns error "No assembly loaded on pru0". Status shows red.
- **Empty payload**: Validation rejects, shows "Payload must contain at least 1 byte".
- **Re-inject**: A second inject overwrites the previous generator. Only one generator active at a time per core.
- **Multi-core mode**: Uses the currently selected core (same pattern as `set_input` action).
- **SD mode active**: UART inject section remains visible (it uses GPI pins, not SD interface).
