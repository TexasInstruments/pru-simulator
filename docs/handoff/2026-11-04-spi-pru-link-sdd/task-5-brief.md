### Task 5: Rate sweep, supported-rate table, timing doc — **Opus**

**Files:**
- Create: `tools/spi_sweep.py`, `pru_io/spi_rates.json` (generated), `docs/spi_link_timing.md` (generated)
- Test: `tests/test_spi_sweep.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces: `run_test_sequence(runtime: SpiLinkRuntime) -> dict` in `pru_io/spi_link.py`. It runs spec §9's sequence and returns `{"ok": bool, "byte_errors": int, "steps": [...]}`. Also `sweep(hop_delays=(0, 5)) -> list[dict]` in `tools/spi_sweep.py`, one row per (mode, core, k, hop_delay): `{"id", "mode", "core_mhz", "half_k", "spi_mhz", "hop_delay", "miso_delay_window": [lo, hi] | None, "miso_delay": int | None, "pass": bool, "p1_cycles_per_bit": int}`, where `miso_delay` is the middle of the window. CLI: `python tools/spi_sweep.py --write` writes the JSON and the Markdown. `pru_io/spi_rates.json` schema: `{"generated": iso8601, "hop_delay_supported": 5, "rows": [...]}`. `load_supported() -> dict[int, list[dict]]` in `spi_link.py` maps mode → the passing rows at hop delay 5, one per rate, at the lowest passing core clock.
- [ ] **Step 1: Write failing tests:**
  - `test_supported_json_matches_fresh_sweep_subset`: re-run three rows from the JSON (the slowest rate, the fastest passing rate, and the fastest failing rate if there is one) and compare `pass` and `miso_delay`.
  - `test_supported_rows_pass_full_sequence`: for the fastest supported row per mode, `run_test_sequence` → `ok` and `byte_errors == 0`.
  - `test_timing_doc_lists_every_supported_row`: every passing id appears in `docs/spi_link_timing.md` with PRDO1 and the "design budget" wording.
  - `test_no_50mhz_at_200`.
- [ ] **Step 2:** Run → FAIL (no JSON).
- [ ] **Step 3:** Implement the sweep. If PRU1 is the limit, optimise `spi_peripheral.asm` (per-bit loop) and re-run. Each optimisation is validated by single-stepping, and its cycles/bit is recorded. Stop when the fastest rate reached has stopped improving, and document the limiting instruction sequence in `docs/spi_link_timing.md`.
- [ ] **Step 4:** `python tools/spi_sweep.py --write`, then `pytest tests/test_spi_sweep.py -v` → PASS.
- [ ] **Step 5:** Commit `feat(spi): rate sweep, supported-rate table and timing doc`.

