## Latest review fixes published — 20 October 2026

The latest local Qodo fixes are now committed and pushed:
PR50 `2e1a4df`, PR51 `4892dfc`, PR52 `ab10aab`. PR49 stays at `9c1845f`.
Parent ancestry is preserved with real merges; the merge trees match the
freshly tested trees exactly. Full suites: 1718 / 1829 / 2075 passed,
each with 2 existing xfails and one existing warning. Five Node checks per
branch and the generated SSI ABI check pass. Independent SSI decoding stays.
Remote refs were verified. No force push, PR merge, review reply, bot command,
approval or remote description edit occurred. Your assembly/config bytes
are unchanged. UI/motor implementations remain local and uncommitted.

GitHub reports clean merge state for 50–52, but 49 is conflict-free and
`blocked`. Its recorded reviews are COMMENTED, with no APPROVED review.
The connector cannot read main's branch-protection rules (403), so the exact
blocking rule is not confirmed. A maintainer must complete the merge gate.
Merge order remains 49 → 50 → 51 → 52; do not treat a clean Git diff as approval.

Fresh logs and publication baseline:
`.worktrees/pru-simulator/validation/qodo-retry-publish-2026-10-20/`.
The dated local-only sections below describe earlier checkpoints.

## Latest Qodo follow-up — local fixes, not committed or pushed

The new paced-retry, standalone timed-device and SSI mailbox findings are fixed
in the owning branches and dependent UI working files. Pending pacing targets
survive retry, mux changes, follower-selection changes and undo; reset clears
them. A retry does not advance the lead while the selected followers remain
behind. Time-driven models tick only on elapsed cycles. Successful SSI reader
loads clear the old mailbox, while failed reloads preserve config and mailbox.
Independent host decoding remains, per Pratheesh's validation requirement.

Final full suites: PR50 1718, PR51 1829, PR52 2075, UI 2311 passed, each
with 2 existing xfails and the existing AnyIO warning. Five legacy Node checks
per PR and 50 UI Node checks pass; both SSI ABI checks pass.
Independent read-only review accepted the final changes. Full result details
and recovery evidence: `pru-simulator-prs/qodo-retry-fixes-2026-10-20.md` in the
root workspace. No commit, push, Git merge, bot command, review reply or remote
PR-body edit occurred. The published heads listed below have not moved.
PR49 and motor-control files are unchanged; user assembly/config bytes remain.

## Published review fixes — 20 October 2026

Supersedes the older local-only notes below. The four reviewed branches were
committed and pushed, with real parent merges and verified remote heads:
PR49 `9c1845f` (0.2.8), PR50 `18822fc` (0.2.9), PR51 `e102936` (0.2.10),
PR52 `35c0d60` (1.0.0). Full suites: 1616 / 1711 / 1820 / 2064 passed,
each with two existing xfails. Each parent-merge tree matches its tested tree.
No PR merge, review reply, bot command or remote description edit was made.
User assembly/config bytes are unchanged. PR49/50 keep the user's assembly edit.

The UI working files include the applicable fixes and 2.0.0 README/header
version, but remain uncommitted. Fresh checks: 2299 passed, 2 xfailed, 50 Node
checks and SSI ABI check. UI HEAD remains `1f526df`; before its eventual commit,
update ancestry to the new PR52 head and any then-merged main changes. Its
constant-table display still needs to follow actual per-core addresses.
The motor branch remains at `9f4bbd1`, with only local README/header 3.0.0
preparation; integration and full review are pending. Its notes explicitly
avoid claiming new UI/SSI fixes absent from that stale checkout.

The full nine-PR audit, merge order, Pratheesh's open findings and UI follow-up
checks are recorded in `pru-simulator-prs/merge-order-2026-10-20.md` in the root
workspace. Recovery hashes and fresh logs:
`.worktrees/pru-simulator/validation/publish-2026-10-20/`.
Important follow-ups: AM261x target compatibility (67/71), MAC latch history,
XFR state/VBUS undo and task-ring progress after HALT. The unmerged XFR PRs
do not need to block publishing the UI against otherwise-ready main.

## Local Qodo fixes — 20 October 2026 (not committed or pushed)

Supersedes the older readiness note: the new Qodo run exposed further cases,
and these fixes now exist in working files only. No branch head moved and no
Git merge, commit, push, bot command, PR-body edit or review reply was made.
All four owning PR worktrees and the unpublished UI worktree contain the
applicable fixes; motor control is untouched.

- PR49: validate both WBS/WBC bit operand forms (immediate 0–31; no labels),
  keeping register selectors and resolved constants. 36 new parser cases.
- PR50: return actual `lead_steps` from complete/incomplete/failed pacing;
  count actual RX-driver work, retry catch-up, and stop on non-progress/terminal
  TX. Map Peripheral Interface cycle time through the IEP's shared core origin,
  including direct/core resets. Reset IEP before cores on hardware reset.
  Paired loopback tests distinguish the new byte from old transmit history.
  Restore actual GPIO loopback display on target changes, clear removed-core
  source caches and reverse legacy keyboard undo order. PR51+ keeps its existing
  coherent all-core undo; UI retains the existing CORES deck and synchronization.
- PR51: restore the two historical v0.2.8 changelog bullets. New coherent undo
  remains in v0.2.9; current API docs explain pacing counts and reset origins.
- PR52: roll back reader configuration after failed reload while keeping its
  previous firmware/core/lease state. Keep initial-load cleanup and independent
  host decoding; the latter is a deliberate review requirement, not a defect.

Full suites: PR49 **1616 passed**, PR50 **1711 passed**, PR51 **1820 passed**,
PR52 **2064 passed**, UI **2299 passed**. Each has two existing xfails and the
existing AnyIO warning. Legacy selector/keyboard checks: 5 per branch; UI: 50
Node checks. SSI ABI, JS syntax and scoped whitespace checks pass. Independent
read-only review found no material issues and passed additional reset and
multi-follower completion checks.

Recovery snapshots, before/after patches, hashes and logs:
`.worktrees/pru-simulator/validation/qodo-followup-2026-10-20/`.
Plan: `docs/superpowers/plans/2026-10-20-qodo-followup.md`.
Every branch's memory.cfg/source/program.asm bytes and HEAD match the saved
pre-task state. The user's pre-existing assembly EOF whitespace remains.
When authorized later, commit owning fixes and update dependent ancestry;
do not publish only the UI or accidentally include user assembly/session config.

# Simulator dashboard continuation handoff

Updated 20 October 2026. The two unpublished PR scopes have been reordered.
The CORES deck is implemented and verified locally on `feat/pr5-simulator-ui`.
Changes are uncommitted and unpushed; older verification sections retain their dates.

## Reviewed stack integration — 20 October 2026

The UI branch `feat/pr5-simulator-ui` now includes reviewed PR49 `80c7981`,
PR50 `82670af`, PR51 `c4596c1`, PR52 `f5fc40b`, and current main `1c2087d`.
Local merge commits: `2c4fb8e` (reviewed stack), `1f526df` (main configuration).
All parent heads are ancestors of the UI branch. Nothing was pushed or
published; the four PR branches and motor-control branch were not changed.

The existing CORES-deck edits were backed up, stashed, restored and reconciled.
The deck and final integration adaptations remain unstaged, as did the UI work
before this task. Preserve them when preparing the eventual UI commit. The
recovery stash is retained. `memory.cfg` and `source/program.asm` retain their
exact pre-task bytes. Recovery files and test logs are under the workspace's
`.worktrees/pru-simulator/validation/ui-stack-integration-2026-10-20/`.

Resolved five stack-merge conflicts and three overlapping stash conflicts:
- Device state keeps per-device core metadata and the reviewed event bound.
- SSI's UI reader-loading helper retains reviewed reset/failure cleanup;
  emulator lifecycle, status and closing-edge corrections are inherited.
- Multi-core Run retains partner-list capture and final `run_id` acknowledgement,
  and now reports bounded catch-up failure and supports retry after lead finish.
- Core discovery refreshes the CORES deck rather than removed dropdowns;
  config reload refreshes all selected execution cores.
- CRC state from main appears in the focused readout and per-core register panes.
- Core-clock fallback and UI documentation use 200 MHz. Tests needing 250 MHz
  now choose it in temporary configs instead of reading the user's saved rate.
  Clock pairings remain simulator pairings, without hardware-domain claims.
- Design checks follow the existing shared red/neutral button wipe and exclude
  geometry from colour-token checks; partial JS template names are not tokens.

Final verification: **2248 passed, 2 xfailed**, one existing AnyIO deprecation
warning; **50 Node checks passed**; generated SSI ABI, six JS syntax checks and
whitespace checks pass. Independent read-only review found no material issues.
Root graphify update completed. No browser surface was connected, so no new
rendered/browser verification was performed. Older verification sections below
are historical and do not describe the integrated branch's current failures.

Publication follows the merge of PR49–52: commit the pending UI work, refresh
its PR description, compare against then-current main, and publish the UI PR.
Keep `feat/pr6-motor-control` as the later, separate motor-control change.

## Branch order

1. `feat/pr5-simulator-ui`, integrated with pushed SSI `f5fc40b` and main `1c2087d`.
   This branch has Simulator and I/O & Devices tabs, both SSI demos, device
   controls, themes, panels, GPIO directions, IEP controls, the CORES deck and
   shared multicore panes, fault displays and the Run acknowledgement fix. It contains no
   motor firmware, model, control ABI, Motor control tab or motor scripts.
2. `feat/pr6-motor-control` will build on the dashboard branch and add the
   motor firmware, model, SD current routing and complete Motor control UI.

The original `feat/pr5-foc-open-loop` (`15e2be4`) and
`feat/pr6-simulator-ui` (`393d512`) are preserved as recovery references.
The source commits are local and remote references, not newly opened PRs.
PRs #49–#52 remain separate and have not been rewritten by this extraction.

## CORES deck — 15 October working-tree integration

The new Target **CORES** overlay replaces the Execute bar's MULTI-CORE,
partner and +core controls. `ui/static/cores.js` owns selection invariants;
`cores-deck.js` builds the overlay once and patches its controls; `cores.css`
supplies card, pane, status and core-colour styling. `app.js` connects execution,
focus, panes and clock reloads; `layout.js` retains the same eight panel types.
`index.html` holds one Source and one Registers panel with inner pane hosts.
The existing local source-cache, UART and layout issue fixes are retained.

- **Use** controls Run/Step/SIM membership. **Show** controls panes inside
  Source and Registers. Shown cores are always used, with at least one of each.
  Enabling Use leaves a core hidden; disabling it hides the core and repairs focus. Use all preserves the shown set; Show all used reveals it.
  Hidden used cores still execute and can stop Run at their breakpoints.
- One shown core has no pane heading. Source uses two columns for two cores,
  two above one for three, and 2×2 for four; Registers uses one column per shown
  core. Pane **×** buttons hide that core in both panels without removing it
  from execution. Core selection never adds tiles or swaps the tile tree.
- Telemetry lists used cores. Its focus selects the Assembly Editor load
  target, shared breakpoint row, I/O Pins, SPAD and constant-table inspector.
  Focusing a hidden used core shows it. Hiding focus selects the first shown
  core. Source-line double-clicks target that pane's core; Space targets the
  focused core's current PC. I/O & Devices keeps its own available-core target.
- Hover or keyboard focus opens the deck. Pill/pin clicks pin it; Escape or
  an outside click closes it. Cards and clock controls remain in place during
  state updates. Reduced motion disables the card fan and pulse/shake effects.
  The first-use hint has a persistent dismissal separate from layout reset.
- Browser preferences persist used/shown/focus/pin state. Restoration prunes
  unavailable target cores and repairs empty selections. An explicit legacy
  selection migrates; an abandoned multicore layout alone does not enable
  cores. Reset Layout restores PRU0 used/shown and unpinned, plus visible,
  expanded default panels, without writing clock configuration or recreating
  the simulator.

Clock membership comes from `simulator.py`: Group 0 is PRU0 + RTU0 using
`pru_clock_mhz`; Group 1 is PRU1 + RTU1 using `pru1_clock_mhz`. AM263x omits
RTU1. `ui/server.py:ALLOWED_CLOCK_MHZ` supplies 200, 225, 250, 300 and 333 MHz
dashboard presets, not hardware limits. Configured finite positive fractional
rates remain visible unchanged, and arrows choose the next preset on either
side. Omitted Group 0 uses the 200 MHz fallback; omitted Group 1
inherits it. External IEP remains separately configured, with a 200 MHz
omitted-value fallback and its existing configured/session/active semantics.

`GET /config/clock_speed` retains legacy `mhz` and adds `groups` and
`choices_mhz`; state replies include the same object as `clocks`.
`PUT /config/clock_speed` accepts optional numeric `group` 0/1. A selected edit
preserves the other effective group rate, materializing an inherited Group 1
rate only when needed. Omitting group retains the legacy update-both behavior.
IEP configuration and the external session override are preserved.

The deck warns before editing that a clock change recreates the simulator:
programs must be reloaded, registers/devices/history are reset, and firmware's
IEP source selection returns to reset state. The UI disables edits during
Run/SIM, a pending execution chunk, a clock change, or when the group has no
used member. This uses the existing per-command backend execution model;
there is no new server-wide run-session lock. Successful edits refresh source,
registers, devices, memory and clocks.

Final verification for this integration (15 October):

- All frontend test files: **46 checks passed** using `node --test --test-isolation=none tests/ui_*.test.js`.
- Affected backend clock/server/scope/IEP/scenario/SSI suites: **261 passed** using the workspace Python environment. Existing Starlette/AnyIO deprecation and Node module-type warnings remain.
- Firefox against a separate server on 8094 with a complete temporary config directory: 1/2/4 panes, unchanged eight-panel registry, hidden used cores still running, execution without PRU0, focused register edits and breakpoints, isolated group edits/reload, reset, persistence, native keyboard activation, both SSI demos publishing frames, mobile overlay and high-contrast theme, stable deck DOM. Separate normal-motion Firefox pass verified hover/fan, first-use hint/dismissal, and per-core loopback masks surviving focus changes. No browser console errors in these checks.
- Review findings fixed and regression-tested: disconnected Step cannot leave an unacknowledged pending token; device targeting stays independent of used-core count; GPIO loopback controls follow the focused core without detaching another core's loopback. The final scoped re-review approved all three fixes.
- The two affected tests that assumed the editable config contained literal 250 MHz values now use the selected rate / an explicit copied 300 MHz fixture. The user's `memory.cfg` is unchanged (SHA256 `0dcb95ceb7e79adc1314270b98f0445fe26baaaefac20ec9b5d556ac9db51a50`). Other rational-timing tests and the historical full-suite SDK failures were not part of this final subset; no full-suite-green claim is made.
- JavaScript syntax and `git diff --check` pass. Root `graphify update .` completed; its 64 existing hardware-source parse warnings are unrelated.

Screenshots live at the workspace root under `artifacts/ui/previews/`:
`cores-before.png`, `cores-after-1.png`, `cores-after-2.png`,
`cores-after-4-deck.png`, `cores-motion-desktop.png`, and
`cores-mobile-contrast.png`.

Toolbar follow-up: a round icon toggles Dark/High contrast and persists the choice; the IEP selector and SIM seconds field use matching pills. The TARGET label and visible clock summary are removed. IEP details remain in its tooltip. This branch has no Light theme. The focused inspector retains the existing SPAD and constant-table
controls. Target availability comes from the server. Clock controls demonstrate
shared group rates and retain the existing simulator-reload semantics.

The motor-control branch has not been rebased onto these new local changes.

## Verification — 14 October snapshot

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
There is no Motor control tab on this branch.

Omitted clock rates use a 200 MHz core and an independently
configured 200 MHz external IEP. The IEP selector accepts positive
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

## UI issue fixes — 14 October follow-up

Local working-tree fixes on `feat/pr5-simulator-ui` address open issues #53,
#55, #56 and #61. They are not pushed or posted to GitHub. The preserved old
`feat/pr6-simulator-ui` and the separate motor branch have not been modified.

- Source cache invalidation now matches list clearing on assembly loads and
  core changes. Repeated unchanged assembly remains visible.
- UART Record uses the existing Signal Graph buffer; both controls synchronize
  on user changes and automatic single-shot capture completion.
- UART Reset inputs restores defaults and clears feedback locally. It does not
  cancel a previously armed generator or reset firmware.
- Reset Layout expands collapsed panels before rebuilding the default layout,
  restores hidden panels and corrects the collapse buttons.

Fresh verification: all 8 frontend test files pass (30 Node runner checks),
and 48 affected Python server/scope tests pass. JavaScript syntax and whitespace
checks pass. Python reports the existing Starlette/AnyIO deprecation warning;
Node reports the existing package module-type warning. No live browser is
available. The earlier full-suite MCP SDK failures remain outside this change.
See `docs/simulator_dashboard.md` for manual reproduction and test steps.

## Compact toolbar follow-up — 15 October

The icon theme control, IEP/SIM pills and removed summary/Target label were verified in Firefox at 1920 and 390 px. Theme persistence, IEP preset/custom/session changes and SIM operation pass; all 46 frontend checks and JavaScript syntax/whitespace checks pass. No backend or configuration changes. Screenshots: workspace `artifacts/ui/previews/compact-toolbar-desktop.png` and `compact-toolbar-mobile.png`.

## Full-width toolbar and Use defaults — 15 October

`#toolbar-main` and `#toolbar-secondary` distribute their widths independently. Execute has content width; the context group receives spare space so CORES and IEP remain adjacent on wide screens. Telemetry begins with used-core buttons instead of a heading and duplicate core name. New Use selections and Use all preserve the shown set; Show/focus/name/Show all remain explicit ways to inspect cores. Existing saved shown selections and SSI scenario defaults are preserved.

Verified this follow-up: 46 frontend checks pass. Firefox geometry checks at 1760, 1920 and 2560 px keep CORES/IEP, execution groups, panel toggles and telemetry on one line with all four cores shown, including ten-digit cycle/stall/instruction counters. Use and Use all leave new cores hidden; Show all explicitly creates the panes. The 390 px view has no page overflow. Screenshot: workspace `artifacts/ui/previews/four-core-toolbar-single-row.png`. No backend changes or configuration writes.

## Readable core names and telemetry — 15 October

The CORES pill displays PRU0/PRU1/RTU0/RTU1 for shown cores at widths from 1840 px and compact P0/P1/R0/R1 below that. Telemetry is 12 px with 11 px core buttons, taking extra width from the panel bar. A container query hides decorative panel glyphs when needed while retaining panel names. Firefox checks passed at 1760/1920/2560 px with four cores and ten-digit counters; mobile has no page overflow. All 46 frontend checks pass. Screenshot: workspace `artifacts/ui/previews/readable-core-labels-desktop.png`.
