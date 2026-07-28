# Handoff — Signal Graph hides GPO/GPI in peripheral mode (2026-07-28)

Cross-machine snapshot (agent memory is per-PC; this file travels with the
repo so any PC sees it after `git pull`). Continues from
[`2026-07-27-perif-reset-and-graph-clock-lanes.md`](2026-07-27-perif-reset-and-graph-clock-lanes.md).

## Status

**Complete** — released as **v0.2.4** (UI logo tooltip + readme changelog both
bumped). Suite green: 1210 passed, 2 xfailed.

## The report

> "On peripheral mode signal capture there are also direct GPOs shown which is
> confusing as in peripheral mode these pins are not available."

Correct, and the fix is the one recommended: in perif mode show only the
peripheral signals — out, out_en, tx_clk.

The graph's channel filter was mode-blind. It walked all 20 GPO and 20 GPI bits
from `state.io.gpo_pins` / `gpi_pins` (derived from R30 / `read_r31()`) and drew
a lane for anything that transitioned, regardless of `io.mode`. In perif mode
the GP Mux has handed those pads to the Peripheral Interface, so firmware still
writing R30 — as `perif_tx_pattern.asm` does, since the perif `tx_go` strobe
*is* an R30 write — produced GPO lanes sitting right beside the perif lanes,
describing pins that are not on the wire.

## What changed (`ui/static/app.js` only)

- `graphSample()` records `mode: state.io.mode` per sample, plus a new
  `perifOe[3]` field sampled from each channel's `tx_out_en`. Both were already
  in the websocket state payload — no server change was needed.
- `drawDigitalGraph()` decides from the **newest** sample's mode. In perif mode
  the GPO/GPI loops are skipped entirely; GP mode is byte-for-byte unchanged.
- Perif channels now contribute **three** lanes instead of two —
  `perifN_out`, `perifN_out_en`, `perifN_clk`, in that order, with
  `GRAPH_PERIF_OE_COLORS` as the darkest of the three same-hue variants.
- The active-channel test moved from per-signal to per-channel: a channel is
  drawn if *any* of its three signals transitions, and then all three lanes are
  drawn. Without this, a channel that drives continuously (steady `out_en=1`)
  would have had its out_en lane filtered out as "inactive" — exactly the lane
  the user asked for. Fully idle channels are still omitted.
- CSV export gains a `mode` column and three `perifN_out_en` columns.
- Old samples lack `mode` / `perifOe`; both paths default (`"gpio"` / 0).

Docs: `getting_started.md` §Signal Graph and the panel table; readme changelog.

## Testing

`tests/test_perif_server.py::test_state_carries_out_en_and_mode_for_perif_graph_lanes`
— the sibling of the v0.2.3 `tx_clk_pin` test. It guards the two payload keys
the JS filter depends on (`io.mode == "perif"` on every push, `tx_out_en`
present) and asserts out_en actually asserts while transmitting.

There is still no JS test infra in the repo, so that payload-contract test is
the only *committed* guard. The rendering itself was verified out-of-tree by
running the real `app.js` under `node:vm` with a Proxy-based DOM stub and a
recording 2D context, feeding synthetic states through the real
`graphSample()` → `drawDigitalGraph()` path and reading back the `fillText`
lane labels:

| scenario | lanes drawn |
|---|---|
| gpio mode, GPO3+GPI5 toggling | `GPO 3`, `GPI 5`, `perif0_out`, `perif0_out_en`, `perif0_clk` |
| perif mode, same stimulus | `perif0_out`, `perif0_out_en`, `perif0_clk` |
| perif mode, `out_en` steady high | `perif0_out`, `perif0_out_en`, `perif0_clk` |

Idle channels 1/2 correctly omitted in all three. Two gotchas if that harness
is ever rebuilt: `layout.js` must be evaluated in the same context first (app.js
calls `resetLayout` at top level), and the DOM stub must return `null` for
`firstChild`/`parentNode`-style links or a `while (el.firstChild)` idiom spins
forever.

## How to reproduce

```bash
python3 -m pytest tests/ -q                    # 1210 passed, 2 xfailed
python3 ui/server.py                           # dashboard on :8080
```

Load `source/perif_tx_pattern.asm` (self-configuring), open **Signal Graph**,
click **● REC**, then **Run**. Expect three perif lanes and no GPO/GPI lanes.
Switching the GP Mux back to 0 restores GPO/GPI on the next sample.

## Open items

- **Still never viewed in a browser** — same caveat as v0.2.3. The lane
  *selection* is now verified by executing the real drawing function, but the
  on-screen result and the `GRAPH_PERIF_OE_COLORS` choices are unconfirmed by
  eye.
- **Mode is decided by the newest sample.** A capture that spans a mux switch
  is drawn entirely under the mode in effect at the end, so the GPO lanes from
  the GP-mode portion vanish the moment perif mode is entered. Reasonable for a
  live scope; if per-sample masking is ever wanted, `mode` is already in every
  sample.
- **SD mode is untouched** — it still plots GPO/GPI, and the same objection
  arguably applies (`mux_sel == MUX_SD` hands the pads over too). Left alone
  because the SD path has no equivalent per-channel line signals in the state
  payload to plot instead.
- Unchanged from the previous handoff: the speed-selector endpoint rewriting
  `memory.cfg` line endings, `PerifRegisters.get_busy()`, pif_eth Options 2/3,
  the `PROJECT_REPORT.md` RX section, channel-1/2 striping, broadside CRC.
