# Task 1 report: Per-core GPI input delay with silicon calibration

Status: DONE_WITH_CONCERNS
Commit: 153e8a1 feat(core): per-core GPI input delay, calibrated to 18 ns at 333 MHz

## What was implemented
- `pru_io/io_port.py`
  - `gpi` is now a property over `_gpi`. While the delay N > 0, every change goes into a `(cycle, word)` history stamped with the owning core's `counters.cycles`. Changes in the same cycle are merged, and entries older than the newest one at or before `now - N` are dropped, so the history holds at most N+1 entries. Every GPI writer already assigns `port.gpi` (DeviceBus `_refresh_shared` and restore, loopback, I2C, the UART generator, `set_gpi_pin`/`set_gpi_word`, `ui/server` restore), so all of them are covered without editing each one.
  - `gpi_delay_cycles` property. Accepts an int from 0 to 16; a bool, float or out-of-range value raises `ValueError` (upper bound is `GPI_DELAY_MAX = 16`). Setting it starts the history at `[(now, gpi)]`, so reads before N cycles of history exist get the earliest known word.
  - `read_r31(cycle=None)`. PERIF and SD status are returned first and never delayed. With N > 0 and a cycle given, the read returns the word as it was at `cycle - N`. With no argument it returns the current word, so existing callers are unchanged.
  - `bind_cycle_counters(counters)`. If no counters are bound, the stamp falls back to `_device_cycle`.
  - `gpi_delay_snapshot()` / `gpi_delay_restore()`.
  - `reset()` keeps the delay setting and restarts the history at `[(0, gpi)]`, because the core counter has just been zeroed.
- `core/pru_core.py`: `PRUCore.__init__` binds its counters to the IO port. All three R31 read sites (WBS/WBC implicit-R31 form, `_read_operand`, `_mvi_read_direct`) now pass `self.counters.cycles`.
- `simulator.py`: added `set_gpi_delay(core, cycles)` and `get_gpi_delay(core)` next to `set_gpio_drive_mask`.
- `ui/server.py`: the step-back snapshot code lives in `_snapshot_core`/`_restore_core` in this file, not in `simulator.py`. It now saves and restores `"gpi_delay"`. The history is restored last in `_restore_core`. The later `device_bus.restore` writes back the same `gpi` value, and the setter ignores writes that don't change the value, so the restored history is not altered.
- `tests/test_pru_core.py:810`: an existing test replaced `read_r31` with a lambda that takes no arguments. I changed it to `lambda cycle=None: ...` because the core now passes the cycle. Only the signature changed.

## Tests (`tests/test_gpi_delay.py`, 10 tests)
- default_delay_is_zero_and_unchanged: input raised at cycle 5, output edge visible 2 cycles later (asserted 1-2).
- delay_shifts_r31_view: back-to-back `mov r1, r31` lines (unrolled, because a `qba` loop only samples every other cycle) see the change exactly 3 cycles later at delay 3.
- delay_is_per_core, rejects_bad_values[-1, 17, 2.0, True], delay_survives_hard_reset (also checks that a new Simulator starts at 0).
- gpi_delay_survives_snapshot_restore: uses `ui.server._snapshot`/`_restore` with `server.sim` swapped in. Before restoring it scrambles both GPI and the delay; after restoring, the R31 sequence is identical, and the falling edge at cycle 18 is first seen at 22. I checked that this test fails if `gpi_delay` is removed from the snapshot.
- calibration_matches_silicon_18ns_at_333mhz: run with `sim_config(333/333)`, the wire pru0:16 to pru1:1, and `step_paced_many("pru0", ["pru1"])` one step at a time. Edge cycle = each core's `counters.cycles` after the step in which the R30 bit changed.

## TDD evidence
RED: `python -m pytest tests/test_gpi_delay.py -v` gave 10 failed, `AttributeError: 'Simulator' object has no attribute 'set_gpi_delay'`.
GREEN: `python -m pytest tests/test_gpi_delay.py -v` gave 10 passed in 0.20s.

## Measured calibration (simulator single-stepped, 333 MHz)
PRU0 GPO16 edge to PRU1 GPO11 edge:

| delay | cycles | ns |
|---|---|---|
| 0 | 1 | 3.0 |
| 1 | 2 | 6.0 |
| 2 | 3 | 9.0 |
| 3 | 4 | 12.0 |
| 4 | **5** | **15.0** |
| 5 | 6 | 18.0 |
| 6 | 7 | 21.0 |

Single core, `set_input` then `wbs`/`set`, delay 0: 2 cycles.

## Full suite
`pytest -q`: 3 failures out of 3290 tests after the fix, the same 3 that fail on a clean HEAD worktree:
- `test_clock_speed_endpoint.py::test_clock_readout_reports_actual_groups_and_presets[AM263x...]`
- `test_clock_speed_endpoint.py::test_group_one_edit_does_not_materialize_default_group_zero`
- `test_server.py::test_am263x_state_advertises_only_supported_cores`

All three are about rtu0 group membership and have nothing to do with GPI. The one new failure (the `test_pru_core` lambda) is fixed, and `test_pru_core.py` plus `test_gpi_delay.py` give 98 passed.

## Self-review / concerns
1. **Calibration lands at the low edge of the band.** Delay 4 gives 5 cycles (15 ns), not 6 (18 ns). It is inside the brief's 6 ± 1 tolerance, so the test passes as specified. The reason is that the wire model delivers PRU0's R30 write to PRU1's GPI in the same cycle PRU0 executes the `set`, one cycle before PRU0's own edge counts as visible under this measurement. So the simulator's native PRU0-to-PRU1 latency is 1 cycle, not 2. Delay 5 would match 18 ns exactly. I kept 4 as the brief requires; whether to change the global "GPI delay 4" constraint is the controller's or user's decision.
2. Snapshots live in `ui/server.py`, not `simulator.py`, so that is where I wired them.
3. `gpi` is now a Python property, which adds a small cost to every GPI write. The full suite runtime did not change (about 100 s, same as baseline).
4. If several GPI changes happen in the same cycle, the last one wins for that cycle, including over the initial entry written when the delay is set.

## Fix note (Ruling R6, commit see below)
- Calibration test now uses `set_gpi_delay("pru1", 5)` and asserts exactly 6 cycles (18 ns), plus exactly 1 cycle at delay 0. Test name unchanged.
- Module docstring (tests/test_gpi_delay.py) and `Simulator.set_gpi_delay` docstring now state the calibrated value is 5. The first commit's message (153e8a1) still says "delay 4 gives 5 cycles". That was the measurement, not a recommendation, and I left the commit as is rather than rewrite history.
- `python -m pytest tests/test_gpi_delay.py tests/test_pru_core.py -q` gave `98 passed in 0.27s`.
- Commit: c2c445c test(core): GPI delay calibration is 5 cycles (Ruling R6)
