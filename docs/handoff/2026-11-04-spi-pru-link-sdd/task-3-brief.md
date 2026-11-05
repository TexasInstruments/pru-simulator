### Task 3: PRU0 controller firmware + runtime transfer API — **Opus** (use the `pru-assembly` skill)

**Files:**
- Create: `source/spi/spi_controller.asm`
- Modify: `pru_io/spi_link.py` (add `SpiLinkRuntime`; add a test-only `IdealSpiPeripheral(DeviceModel)`)
- Test: `tests/test_spi_controller.py`

**Interfaces:**
- Consumes: Task 1 `set_gpi_delay`; Task 2 constants, `SpiConfig`, `render_header`, `SpiBusMonitor`.
- Produces:
  - `IdealSpiPeripheral(mode: int, response: bytes, reaction_cycles: int)`. It is attached on `"pru0"`, drives pin 14 (PUSH_PULL) and returns `response` (19 bytes) bit by bit, changing MISO `reaction_cycles` after each launch edge. Test use only, but it lives in the module so the sweep can reuse it.
  - `SpiLinkRuntime(sim, cfg: SpiConfig, hop_delay: int = HOP_DELAY_CYCLES)`:
    - `load_controller() -> None`: assembles `render_header(cfg) + source`, loads on pru0, sets the pru0 drive mask to pins 16/13/17, sets GPI delay on pru0, attaches the monitor (`self.monitor`).
    - `load_peripheral() -> None`: the same for pru1 (Task 4 fills in the source; this task may raise `NotImplementedError` until then).
    - `wire() -> None`: the four `add_gpio_wire` calls of spec §5.3 and the pru1 drive mask.
    - `transfer(cmd: int, addr: int, data: bytes = bytes(16), max_cycles: int = 20000, abort_after_bits: int | None = None) -> dict`: writes CTRL/C_TX and the abort-bit count at `CTRL+8` (u16, 0 = none), sets go, steps (`step_paced_many("pru0", ["pru1"])` when pru1 is loaded, else `step("pru0")`) until status == done. Returns `{"miso": bytes(19), "c_rx": bytes(16), "cycles": int, "frame": monitor frame dict}`.
    - `close() -> None`: removes wires, masks and monitor, and sets GPI delay back to 0 on both cores.
  - Firmware contract: if `CTRL+8` ≠ 0, PRU0 raises /CS after that many bits (aborted frame) and then completes as normal. PRU0 waits for go, drives /CS low, waits t<sub>LEAD</sub> = 2·HALF_K cycles, clocks 152 bits at exactly HALF_K cycles per half period, samples MISO MISO_DELAY cycles after each launch edge (pipelined across bits if needed), waits t<sub>LAG</sub> = 2·HALF_K, raises /CS, stores C_RX_HDR/C_RX, holds /CS high ≥ 4·HALF_K cycles before accepting the next go, increments frame_count, sets status done.
- [ ] **Step 1: Write failing tests** (all use `sim_config` with an explicit clock):
  - `test_controller_mosi_bytes_mode1` and `_mode0`, at 200 MHz/k=10 and at 300 MHz/k=3: monitor frame `mosi == bytes([cmd, addr, 0]) + data` for every `PATTERNS` entry.
  - `test_controller_clock_exact`: `measure_timing` at 200/k=4 → t<sub>CLK</sub> 40.0 ns, t<sub>H</sub> = t<sub>L</sub> = 20.0, t<sub>LEAD</sub> ≥ 40, t<sub>LAG</sub> ≥ 40. At 300/k=3 → t<sub>CLK</sub> 20.0.
  - `test_controller_reads_ideal_peripheral`: with `IdealSpiPeripheral(reaction_cycles=…)` plus hop delay 5, there is a `miso_delay` for which `c_rx == response[3:]`. Report the passing window in the test output.
  - `test_back_to_back_gap`: two transfers → t<sub>CSH</sub> ≥ 2 t<sub>CLK</sub>.
- [ ] **Step 2:** `pytest tests/test_spi_controller.py -v` → FAIL.
- [ ] **Step 3:** Write `spi_controller.asm` using `.if SPI_MODE` / `HALF_K` / `MISO_DELAY` constants. Bit timing must be exact for every k ≥ 3 (unroll per byte; use the hardware `LOOP` over bytes where its zero overhead keeps the timing exact). Confirm cycle counts by single-stepping in the simulator and note the measured counts in comments.
- [ ] **Step 4:** `pytest tests/test_spi_controller.py -v` → PASS.
- [ ] **Step 5:** Commit `feat(spi): PRU0 controller firmware and transfer runtime`.

