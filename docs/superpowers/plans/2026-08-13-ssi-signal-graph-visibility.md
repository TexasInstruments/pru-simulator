# SSI Signal Graph Visibility Implementation Plan

**Goal:** Preserve sub-pixel SSI activity in the full-capture Signal Graph, add manual frame fitting and resolution feedback, and verify the encoder flow end to end.

**Architecture:** Add pure JavaScript analysis helpers beside the existing graph code, then make the canvas renderer and toolbar consume them without changing capture samples or backend wire formats. Keep UI edits serial because they share `app.js`; run backend, MCP, and documentation tasks only when their write sets are disjoint.

**Tech Stack:** Vanilla JavaScript, Canvas 2D, HTML/CSS, Python 3.12, pytest, FastAPI TestClient, PRU Simulator MCP wrapper.

## Global Constraints

- Default view remains the complete buffered capture.
- `CAPTURE_STRIDE_GP = 10`, capture payloads, shared `runStep`, CSV output, and firmware remain unchanged.
- Use red-green TDD: demonstrate each new test fails for the expected reason before production edits.
- No new dependency.
- Touch only the files listed by each task.
- Browser UI validation is required before completion.

---

### Task 1: Transition-Preserving Pixel Buckets

**Files:**
- Create: `pru-simulator/tests/js/test_signal_graph_helpers.js`
- Modify: `pru-simulator/ui/static/app.js`

**Interfaces:**
- Produces: `graphBuildDigitalBuckets(samples, data, visMin, visMax, width)` returning ordered `{x, enter, exit, sawHigh, sawLow}` buckets.
- Preserves: input arrays and sample objects.

- [ ] Write Node assertions for a two-edge pulse inside one pixel, adjacent sub-pixel pulses, a zoomed ordinary waveform, fallback from `runStep` to `step`, and input immutability.
- [ ] Run `node tests/js/test_signal_graph_helpers.js`; verify RED because the helper is absent.
- [ ] Implement only the pure bucket helper and use it in `drawDigitalGraph()` to draw a full-height vertical transition for buckets containing both levels while connecting bucket entry/exit levels.
- [ ] Re-run the Node test; verify GREEN.
- [ ] Run `python -m pytest tests/test_server.py tests/test_perif_server.py -q`; verify no capture regression.

### Task 2: Manual SSI `Fit frame`

**Files:**
- Modify: `pru-simulator/tests/js/test_signal_graph_helpers.js`
- Modify: `pru-simulator/ui/static/app.js`
- Modify: `pru-simulator/ui/static/index.html`

**Interfaces:**
- Produces: `graphFindNewestSsiFrame(samples, preferredCore="pru1", clockPin=0)` returning `{minStep,maxStep}` or `null`.
- Produces: toolbar button `#graph-fit-frame-btn`.

- [ ] Add assertions for newest complete 24-transition group, idle-high separation, incomplete rejection, PRU1 preference, current-core fallback, bounds clamping, and no mutation.
- [ ] Run the Node test; verify RED because frame detection is absent.
- [ ] Implement the pure detector exactly as specified: median half-period, threshold `max(20, median*5)`, newest complete group, padded and clamped range.
- [ ] Wire `Fit frame` to set `signalGraph.view` only on success; on failure leave it unchanged and display `No complete SSI frame in capture`.
- [ ] Re-run the Node test; verify GREEN.
- [ ] Run a static HTML/JS assertion confirming the button ID and click binding exist.

### Task 3: Cycles-Per-Pixel and Sub-Pixel Feedback

**Files:**
- Modify: `pru-simulator/tests/js/test_signal_graph_helpers.js`
- Modify: `pru-simulator/ui/static/app.js`

**Interfaces:**
- Produces: `graphResolutionInfo(channels, visMin, visMax, width)` returning `{cyclesPerPixel, subPixel}`.
- Extends: `#graph-step-label` text without changing its element ID.

- [ ] Add assertions for cycles-per-pixel arithmetic, shortest non-zero run detection, warning clearance after zoom, empty channels, and zero-width/range handling.
- [ ] Run the Node test; verify RED.
- [ ] Implement the pure helper and extend `_graphUpdateStepLabel` to show visible range, formatted cycles/pixel, and `sub-pixel activity` when applicable.
- [ ] Re-run the Node test; verify GREEN.
- [ ] Run `node --check ui/static/app.js`.

### Task 4: Reciprocal Encoder GPIO Wire Regression

**Files:**
- Modify: `pru-simulator/tests/test_server.py`

**Interfaces:**
- Verifies: PRU1 GPO0 -> PRU0 GPI8, complementing the existing PRU0 GPO0 -> PRU1 GPI16 test.

- [ ] Add a focused test that adds the reciprocal wire and asserts HIGH and LOW propagation.
- [ ] Temporarily target an intentionally wrong destination pin and run the test to prove RED; restore GPI8 before production completion.
- [ ] Run the corrected test; verify GREEN.
- [ ] Run all GPIO-wire and multicore-capture tests in `tests/test_server.py`.

### Task 5: Project Documentation

**Files:**
- Modify: `pru-simulator/getting_started.md`
- Modify: `open-pru/ENCODER/simulator_testing.md`
- Modify: `pru-simulator/tests/test_server.py` only if its stale stride-100 comment remains after Task 4.

**Interfaces:**
- Documents: stride 10, full-capture behavior, transition-preserving wide view, cycles/pixel warning, manual `Fit frame`, and existing zoom/reset controls.

- [ ] Locate every directly affected stride/control statement with `rg`.
- [ ] Update only stale or missing behavior named above.
- [ ] Run `rg -n "CAPTURE_STRIDE_GP=100|decimated 100:1|Fit frame|sub-pixel"` and verify no directly affected stale claim remains.
- [ ] Run `git diff --check`.

### Task 6: MCP SSI Injection Validation

**Files:** None.

**Interfaces:**
- Consumes: `PRUSimulatorMCP.pru_ssi_inject` and `source/ssi_reader_4mhz_12bit/ssi_reader_4mhz_12bit.asm`.

- [ ] Run a Python validation for values `0x000`, `0xA5A`, and `0xFFF` using `memory.cfg`.
- [ ] Require `status == "success"`, `match is True`, and `frames_captured >= 1` for every value.
- [ ] Save the exact command and output in the task report; make no file changes.

### Task 7: Dual-Core Reader/Emulator Validation

**Files:** None unless a failing product test requires a separately approved fix task.

**Interfaces:**
- Loads: simulator-compatible reader and emulator sources.
- Wires: PRU1 GPO0 -> PRU0 GPI16 and PRU0 GPO0 -> PRU1 GPI8.

- [ ] Identify simulator-loadable firmware without changing it.
- [ ] Configure position `0xA5A`, pace both cores, and collect shared-step samples.
- [ ] Require DRAM1 result `0xA5A`, both frame counters >= 1, and zero loopback mismatches.
- [ ] If the existing emulator source cannot load, report the exact parser/runtime blocker; do not broaden this task into firmware porting.

### Task 8: Full Automated Regression

**Files:** None.

- [ ] Run `node tests/js/test_signal_graph_helpers.js`.
- [ ] Run `node --check ui/static/app.js`.
- [ ] Run `python -m pytest -q` from `pru-simulator`.
- [ ] Run `git diff --check` and inspect changed-file scope.
- [ ] Report exact pass/fail/skip counts and any warnings.

### Task 9: Real Browser UI Validation

**Files:** None unless a separately reviewed correction is required.

- [ ] Start the local dashboard with Python 3.12.
- [ ] Open it in an available browser and create/load the dual-core encoder setup.
- [ ] Record an 8,192-sample GPIO capture and confirm the initial view is full capture.
- [ ] Confirm transition activity remains visible, cycles/pixel and the warning appear, and `Fit frame` selects the newest complete frame.
- [ ] Confirm wheel zoom, drag pan, double-click reset, resize, CSV export, and shared-x loopback alignment.
- [ ] Capture screenshots or concrete browser evidence for the task report.

### Task 10: Final Whole-Change Review

**Files:** None.

- [ ] Give a fresh reviewer the approved spec, this plan, complete diff, task reports, and verification outputs.
- [ ] Require explicit spec-compliance and code-quality verdicts.
- [ ] Route any Critical/Important corrections back through a new small fix task, then re-run affected checks and UI validation.
