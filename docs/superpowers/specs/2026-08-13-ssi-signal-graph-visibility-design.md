# SSI Signal Graph Visibility Design

**Goal:** Keep the Signal Graph's default full-capture view while making sub-pixel SSI clock and data activity visible, measurable, and easy to inspect in one frame.

## Scope

This change is limited to the `pru-simulator` Signal Graph, its automated tests, and directly affected documentation. It does not change PRU execution, GPIO wiring, capture cadence, the capture wire format, CSV export, firmware timing, or SSI injection semantics.

The existing behavior remains authoritative where this design does not say otherwise:

- GPIO Run capture remains decimated by `CAPTURE_STRIDE_GP = 10`.
- Peripheral capture remains per instruction and single-shot.
- Multicore samples remain aligned by the shared `runStep` coordinate.
- The initial graph view covers the complete buffered capture.
- Mouse-wheel zoom, drag pan, and double-click reset remain available.

## Problem

An 8,192-sample dual-core capture can span about 40,000 simulator steps. At a canvas width near 1,000 pixels, an SSI half-period of 30-40 steps occupies less than one pixel. The current renderer maps every edge directly to a floating-point x-coordinate, so multiple edges may fall in the same pixel column and visually collapse into sparse vertical strokes. The underlying capture and simulated loopback can still be correct.

The graph also lacks a concise indication that the visible time range is below pixel resolution, and inspecting one recent SSI frame requires manual wheel zooming without a protocol-oriented shortcut.

## Design

### 1. Full-capture view remains the default

Recording, loading captures, resizing the sample window, and resetting the view leave `signalGraph.view` unset. An unset view means the complete buffered step range is rendered. No automatic SSI zoom or protocol detection changes the user's view.

### 2. Transition-preserving digital rendering

Digital rendering groups each lane's visible intervals by integer canvas pixel column. Each bucket records:

- the lane value entering the bucket;
- whether HIGH occurred in the bucket;
- whether LOW occurred in the bucket;
- the lane value leaving the bucket; and
- the ordered boundary transitions needed to connect adjacent buckets.

For a bucket containing only one level, the renderer draws that level normally. For a bucket containing both levels, it draws a vertical transition spanning HIGH to LOW in that pixel column and connects it to adjacent buckets using the entering and leaving values. This guarantees that activity narrower than one pixel remains visible without claiming a wider duration than the available display resolution.

When zoom makes intervals at least one pixel wide, the same helper produces the ordinary step waveform. The source samples and exported CSV remain untouched; aggregation exists only in the canvas rendering path.

The bucketing logic is implemented as a pure JavaScript helper with no DOM dependency so it can be tested through the existing Node-based frontend test approach.

### 3. Manual `Fit frame` control

The Signal Graph toolbar gains a `Fit frame` button. It never runs automatically.

The control operates on the newest core lane matching the configured SSI clock output (`GPO0` by the current SSI panel default). It uses the shared `runStep` coordinate and searches backward for SSI frame boundaries:

- A clock frame is a group of clock transitions separated from the previous group by an idle-HIGH interval.
- The boundary threshold is derived from the observed active clock: five times the median half-period, with a minimum of 20 steps.
- The newest group must contain at least 24 transitions, corresponding to 12 complete clock periods.
- The selected range starts one median half-period before the group's first transition and ends at the next observed transition after the twelfth period, or one median half-period after the last required transition when no later transition is buffered.
- The range is clamped to the buffered data bounds.

If no complete frame is available, the view is unchanged and the toolbar status reports `No complete SSI frame in capture`.

In multicore mode, GPO0 on PRU0 is preferred because the reader owns the SSI clock. If PRU0 is absent, the current core's GPO0 is used. The control does not infer frames from data lanes.

### 4. Resolution feedback

The existing graph step label is extended with:

- visible step range;
- cycles per pixel, calculated as `visibleStepRange / canvasWidth`; and
- a `sub-pixel activity` warning when any active digital lane has an observed non-zero run shorter than the current cycles-per-pixel value.

The warning describes display resolution only. It does not claim capture loss or invalid firmware timing. When zoomed far enough that every observed run is at least one pixel wide, the warning disappears.

### 5. Error handling and compatibility

- Empty or single-sample captures render without errors and disable effective frame fitting.
- Duplicate `runStep` values from different cores are handled per lane and do not divide by zero.
- Missing `runStep` falls back to `step`, preserving older capture compatibility.
- Constant lanes remain hidden under the existing active-lane rule.
- The renderer does not mutate samples, circular-buffer state, zoom state, or capture messages.
- No new dependency is introduced.

## Testing

Implementation follows red-green TDD for each behavior.

### Pure frontend regression tests

- A pulse whose two transitions map to one pixel produces a transition-preserving bucket.
- Adjacent sub-pixel pulses remain represented as activity without changing source samples.
- A fully zoomed waveform produces the expected ordinary level sequence.
- Frame detection selects the newest complete 12-clock SSI frame after an idle-HIGH gap.
- Frame detection rejects incomplete groups and leaves the view unchanged.
- Resolution metadata reports cycles per pixel and detects/clears sub-pixel activity correctly.

### Python/backend tests

- Existing `run_multicore` capture tests continue to prove both cores receive shared `run_step` values.
- Existing GPIO wire tests continue to prove PRU0 GPO0 -> PRU1 GPI16 propagation.
- Add or extend a focused test proving the reciprocal PRU1 GPO0 -> PRU0 GPI8 wire used by the encoder pair.
- Run the complete `pru-simulator` pytest suite.

### Simulator MCP validation

Use `PRUSimulatorMCP.pru_ssi_inject` with `source/ssi_reader/ssi_reader.asm` and at least the values `0x000`, `0xA5A`, and `0xFFF`. Each run must report `match: true` and at least one captured frame.

For dual-core validation, load the reader and emulator firmware into PRU0 and PRU1, add both GPIO wires, pace the cores together, and verify:

- the reader's DRAM0 result equals the emulator's configured position;
- both loopback signal pairs agree at shared capture steps; and
- the frame counter advances on each core.

If the current MCP wrapper lacks dual-core wire/capture operations, this validation may use the same underlying `Simulator` API directly. The limitation is documented rather than expanding the MCP public API in this change.

### UI validation

Launch the local dashboard and test the real UI in a browser:

1. Load the dual-core reader/emulator setup and record an 8,192-sample GPIO capture.
2. Confirm the initial view remains the full capture.
3. Confirm the clock and data lanes show activity instead of isolated misleading strokes.
4. Confirm the resolution label shows cycles per pixel and the sub-pixel warning at full view.
5. Press `Fit frame`; confirm the newest full SSI frame fills the view.
6. Confirm wheel zoom, drag pan, double-click reset, window resizing, and CSV export still work.
7. Confirm matching loopback lanes align vertically at identical x positions.

Browser unavailability is a completion blocker for this UI-focused change; static replay alone is insufficient.

## Documentation

Update only documentation directly affected by verified behavior:

- Signal Graph usage and controls in `pru-simulator/getting_started.md`.
- Stale references that still describe GPIO capture stride as 100 instead of 10.
- SSI simulator instructions that explain full-view sub-pixel activity and the manual `Fit frame` workflow.

Do not reconcile unrelated 200/250/300 MHz firmware documentation in this change; clock-profile normalization requires a separate hardware/firmware decision.

## Acceptance Criteria

- The graph opens at full capture.
- Every pixel column containing a digital transition visibly represents activity.
- `Fit frame` manually selects the newest complete 12-bit SSI clock frame or reports that none exists without changing the view.
- Cycles-per-pixel and sub-pixel status match the current visible range.
- Source samples, CSV output, capture cadence, and shared multicore alignment are unchanged.
- Focused frontend tests, the complete Python suite, MCP SSI injection, dual-core simulator validation, and the browser walkthrough all pass.
- No unrelated files or behavior are changed.
