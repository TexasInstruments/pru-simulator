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
  - `test_calibration_matches_silicon_18ns_at_333mhz`: `sim_config(pru_clock_mhz=333, pru1_clock_mhz=333)`. PRU0 toggles GPO16 at a known cycle; wire `add_gpio_wire("pru0",16,"pru1",1)`; PRU1 runs `wbs r31, 1` / `set r30, r30, 11`. With `set_gpi_delay("pru1", 5)`, the PRU1 GPO11 edge comes **exactly 6 cycles** after the PRU0 GPO16 edge (18 ns); with delay 0 it comes 1 cycle after (Ruling R6). Measure from per-cycle stepping with `step_paced_many("pru0", ["pru1"])` and each core's `counters.cycles`.
- [ ] **Step 2:** Run `pytest tests/test_gpi_delay.py -v`. Expected: FAIL (`AttributeError: set_gpi_delay`).
- [ ] **Step 3:** Implement. IOPort keeps a short history of (cycle, gpi) changes, bounded to N+1 entries, stamped with the owning core's `counters.cycles` at the time `gpi` changes. The core passes its current cycle to `read_r31` (all three call sites in `core/pru_core.py`: ~681, ~872, ~1056). Keep `read_r31()` callable with no argument for existing callers (it then means "now").
- [ ] **Step 4:** Run `pytest tests/test_gpi_delay.py -v` (PASS), then the full suite with `pytest -q` (all previously passing tests still pass).
- [ ] **Step 5:** Commit `feat(core): per-core GPI input delay, calibrated to 18 ns at 333 MHz`.

