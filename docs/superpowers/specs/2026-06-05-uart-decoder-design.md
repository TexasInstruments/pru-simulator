# UART Decoder — IO Panel

**Date:** 2026-06-05
**Status:** Approved

## Summary

Add a UART decoder section to the IO panel that post-processes a recorded GPO0 signal graph capture and displays decoded ASCII text. No server changes required.

---

## Context

`uart_print.asm` bit-bangs a UART TX signal on GPO0 (R30.t0) using a tight delay loop (`BAUD_COUNT = 868` iterations × 2 instructions = ~1736 steps/bit at the default memory latency). The Signal Graph panel can record GPO0 transitions via the `● REC` button. The decoder reads those recorded samples and reconstructs the transmitted bytes.

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Placement | IO panel section, below GPI grid | Directly associated with the pin being decoded; no new panel needed |
| Timing reference | Auto-detect from signal | Minimum run-length = 1 bit period; no user input required |
| Trigger | Manual **▶ Decode** button | User explicitly decides when to decode; avoids stale output during live stepping |
| Output format | Plain ASCII text | Sufficient for firmware string output; non-printable bytes as `\xNN` |
| Framing | 8N1 fixed | Matches all PRU UART examples in this repo |
| Data direction | GPO0 only (TX) | Scope is TX decode; RX decode is a separate future feature |

---

## UI Layout

Inside `#io-panel .panel-body`, after the GPI section:

```
──────────────────────────────────────
UART DECODER
Pin: GPO0   Format: 8N1   [▶ Decode]  [Clear]
Auto-detected: 1736 steps/bit · 11 bytes decoded
┌──────────────────────────────────────┐
│ Hello PRU\r\n                        │
└──────────────────────────────────────┘
Use Signal Graph ● REC to capture GPO0 first
──────────────────────────────────────
```

- **Pin** and **Format** are static labels (GPO0, 8N1) — no selectors needed for this scope.
- **Status line** is hidden until a decode has run. Shows auto-detected steps/bit and byte count.
- **Output box** is a read-only `<pre>` or `<div>` with monospace font. CR rendered as `\r`, LF as `\n` (both in accent colour), other non-printable bytes as `\xNN`.
- **Hint line** is shown when the signal graph buffer is empty or all-idle (constant HIGH).

---

## Algorithm — `decodeUART(samples)`

Input: array of sample objects from `graphGetSamples()` — each has `{ gpo: [bit0..bit19], ... }`.

```
1. Extract bit stream: bits = samples.map(s => s.gpo[0] ?? 1)

2. Run-length encode:
     runs = []
     for each consecutive run of equal bits: runs.push({ bit, len })

3. Auto-detect bit period:
     T = min(run.len for all runs)
     If T < 2 → signal too noisy or no capture → show hint, return

4. Find UART frames:
     i = 0
     while i < bits.length:
       if bits[i] == 1: i++; continue         // idle / stop bit, skip
       // falling edge found — potential START bit
       start = i
       // verify this looks like a full start bit (run ≥ T*0.5)
       if run_at(i).len < T * 0.5: i++; continue

       // sample 8 data bits at midpoints
       byte_val = 0
       for n in 0..7:
         sample_pos = start + floor(T * (n + 1.5))
         if sample_pos >= bits.length: abort frame
         byte_val |= bits[sample_pos] << n

       // verify STOP bit
       stop_pos = start + floor(T * 9.5)
       if stop_pos < bits.length and bits[stop_pos] == 0:
         // framing error — skip one T and retry
         i = start + T; continue

       bytes.push(byte_val)
       i = start + floor(T * 10)   // advance past full frame

5. Render: bytes → string, replacing:
     0x0D → '\r' (styled accent)
     0x0A → '\n' (styled accent)
     0x00..0x1F, 0x7F..0xFF (excluding above) → '\xNN' (dim)
     0x20..0x7E → literal character
```

---

## Error States

| Condition | Behaviour |
|-----------|-----------|
| Signal graph buffer empty | Show hint: "Use Signal Graph ● REC to capture GPO0 first" |
| All bits HIGH (idle, no transmission) | Show hint: "No UART activity detected on GPO0" |
| T < 2 (degenerate signal) | Show hint: "Signal too noisy to auto-detect bit width" |
| Framing error on a frame | Skip frame, continue; count framing errors and show in status |
| Partial final frame (buffer ends mid-byte) | Discard incomplete frame |

---

## Files Changed

| File | Change |
|------|--------|
| `ui/static/index.html` | Add `#uart-decoder` section HTML inside `#io-panel .panel-body` after GPI grid; add CSS for section title, controls row, status line, output box, hint line |
| `ui/static/app.js` | Add `decodeUART(samples)` function; wire `#uart-decode-btn` click → `decodeUART(graphGetSamples())`; wire `#uart-clear-btn` click → clear output; update hint visibility on Decode when buffer empty |

No changes to `ui/server.py`, `simulator.py`, or any Python core modules.

---

## Out of Scope

- Pin selection (only GPO0)
- RX decode (GPI)
- Live / auto-decode on every step
- Baud rate display in real Hz (would require knowing PRU clock)
- Protocol variants other than 8N1
