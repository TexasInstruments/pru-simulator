### Task 6: Scenario and server API — **Sonnet**

**Files:**
- Modify: `pru_io/scenarios.py`, `ui/server.py`, `tests/test_dashboard_scope.py` (scenario set)
- Test: `tests/test_spi_scenario.py`

**Interfaces:**
- Consumes: Task 5 `load_supported`, the `SpiLinkRuntime` from Tasks 3–4, `measure_timing`.
- Produces:
  - Scenario `"SPI multi-core (controller + peripheral)"`, cores (pru0, pru1), lead pru0, firmware `{"pru0": "spi/spi_controller.asm", "pru1": "spi/spi_peripheral.asm"}`, ui `{"view": "io", "io_tab": "spi", "capture_stride": 1, ...}`. Setup: `SpiLinkRuntime` at the default config (mode 1, 25 MHz, 200 MHz) with the supported `miso_delay`, preloading `P_TX` blocks 0–0x7F with the patterns cycled per block.
  - `spi_demo_state(sim) -> dict | None`: `{"config": {...}, "supported": load_supported(), "core_mhz", "hop_delay", "frame": last monitor frame with "edges" (cycle, levels) limited to that frame, "memory": {"c_tx", "c_rx", "p_rx", "p_tx"} hex lists of the 16-byte blocks at the last addr, "match": {"mosi": int, "miso": int}, "timing": measure_timing rows, "status": peripheral_status()}`. It is included in the state push wherever `ssi_demo_state` is.
  - Websocket actions: `spi_setup {mode, spi_mhz}` → stops execution, picks the supported row, and rebuilds the simulator at that core clock **without writing memory.cfg** (temp config like the `sim_config` fixture), then re-applies the scenario. `spi_send {cmd, addr, pattern}` and `spi_sequence {}` → run on the server and push state. Errors use `{"type": "error", "tag": "spi"}`.
  - Leaving the scenario through the existing paths (`load`, `scenario_load` of another scenario, `reset`, `hard_reset`) calls `runtime.close()`.
- [ ] **Step 1: Write failing tests:**
  - `test_scenario_listed_and_applies`; `test_spi_send_round_trip_via_state` (R/W 0x02 → `match == {"mosi": 16, "miso": 16}`).
  - `test_spi_setup_session_only`: `memory.cfg` bytes are identical before and after `spi_setup` to 50 MHz (if 50 MHz is supported, else to the fastest supported rate). `sim.cores["pru0"].clock_mhz` equals the row's core clock.
  - `test_leaving_spi_scenario_restores_defaults`: after the SPI scenario, applying the SSI multi-core scenario → `get_gpi_delay` 0 on both cores, no SPI wires in `list_gpio_wires()`, no `SpiBusMonitor` in `sim.device_bus.devices`.
  - `test_spi_setup_while_running_stops_and_clears`: with execution running, `spi_setup` → execution stopped and the frame edges are empty until the next send.
  - Update `tests/test_dashboard_scope.py` to the new scenario set.
- [ ] **Step 2:** `pytest tests/test_spi_scenario.py tests/test_dashboard_scope.py -v` → FAIL.
- [ ] **Step 3:** Implement, following `_ssi_multicore`/`ssi_demo_state` and the existing websocket action dispatch in `ui/server.py`.
- [ ] **Step 4:** Run the tests above → PASS, then `pytest -q` passes overall.
- [ ] **Step 5:** Commit `feat(spi): scenario, session-only rate rebuild, websocket actions`.

