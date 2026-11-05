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
