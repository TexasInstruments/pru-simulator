# SPI Link (PRU0 controller ↔ PRU1 peripheral) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A bit-banged SPI link between ICSSG0 PRU0 (controller) and PRU1 (a real edge-polling
peripheral) in pru-simulator, with a calibrated pin-to-pin delay, a rate/mode sweep that
proves which rates work, an SPI view in I/O & Devices, and a session report.

**Architecture:** The firmware is two clpru-compatible `.asm` files. Each is specialised
per (mode, half-period k, MISO sample delay) by a generated `.set` header that is prepended
at load time. A Python module `pru_io/spi_link.py` owns the protocol constants, the rate
table, test patterns, a passive bus-monitor `DeviceModel` (scope plus independent
decoder), timing measurement, and a runtime that drives transfers. The simulator gets a
per-core GPI input delay. The UI gets a scenario, websocket actions and a new SPI subtab
that renders monitor data.

**Tech Stack:** Python 3 (pytest), PRU assembly (simulator assembler, clpru syntax),
FastAPI websocket server (`ui/server.py`), vanilla JS (`node --test`, vm-sandbox tests).

**Spec:** `docs/superpowers/specs/2026-11-04-spi-pru0-pru1-design.md` (approved 2026-11-04). Read it before any task.

## Global Constraints

- Pins (R30/R31 bit numbers): PRU0 CLK out 16, MOSI out 13, /CS out 17, MISO in 14. PRU1 CLK in 1, MOSI in 2, /CS in 6, MISO out 11.
- Frame: /CS low, 19 bytes (cmd, addr, idle, 16 data), MSB first, /CS high. Commands `0x01` WRITE, `0x02` READ, `0x03` READ/WRITE. Valid addr 0x00–0x7F, byte offset = addr × 16.
- PRU1 drives MISO 0x00 during cmd/addr/idle, and during data for WRITE, unknown commands and bad addresses.
- Modes 0 and 1 only (CPOL = 0). Mode 1 = set on rising, capture on falling. Mode 0 = capture on rising, set on falling, first bit valid before the first rising edge.
- Core clocks {200, 225, 250, 300, 333} MHz; both PRUs always the same clock. SPI CLK = core / (2·k), 10–50 MHz, never 50 MHz at 200 MHz.
- "Supported" = zero byte errors over the full test sequence with **GPI delay 4 cycles** on both cores.
- PRU1 must stay an edge-polling peripheral: it never counts cycles from /CS to find bit positions. It may time a sample from the previous edge it observed.
- No cycle claim from hand counting: every cycle budget quoted in code comments, docs or the report is confirmed by stepping in the simulator.
- AM243 numbers only from SPRSP65J §6.11.5.16.1: SR<sub>I</sub> 1–3 V/ns, C<sub>L</sub> 2–30 pF (Table 6-112); t<sub>sk(GPO-GPO)</sub> max 2 ns (Table 6-113, PRDO1). There is no direct-input-mode table; input setup/hold rows are labelled "design budget".
- Pins/board from SPRUJ12F (Tables 4-11…4-14, B-2), as in spec §7.
- Firmware timing tests pin the core clock with the `sim_config` fixture (`tests/conftest.py`), never the repo's `memory.cfg`.
- SPI-view clock changes are for the session only; `memory.cfg` is never written by SPI code.
- Default GPI delay is 0; no existing test or demo may change behaviour.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Record per dispatch: task, role (implementer/reviewer/fix), model, subagent_tokens, duration_ms → append to `docs/reports/spi-pru-link-dispatch-log.csv` (controller does this, not subagents).

## Review Focus

1. Leaving the SPI scenario (loading another example, editing and assembling the firmware, or changing the core selection) must restore GPI delay 0 and remove the SPI wires, drive masks and monitor. Other demos must behave exactly as before. → Task 6 test `test_leaving_spi_scenario_restores_defaults`.
2. Step-back (undo history) in the middle of a frame must restore the GPI delay line and the monitor's edge list consistently. No phantom edges, no lost bits after resuming. → Task 1 `test_gpi_delay_survives_snapshot_restore`, Task 2 `test_monitor_snapshot_restore`.
3. Changing the rate or mode while a run is executing must stop the run, rebuild, re-apply the scenario and clear the old trace. No mixed-rate waveform. → Task 6 `test_spi_setup_while_running_stops_and_clears`.
4. A browser refresh or reconnect in the middle of the demo must rebuild the SPI view from the state push, without needing a new frame. → Task 7 `spi view rehydrates from spi_state on connect`.
5. /CS rising mid-byte (abort), followed immediately by a new frame at the minimum gap, must leave PRU1 in sync: the next frame is correct and the abort is counted. → Task 4 `test_abort_then_back_to_back_frame`.

---

## File Structure

| File | Responsibility |
|---|---|
| `pru_io/io_port.py` (modify) | GPI input delay line (`gpi_delay_cycles`), applied in `read_r31` |
| `simulator.py` (modify) | `set_gpi_delay` / `get_gpi_delay`, snapshot/restore and reset of the delay |
| `pru_io/spi_link.py` (create) | Constants, `SpiConfig`, rate table, patterns, header render, `SpiBusMonitor`, `measure_timing`, `SpiLinkRuntime` |
| `source/spi/spi_controller.asm` (create) | PRU0 controller firmware |
| `source/spi/spi_peripheral.asm` (create) | PRU1 peripheral firmware |
| `tools/spi_sweep.py` (create) | Runs the rate × mode × delay matrix, writes `pru_io/spi_rates.json` and `docs/spi_link_timing.md` |
| `pru_io/spi_rates.json` (generated, committed) | Sweep results the UI gates on |
| `docs/spi_link_timing.md` (generated, committed) | Timing tables per supported combination |
| `pru_io/scenarios.py` (modify) | "SPI multi-core (controller + peripheral)" scenario, `spi_demo_state` |
| `ui/server.py` (modify) | `spi_setup`, `spi_send`, `spi_sequence` actions; session-only clock rebuild; state push |
| `ui/static/spi.js`, `ui/static/spi.css` (create) | SPI view: controls, frame strip, scope, memory, timing table |
| `ui/static/index.html` (modify) | SPI subtab and pane, script/style tags |
| `docs/spi_link.md` (create) | How to run the demo; pins; protocol |
| `docs/reports/2026-11-04-spi-pru-link-session-report.html` (create) | Session report |
| Tests | `tests/test_gpi_delay.py`, `tests/test_spi_link_model.py`, `tests/test_spi_controller.py`, `tests/test_spi_peripheral.py`, `tests/test_spi_sweep.py`, `tests/test_spi_scenario.py`, `tests/ui_spi_view.test.js` |

Memory map (global ICSSG addresses; each PRU uses its own DMEM via `c24`):

| Name | Address | Size | Contents |
|---|---|---|---|
| `CTRL` | 0x0000 | 16 | +0 go (u8; host sets 1, PRU0 clears when /CS is high again), +1 cmd, +2 addr, +3 status (0 idle, 1 busy, 2 done), +4 frame_count (u32) |
| `C_TX` | 0x0100 | 16 | PRU0 data bytes sent on MOSI |
| `C_RX` | 0x0110 | 16 | PRU0 data bytes captured on MISO (bytes 3–18 of the frame) |
| `C_RX_HDR` | 0x0120 | 3 | MISO bytes 0–2 (expected 0x00) |
| `P_STATUS` | 0x2000 | 16 | +0 frames u32, +4 errors u32 (bad cmd/addr), +8 aborts u32, +12 last_cmd u8, +13 last_addr u8 |
| `P_TX` | 0x2800 | 2048 | PRU1 read source, block n at +16·n |
| `P_RX` | 0x3000 | 2048 | PRU1 write target, block n at +16·n |

---

### Task 1: Per-core GPI input delay with silicon calibration — **Opus**

**Files:**
- Modify: `pru_io/io_port.py` (`__init__`, `read_r31`, `reset`, plus snapshot helpers)
- Modify: `simulator.py` (public API next to `set_gpio_drive_mask` at line ~597; snapshot/restore)
- Test: `tests/test_gpi_delay.py`

**Interfaces:**
- Produces: `Simulator.set_gpi_delay(core: str, cycles: int) -> None` (0 ≤ cycles ≤ 16, int, not bool, else `ValueError`); `Simulator.get_gpi_delay(core: str) -> int`; `IOPort.gpi_delay_cycles: int`. R31 reads at core cycle *c* return the GPI word as it was at cycle *c − N*. Before the first N cycles of history, the earliest known word is returned. Applies to every GPI source (wires, devices, `set_gpi_pin`), not to SD/PERIF status reads. Included in step-back snapshots. `Simulator` reset (`hard_reset`) keeps the setting; a new `Simulator` starts at 0.

- [ ] **Step 1: Write failing tests** in `tests/test_gpi_delay.py`:
  - `test_default_delay_is_zero_and_unchanged`: new sim → `get_gpi_delay("pru0") == 0`. A program `wbs r31, 14` / `set r30, r30, 16` / `halt`, with `set_gpi_pin` raised at a known cycle, shows the output edge 1–2 cycles after the input edge.
  - `test_delay_shifts_r31_view`: with `set_gpi_delay("pru1", 3)`, a loop `mov r1, r31` sampling every cycle sees a pin change exactly 3 cycles later than with delay 0.
  - `test_delay_is_per_core`: setting pru1 leaves pru0 at 0.
  - `test_rejects_bad_values`: −1, 17, 2.0, True → `ValueError`.
  - `test_gpi_delay_survives_snapshot_restore`: step 10 cycles past an input edge with delay 4, take a snapshot, step on, restore, step again → the same R31 sequence both times.
  - `test_calibration_matches_silicon_18ns_at_333mhz`: `sim_config(pru_clock_mhz=333, pru1_clock_mhz=333)`. PRU0 toggles GPO16 at a known cycle; wire `add_gpio_wire("pru0",16,"pru1",1)`; PRU1 runs `wbs r31, 1` / `set r30, r30, 11`. With `set_gpi_delay("pru1", 4)`, the PRU1 GPO11 edge comes **6 ± 1 cycles** after the PRU0 GPO16 edge (18 ns ± 3 ns). Measure from per-cycle stepping with `step_paced_many("pru0", ["pru1"])` and each core's `counters.cycles`.
- [ ] **Step 2:** Run `pytest tests/test_gpi_delay.py -v`. Expected: FAIL (`AttributeError: set_gpi_delay`).
- [ ] **Step 3:** Implement. IOPort keeps a short history of (cycle, gpi) changes, bounded to N+1 entries, stamped with the owning core's `counters.cycles` at the time `gpi` changes. The core passes its current cycle to `read_r31` (all three call sites in `core/pru_core.py`: ~681, ~872, ~1056). Keep `read_r31()` callable with no argument for existing callers (it then means "now").
- [ ] **Step 4:** Run `pytest tests/test_gpi_delay.py -v` (PASS), then the full suite with `pytest -q` (all previously passing tests still pass).
- [ ] **Step 5:** Commit `feat(core): per-core GPI input delay, calibrated to 18 ns at 333 MHz`.

### Task 2: `spi_link` model — config, rates, patterns, monitor, timing — **Sonnet**

**Files:**
- Create: `pru_io/spi_link.py`
- Test: `tests/test_spi_link_model.py`

**Interfaces:**
- Produces (all in `pru_io/spi_link.py`):
  - Pin constants `C_CLK=16, C_MOSI=13, C_MISO=14, C_CS=17, P_CLK=1, P_MOSI=2, P_CS=6, P_MISO=11`. Address constants from the memory map table (`CTRL, C_TX, C_RX, C_RX_HDR, P_STATUS, P_TX, P_RX`). `CMD_WRITE=1, CMD_READ=2, CMD_RW=3`, `ADDR_MAX=0x7F`, `FRAME_BYTES=19`, `HOP_DELAY_CYCLES=4`.
  - `@dataclass(frozen=True) SpiConfig(mode: int, core_mhz: int, half_k: int, miso_delay: int = 0)`. Properties `spi_mhz -> float` (= core/(2k)) and `id -> str` (`f"m{mode}_c{core}_k{k}_d{d}"`). It validates mode ∈ {0,1}, core ∈ {200,225,250,300,333}, half_k ≥ 3, 10 ≤ spi_mhz ≤ 50, and not (spi_mhz == 50 and core == 200). Otherwise `ValueError`.
  - `rate_candidates() -> list[tuple[int, int]]` → all (core_mhz, half_k) with 10 ≤ rate ≤ 50 MHz under the rule above, sorted by rate then core.
  - `render_header(cfg: SpiConfig, role: str = "controller") -> str` → controller: `.set` lines `SPI_MODE`, `HALF_K`, `MISO_DELAY`; peripheral: `SPI_MODE` only. Both add a comment line with the config id and rate.
  - `PATTERNS: dict[str, tuple[bytes, bytes]]` → name → (controller TX 16 B, peripheral TX 16 B), with names `a5_5a`, `walking1`, `walking0`, `count`, `toggle`. Spec §9 values: `count` = 00..0F / F0..FF. Every pair has controller ≠ peripheral.
  - `class SpiBusMonitor(DeviceModel)`: passive (`nets = {}`, `time_driven = False`). It is attached on the `"pru0"` endpoint and watches pins 16/13/14/17. It records `edges: list[tuple[int, int]]` (cycle, 4-bit level word: bit0 CS, bit1 CLK, bit2 MOSI, bit3 MISO), only on change. `frames(mode) -> list[dict]` decodes independently of the firmware: `{"start": cycle, "end": cycle, "mosi": bytes, "miso": bytes, "bits": int, "aborted": bool}`. `events()` → one event per completed frame. `faults()` → e.g. "CLK edge while /CS high". Plus `reset/snapshot/restore/get_state`. `clear()` empties edges.
  - `measure_timing(edges, cfg: SpiConfig, frame_index: int = -1) -> list[dict]`. Each row: `{"param", "symbol", "design_ns", "cycles", "sim_ns", "am243_min", "am243_max", "source", "margin_ns", "worst": bool}`. The rows and data-sheet values are those of spec §6 and the approved mockup v3. Exactly one row is `worst=True` (smallest margin). `sim_ns` is measured from edges: t<sub>CLK</sub>, t<sub>H</sub>, t<sub>L</sub>, t<sub>LEAD</sub>, t<sub>LAG</sub>, t<sub>CSH</sub> (needs two frames, else `None`), MOSI change → capture edge, capture edge → next MOSI change, launch edge → MISO change, MISO change → PRU0 sample point (launch + MISO_DELAY).
- Consumes: `DeviceModel` from `pru_io/device_model.py`.

- [ ] **Step 1: Write failing tests:**
  - `test_rate_candidates_exclude_50_at_200` (no (200, 2)); `test_rate_candidates_include` (300,3) → 50.0, (225,3) → 37.5, (200,3) → 33.333.
  - `test_config_validation`: SpiConfig(2, 200, 4) raises; SpiConfig(1, 200, 2) raises; SpiConfig(1, 300, 3).spi_mhz == 50.0.
  - `test_patterns_directions_differ`: for every pattern, both are 16 bytes and differ. `PATTERNS["count"] == (bytes(range(16)), bytes(range(0xF0, 0x100)))`.
  - `test_render_header`: the controller header contains `SPI_MODE .set 1`, `HALF_K .set 3`, `MISO_DELAY .set 7` for SpiConfig(1, 300, 3, 7). The peripheral header contains `SPI_MODE .set 1` and no `HALF_K`.
  - `test_monitor_decodes_synthetic_mode1_frame` / `..._mode0_frame`: feed `tick(cycle, bus)` with a generated 19-byte waveform (cmd 0x03, addr 0x02, idle, `walking1`) → `frames(mode)[0]["mosi"] == bytes([3,2,0]) + walking1`, `bits == 152`, `aborted is False`.
  - `test_monitor_flags_abort`: /CS high after 20 bits → `aborted True`, `bits == 20`.
  - `test_monitor_snapshot_restore`: snapshot mid-frame, add edges, restore → edges equal the snapshot.
  - `test_measure_timing_synthetic`: an ideal synthetic 25 MHz/200 MHz waveform gives `sim_ns` t<sub>CLK</sub> == 40.0, t<sub>H</sub> == 20.0, the PRDO1 row `am243_max == 2.0`, input setup rows `source == "design budget"`, and exactly one `worst` row.
- [ ] **Step 2:** `pytest tests/test_spi_link_model.py -v` → FAIL (import error).
- [ ] **Step 3:** Implement the module to the interfaces above.
- [ ] **Step 4:** `pytest tests/test_spi_link_model.py -v` → PASS.
- [ ] **Step 5:** Commit `feat(spi): protocol model, rate table, patterns, bus monitor, timing measurement`.

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
  - `test_controller_reads_ideal_peripheral`: with `IdealSpiPeripheral(reaction_cycles=…)` plus hop delay 4, there is a `miso_delay` for which `c_rx == response[3:]`. Report the passing window in the test output.
  - `test_back_to_back_gap`: two transfers → t<sub>CSH</sub> ≥ 2 t<sub>CLK</sub>.
- [ ] **Step 2:** `pytest tests/test_spi_controller.py -v` → FAIL.
- [ ] **Step 3:** Write `spi_controller.asm` using `.if SPI_MODE` / `HALF_K` / `MISO_DELAY` constants. Bit timing must be exact for every k ≥ 3 (unroll per byte; use the hardware `LOOP` over bytes where its zero overhead keeps the timing exact). Confirm cycle counts by single-stepping in the simulator and note the measured counts in comments.
- [ ] **Step 4:** `pytest tests/test_spi_controller.py -v` → PASS.
- [ ] **Step 5:** Commit `feat(spi): PRU0 controller firmware and transfer runtime`.

### Task 4: PRU1 edge-polling peripheral firmware — **Opus** (use the `pru-assembly` skill)

**Files:**
- Create: `source/spi/spi_peripheral.asm`
- Modify: `pru_io/spi_link.py` (`load_peripheral`, `peripheral_status() -> dict`, `preload_peripheral_tx(addr: int, data: bytes)`, `read_peripheral_rx(addr: int) -> bytes`)
- Test: `tests/test_spi_peripheral.py`

**Interfaces:**
- Consumes: Task 3 `SpiLinkRuntime` and controller firmware.
- Produces: the PRU1 firmware contract of spec §3/§5.2: `peripheral_status()` returns `{"frames", "errors", "aborts", "last_cmd", "last_addr"}` from P_STATUS. RX is committed only when the frame completes.
- [ ] **Step 1: Write failing tests**, at 200 MHz/k=10 (10 MHz) mode 0 and mode 1, hop delay 4:
  - `test_write_lands_in_rx_block`: WRITE addr 0x05 with `walking1` → `read_peripheral_rx(5) == walking1`; P_TX unchanged.
  - `test_read_returns_tx_block`: preload P_TX block 0x7F, READ 0x7F → `c_rx == block`, MISO header bytes == 00 00 00.
  - `test_rw_full_duplex`: R/W addr 0x02 with `count` → `c_rx == PATTERNS["count"][1]` and RX block 2 == `PATTERNS["count"][0]`.
  - `test_unknown_command_counted` (cmd 0x04 → errors +1, MISO data all 0, nothing written). `test_out_of_range_address` (0x80 → same).
  - `test_abort_then_back_to_back_frame`: `transfer(..., abort_after_bits=50)` (Task 3 API). The next frame at the minimum gap is correct; aborts +1; RX of the aborted frame is not committed.
  - `test_peripheral_is_edge_polling`: assemble the peripheral **once** and run the controller at 200 MHz with k=10 and then k=4 (10 and 25 MHz), reloading only PRU0. Both pass. This shows PRU1 does not depend on the rate. The peripheral's header therefore contains only `SPI_MODE`; it must not use `HALF_K` or `MISO_DELAY` (`render_header(cfg, role="peripheral")`).
- [ ] **Step 2:** `pytest tests/test_spi_peripheral.py -v` → FAIL.
- [ ] **Step 3:** Write `spi_peripheral.asm` with the spec §5.2 techniques: one masked R31 snapshot per poll for CLK+MOSI+/CS, sample timed from the previous observed edge, MISO driven by one R30 write of a pre-shifted byte (check that writing other GPO bits is harmless under the pru1 drive mask), idle-byte fetch of 16 bytes with `lbco`. Mode 0 and mode 1 may be separate code paths. Count the per-bit cycle cost by single-stepping and record it in a comment.
- [ ] **Step 4:** `pytest tests/test_spi_peripheral.py tests/test_spi_controller.py -v` → PASS.
- [ ] **Step 5:** Commit `feat(spi): PRU1 edge-polling peripheral firmware`.

### Task 5: Rate sweep, supported-rate table, timing doc — **Opus**

**Files:**
- Create: `tools/spi_sweep.py`, `pru_io/spi_rates.json` (generated), `docs/spi_link_timing.md` (generated)
- Test: `tests/test_spi_sweep.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces: `run_test_sequence(runtime: SpiLinkRuntime) -> dict` in `pru_io/spi_link.py`. It runs spec §9's sequence and returns `{"ok": bool, "byte_errors": int, "steps": [...]}`. Also `sweep(hop_delays=(0, 4)) -> list[dict]` in `tools/spi_sweep.py`, one row per (mode, core, k, hop_delay): `{"id", "mode", "core_mhz", "half_k", "spi_mhz", "hop_delay", "miso_delay_window": [lo, hi] | None, "miso_delay": int | None, "pass": bool, "p1_cycles_per_bit": int}`, where `miso_delay` is the middle of the window. CLI: `python tools/spi_sweep.py --write` writes the JSON and the Markdown. `pru_io/spi_rates.json` schema: `{"generated": iso8601, "hop_delay_supported": 4, "rows": [...]}`. `load_supported() -> dict[int, list[dict]]` in `spi_link.py` maps mode → the passing rows at hop delay 4, one per rate, at the lowest passing core clock.
- [ ] **Step 1: Write failing tests:**
  - `test_supported_json_matches_fresh_sweep_subset`: re-run three rows from the JSON (the slowest rate, the fastest passing rate, and the fastest failing rate if there is one) and compare `pass` and `miso_delay`.
  - `test_supported_rows_pass_full_sequence`: for the fastest supported row per mode, `run_test_sequence` → `ok` and `byte_errors == 0`.
  - `test_timing_doc_lists_every_supported_row`: every passing id appears in `docs/spi_link_timing.md` with PRDO1 and the "design budget" wording.
  - `test_no_50mhz_at_200`.
- [ ] **Step 2:** Run → FAIL (no JSON).
- [ ] **Step 3:** Implement the sweep. If PRU1 is the limit, optimise `spi_peripheral.asm` (per-bit loop) and re-run. Each optimisation is validated by single-stepping, and its cycles/bit is recorded. Stop when the fastest rate reached has stopped improving, and document the limiting instruction sequence in `docs/spi_link_timing.md`.
- [ ] **Step 4:** `python tools/spi_sweep.py --write`, then `pytest tests/test_spi_sweep.py -v` → PASS.
- [ ] **Step 5:** Commit `feat(spi): rate sweep, supported-rate table and timing doc`.

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

### Task 7: SPI view in I/O & Devices — **Sonnet**

**Files:**
- Create: `ui/static/spi.js`, `ui/static/spi.css`
- Modify: `ui/static/index.html` (a subtab button `id="io-subtab-spi" data-io-tab="spi"` in `#io-subtabs`, plus a pane `id="spi-view"`; script and style tags), `ui/static/app.js` (dispatch `spi_state` from the state push to `SpiView.render`; the "Set up SPI demo" button sends `scenario_load`)
- Test: `tests/ui_spi_view.test.js` (vm-sandbox style as in `tests/ui_ssi_demo.test.js`)

**Interfaces:**
- Consumes: Task 6 `spi_demo_state` payload and actions.
- Produces: `window.SpiView = { render(state), decodeBoxes(edges, mode) -> [{lane, startCycle, endCycle, label, value}], rateOptions(supported, mode) -> [{spi_mhz, core_mhz, label, disabled, title}], timingRows(rows) -> rows with "worst" flag }`. These are pure functions, so they can be tested without a DOM.
- Layout: the approved mockup A plus timing (`.superpowers/brainstorm/10947-1793824791/content/spi-view-layout-a-v2.html` and `-v3.html`). The controls bar has mode, SPI CLK (core clock shown, "session only" note), cmd, addr ("= byte N"), pattern, Send frame and Run test sequence. Then the 19-cell frame strip, the SVG scope from the real `edges` (with cycle → ns via `core_mhz`, byte boxes, capture-edge markers, cursor arrows), four memory panels with the compare badge (mismatches in red), the timing table (AM243 columns tinted, worst row flagged), and the "GPI delay 4 cycles (calibrated 18 ns @ 333 MHz)" label.
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

### Task 8: User doc, whole-branch review, session report — **Sonnet** (report), **Opus** (review), **Haiku** (doc edits, tables)

**Files:**
- Create: `docs/spi_link.md`, `docs/reports/2026-11-04-spi-pru-link-session-report.html`, `docs/reports/spi-pru-link-dispatch-log.csv` (kept up to date throughout)
- Modify: `readme.md` (one line under the examples list)

**Interfaces:**
- Consumes: everything; the session transcript `~/.claude/projects/-home-thomas-GitHub-pru-simulator/248cc36f-e40d-4bf1-9042-e823bf236b73.jsonl` (main-session `usage` totals), the dispatch log, `git log --format='%h %aI %s' main..HEAD`.
- [ ] **Step 1 (Haiku):** `docs/spi_link.md` covers how to run the demo (UI and Python), pins (spec §7 table), protocol (spec §3), the memory map, and a link to `docs/spi_link_timing.md`. Add the readme line.
- [ ] **Step 2 (Opus):** whole-branch review against the spec and this plan's Review Focus. Fix wave if needed. Run `pytest -q` and `node --test --test-isolation=none tests/ui_*.test.js`, and paste the counts into the report.
- [ ] **Step 3 (Sonnet):** write the report in the section order of `docs/reports/2026-08-12-i2c-tca9538-session-report.html` (reuse its CSS):
  1. the verbatim initial prompt, with typos kept and interpretations noted
  2. process
  3. clarifications (spec §2, all 12)
  4. design choices and bugs caught in review
  5. sources (SPRSP65J, SPRUJ12F, the user's 18 ns measurement)
  6. results: rate × mode pass/fail, PRU1 cycles/bit, the fastest supported rate and why
  7. token use per dispatch with model column, plus main-session totals
  8. git-derived timeline
  9. screenshots

  Numbers come only from the CSV, the transcript and git.
- [ ] **Step 4:** Commit `docs(spi): user guide and session report`.

---

## Self-review notes
- Spec coverage: §3 → T2–T4; §4 → T2, T5, T6; §5.1 → T3; §5.2 → T4; §5.3 → T3/T6; §5.4 → T1; §6 → T2, T5; §7 → constants T2, docs T8; §8 → T7; §9 → T2, T5; §10 → each task; §11 → T8 and the dispatch-log constraint.
- Type names used across tasks: `SpiConfig`, `SpiLinkRuntime`, `SpiBusMonitor`, `IdealSpiPeripheral`, `measure_timing`, `run_test_sequence`, `load_supported`, `spi_demo_state`, `SpiView`. All are defined in the task that produces them.
