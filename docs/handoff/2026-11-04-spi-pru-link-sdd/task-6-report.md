# Task 6 report: SPI scenario and server API

Commit 0d884d7 `feat(spi): scenario, session-only rate rebuild, websocket actions`.

## Implemented
- `pru_io/scenarios.py`: scenario `SPI multi-core (controller + peripheral)` (pru0/pru1, lead pru0, firmware paths verbatim, ui `{view: io, io_tab: spi, capture_stride: 1, graph_window: 4096}`). `apply_scenario` dispatches config validation per scenario (R4): SPI config is exactly `{"mode", "spi_mhz"}`; default (config None) = mode 1 at 25 MHz (R3), 200 MHz core, miso_delay from the sweep row. Teardown factored into `_clear_for_scenario` (shared with SSI, SSI behaviour unchanged). Preloads P_TX blocks 0..0x7F with the pattern p_tx bytes cycled per block. If the simulator core clock differs from the row's, `apply_scenario` raises ValueError (the server rebuilds first). New helpers: `spi_row`, `spi_default_row`, `leave_spi_scenario`, `spi_send`, `spi_sequence`, `spi_demo_state`. `ssi_demo_state` returns None for an SPI runtime.
- `pru_io/spi_link.py`: `SpiBusMonitor.events()` now carries mosi/miso as hex strings (they went into `device_bus` state and bytes broke JSON).
- `ui/server.py`: `_build_session_sim(core_mhz)` (temp config + copied `config/`, memory.cfg never written), `_load_spi_scenario`, actions, `io_section["spi_demo"]` (R1), leave paths call `runtime.close()`, R31 register fix (`read_r31(c.counters.cycles)`, done).
- Tests: `tests/test_spi_scenario.py` (14 tests incl. parametrised load/reset/hard_reset/scenario_load leaving), `tests/test_dashboard_scope.py` scenario set.

## TDD / results
RED: test module failed at import (`spi_demo_state` missing), then JSON TypeError (bytes in monitor events) found by the first ws tests. GREEN: `pytest tests/test_spi_scenario.py tests/test_dashboard_scope.py` 15 passed. Full `pytest -q`: 3465 passed, 2 xfailed, 3 known failures (test_clock_speed_endpoint x2, test_server am263x). The full run overlapped one stale assertion in my own test (wire-list key names) which I fixed afterwards and re-ran the SPI tests (14 pass); the full suite was not re-run after that test-only fix.

## Websocket API
- `scenario_load {name: SPI, config?: {mode, spi_mhz}}` -> `scenario_loaded` + state per core; rebuilds session-only if the core clock must change.
- `spi_setup {mode, spi_mhz}` -> stops execution, always builds a fresh Simulator at the row's core clock (temp config), applies the scenario, keeps IEP override, clears history, publishes to other tabs; replies `scenario_loaded` + both core states.
- `spi_send {cmd 0..255, addr 0..255, pattern in a5_5a|walking1|walking0|count|toggle}`: preloads P_TX[addr] with the pattern's peripheral bytes (if addr<=0x7F), sends the controller bytes, replies with state. `spi_sequence {}` runs `run_test_sequence`. All three also stop execution and clear history.
- Errors: `{"type":"error","tag":"spi","errors":[...]}` (bad rate, bad cmd/addr/pattern, SPI scenario not loaded, timeouts). A state follows every reply.
- Leaving via `load`, `load_elf`, `reset`, `hard_reset` calls `leave_spi_scenario`; `scenario_load` of another scenario closes via `apply_scenario`.

## spi_demo_state payload (`msg.io.spi_demo`)
Keys: name; config {mode, spi_mhz, half_k, miso_delay, core_mhz, id}; core_mhz; hop_delay; supported ({"0": rows, "1": rows} after JSON); last {cmd, addr, pattern} or null; expect {mosi, miso} bools by command or null; frame {start, end, bits, aborted, mosi[19 hex], miso[19 hex], edges [[cycle, word],...]}; memory {c_tx, c_rx, p_rx, p_tx} (16 hex strings each; p_* null if last addr > 0x7F); match {mosi, miso} (matching byte counts, null before the first send); timing (measure_timing rows with hop_delay=runtime.hop_delay); status {frames, errors, aborts, last_cmd, last_addr}; faults; sequence {ok, byte_errors, steps} or null.
Edge word bits: 0 /CS, 1 CLK, 2 MOSI, 3 MISO. `edges` starts with the edge before the frame (idle word) then every edge from frame start to /CS rise. With no frame yet (e.g. after spi_setup) `frame` is a dict with `edges: []`, start/end null.
Example after `spi_send(cmd=3, addr=2, "count")` at default: config {mode 1, spi_mhz 25.0, half_k 4, miso_delay 17, core_mhz 200, id m1_c200_k4_d17}, frame start 42 end 1270 bits 152, edges [[0,1],[42,0],[50,2],[54,0],...], match {mosi: 16, miso: 16}, status {frames 1, errors 0, aborts 0, last_cmd 3, last_addr 2}, memory.c_tx 00..0F, p_rx 00..0F, p_tx F0..FF, c_rx F0..FF.

## Self-review / concerns
- Sends and the sequence run synchronously on the event loop (a sequence takes about a second); the UI should show a busy state.
- Status block can be torn if state is pushed mid-transfer (only possible while Run is executing PRU1 free-running); state after a send is read post-completion.
- After spi_setup to another core clock, the global sim stays at that clock (session only) until a normal rebuild; loading an SSI scenario afterwards does not restore memory.cfg's clock.
- `spi_demo.supported` is sent in every state push (about 58 KB json is NOT included: load_supported returns summary rows only, but still a few KB); the UI may cache it.
- Monitor edges and `events` grow with every frame; no cap added.
- Tests rely on the repo's memory.cfg being 200 MHz (as the fresh_sim pattern in test_scenarios_ws does).

## Fix round 1
Changes:
- R13: `_leave_spi_restoring_clock()` in ui/server.py. When the SPI runtime is left via load, load_elf, reset, hard_reset or scenario_load of another scenario, the runtime is closed. If the core clocks differ from memory.cfg's (read only), the sim is rebuilt from the real config and the IEP override is kept. memory.cfg is never written.
- R14a: `supported` removed from spi_demo_state. `scenario_loaded` (scenario_load and spi_setup) now carries `"spi_supported": {"0": rows, "1": rows}` for the SPI scenario. New action `spi_supported {}` replies `{"type":"spi_supported","supported":{"0":[...],"1":[...]}}`. Task 7 should read the table from either message and cache it.
- R14b: `SpiBusMonitor` keeps edges of only the last `KEEP_FRAMES = 8` complete frames (plus the idle edge before them). `transfer` now detects its new frame by frame end cycle instead of the frame count.
- Minor: the ~1 s event-loop stall of `spi_sequence` is documented in its docstring.
Covering tests (tests/test_spi_scenario.py): test_supported_table_sent_once_not_in_state, test_leaving_after_setup_restores_configured_clock (4 leave paths; also checks the memory.cfg hash), test_monitor_edges_bounded_and_timing_survives (20 transfers, bounded edges, tCSH row present), updated test_scenario_listed_and_applies.
Commands: `pytest tests/test_spi_scenario.py tests/test_dashboard_scope.py tests/test_spi_sweep.py tests/test_spi_link_model.py -q` -> 55 passed. Full suite with the known-failing tests deselected (test_clock_speed_endpoint.py, the test_server am263x test): 3436 passed, 39 deselected, 2 xfailed.
