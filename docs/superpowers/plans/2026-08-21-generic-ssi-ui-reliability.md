# Generic SSI Dashboard Reliability Follow-up

## Problem

The generic 4 MHz/12-bit PRU0-emulator / PRU1-reader loopback could appear
incorrect in the dashboard even when the cores were running. Long paired
captures arrived as two complete core batches, the memory grids could queue
refreshes faster than they rendered, and stopping the toolbar left a client
run request marked as active. The emulator also treated asynchronous
`formation_mode` as enabled when stale upper register bytes survived a one-byte
`LBCO`.

## Changes

- [x] Tag the two multicore capture batches with one shared timeline key.
- [x] Interleave paired samples by `runStep` before writing the graph buffer.
- [x] Bound each memory panel to one outstanding read and coalesce refreshes.
- [x] Track run request ownership and ignore completion messages for requests
      abandoned by Stop, Reset, Hard Reset, or reconnect.
- [x] Clear the destination register before reading byte-valued formation mode
      in the SSI emulator.
- [x] Add an SSI mailbox quick-jump and visible memory-map hint so the UI does
      not suggest that unused PRU-local DRAM should contain SSI samples.
- [x] Add regressions for paired capture grouping and consecutive default SSI
      frame validity.

## Follow-up: source refresh and mailbox debugging (2026-08-24)

- [x] Publish ordinary `state` messages for PRU0 and PRU1 immediately after
      `ssi_runtime_load`, so Multi-core source tabs do not depend on a later
      mode switch or manual refresh.
- [x] Refresh both source states whenever the generic SSI partner is selected
      while Multi-core mode is active, including repeated loads.
- [x] Replace the compact mailbox summary with an address-labeled seqlock
      snapshot showing raw frame, raw/decoded position, status, frame counter,
      timestamp, and trace counters.
- [x] Preserve fixed-width 64-bit display strings in the server response so
      the UI can compare raw frames and timestamps with Shared RAM directly.

Verification for this follow-up:

```text
python -m pytest -q tests/test_ssi_runtime_ui.py
python -m pytest -q tests/test_mcp_server.py tests/test_ssi_runtime.py tests/test_ssi_runtime_ui.py
node --check ui/static/app.js
```

## Expected result

With the default loopback, the graph retains PRU1 GPO0 (clock), PRU0 GPI8
(clock), PRU0 GPO0 (data), and PRU1 GPI16 (data). The emulator returns the
configured sequence without alternating all-ones frames. Auto-refresh remains
responsive, and a stopped toolbar run cannot block a later Run pair action.
After generic load, both Multi-core source tabs show the loaded PRU programs
immediately. The runtime panel labels every mailbox field with its absolute
Shared RAM address, and its fixed-width values match the memory grid.

## Verification

```text
python -m pytest -q tests/test_multicore_capture.py tests/test_ssi_runtime_ui.py
python -m pytest -q tests/test_ssi_runtime.py::test_default_loopback_does_not_drop_every_other_frame
python -m pytest -q tests/test_perif_server.py
node tests/js/test_signal_graph_helpers.js
node --check ui/static/app.js
```

The dashboard should still be checked manually by loading the generic pair,
enabling Signal Graph recording, running the default sequence, enabling Auto
on both memory panels, stopping the toolbar Run, and then using Run pair.

The generic pair's live mailbox is at global `0x00010200`; local PRU DRAM
windows are valid memory regions but are not written by this firmware.
