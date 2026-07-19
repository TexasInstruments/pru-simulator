# Time-Paced Multicore Run — Design

**Date:** 2026-07-19
**Status:** Approved

## Problem

The UI's multicore Run drives the two cores by instruction counts:
`startRun()` sends two sequential `run` actions (1000 instructions of
PRU0, then 1000 of the partner). But instruction counts are not cycle
counts — PRU1's perif RX capture firmware stalls on `sbbo` stores and
accumulates ~32 extra cycles per 1000 instructions, so its peripheral
clock runs ~3% (≈32,000 ppm) ahead of PRU0's. The perif loopback is
timestamp-based, so this pacing error acts as a huge artificial clock
drift and corrupts the drift-demo capture within the first bytes
(observed: `00 01 81 01 82 41 81 c2 02 …` instead of `00 01 02 …`).

`tools/perif_drift_report.py` avoids this by pacing PRU1 against
PRU0's peripheral time with a guard band (`run_drift`, guard 20 ns).
The UI has no equivalent.

## Goal

Make the multicore **Run** button keep both cores' peripheral clocks
aligned so the in-UI drift demo produces the clean counter pattern
(and configured `pru1_clock_mhz` drift shows at its true magnitude).

**Per user decision:** only Run is paced. The multicore **Step**
button keeps today's strict 1-instruction-each interleave.

## Design

### Simulator: `step_paced`

New method on `Simulator` (`simulator.py`):

```
step_paced(lead: str, follow: str, count: int = 1,
           guard_ns: float = 20.0) -> None
```

- Steps `lead` one instruction at a time, `count` times. After each
  lead instruction, steps `follow` while
  `follow_perif._now_ns < lead_perif._now_ns - guard_ns`.
  `follow` therefore always *trails* lead's peripheral time, so an RX
  on `follow` only samples line history the TX on `lead` has already
  recorded (same invariant as `run_drift`).
- Inner-loop termination: stop stepping `follow` if it is halted or
  its `pc` runs past its program, and cap follow-steps at 1000 per
  lead instruction as a safety valve.
- Fallback: if either core has no perif block (`sim._perif`), pace
  1:1 by instructions instead — partner = RTU0 keeps exactly the
  current behavior (existing XFR demos unaffected).
- Skips stepping `lead` when it is halted or past its program (no-op,
  mirroring `PRUCore` run semantics).

### Server: `run_multicore` action

New websocket action in `ui/server.py`:

```
{action: "run_multicore", core: "pru0", partner: "pru1"|"rtu0",
 max_steps: 1000}
```

- Loops up to `max_steps` times calling `sim.step_paced(core,
  partner, 1)`; after each iteration checks both cores: stop when
  either hits a breakpoint (`pc in breakpoints`) or when the lead
  halts/finishes (same stop conditions as the existing `run`).
- On completion sends `_send_state` for **both** cores (two state
  messages, as the frontend already handles per-core state pushes).
- `ValueError` handling identical to `run` (error message to client).

### Frontend (`ui/static/app.js`)

In `startRun()`'s interval callback, replace the multicore branch's
two `run` sends with one:

```
sendAction({ action: "run_multicore", core: "pru0",
             partner: mcPartner, max_steps });
```

Nothing else changes: Step button, single-core Run, snapshots,
memory auto-refresh all stay as they are.

## Out of scope

- Pacing the multicore Step button (user decision: keep 1:1).
- Refactoring `run_drift` to use `step_paced` (possible follow-up).
- Cycle-accurate lockstep of the XFR bus.

## Testing

- Unit (`tests/test_perif_drift_experiment.py` or new file):
  - Full demo setup + `step_paced` loop → first 32 captured bytes are
    `00 01 02 …` (regression for the reported corruption).
  - Pacing invariant: after each chunk, PRU1's perif `_now_ns` trails
    PRU0's by at least `guard_ns` minus one step and never leads.
  - RTU0 fallback: `step_paced("pru0", "rtu0", n)` advances both
    cores exactly `n` instructions.
- Server (`tests/test_perif_server.py`):
  - Websocket test: set up the demo via UI actions (`gpcfg_write`,
    `write_perif_register`, `perif_loopback`, `load`), loop
    `run_multicore`, read DRAM1 `0x2000` → clean pattern; both state
    messages arrive.
  - Breakpoint test: breakpoint on the lead core stops the run and
    reports `at_breakpoint`.
