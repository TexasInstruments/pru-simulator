# Handoff — PRU core speed selector + pif_eth n=2 (125 Mbaud) firmware (2026-07-21)

Cross-machine snapshot (agent memory is per-PC; this file travels with the
repo so any PC sees it after `git pull`). Continues from
[`2026-07-20-pif-eth.md`](2026-07-20-pif-eth.md).

> **Continued in
> [`2026-07-22-pif-eth-rx-o1.md`](2026-07-22-pif-eth-rx-o1.md)**: the n=2
> firmware below was promoted from reference-only to CI-exercised firmware
> (RX driver selects it by divider), and a PRU1 RX path was added on top of
> it. Also fixes a real frame-buffer overrun bug.

## Status

**Both complete, committed and pushed to `main`** (`fba9914` firmware,
`0e60f99` version bump). UI version bumped **v0.2.0 → v0.2.1** (hover
"PRU SIM" in the dashboard header to confirm).

## 1. PRU core speed selector

A controls-bar dropdown lets you pick PRU core clock speed from
`{200, 225, 250, 300, 333}` MHz, applying to PRU0/RTU0/PRU1 together.

- Backend: `GET`/`PUT /config/clock_speed` in `ui/server.py` — regex-patches
  `pru_clock_mhz` and `pru1_clock_mhz` in `memory.cfg` (not
  `configparser.write()`, to preserve file formatting/comments), then
  rebuilds the `Simulator`.
- Frontend: `#pru-speed-select` in `ui/static/index.html` / `ui/static/app.js`,
  next to the core-select dropdown.
- **Always syncs all three cores.** The PRU0→PRU1 perif clock-drift demo (see
  `ui-drift-demo-walkthrough` in per-PC memory, or
  `docs/superpowers/specs/2026-07-18-pru1-perif-drift-design.md`) sets
  `pru1_clock_mhz` to a deliberately offset value as a *separate* manual step
  — touching the speed dropdown afterward silently resets that offset back in
  sync. Not a bug, just a footgun to remember.
- Tests: `tests/test_clock_speed_endpoint.py` (4 tests, incl. a regression
  test for a real bug caught during manual verification: the ini-patcher's
  insert-branch was eating the blank line before `[DRAM0]` when adding a
  missing key).
- Design: `docs/superpowers/specs/2026-07-20-pru-core-speed-selector-design.md`.
  Plan: `docs/superpowers/plans/2026-07-20-pru-core-speed-selector.md`.

## 2. pif_eth experimental 125 Mbaud TX firmware

Follow-up to the original pif_eth work (see the 2026-07-20 handoff): used the
new speed selector to explore whether the documented 125 Mbaud target
(TXCFG n=2 @ 250 MHz core) is actually reachable. Two new firmware files in
`source/pif_eth/`, **neither wired into `driver.py` or `tests/test_pif_eth.py`
— both are reference artifacts**:

- **`pif_eth_tx_n2.asm`** — batched 4-octet `LBBO` loads + fixed-shift
  unrolled 8b/10b bit-packing (the bit-remainder cycles 0→2→4→6→0
  deterministically, so shift amounts are compile-time constants instead of
  runtime `nbits`-tracked values), applied to both the data loop and the
  leading/trailing K28.5 commas. All FIFO-full checks intact.
  **Validated zero-BER at n=3 through n=8** (83.33 MHz down to 31.25 MHz) —
  a genuine ~4x speedup over the original 25 MHz design, with the same
  self-adapting safety margin. **Fails at n=2** (underrun, then deadlock).
- **`pif_eth_tx_n2_skipchecks.asm`** — same, with the FIFO-full check removed
  on the data loop's phase 0/1 (single-byte-emit) pushes. **Hits the full
  125 Mbaud (n=2) target with zero BER**, validated over 100 BERT + 100 UDP
  frames. **Critical caveat: hard-pinned to n=2.** The same firmware overruns
  (~68% BER) at n=3/n=4 — the removed checks were providing automatic
  rate-adaptation to whatever the drain rate is, not just a safety margin.
  If `pru_clock_mhz` or the TXCFG divider ever changes, this firmware must be
  re-validated before trusting its output — it fails *silently* (wrong bytes,
  not a crash) except right at n=2 itself.

Design note with full results table and root-cause explanation:
`docs/superpowers/specs/2026-07-21-pif-eth-n2-125mbaud-design.md`.

## How to reproduce the n=2 experiment

```bash
# not a pytest target -- was run as an ad-hoc script against a 250 MHz config;
# see the design note's results table for the numbers. To rerun by hand:
python3 -c "
import sys; sys.path.insert(0, '.'); sys.path.insert(0, 'source')
from simulator import Simulator
# ... build a temp memory.cfg with pru_clock_mhz=250, load
# source/pif_eth/pif_eth_tx_n2_skipchecks.asm on pru0, drive via the same
# pattern as source/pif_eth/driver.py's run() function.
"
```

## Notes for future work

- The n=2 underrun is a genuine 2-cycle race (FIFO-full check passes, then
  the drain's next bit-edge lands in the gap before the push instruction
  executes) — confirmed by single-stepping the simulator, not just hand
  cycle-counting.
- If 125 Mbaud needs to be reachable *robustly* (not hard-pinned to one
  divider), the real lever is cutting the bit-packing loop's cycle cost
  further (or a hardware-level multi-byte FIFO push, which does **not**
  currently exist — confirmed neither the simulator nor the documented
  `references/endat/ENDAT_INTERFACE_SPEC.md` support a 2-byte `r30.w0`
  write; it would silently drop the second byte).
- pif_eth's other future-work items from the 2026-07-20 handoff (broadside
  CRC accelerator, striping across all 3 perif channels) are unchanged.
