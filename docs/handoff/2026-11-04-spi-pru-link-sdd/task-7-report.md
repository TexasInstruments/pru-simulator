# Task 7 report: SPI view

Commit 510622e `feat(ui): SPI view with scope, byte decode, memory and timing`.

## Implemented
- ui/static/spi.js (window.SpiView: render, init, setSupported, showError, rateOptions, decodeBoxes, timingRows, compareBadge, mismatchIndexes, fieldName, scopeSvg, timingTableHtml) and spi.css.
- index.html: SPI subtab `io-subtab-spi`, pane `spi-view`, script/style tags. workspace.js: `spi` pane registered. app.js: state push -> SpiView.render(msg.io.spi_demo) when msg.io exists; `scenario_loaded.spi_supported` and `spi_supported` messages cached; error tag "spi"; applyScenario opens `ui.io_tab`; SpiView.init({send, stop}).
- The setup button is `#spi-setup-button` (not data-scenario, so the SSI frame-config handler does not catch it) and sends `{action:"scenario_load", name: SPI}`.
- The view asks once for `spi_supported` if a state arrives with no cached table (rehydrate after refresh).
- MISO decode uses PRU0's actual sample point (launch + miso_delay - hop_delay) because at 25 MHz / 200 MHz the pin value at the nominal capture edge is the previous bit (the backend monitor's own decode shows 78 for F0). Boxes for MISO are placed at the pin-referred position (launch + hop).

## Mockup mapping
Controls bar (mode, SPI CLK, core note "session only", cmd, addr "= byte N", pattern, Send frame, Run test sequence) -> `.spi-bar`; frame strip 19 cells (MOSI over MISO hex, cursor outline) -> `frameStripHtml`; SVG scope with byte boxes, capture-edge triangles on CLK, MISO sample dots, cursor with tCLK/tLEAD/tSU/tH/tSU,MISO arrows (click scope to move cursor) -> `scopeSvg`/`cursorGroup`; four memory panels + compare badge (red mismatches) -> `memoryHtml`/`compareBadge`; timing table with tinted AM243 min/max/source columns, worst row flagged, derived marked -> `timingTableHtml`; label "GPI delay 5 cycles (calibrated 18 ns @ 333 MHz)" -> `#spi-gpi-delay`; pins table static in index.html.

## Tests
- tests/ui_spi_view.test.js (7 tests) + tests/fixtures/spi_view_fixture.json (real edges, mode 0 and mode 1, and a full state from the backend). RED first (spi.js missing: 7/7 failed), then GREEN after implementation; two test-side fixes during GREEN (partial box in the fixture, cross-realm deepEqual).
- tests/ui_workspace.test.js: added 'spi-view' id to its fake DOM (workspace.js now registers the pane).
- Result: ui_spi_view 7/7, ui_workspace pass, pytest tests/test_dashboard_scope.py tests/test_spi_scenario.py 21 passed.
- Node 22 here rejects `--test-isolation=none`; I ran each file with `node --test tests/ui_X.test.js`. Pre-existing failures, identical with my changes stashed: ui_capture_integrity (2), ui_chrome (1), ui_shared_selection (6). All other ui_* files pass.

## Browser check: done (headless Chrome via CDP script, extension not connected)
Start: `python -m ui.server`, URL http://127.0.0.1:8080/ (I/O & Devices -> SPI). Driven: setup demo, send R/W 0x02 walking1 at default 25 MHz, switch to the fastest mode-1 rate (50 MHz, core 300 auto), mode 0, page refresh. All showed "16/16 bytes match both ways", no errors; refresh rehydrated the view. Screenshots: docs/reports/img/spi-view-default-25mhz.png, -fastest.png, -mode0.png, -after-refresh.png.

## Self-review / concerns
- At 152 bits across the scope the cursor arrow labels near bit 6 crowd each other and the byte boxes; click-to-move cursor helps but the layout is tight. Cosmetic.
- tCSH shows "—" after a single frame (needs two frames); backend behaviour.
- Mode change keeps the current rate if available in the new mode, else 25 MHz.
- Controls bar wraps to two lines at 1400 px.

## Fix round 1
1. R15: the frame strip's MISO row now comes from `SpiView.misoBytes(state)` (decodeBoxes miso lane, PRU0 sample point); `frame.miso` is no longer shown. Visible note under the scope added. New test: strip MISO bytes equal box MISO bytes (F0 F1 ... on the fixture, not the monitor's 78).
2. tests/ui_chrome.test.js io-tab list now includes 'spi'. ui_chrome still fails, same as on base: later assertion at line 214, "app.js looks up #cnt-rtu-cycles, which is not in index.html" (pre-existing, unrelated). The io-tab assertion at line 122 now passes.
3. Cursor default moved to bit 31 (D0); tLEAD/tCLK arrows at y=56, MOSI/MISO arrows spaced under their lanes (MISO lane moved down, viewBox 292 high); SVG text fill no longer overridden by CSS (labels were invisible); tSU/tH MOSI arrows drawn only when the data change is within one bit period. Screenshots re-taken (all four).
Results: all ui_* files run individually, only the pre-existing failures remain (capture_integrity 2, chrome 1, shared_selection 6); pytest scope+scenario 21 passed.
