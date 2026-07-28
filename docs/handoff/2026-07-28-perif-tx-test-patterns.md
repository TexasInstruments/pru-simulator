# Handoff — Peripheral Interface TX test patterns (2026-07-28)

Cross-machine snapshot (agent memory is per-PC; this file travels with the
repo so any PC sees it after `git pull`). Continues from
[`2026-07-28-run-mode-graph-capture.md`](2026-07-28-run-mode-graph-capture.md),
same day — third change in this session.

## Status

**Complete** — released as **v0.2.6**. Suite green: 1221 passed, 2 xfailed.

## Why

`perif_tx_pattern.asm` streams an 8-bit counter, which is the right stimulus
for the drift experiment but a poor one for looking at a waveform: MSB-first,
a small counter is ~90% zeros, so the Signal Graph shows sparse narrow pulses
that are hard to check by eye. (Confirmed again this session that the counter
itself is correct — decoded 300 bytes over the loopback, `0x00…0xFF` wrapping
clean, including the wrap.) The ask was for the classic patterns instead:
running 1, running 0, `0xAA`, `0x55`, `0x00`, `0xFF`.

## Shape, and why a new file

`source/perif_tx_patterns.asm` — new, with a `PATTERN .set` selector; the
default `0` transmits the whole sequence then falls into the counter.

**`perif_tx_pattern.asm` is deliberately untouched.** `tools/perif_drift_report.py:73`
hard-codes `b != (i & 0xFF)` and four tests assert the plain counter from byte
0, so changing its default output would break the drift tooling. Adding the
selector there with a counter default would have kept them green but would
have made the patterns the non-default behavior — the opposite of the point.

| `PATTERN` | transmits |
|---|---|
| `0` (default) | `00 FF AA 55` \| `01 02 04 08 10 20 40 80` \| `FE FD FB F7 EF DF BF 7F`, then the counter forever |
| `1` / `2` | `0x00` / `0xFF` forever |
| `3` / `4` | `0xAA` / `0x55` forever |
| `5` / `6` | walking 1 / walking 0, looping |
| `7` | counter only — same stream as `perif_tx_pattern.asm` |

One byte per pattern in the sequence, chosen against the graph: at `div=7` a
byte is ~64 samples, so 20 bytes is ~1280 and fits the 2048 window in one
single-shot capture. Measured 8.2 samples/bit on the real capture path.

## Implementation notes

- **Patterns live in a DRAM table**, not in code: the prologue writes five
  little-endian words to `0x1F00..0x1F13` (this core's own DRAM, clear of the
  low region, visible in the Memory panel). Changing the patterns is five
  `ldi` pairs. `PAT_START` / `PAT_END` are set per `PATTERN` by `.if` blocks
  to absolute addresses — the parser does not evaluate arithmetic in
  immediates, so no `BASE + 4` expressions.
- **One loop body, not a prefill loop plus a main loop.** The FIFO is 4 deep
  and must be primed before the TX go strobe, so the original file has the
  generator written twice. Here `r6` counts pushes and saturates at 4:
  `qbgt push, r6, 4` skips the room check while priming, and the go strobe
  fires on the transition to 4. That keeps the pattern generator in exactly
  one place — there is no call/ret in this ISA, and a `.macro` would have
  duplicated its internal labels.
- **Framing is unchanged** from `perif_tx_pattern.asm`: `push_k = carry |
  (p_k >> 1)`, `carry_next = (p_k & 1) << 7`, `carry_0 = 0x80`. The RX eats
  the leading 1 as its start bit and byte-aligns.
- `lbbo r2, r7, 0, 1` writes only r2's low byte, so an explicit
  `and r2, r2, 0xFF` follows rather than relying on the upper bytes being
  clean from the previous `mov r2, r10`.
- Branch operands are reversed in this ISA — `qbXX label, reg, op` compares
  `op` against `reg`, so `qbgt push, r6, 4` means "taken when 4 > r6".

## Testing

`tests/test_perif_tx_patterns.py` — 9 tests, all eight `PATTERN` values
decoded end-to-end over the ch0 loopback with a Python-armed RX, plus a guard
that the shipped file still defaults to `0`. Checking through the receiver
rather than reading the DRAM table is deliberate: it exercises the carry
chain, and a wrong pre-shift would show up as every value coming out rotated.
Uses `nominal_config` so it does not inherit the UI speed selector's clock.

Also verified independently through the graph path — a real `capture` batch
from the websocket, bit grid taken from the `perif0_clk` edges, decodes to
the expected 20 bytes.

**Gotcha for anyone decoding that lane by hand:** in `clk_mode 0` the clock
toggles once per *bit*, so a rising edge is every *two* bits. Counting rising
edges only gives half the bits and a garbage decode — use every clock edge.
Cost a debugging cycle here.

## How to reproduce

```bash
python3 -m pytest tests/test_perif_tx_patterns.py -q     # 9 passed
python3 ui/server.py                                     # dashboard on :8080
```

Load `source/perif_tx_patterns.asm`, set the graph window to 2048, arm
**REC**, click **Run**. To receive it, enable the ch0 loopback and run
`perif_rx_capture.asm` on PRU1.

## Open items

- **Still never viewed in a browser** — fourth handoff carrying this caveat.
  The byte stream, the framing and the graph lane are all verified by
  executing real code; nobody has looked at the dashboard.
- **`PATTERN` is assemble-time.** Switching means editing the constant and
  clicking Load & Assemble again. A host-writable mode byte in DRAM would
  make it switchable at runtime, at the cost of another undocumented host
  prerequisite like the `CH0CFG0 = 0` one that keeps biting.
- **Channel 0 only**, like every other perif example. Channels 1/2 are still
  the flagged TRM simplification.
- Unchanged from earlier handoffs: the speed-selector endpoint rewriting
  `memory.cfg` line endings, `PerifRegisters.get_busy()`, SD mode still
  plotting GPO/GPI in the graph, single-shot having no UI affordance beyond
  REC switching off, pif_eth Options 2/3, the `PROJECT_REPORT.md` RX section,
  broadside CRC.
