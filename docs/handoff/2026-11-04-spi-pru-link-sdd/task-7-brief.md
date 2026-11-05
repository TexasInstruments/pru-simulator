### Task 7: SPI view in I/O & Devices — **Sonnet**

**Files:**
- Create: `ui/static/spi.js`, `ui/static/spi.css`
- Modify: `ui/static/index.html` (a subtab button `id="io-subtab-spi" data-io-tab="spi"` in `#io-subtabs`, plus a pane `id="spi-view"`; script and style tags), `ui/static/app.js` (dispatch `spi_state` from the state push to `SpiView.render`; the "Set up SPI demo" button sends `scenario_load`)
- Test: `tests/ui_spi_view.test.js` (vm-sandbox style as in `tests/ui_ssi_demo.test.js`)

**Interfaces:**
- Consumes: Task 6 `spi_demo_state` payload and actions.
- Produces: `window.SpiView = { render(state), decodeBoxes(edges, mode) -> [{lane, startCycle, endCycle, label, value}], rateOptions(supported, mode) -> [{spi_mhz, core_mhz, label, disabled, title}], timingRows(rows) -> rows with "worst" flag }`. These are pure functions, so they can be tested without a DOM.
- Layout: the approved mockup A plus timing (`.superpowers/brainstorm/10947-1793824791/content/spi-view-layout-a-v2.html` and `-v3.html`). The controls bar has mode, SPI CLK (core clock shown, "session only" note), cmd, addr ("= byte N"), pattern, Send frame and Run test sequence. Then the 19-cell frame strip, the SVG scope from the real `edges` (with cycle → ns via `core_mhz`, byte boxes, capture-edge markers, cursor arrows), four memory panels with the compare badge (mismatches in red), the timing table (AM243 columns tinted, worst row flagged), and the "GPI delay 5 cycles (calibrated 18 ns @ 333 MHz)" label.
- [ ] **Step 1: Write failing tests:**
  - `rateOptions disables unsupported and 50 MHz@200` (fixture rows).
  - `decodeBoxes labels CMD ADR IDLE D0..D15 with hex` from a fixture edge list (generate it in Python once and paste a small 3-byte fixture).
  - `compare badge shows mismatch count`.
  - `spi view rehydrates from spi_state on connect`: calling `render(state)` with no prior frame action draws the scope and memory.
  - `setup button sends scenario_load with the SPI scenario name`.
- [ ] **Step 2:** `node --test --test-isolation=none tests/ui_spi_view.test.js` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `node --test --test-isolation=none tests/ui_*.test.js` → PASS.
- [ ] **Step 5:** Browser check (`python -m ui.server` or the project's launch command, or the `run` skill). Load the SPI scenario, send R/W 0x02 `walking1` at the default rate and at the fastest supported rate, switch modes, refresh the page. Save screenshots to `docs/reports/img/spi-view-*.png`.
- [ ] **Step 6:** Commit `feat(ui): SPI view with scope, byte decode, memory and timing`.

