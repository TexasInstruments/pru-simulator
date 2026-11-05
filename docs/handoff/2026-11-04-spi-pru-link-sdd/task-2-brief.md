### Task 2: `spi_link` model — config, rates, patterns, monitor, timing — **Sonnet**

**Files:**
- Create: `pru_io/spi_link.py`
- Test: `tests/test_spi_link_model.py`

**Interfaces:**
- Produces (all in `pru_io/spi_link.py`):
  - Pin constants `C_CLK=16, C_MOSI=13, C_MISO=14, C_CS=17, P_CLK=1, P_MOSI=2, P_CS=6, P_MISO=11`. Address constants from the memory map table (`CTRL, C_TX, C_RX, C_RX_HDR, P_STATUS, P_TX, P_RX`). `CMD_WRITE=1, CMD_READ=2, CMD_RW=3`, `ADDR_MAX=0x7F`, `FRAME_BYTES=19`, `HOP_DELAY_CYCLES=5`.
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

