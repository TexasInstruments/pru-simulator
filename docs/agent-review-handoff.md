# Simulator dashboard continuation handoff

Updated 14 October 2026. The two unpublished PR scopes have been reordered.

## Branch order

1. `feat/pr5-simulator-ui`, based on the pushed SSI head `ad0c720`.
   This branch has Simulator and I/O & Devices tabs, both SSI demos, device
   controls, themes, panels, GPIO directions, IEP controls, supported multicore
   layouts, fault displays and the Run acknowledgement fix. It contains no
   motor firmware, model, control ABI, Motor control tab or motor scripts.
2. `feat/pr6-motor-control` builds on dashboard commit `6bc9473` and adds the
   motor firmware, model, SD current routing and complete Motor control UI.

The original `feat/pr5-foc-open-loop` (`15e2be4`) and
`feat/pr6-simulator-ui` (`393d512`) are preserved as recovery references.
The source commits are local and remote references, not newly opened PRs.
PRs #49–#52 remain separate and have not been rewritten by this extraction.

## Verification

Fresh motor-branch checks:

- Python suite: 2,180 passed, 2 xfailed, 2 failed (166.62 s).
- Both failures are the same MCP SDK registration dependency described below.
- All nine Node frontend test files passed, including the motor UI tests.
- Both generated ABIs and whitespace checks passed. All restored motor source
  and existing test files match the original combined branch exactly.
- The final combined tree differs intentionally in scope tests, documentation,
  the shipped core configuration (250 MHz), and retaining the reviewed generic
  bus resolver instead of the unrelated settlement optimization.
- No browser was available; actual browser rendering remains unverified.

Fresh first-branch checks:

- Python suite: 1,977 passed, 2 xfailed, 2 failed (73.74 s).
- Both failures are in `tests/test_mcp_stdio_device_sdk.py`: the installed MCP
  SDK has no `Server.list_tools` decorator. The SSI base independently had
  1,780 passed, 2 xfailed and the same registration failure before extraction.
  The new dashboard adds a second SDK transport test; neither is suppressed.
- All seven Node frontend test files passed. This includes Run flow, GPIO/IEP
  controls, panel recovery, SSI demo controls and a saved unavailable motor
  view falling back to Simulator.
- SSI ABI generation check, JavaScript syntax and whitespace checks passed.
- The dashboard imports successfully with motor modules completely absent.
- No browser was available. Rendered layout and live browser clicks remain
  unverified; Python WebSocket tests exercise SSI results and position updates.

MCP SDK integration belongs with PR #47 and the owning base PRs. Do not mark
the complete suite green until the actual registration fix is integrated.
Do not hide that dependency inside the dashboard redesign.

## Run and test

From this branch's checkout, using a Python environment with `requirements.txt`:

```sh
python ui/server.py --host 127.0.0.1 --port 8082
```

Open `http://127.0.0.1:8082`. In I/O & Devices → Devices → SSI encoder, load
reader + model or encoder + reader, then Run. Received position starts at
2748. Set the next position to 1234 and verify the received value changes on
a later frame. Show waveforms opens the Signal Graph. Stop pauses execution.
The first branch has no Motor control tab. This motor branch adds that tab:
load the firmware or example, set the references, then Start. Speed is an
open-loop command; rotor speed is measured from the motor model. The tab also
shows currents, duties, voltage, angles and clocks, and permits live changes
to motor physics. Stop pauses execution. See `simulator_dashboard.md` for
the complete controls and clock-dependent PWM cadence.

Clock defaults are unchanged: project-selected 250 MHz core, independently
configured 200 MHz external IEP when omitted. The IEP selector accepts positive
fractional rates. Selecting 250 MHz for a demo is a session choice. Committed
defaults do not include the old combined branch's 300 MHz session setting.

```sh
python -m pytest tests -q
node --test tests/ui_*.test.js
python -m tools.gen_ssi_abi --check
git diff --check
```

The local virtual environment is
`/home/j-martinez8/j-martinez8/Encoder-PRU/.venv/bin/python`.
Preserve user-edited configuration and assembly files. Existing PR6 live
configuration was not copied into or changed by this reconstruction.

## Remaining review work

The latest review submissions for PRs #49–#52 remain dated 9 October.
SDK registration/schema cleanup, test-count reconciliation, recurring
contention bounds, Boolean drive-mask validation, copied-history cleanup and
independent SSI/preset verification still belong in their owning PRs.
The SSI frequency-rounding and emulator shared-IEP lifecycle notes are already
in the pushed PR4 docs. RTU1 dashboard selection is delivered by this branch.
The generic bus settlement optimization from the old combined branch was
excluded: this dashboard keeps the reviewed base resolver and adds only the
device lookup/owner/state fields required by its controls.
