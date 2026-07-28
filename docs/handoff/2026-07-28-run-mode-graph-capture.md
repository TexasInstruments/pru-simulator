# Handoff — Signal Graph capture rate under Run (2026-07-28)

Cross-machine snapshot (agent memory is per-PC; this file travels with the
repo so any PC sees it after `git pull`). Continues from
[`2026-07-28-perif-mode-graph-lanes.md`](2026-07-28-perif-mode-graph-lanes.md),
same day.

## Status

**Complete** — released as **v0.2.5**. Suite green: 1212 passed, 2 xfailed.

## The report

> "The signal graph works good in simulation but not when I run the code. I am
> testing with perif_duty_cycle.asm"

(The file is `source/perif_duty_cycle_sweep.asm`.) Correct, and it is **not**
the v0.2.4 mode filter — v0.2.4 only made a pre-existing problem visible, by
removing the GPO lanes that used to fill the empty graph with something.

## Root cause: SIM and Run sample at rates 100x apart

The two dashboard buttons drive the simulator completely differently, and the
graph samples once per state push either way:

| button | action sent | instructions per state push |
|---|---|---|
| **SIM** | `{"action":"step","count":1}` every `sim-interval` | 1 |
| **Run** | `{"action":"run","max_steps":N}` every 10 ms | up to N (was 100 while recording, else 1000) |

So Run sampled the graph once per 100+ instructions. A perif bit at the
channel-0 `N=2` divider is **2 core cycles** wide, and the whole nine-byte
sweep is ~160 instructions — the entire transmission fell inside one or two
samples. Measured on the real simulator before the fix:

```
SIM  (1 instr/sample):  out 00000000000000000000000000110000000000000011110000...
                        clk 11111111111100110011001100110011001100110011001100...
Run  (100 instr/sample): out 10000000000000000000000000000000000000000000000000...
Run  (1000 instr/sample): all three lanes flat -> channel filtered out, nothing drawn
```

At 1000 the lanes never transition, so the active-channel filter drops them and
the graph is blank. That is the reported symptom.

## Fix: sample inside the server's run loop

`ui/server.py` — `run` and `run_multicore` accept `capture: true`, sample the
graph per instruction inside their loop, and ship the batch ahead of the state
push as `{"type":"capture","core","mode","samples":[...]}`. Samples are packed
ints — `[step, r30, gpi_bits, out_bits, oe_bits, clk_bits]` — to keep the batch
small. The state push carries `"captured": true` so the client does not sample
it again and append a duplicate.

**The stride is not global.** `_capture_due()` samples every instruction while
`perif.enabled`, every `CAPTURE_STRIDE_GP = 100` otherwise. GP-mode traces are
firmware-paced — a bit-banged 115200-baud UART bit is ~1736 core cycles — so
full-rate capture there would buy nothing and shrink the window's time span
100x, which would break Example 5's UART decode (its auto-detected bit period
needs the whole frame in the window). This was caught before commit: an earlier
draft used full rate unconditionally.

The stride is decided **per instruction, not once per chunk**, because firmware
enables peripheral mode from inside the run — the sweep writes GPCFG about 2
instructions in and is only ~160 long, so a stride fixed before the loop would
have decimated away the entire transmission. Side effect worth knowing: the
first couple of GP-mode setup instructions are decimated out, so a perif
capture starts at step 3, not step 1.

`ui/static/app.js` — `graphHandleCapture()` unpacks the batch; `graphSample()`
early-returns on `state.captured`; Run sends `capture: signalGraph.recording`
and its chunk size goes back to 1000 (the chunk no longer sets the sample rate,
so the old 100-while-recording workaround is gone).

**Perif captures are single-shot.** At one sample per instruction a run fills
even an 8192 window in milliseconds of wall clock, so a rolling buffer would
blur and evict the transmission before anyone could look at it. It fills once
and then `graphSetRecording(false)` switches REC off — the button and the dot
update, so it is visible. GP captures are decimated 100:1 and keep rolling
exactly as before.

## Testing

Two new tests in `tests/test_perif_server.py`:

- `test_run_capture_samples_every_instruction_for_the_graph` — drives Run with
  `capture: true` over the sweep firmware, asserts a `capture` message with
  `mode == "perif"`, contiguous per-instruction steps, all three ch0 lanes
  toggling **within a single chunk**, and `captured: true` on the state push
  (plus `false` when capture is off).
- `test_run_capture_decimates_in_gp_mode` — Run over `uart_tx.asm` yields
  exactly 10 samples per 1000 instructions at steps 100, 200, … This is the
  guard on not breaking the UART decode example.

Client half verified with the same `node:vm` + stubbed-DOM harness as v0.2.4:
a **real** capture batch, dumped from the websocket, fed through the real
`graphHandleCapture()` → `drawDigitalGraph()`. 157 samples buffered, lanes
`["perif0_out","perif0_out_en","perif0_clk"]`, and the reconstructed data lane
is the duty sweep — pulse widths 2, 4, 6, 8, 10, 12, 14, 16 samples, i.e.
12.5% → 100% in 12.5% steps after the 0% first byte.

## Gotcha that cost a debugging cycle

The new test passed alone and failed in module order. Cause:
`test_write_perif_register_via_ws` sets **CH0CFG0 `tx_frame_size = 8`** and
restores TXCFG but not CH0CFG0, and `TestClient(app)` shares one simulator
across the whole module. The sweep firmware needs `CH0CFG0 = 0` (continuous
mode) — a documented *host* prerequisite it does not set itself, which
`test_run_multicore_paces_perif_demo` already works around with the same
explicit write. **This applies to the dashboard too:** a leftover frame size in
the perif register editor makes the sweep transmit one fixed frame instead of
streaming, and the waveform never appears.

## How to reproduce

```bash
python3 -m pytest tests/ -q                    # 1212 passed, 2 xfailed
python3 ui/server.py                           # dashboard on :8080
```

Load `source/perif_duty_cycle_sweep.asm`, set `CH0CFG0 = 0`, arm **REC**, click
**Run**. Expect three perif lanes and a trace that freezes when the window
fills. For the exact 125 Mbit/s bit rate the demo is named for, start the
server against `memory_perif_125mbit_demo.cfg` (250 MHz); at the default
200 MHz the waveform is correct, just not 8 ns/bit.

## Open items

- **Still never viewed in a browser** — third handoff running with this caveat.
  Both halves are now exercised by executing the real code, but nobody has
  looked at the dashboard.
- **Single-shot has no UI affordance** beyond REC switching itself off. A
  "capture full" label, or an explicit single-shot/rolling toggle, would make
  the behavior discoverable rather than something you have to read about here.
- **The 100:1 GP stride is a constant**, not a user control. A timebase /
  decimation selector next to the window-size dropdown is the real fix if
  anyone needs a GP capture at finer resolution, or a perif capture spanning
  more instructions than the window holds.
- **SIM is untouched** and still samples one instruction per message with no
  decimation and no single-shot. That is fine, but it means the two buttons now
  differ in capture semantics as well as speed.
- Unchanged from earlier handoffs: the speed-selector endpoint rewriting
  `memory.cfg` line endings, `PerifRegisters.get_busy()`, SD mode still
  plotting GPO/GPI, pif_eth Options 2/3, the `PROJECT_REPORT.md` RX section,
  channel-1/2 striping, broadside CRC.
