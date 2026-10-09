# Simulator continuation handoff — 9 October 2026

Work on these branches in TexasInstruments/pru-simulator:

- FOC: `feat/pr5-foc-open-loop`, implementation head `15e2be4`.
- Dashboard: `feat/pr6-simulator-ui`, implementation head `8d1687e`.

The dashboard branch includes the FOC implementation. This handoff can be
committed after the implementation head above. Use the latest remote branch
head when continuing. Do not rewrite the stack history without authorization.

## Current behavior

FOC runs ordinary PRU assembly against a pin-coupled motor model. Engineering
unit conversions use the active IEP cadence and the firmware's 12500-tick
period. Runtime models use the standard library. Motor reset clears sample
history and invalid numeric configuration is rejected before changing leases.

Run schedules at most one outstanding instruction chunk. Only its matching
final state releases the next chunk, including all states in multicore mode.
Stop prevents further chunks; an already executing chunk can finish.

The normal dashboard provides two SSI examples in I/O & Devices → Devices:

- **Load reader + model:** PRU0 reads an attached 12-bit binary encoder model.
- **Load encoder + reader:** PRU0 reads PRU1 encoder firmware over cross-core
  clock/data wires. Only those two cores execute; no host encoder is attached.

Both prepare position 2748 (0xABC). Received readouts come from the real reader
mailbox. Next-position editing accepts 0–4095 and applies to a later frame.
Odd-sequence mailbox contents are hidden. Firmware status is separate from the
encoder error field, which is absent in these examples. Repeated loads close
the old runtime and release its ownership. Source-root include resolution is
available for firmware loaded from a subdirectory.

Examples preserve the selected external IEP rate and session override. The FOC
example retains its 400 rpm target and rapid 300000 rpm/s demonstration ramp.
References use the actual cadence. Normal core switching updates the C-table
and register display. The dashboard accepts custom finite positive IEP rates,
including fractional values.

Invalid saved panel trees recover the current mode's default layout. Valid
arrangements and separate hidden-panel preferences remain intact. Multicore
execution controls and extra-core buttons wrap instead of overlapping adjacent
toolbar groups.

## Verification and limits

- Full Python checkpoint at `d5d65f0`: 2179 passed, 2 failed, 2 xfailed,
  1 warning, 92.56 seconds. Both actual failures are in
  `tests/test_mcp_stdio_device_sdk.py`: MCP 2 Server lacks the legacy
  `list_tools` registration decorator. They were not skipped or suppressed.
- The later `8d1687e` changes are frontend only. All 9 Node UI test files pass,
  including 12 panel-recovery cases. All 103 Python UI style checks pass.
  Syntax, generated SSI ABI and diff checks passed during implementation.
- Focused independent reviews found no important remaining findings after the
  core-switch regression was fixed.
- No browser was enabled for rendered sizing or keyboard verification. The
  user's actual saved panel data was unavailable. Invalid-layout failures were
  reproduced in tests; the exact cause of the reported screenshot is unconfirmed.

The first machine's existing uncommitted `memory.cfg` edit was preserved and
excluded from commits. Set clock rates explicitly on the new machine when
reproducing the examples. `source/program.asm` was not changed.

## Remaining work

1. Resolve MCP 2 stdio registration and repeat actual SDK transport tests.
2. Correct the parent IEP default policy. The implementation still defaults an
   omitted IEP rate to 200 MHz. The agreed policy is a project-selected 250 MHz
   starting convention, omitted IEP rate inheriting the selected core rate,
   and independent finite positive overrides. This is not a universal hardware
   reset frequency. Keep hardware register reset values separate.
3. Verify rendered desktop/narrow layouts and keyboard use in a real browser.
   Check the reported panel symptom after hard reload and Reset Layout.
4. Preserve the earlier review stack and its ownership boundaries. Historical
   duplicate-commit cleanup and merge readiness remain separate work.

## Start and test on another machine

From a checkout of `feat/pr6-simulator-ui`, use the Python environment installed
from the repository's maintained requirements:

```sh
python ui/server.py --host 127.0.0.1 --port 8082
```

Open http://127.0.0.1:8082. Choose the desired core and IEP clock rates before
loading an example. Press Run after loading. Check received position 2748,
then set 1234 and check a subsequent received frame. Inspect Show waveforms,
Stop/restart, repeated example loading and firmware statuses.

After changing IEP rate, reload the encoder firmware example to recalculate
its timeout. Hardware reset disables that timer. A frontend-only panel update
needs a hard page reload; updates to the Python backend require a server restart.

See [simulator_dashboard.md](simulator_dashboard.md) for the full UI walkthrough.

```sh
python -m pytest tests/test_scenarios.py tests/test_scenarios_ws.py -q
python -m pytest tests/test_ui_design_tokens.py -q
python -m tools.gen_ssi_abi --check
python -m pytest tests -q
git diff --check
```

Run each `tests/ui_*.test.js` file directly with Node. The original restricted
execution sandbox could hang TestClient sessions; local tests required normal
local socket access. Preserve configuration and scratch-file bytes when testing.
