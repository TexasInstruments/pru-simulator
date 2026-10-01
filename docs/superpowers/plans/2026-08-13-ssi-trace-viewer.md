# SSI Trace Viewer Implementation Plan

**Goal:** Add a small Tkinter desktop viewer for exported SSI CSV traces with full-data plotting, frame selection, and zoom/pan controls.

**Architecture:** Reuse `tools.plot_ssi_trace.load_trace()` and `decode_ssi_frames()` for CSV parsing and SSI decoding. Keep GUI state in a focused `tools/view_ssi_trace.py`; tests cover the data-to-series boundary and zoom math without requiring a display.

**Tech Stack:** Python 3, Tkinter, Matplotlib Agg/Tk canvas, pytest.

## Global Constraints

- Plot every exported row; do not decimate or rewrite the CSV.
- Keep PRU0 clock/data and PRU1 clock/data roles explicit.
- Leave all changes unstaged and uncommitted.

### Task 1: Trace viewer data helpers

**Files:**
- Create: `pru-simulator/tools/view_ssi_trace.py`
- Test: `pru-simulator/tests/test_trace_viewer.py`

- [ ] Add tests for full-row series construction and centered zoom limits.
- [ ] Implement the helpers and GUI shell.
- [ ] Verify tests, Python syntax, and launch behavior.
