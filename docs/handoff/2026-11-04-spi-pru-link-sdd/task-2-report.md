# Task 2 report: spi_link model

Status: DONE_WITH_CONCERNS. Commit: see `git log -1` (feat(spi): protocol model, ...).

## Implemented
pru_io/spi_link.py: pin/address/command constants (HOP_DELAY_CYCLES=5), SpiConfig, rate_candidates, render_header, PATTERNS, SpiBusMonitor (passive, edges, frames(mode), events, faults, clear/reset/snapshot/restore/get_state), measure_timing (17 rows, one worst).

## Tests / TDD
- RED: `pytest tests/test_spi_link_model.py` -> ImportError: cannot import name 'spi_link' (collection error).
- GREEN: 15 passed (modes 0 and 1 for decode and timing, abort, fault, snapshot/restore, tCSH needs 2 frames, empty edges).
- Full `pytest -q`: 3299 passed, 3 failed (the 3 known pre-existing failures), 2 xfailed.

## Files
pru_io/spi_link.py, tests/test_spi_link_model.py (only these were staged).

## Self-review / concerns
- Brief's "MISO change -> PRU0 sample point (launch+MISO_DELAY)" is ambiguous. I measure tSU,MISO/tH,MISO against the capture edge (as in the mockup) and report MISO_DELAY row as the delay in ns/cycles; no negative-valued "change to sample point" quantity. Task 6 may want to refine.
- Margins: setup/hold = sim - 2 ns skew - one core cycle; tH/tL, tLEAD, tLAG = sim - 2; tCSH = sim - 2 tCLK; skew = 2 - sim (0 at cycle resolution). Rows without a margin (tCLK, tV, slew, load, delay, idle) never win "worst".
- tV,MISO reports max over MISO changes; setup/hold use min over observed changes (bits with no data transition are unobserved).
- Monitor frames only include /CS-terminated frames; aborted = bits != 152. Monitor mode defaults to 1 (constructor arg).
- Added rows: tCSH, MISO_DELAY, HOP (symbols "tCSH", "MISO_DELAY", "HOP").

## Fix round 1
- tSU,MISO/tH,MISO now measured against sample point = launch edge + miso_delay (R7), handling pipelined samples (setup uses the change of that bit in [launch, sample]; hold uses first change after sample point). When delay is 0 and MISO changes after launch, setup is None.
- Every row has `derived`; tsk (0.0, no pad skew modelled) and tIDLE (design value) are derived=True and never worst.
- Removed no-op in tH/tL row and unused "pre" key.
- Tests added: test_measure_timing_miso_rows_use_sample_point (delay 3, 0, 6 incl. pipelined; asserts tV, tSU, tH, HOP, MISO_DELAY), test_derived_rows_never_worst.
- `pytest tests/test_spi_link_model.py -q` -> 19 passed.

## Fix round 1b
- tSU,MISO = sample point - this bit's MISO change (may be negative, giving a negative margin and the worst row); tH,MISO = first change after sample - sample. None only when MISO never changes.
- Tests: delay-0 case asserts tSU -10 ns, tH 10 ns, margin < 0, worst; new test for constant MISO -> None.
- `pytest tests/test_spi_link_model.py -q` -> 20 passed.
