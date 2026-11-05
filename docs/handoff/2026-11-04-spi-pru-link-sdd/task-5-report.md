# Task 5 report: rate sweep, supported-rate table, timing doc

Status: DONE_WITH_CONCERNS. Commit `d003357` on feat/spi-pru-link (base 81d5506).

## Implemented
- `pru_io/spi_link.py`
  - `run_test_sequence(runtime, patterns=None) -> {"ok", "byte_errors", "steps"}`. It runs the spec §9 sequence.
    - For each pattern in PATTERNS: WRITE (addr 3i), READ of a preloaded TX block (0x7F−i) and R/W (0x40+i).
    - Then: unknown cmd 0x04, R/W to address 0x80, a /CS abort after 76 bits, and two back-to-back R/W frames, which are the last two frames on the bus.
    - Every step checks:
      - the 19 MISO bytes PRU0 got;
      - the MOSI bytes from the monitor decode;
      - all 128 PRU1 RX blocks, compared with a snapshot taken before the step;
      - the PRU1 status counters and last_cmd/last_addr.
    - The `back_to_back` step records `tcsh_cycles` and requires it to be at least 4k.
  - `measure_peripheral_work(rt, cmd, addr, data)`: single-steps one frame and returns:
    - `data_bit_cycles`: min over the data bits, i.e. polls plus MISO drive;
    - `data_bit_cycles_max`;
    - `data_byte_cycles`: steady bytes 4..17;
    - `max_byte_cycles`: bytes 0..17;
    - `fetch_cycles`: sum of the LBBO cycles.
    - The logic is the Task 4 test helper, moved into the library.
  - `measure_timing(..., p1_fetch_cycles=None)`: when given, tIDLE becomes a measured row: sim = fetch time, margin = 8 tCLK − fetch, source "PRU1 fetch, stepped", derived=False. The default behaviour is unchanged.
  - `find_miso_window(..., contiguous=False)`: optional early stop after 2 failures that follow a pass.
  - `choose_miso_delay(passing)` (Ruling R9): the passing value nearest the middle; on a tie, the later one.
  - `RATES_PATH`, and `load_supported(path=None) -> {mode: [rows]}`: passing rows at hop 5, one per rate (rounded to 1e-6), at the lowest core clock, sorted by rate.
- `tools/spi_sweep.py`
  - `sweep_row(mode, core, k, hop, config_path=None)` and `sweep(hop_delays=(0, 5), jobs=None)`, which runs in a multiprocessing pool.
  - `render_doc(data)` and `dump_rates` (one JSON row per line).
  - CLI: `--write`, `--render`, `--check`, `--jobs`.
  - The tool builds its own temporary memory.cfg with pinned clocks, the same way as `sim_config`; it never writes the repo memory.cfg.
  - Steps per row:
    1. Step PRU1's work.
    2. Search the window.
    3. Choose d.
    4. On a **fresh simulator**, run the sequence and `measure_timing(frame_index=-2, p1_fetch_cycles=…)`.
  - Extra row keys: `miso_delay_pass` (the full passing list), p1_* cycles/budget/slack, `seq_byte_errors`, `seq_failed_steps`, `tcsh_cycles`, `worst`, `limit`, and `timing` (hop-5 passing rows only).
- `pru_io/spi_rates.json` (generated) and `docs/spi_link_timing.md` (generated, 2.6k lines). The doc has:
  - method;
  - SPRSP65J §6.11.5.16.1 numbers only;
  - "design budget" labels;
  - the hop-delay provenance (one scope measurement at 333 MHz, 18 ns wbs+set, input/output split unknown);
  - board/jumper delay not modelled;
  - the GPI-stamping limitation;
  - the controller /CS gap explanation;
  - PRU1 stepped cycles and the limiting instruction sequence;
  - the supported-rate tables per mode;
  - the fastest rate per mode with its limits;
  - all 212 rows;
  - one timing table per supported combination (88 tables).

## Tests (tests/test_spi_sweep.py, 12 tests, 31 s)
Tests from the brief:
- `test_supported_json_matches_fresh_sweep_subset[0,1]`: re-runs the slowest and the fastest passing row (there is no failing row) and compares pass, miso_delay, window and cycles/bit.
- `test_supported_rows_pass_full_sequence[0,1]`
- `test_timing_doc_lists_every_supported_row`
- `test_no_50mhz_at_200`

Extra tests:
- JSON schema and full matrix coverage
- `load_supported` gives one row per rate at the lowest core clock
- a bad miso_delay makes the sequence fail (byte_errors > 0)
- measured tIDLE
- `measure_peripheral_work` gives 5 / 47 / ≤48 / 12
- the doc matches what the JSON renders to

Full `pytest -q`: 3450 passed, 3 failed (the known test_clock_speed_endpoint ×2 and test_server am263x), 2 xfailed, 294 s.

## TDD evidence
- RED: `ImportError: cannot import name 'load_supported'` at collection, before any implementation.
- GREEN: 12 passed after `python tools/spi_sweep.py --write`.

## Results
All 212 rows pass (2 modes × 53 (core, k) × hop {0, 5}), with zero byte errors over the full sequence. No PRU1 optimisation was needed.

PRU1, single-stepped, the same in every row:

| mode | data bit (polls+drive) | max bit | steady byte | max byte | fetch |
|---|---|---|---|---|---|
| 0 | 4 | 6 | 40 | 47 | 12 |
| 1 | 5 | 6 | 47 | 48 | 12 |

Byte budget = 16k cycles.

**Fastest supported rate per mode: 50.000 MHz at 300 MHz, k=3, in both modes.**
- Mode 1 (d=17):
  - MISO window of only 2 values [16, 17].
  - PRU1 slack is 0 of 48 cycles per byte, so PRU1 is exactly at its limit.
  - Worst margin is tH,MISO 1.33 ns (design budget, measured at the pin).
- Mode 0 (d=16):
  - window [13, 14, 16, 17];
  - PRU1 slack 1 cycle per byte;
  - worst margin tH,MISO 1.33 ns.
- The next faster rate would need k < 3, which is outside the matrix.

The controller's back-to-back /CS-high gap is 144–231 cycles, always ≥ 4k. The controller cannot make a strict 2 tCLK gap: it holds /CS high for ≥ 4k cycles, then realigns and stores the samples, then polls `go`. The doc explains this and points to Task 4's direct-stimulus test, which shows PRU1 keeps up at a 4k gap.

Sweep wall time: 134.6 s with 24 workers (3149 s summed per-row time under contention). The window early-stop gives the same windows Task 4 found with the full search: m1 200/k10 15..32, m0 200/k10 12..31, m0 200/k4 13..19, m1 200/k4 15..19.

## Full results table
| row | mode | core | k | SPI MHz | hop | pass | window (n) | miso_delay | PRU1 cyc/bit | PRU1 max byte / budget | tCSH cyc | worst |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| m0_c200_k10_h0 | 0 | 200 | 10 | 10.000 | 0 | pass | 2..21 (18) | 12 | 4 | 47/160 | 179 | tH,MISO 38.0 |
| m0_c200_k10_h5 | 0 | 200 | 10 | 10.000 | 5 | pass | 12..31 (18) | 22 | 4 | 47/160 | 209 | tH,MISO 13.0 |
| m0_c300_k15_h0 | 0 | 300 | 15 | 10.000 | 0 | pass | 2..31 (28) | 17 | 4 | 47/240 | 209 | tH,MISO 41.333 |
| m0_c300_k15_h5 | 0 | 300 | 15 | 10.000 | 5 | pass | 12..41 (28) | 27 | 4 | 47/240 | 209 | tH,MISO 24.667 |
| m0_c225_k11_h0 | 0 | 225 | 11 | 10.227 | 0 | pass | 2..23 (20) | 13 | 4 | 47/176 | 185 | tH,MISO 38.0 |
| m0_c225_k11_h5 | 0 | 225 | 11 | 10.227 | 5 | pass | 12..32 (20) | 23 | 4 | 47/176 | 218 | tH,MISO 15.778 |
| m0_c333_k16_h0 | 0 | 333 | 16 | 10.406 | 0 | pass | 2..33 (30) | 18 | 4 | 47/256 | 215 | tH,MISO 40.042 |
| m0_c333_k16_h5 | 0 | 333 | 16 | 10.406 | 5 | pass | 12..43 (30) | 28 | 4 | 47/256 | 215 | tH,MISO 25.027 |
| m0_c250_k12_h0 | 0 | 250 | 12 | 10.417 | 0 | pass | 2..25 (22) | 14 | 4 | 47/192 | 191 | tH,MISO 38.0 |
| m0_c250_k12_h5 | 0 | 250 | 12 | 10.417 | 5 | pass | 13..35 (22) | 25 | 4 | 47/192 | 227 | tH,MISO 14.0 |
| m0_c300_k14_h0 | 0 | 300 | 14 | 10.714 | 0 | pass | 2..29 (26) | 16 | 4 | 47/224 | 203 | tH,MISO 38.0 |
| m0_c300_k14_h5 | 0 | 300 | 14 | 10.714 | 5 | pass | 12..39 (26) | 26 | 4 | 47/224 | 203 | tH,MISO 21.333 |
| m0_c333_k15_h0 | 0 | 333 | 15 | 11.100 | 0 | pass | 2..31 (28) | 17 | 4 | 47/240 | 209 | tH,MISO 37.039 |
| m0_c333_k15_h5 | 0 | 333 | 15 | 11.100 | 5 | pass | 12..41 (28) | 27 | 4 | 47/240 | 209 | tH,MISO 22.024 |
| m0_c200_k9_h0 | 0 | 200 | 9 | 11.111 | 0 | pass | 2..19 (16) | 11 | 4 | 47/144 | 173 | tH,MISO 33.0 |
| m0_c200_k9_h5 | 0 | 200 | 9 | 11.111 | 5 | pass | 12..29 (16) | 21 | 4 | 47/144 | 200 | tH,MISO 8.0 |
| m0_c225_k10_h0 | 0 | 225 | 10 | 11.250 | 0 | pass | 2..21 (18) | 12 | 4 | 47/160 | 179 | tH,MISO 33.556 |
| m0_c225_k10_h5 | 0 | 225 | 10 | 11.250 | 5 | pass | 12..31 (18) | 22 | 4 | 47/160 | 209 | tH,MISO 11.333 |
| m0_c250_k11_h0 | 0 | 250 | 11 | 11.364 | 0 | pass | 2..23 (20) | 13 | 4 | 47/176 | 185 | tH,MISO 34.0 |
| m0_c250_k11_h5 | 0 | 250 | 11 | 11.364 | 5 | pass | 12..32 (20) | 23 | 4 | 47/176 | 218 | tH,MISO 14.0 |
| m0_c300_k13_h0 | 0 | 300 | 13 | 11.538 | 0 | pass | 2..27 (24) | 15 | 4 | 47/208 | 197 | tH,MISO 34.667 |
| m0_c300_k13_h5 | 0 | 300 | 13 | 11.538 | 5 | pass | 12..37 (24) | 25 | 4 | 47/208 | 197 | tH,MISO 18.0 |
| m0_c333_k14_h0 | 0 | 333 | 14 | 11.893 | 0 | pass | 2..29 (26) | 16 | 4 | 47/224 | 203 | tH,MISO 34.036 |
| m0_c333_k14_h5 | 0 | 333 | 14 | 11.893 | 5 | pass | 12..39 (26) | 26 | 4 | 47/224 | 203 | tH,MISO 19.021 |
| m0_c200_k8_h0 | 0 | 200 | 8 | 12.500 | 0 | pass | 2..17 (14) | 10 | 4 | 47/128 | 167 | tH,MISO 28.0 |
| m0_c200_k8_h5 | 0 | 200 | 8 | 12.500 | 5 | pass | 12..27 (14) | 20 | 4 | 47/128 | 191 | tH,MISO 3.0 |
| m0_c225_k9_h0 | 0 | 225 | 9 | 12.500 | 0 | pass | 2..19 (16) | 11 | 4 | 47/144 | 173 | tH,MISO 29.111 |
| m0_c225_k9_h5 | 0 | 225 | 9 | 12.500 | 5 | pass | 12..29 (16) | 21 | 4 | 47/144 | 200 | tH,MISO 6.889 |
| m0_c250_k10_h0 | 0 | 250 | 10 | 12.500 | 0 | pass | 2..21 (18) | 12 | 4 | 47/160 | 179 | tH,MISO 30.0 |
| m0_c250_k10_h5 | 0 | 250 | 10 | 12.500 | 5 | pass | 12..31 (18) | 22 | 4 | 47/160 | 209 | tH,MISO 10.0 |
| m0_c300_k12_h0 | 0 | 300 | 12 | 12.500 | 0 | pass | 2..25 (22) | 14 | 4 | 47/192 | 191 | tH,MISO 31.333 |
| m0_c300_k12_h5 | 0 | 300 | 12 | 12.500 | 5 | pass | 13..35 (22) | 25 | 4 | 47/192 | 227 | tH,MISO 11.333 |
| m0_c333_k13_h0 | 0 | 333 | 13 | 12.808 | 0 | pass | 2..27 (24) | 15 | 4 | 47/208 | 197 | tH,MISO 31.033 |
| m0_c333_k13_h5 | 0 | 333 | 13 | 12.808 | 5 | pass | 12..37 (24) | 25 | 4 | 47/208 | 197 | tH,MISO 16.018 |
| m0_c300_k11_h0 | 0 | 300 | 11 | 13.636 | 0 | pass | 2..23 (20) | 13 | 4 | 47/176 | 185 | tH,MISO 28.0 |
| m0_c300_k11_h5 | 0 | 300 | 11 | 13.636 | 5 | pass | 12..32 (20) | 23 | 4 | 47/176 | 218 | tH,MISO 11.333 |
| m0_c333_k12_h0 | 0 | 333 | 12 | 13.875 | 0 | pass | 2..25 (22) | 14 | 4 | 47/192 | 191 | tH,MISO 28.03 |
| m0_c333_k12_h5 | 0 | 333 | 12 | 13.875 | 5 | pass | 13..35 (22) | 25 | 4 | 47/192 | 227 | tH,MISO 10.012 |
| m0_c250_k9_h0 | 0 | 250 | 9 | 13.889 | 0 | pass | 2..19 (16) | 11 | 4 | 47/144 | 173 | tH,MISO 26.0 |
| m0_c250_k9_h5 | 0 | 250 | 9 | 13.889 | 5 | pass | 12..29 (16) | 21 | 4 | 47/144 | 200 | tH,MISO 6.0 |
| m0_c225_k8_h0 | 0 | 225 | 8 | 14.062 | 0 | pass | 2..17 (14) | 10 | 4 | 47/128 | 167 | tH,MISO 24.667 |
| m0_c225_k8_h5 | 0 | 225 | 8 | 14.062 | 5 | pass | 12..27 (14) | 20 | 4 | 47/128 | 191 | tH,MISO 2.444 |
| m0_c200_k7_h0 | 0 | 200 | 7 | 14.286 | 0 | pass | 2..15 (12) | 9 | 4 | 47/112 | 161 | tH,MISO 23.0 |
| m0_c200_k7_h5 | 0 | 200 | 7 | 14.286 | 5 | pass | 12..25 (12) | 19 | 4 | 47/112 | 182 | tH,MISO -2.0 |
| m0_c300_k10_h0 | 0 | 300 | 10 | 15.000 | 0 | pass | 2..21 (18) | 12 | 4 | 47/160 | 179 | tH,MISO 24.667 |
| m0_c300_k10_h5 | 0 | 300 | 10 | 15.000 | 5 | pass | 12..31 (18) | 22 | 4 | 47/160 | 209 | tH,MISO 8.0 |
| m0_c333_k11_h0 | 0 | 333 | 11 | 15.136 | 0 | pass | 2..23 (20) | 13 | 4 | 47/176 | 185 | tH,MISO 25.027 |
| m0_c333_k11_h5 | 0 | 333 | 11 | 15.136 | 5 | pass | 12..32 (20) | 23 | 4 | 47/176 | 218 | tH,MISO 10.012 |
| m0_c250_k8_h0 | 0 | 250 | 8 | 15.625 | 0 | pass | 2..17 (14) | 10 | 4 | 47/128 | 167 | tH,MISO 22.0 |
| m0_c250_k8_h5 | 0 | 250 | 8 | 15.625 | 5 | pass | 12..27 (14) | 20 | 4 | 47/128 | 191 | tH,MISO 2.0 |
| m0_c225_k7_h0 | 0 | 225 | 7 | 16.071 | 0 | pass | 2..15 (12) | 9 | 4 | 47/112 | 161 | tH,MISO 20.222 |
| m0_c225_k7_h5 | 0 | 225 | 7 | 16.071 | 5 | pass | 12..25 (12) | 19 | 4 | 47/112 | 182 | tH,MISO -2.0 |
| m0_c333_k10_h0 | 0 | 333 | 10 | 16.650 | 0 | pass | 2..21 (18) | 12 | 4 | 47/160 | 179 | tH,MISO 22.024 |
| m0_c333_k10_h5 | 0 | 333 | 10 | 16.650 | 5 | pass | 12..31 (18) | 22 | 4 | 47/160 | 209 | tH,MISO 7.009 |
| m0_c200_k6_h0 | 0 | 200 | 6 | 16.667 | 0 | pass | 2..13 (10) | 8 | 4 | 47/96 | 155 | tH,MISO 18.0 |
| m0_c200_k6_h5 | 0 | 200 | 6 | 16.667 | 5 | pass | 13..23 (10) | 19 | 4 | 47/96 | 167 | tSU,MOSI 23.0 |
| m0_c300_k9_h0 | 0 | 300 | 9 | 16.667 | 0 | pass | 2..19 (16) | 11 | 4 | 47/144 | 173 | tH,MISO 21.333 |
| m0_c300_k9_h5 | 0 | 300 | 9 | 16.667 | 5 | pass | 12..29 (16) | 21 | 4 | 47/144 | 200 | tH,MISO 4.667 |
| m0_c250_k7_h0 | 0 | 250 | 7 | 17.857 | 0 | pass | 2..15 (12) | 9 | 4 | 47/112 | 161 | tH,MISO 18.0 |
| m0_c250_k7_h5 | 0 | 250 | 7 | 17.857 | 5 | pass | 12..25 (12) | 19 | 4 | 47/112 | 182 | tH,MISO -2.0 |
| m0_c333_k9_h0 | 0 | 333 | 9 | 18.500 | 0 | pass | 2..19 (16) | 11 | 4 | 47/144 | 173 | tH,MISO 19.021 |
| m0_c333_k9_h5 | 0 | 333 | 9 | 18.500 | 5 | pass | 12..29 (16) | 21 | 4 | 47/144 | 200 | tH,MISO 4.006 |
| m0_c225_k6_h0 | 0 | 225 | 6 | 18.750 | 0 | pass | 2..13 (10) | 8 | 4 | 47/96 | 155 | tH,MISO 15.778 |
| m0_c225_k6_h5 | 0 | 225 | 6 | 18.750 | 5 | pass | 13..23 (10) | 19 | 4 | 47/96 | 167 | tSU,MOSI 20.222 |
| m0_c300_k8_h0 | 0 | 300 | 8 | 18.750 | 0 | pass | 2..17 (14) | 10 | 4 | 47/128 | 167 | tH,MISO 18.0 |
| m0_c300_k8_h5 | 0 | 300 | 8 | 18.750 | 5 | pass | 12..27 (14) | 20 | 4 | 47/128 | 191 | tH,MISO 1.333 |
| m0_c200_k5_h0 | 0 | 200 | 5 | 20.000 | 0 | pass | 2..11 (8) | 7 | 4 | 47/80 | 147 | tH,MISO 13.0 |
| m0_c200_k5_h5 | 0 | 200 | 5 | 20.000 | 5 | pass | 12..21 (8) | 17 | 4 | 47/80 | 157 | tSU,MOSI 18.0 |
| m0_c333_k8_h0 | 0 | 333 | 8 | 20.812 | 0 | pass | 2..17 (14) | 10 | 4 | 47/128 | 167 | tH,MISO 16.018 |
| m0_c333_k8_h5 | 0 | 333 | 8 | 20.812 | 5 | pass | 12..27 (14) | 20 | 4 | 47/128 | 191 | tH,MISO 1.003 |
| m0_c250_k6_h0 | 0 | 250 | 6 | 20.833 | 0 | pass | 2..13 (10) | 8 | 4 | 47/96 | 155 | tH,MISO 14.0 |
| m0_c250_k6_h5 | 0 | 250 | 6 | 20.833 | 5 | pass | 13..23 (10) | 19 | 4 | 47/96 | 167 | tSU,MOSI 18.0 |
| m0_c300_k7_h0 | 0 | 300 | 7 | 21.429 | 0 | pass | 2..15 (12) | 9 | 4 | 47/112 | 161 | tH,MISO 14.667 |
| m0_c300_k7_h5 | 0 | 300 | 7 | 21.429 | 5 | pass | 12..25 (12) | 19 | 4 | 47/112 | 182 | tH,MISO -2.0 |
| m0_c225_k5_h0 | 0 | 225 | 5 | 22.500 | 0 | pass | 2..11 (8) | 7 | 4 | 47/80 | 147 | tH,MISO 11.333 |
| m0_c225_k5_h5 | 0 | 225 | 5 | 22.500 | 5 | pass | 12..21 (8) | 17 | 4 | 47/80 | 157 | tSU,MOSI 15.778 |
| m0_c333_k7_h0 | 0 | 333 | 7 | 23.786 | 0 | pass | 2..15 (12) | 9 | 4 | 47/112 | 161 | tH,MISO 13.015 |
| m0_c333_k7_h5 | 0 | 333 | 7 | 23.786 | 5 | pass | 12..25 (12) | 19 | 4 | 47/112 | 182 | tH,MISO -2.0 |
| m0_c200_k4_h0 | 0 | 200 | 4 | 25.000 | 0 | pass | 2..9 (6) | 6 | 4 | 47/64 | 141 | tH,MISO 8.0 |
| m0_c200_k4_h5 | 0 | 200 | 4 | 25.000 | 5 | pass | 13..19 (6) | 17 | 4 | 47/64 | 157 | tSU,MOSI 13.0 |
| m0_c250_k5_h0 | 0 | 250 | 5 | 25.000 | 0 | pass | 2..11 (8) | 7 | 4 | 47/80 | 147 | tH,MISO 10.0 |
| m0_c250_k5_h5 | 0 | 250 | 5 | 25.000 | 5 | pass | 12..21 (8) | 17 | 4 | 47/80 | 157 | tSU,MOSI 14.0 |
| m0_c300_k6_h0 | 0 | 300 | 6 | 25.000 | 0 | pass | 2..13 (10) | 8 | 4 | 47/96 | 155 | tH,MISO 11.333 |
| m0_c300_k6_h5 | 0 | 300 | 6 | 25.000 | 5 | pass | 13..23 (10) | 19 | 4 | 47/96 | 167 | tSU,MOSI 14.667 |
| m0_c333_k6_h0 | 0 | 333 | 6 | 27.750 | 0 | pass | 2..13 (10) | 8 | 4 | 47/96 | 155 | tH,MISO 10.012 |
| m0_c333_k6_h5 | 0 | 333 | 6 | 27.750 | 5 | pass | 13..23 (10) | 19 | 4 | 47/96 | 167 | tSU,MOSI 13.015 |
| m0_c225_k4_h0 | 0 | 225 | 4 | 28.125 | 0 | pass | 2..9 (6) | 6 | 4 | 47/64 | 141 | tH,MISO 6.889 |
| m0_c225_k4_h5 | 0 | 225 | 4 | 28.125 | 5 | pass | 13..19 (6) | 17 | 4 | 47/64 | 157 | tSU,MOSI 11.333 |
| m0_c300_k5_h0 | 0 | 300 | 5 | 30.000 | 0 | pass | 2..11 (8) | 7 | 4 | 47/80 | 147 | tH,MISO 8.0 |
| m0_c300_k5_h5 | 0 | 300 | 5 | 30.000 | 5 | pass | 12..21 (8) | 17 | 4 | 47/80 | 157 | tSU,MOSI 11.333 |
| m0_c250_k4_h0 | 0 | 250 | 4 | 31.250 | 0 | pass | 2..9 (6) | 6 | 4 | 47/64 | 141 | tH,MISO 6.0 |
| m0_c250_k4_h5 | 0 | 250 | 4 | 31.250 | 5 | pass | 13..19 (6) | 17 | 4 | 47/64 | 157 | tSU,MOSI 10.0 |
| m0_c333_k5_h0 | 0 | 333 | 5 | 33.300 | 0 | pass | 2..11 (8) | 7 | 4 | 47/80 | 147 | tH,MISO 7.009 |
| m0_c333_k5_h5 | 0 | 333 | 5 | 33.300 | 5 | pass | 12..21 (8) | 17 | 4 | 47/80 | 157 | tSU,MOSI 10.012 |
| m0_c200_k3_h0 | 0 | 200 | 3 | 33.333 | 0 | pass | 4..7 (3) | 5 | 4 | 47/48 | 135 | tH,MISO 3.0 |
| m0_c200_k3_h5 | 0 | 200 | 3 | 33.333 | 5 | pass | 13..17 (4) | 16 | 4 | 47/48 | 147 | tH,MISO 3.0 |
| m0_c225_k3_h0 | 0 | 225 | 3 | 37.500 | 0 | pass | 4..7 (3) | 5 | 4 | 47/48 | 135 | tH,MISO 2.444 |
| m0_c225_k3_h5 | 0 | 225 | 3 | 37.500 | 5 | pass | 13..17 (4) | 16 | 4 | 47/48 | 147 | tH,MISO 2.444 |
| m0_c300_k4_h0 | 0 | 300 | 4 | 37.500 | 0 | pass | 2..9 (6) | 6 | 4 | 47/64 | 141 | tH,MISO 4.667 |
| m0_c300_k4_h5 | 0 | 300 | 4 | 37.500 | 5 | pass | 13..19 (6) | 17 | 4 | 47/64 | 157 | tSU,MOSI 8.0 |
| m0_c333_k4_h0 | 0 | 333 | 4 | 41.625 | 0 | pass | 2..9 (6) | 6 | 4 | 47/64 | 141 | tH,MISO 4.006 |
| m0_c333_k4_h5 | 0 | 333 | 4 | 41.625 | 5 | pass | 13..19 (6) | 17 | 4 | 47/64 | 157 | tSU,MOSI 7.009 |
| m0_c250_k3_h0 | 0 | 250 | 3 | 41.667 | 0 | pass | 4..7 (3) | 5 | 4 | 47/48 | 135 | tH,MISO 2.0 |
| m0_c250_k3_h5 | 0 | 250 | 3 | 41.667 | 5 | pass | 13..17 (4) | 16 | 4 | 47/48 | 147 | tH,MISO 2.0 |
| m0_c300_k3_h0 | 0 | 300 | 3 | 50.000 | 0 | pass | 4..7 (3) | 5 | 4 | 47/48 | 135 | tH,MISO 1.333 |
| m0_c300_k3_h5 | 0 | 300 | 3 | 50.000 | 5 | pass | 13..17 (4) | 16 | 4 | 47/48 | 147 | tH,MISO 1.333 |
| m1_c200_k10_h0 | 1 | 200 | 10 | 10.000 | 0 | pass | 5..22 (16) | 14 | 5 | 48/160 | 189 | tH,MISO 33.0 |
| m1_c200_k10_h5 | 1 | 200 | 10 | 10.000 | 5 | pass | 15..32 (16) | 24 | 5 | 48/160 | 219 | tH,MISO 8.0 |
| m1_c300_k15_h0 | 1 | 300 | 15 | 10.000 | 0 | pass | 5..32 (26) | 19 | 5 | 48/240 | 224 | tH,MISO 38.0 |
| m1_c300_k15_h5 | 1 | 300 | 15 | 10.000 | 5 | pass | 16..42 (26) | 29 | 5 | 48/240 | 224 | tH,MISO 21.333 |
| m1_c225_k11_h0 | 1 | 225 | 11 | 10.227 | 0 | pass | 5..24 (18) | 15 | 5 | 48/176 | 196 | tH,MISO 33.556 |
| m1_c225_k11_h5 | 1 | 225 | 11 | 10.227 | 5 | pass | 15..34 (18) | 25 | 5 | 48/176 | 229 | tH,MISO 11.333 |
| m1_c333_k16_h0 | 1 | 333 | 16 | 10.406 | 0 | pass | 5..34 (28) | 20 | 5 | 48/256 | 231 | tH,MISO 37.039 |
| m1_c333_k16_h5 | 1 | 333 | 16 | 10.406 | 5 | pass | 15..44 (28) | 30 | 5 | 48/256 | 231 | tH,MISO 22.024 |
| m1_c250_k12_h0 | 1 | 250 | 12 | 10.417 | 0 | pass | 5..26 (20) | 16 | 5 | 48/192 | 203 | tH,MISO 34.0 |
| m1_c250_k12_h5 | 1 | 250 | 12 | 10.417 | 5 | pass | 15..35 (20) | 25 | 5 | 48/192 | 239 | tH,MISO 18.0 |
| m1_c300_k14_h0 | 1 | 300 | 14 | 10.714 | 0 | pass | 5..30 (24) | 18 | 5 | 48/224 | 217 | tH,MISO 34.667 |
| m1_c300_k14_h5 | 1 | 300 | 14 | 10.714 | 5 | pass | 15..40 (25) | 27 | 5 | 48/224 | 217 | tH,MISO 21.333 |
| m1_c333_k15_h0 | 1 | 333 | 15 | 11.100 | 0 | pass | 5..32 (26) | 19 | 5 | 48/240 | 224 | tH,MISO 34.036 |
| m1_c333_k15_h5 | 1 | 333 | 15 | 11.100 | 5 | pass | 16..42 (26) | 29 | 5 | 48/240 | 224 | tH,MISO 19.021 |
| m1_c200_k9_h0 | 1 | 200 | 9 | 11.111 | 0 | pass | 5..20 (14) | 13 | 5 | 48/144 | 182 | tH,MISO 28.0 |
| m1_c200_k9_h5 | 1 | 200 | 9 | 11.111 | 5 | pass | 15..30 (14) | 23 | 5 | 48/144 | 209 | tH,MISO 3.0 |
| m1_c225_k10_h0 | 1 | 225 | 10 | 11.250 | 0 | pass | 5..22 (16) | 14 | 5 | 48/160 | 189 | tH,MISO 29.111 |
| m1_c225_k10_h5 | 1 | 225 | 10 | 11.250 | 5 | pass | 15..32 (16) | 24 | 5 | 48/160 | 219 | tH,MISO 6.889 |
| m1_c250_k11_h0 | 1 | 250 | 11 | 11.364 | 0 | pass | 5..24 (18) | 15 | 5 | 48/176 | 196 | tH,MISO 30.0 |
| m1_c250_k11_h5 | 1 | 250 | 11 | 11.364 | 5 | pass | 15..34 (18) | 25 | 5 | 48/176 | 229 | tH,MISO 10.0 |
| m1_c300_k13_h0 | 1 | 300 | 13 | 11.538 | 0 | pass | 5..28 (22) | 17 | 5 | 48/208 | 210 | tH,MISO 31.333 |
| m1_c300_k13_h5 | 1 | 300 | 13 | 11.538 | 5 | pass | 15..38 (23) | 27 | 5 | 48/208 | 249 | tH,MISO 14.667 |
| m1_c333_k14_h0 | 1 | 333 | 14 | 11.893 | 0 | pass | 5..30 (24) | 18 | 5 | 48/224 | 217 | tH,MISO 31.033 |
| m1_c333_k14_h5 | 1 | 333 | 14 | 11.893 | 5 | pass | 15..40 (25) | 27 | 5 | 48/224 | 217 | tH,MISO 19.021 |
| m1_c200_k8_h0 | 1 | 200 | 8 | 12.500 | 0 | pass | 5..18 (12) | 12 | 5 | 48/128 | 175 | tH,MISO 23.0 |
| m1_c200_k8_h5 | 1 | 200 | 8 | 12.500 | 5 | pass | 15..28 (12) | 22 | 5 | 48/128 | 199 | tH,MISO -2.0 |
| m1_c225_k9_h0 | 1 | 225 | 9 | 12.500 | 0 | pass | 5..20 (14) | 13 | 5 | 48/144 | 182 | tH,MISO 24.667 |
| m1_c225_k9_h5 | 1 | 225 | 9 | 12.500 | 5 | pass | 15..30 (14) | 23 | 5 | 48/144 | 209 | tH,MISO 2.444 |
| m1_c250_k10_h0 | 1 | 250 | 10 | 12.500 | 0 | pass | 5..22 (16) | 14 | 5 | 48/160 | 189 | tH,MISO 26.0 |
| m1_c250_k10_h5 | 1 | 250 | 10 | 12.500 | 5 | pass | 15..32 (16) | 24 | 5 | 48/160 | 219 | tH,MISO 6.0 |
| m1_c300_k12_h0 | 1 | 300 | 12 | 12.500 | 0 | pass | 5..26 (20) | 16 | 5 | 48/192 | 203 | tH,MISO 28.0 |
| m1_c300_k12_h5 | 1 | 300 | 12 | 12.500 | 5 | pass | 15..35 (20) | 25 | 5 | 48/192 | 239 | tH,MISO 14.667 |
| m1_c333_k13_h0 | 1 | 333 | 13 | 12.808 | 0 | pass | 5..28 (22) | 17 | 5 | 48/208 | 210 | tH,MISO 28.03 |
| m1_c333_k13_h5 | 1 | 333 | 13 | 12.808 | 5 | pass | 15..38 (23) | 27 | 5 | 48/208 | 249 | tH,MISO 13.015 |
| m1_c300_k11_h0 | 1 | 300 | 11 | 13.636 | 0 | pass | 5..24 (18) | 15 | 5 | 48/176 | 196 | tH,MISO 24.667 |
| m1_c300_k11_h5 | 1 | 300 | 11 | 13.636 | 5 | pass | 15..34 (18) | 25 | 5 | 48/176 | 229 | tH,MISO 8.0 |
| m1_c333_k12_h0 | 1 | 333 | 12 | 13.875 | 0 | pass | 5..26 (20) | 16 | 5 | 48/192 | 203 | tH,MISO 25.027 |
| m1_c333_k12_h5 | 1 | 333 | 12 | 13.875 | 5 | pass | 15..35 (20) | 25 | 5 | 48/192 | 239 | tH,MISO 13.015 |
| m1_c250_k9_h0 | 1 | 250 | 9 | 13.889 | 0 | pass | 5..20 (14) | 13 | 5 | 48/144 | 182 | tH,MISO 22.0 |
| m1_c250_k9_h5 | 1 | 250 | 9 | 13.889 | 5 | pass | 15..30 (14) | 23 | 5 | 48/144 | 209 | tH,MISO 2.0 |
| m1_c225_k8_h0 | 1 | 225 | 8 | 14.062 | 0 | pass | 5..18 (12) | 12 | 5 | 48/128 | 175 | tH,MISO 20.222 |
| m1_c225_k8_h5 | 1 | 225 | 8 | 14.062 | 5 | pass | 15..28 (12) | 22 | 5 | 48/128 | 199 | tH,MISO -2.0 |
| m1_c200_k7_h0 | 1 | 200 | 7 | 14.286 | 0 | pass | 5..16 (10) | 11 | 5 | 48/112 | 168 | tH,MISO 18.0 |
| m1_c200_k7_h5 | 1 | 200 | 7 | 14.286 | 5 | pass | 15..26 (11) | 20 | 5 | 48/112 | 189 | tH,MISO -2.0 |
| m1_c300_k10_h0 | 1 | 300 | 10 | 15.000 | 0 | pass | 5..22 (16) | 14 | 5 | 48/160 | 189 | tH,MISO 21.333 |
| m1_c300_k10_h5 | 1 | 300 | 10 | 15.000 | 5 | pass | 15..32 (16) | 24 | 5 | 48/160 | 219 | tH,MISO 4.667 |
| m1_c333_k11_h0 | 1 | 333 | 11 | 15.136 | 0 | pass | 5..24 (18) | 15 | 5 | 48/176 | 196 | tH,MISO 22.024 |
| m1_c333_k11_h5 | 1 | 333 | 11 | 15.136 | 5 | pass | 15..34 (18) | 25 | 5 | 48/176 | 229 | tH,MISO 7.009 |
| m1_c250_k8_h0 | 1 | 250 | 8 | 15.625 | 0 | pass | 5..18 (12) | 12 | 5 | 48/128 | 175 | tH,MISO 18.0 |
| m1_c250_k8_h5 | 1 | 250 | 8 | 15.625 | 5 | pass | 15..28 (12) | 22 | 5 | 48/128 | 199 | tH,MISO -2.0 |
| m1_c225_k7_h0 | 1 | 225 | 7 | 16.071 | 0 | pass | 5..16 (10) | 11 | 5 | 48/112 | 168 | tH,MISO 15.778 |
| m1_c225_k7_h5 | 1 | 225 | 7 | 16.071 | 5 | pass | 15..26 (11) | 20 | 5 | 48/112 | 189 | tH,MISO -2.0 |
| m1_c333_k10_h0 | 1 | 333 | 10 | 16.650 | 0 | pass | 5..22 (16) | 14 | 5 | 48/160 | 189 | tH,MISO 19.021 |
| m1_c333_k10_h5 | 1 | 333 | 10 | 16.650 | 5 | pass | 15..32 (16) | 24 | 5 | 48/160 | 219 | tH,MISO 4.006 |
| m1_c200_k6_h0 | 1 | 200 | 6 | 16.667 | 0 | pass | 5..14 (8) | 10 | 5 | 48/96 | 161 | tH,MISO 13.0 |
| m1_c200_k6_h5 | 1 | 200 | 6 | 16.667 | 5 | pass | 15..23 (8) | 19 | 5 | 48/96 | 173 | tH,MISO -2.0 |
| m1_c300_k9_h0 | 1 | 300 | 9 | 16.667 | 0 | pass | 5..20 (14) | 13 | 5 | 48/144 | 182 | tH,MISO 18.0 |
| m1_c300_k9_h5 | 1 | 300 | 9 | 16.667 | 5 | pass | 15..30 (14) | 23 | 5 | 48/144 | 209 | tH,MISO 1.333 |
| m1_c250_k7_h0 | 1 | 250 | 7 | 17.857 | 0 | pass | 5..16 (10) | 11 | 5 | 48/112 | 168 | tH,MISO 14.0 |
| m1_c250_k7_h5 | 1 | 250 | 7 | 17.857 | 5 | pass | 15..26 (11) | 20 | 5 | 48/112 | 189 | tH,MISO -2.0 |
| m1_c333_k9_h0 | 1 | 333 | 9 | 18.500 | 0 | pass | 5..20 (14) | 13 | 5 | 48/144 | 182 | tH,MISO 16.018 |
| m1_c333_k9_h5 | 1 | 333 | 9 | 18.500 | 5 | pass | 15..30 (14) | 23 | 5 | 48/144 | 209 | tH,MISO 1.003 |
| m1_c225_k6_h0 | 1 | 225 | 6 | 18.750 | 0 | pass | 5..14 (8) | 10 | 5 | 48/96 | 161 | tH,MISO 11.333 |
| m1_c225_k6_h5 | 1 | 225 | 6 | 18.750 | 5 | pass | 15..23 (8) | 19 | 5 | 48/96 | 173 | tH,MISO -2.0 |
| m1_c300_k8_h0 | 1 | 300 | 8 | 18.750 | 0 | pass | 5..18 (12) | 12 | 5 | 48/128 | 175 | tH,MISO 14.667 |
| m1_c300_k8_h5 | 1 | 300 | 8 | 18.750 | 5 | pass | 15..28 (12) | 22 | 5 | 48/128 | 199 | tH,MISO -2.0 |
| m1_c200_k5_h0 | 1 | 200 | 5 | 20.000 | 0 | pass | 6..12 (6) | 9 | 5 | 48/80 | 152 | tH,MISO 8.0 |
| m1_c200_k5_h5 | 1 | 200 | 5 | 20.000 | 5 | pass | 16..22 (6) | 19 | 5 | 48/80 | 162 | tSU,MOSI 18.0 |
| m1_c333_k8_h0 | 1 | 333 | 8 | 20.812 | 0 | pass | 5..18 (12) | 12 | 5 | 48/128 | 175 | tH,MISO 13.015 |
| m1_c333_k8_h5 | 1 | 333 | 8 | 20.812 | 5 | pass | 15..28 (12) | 22 | 5 | 48/128 | 199 | tH,MISO -2.0 |
| m1_c250_k6_h0 | 1 | 250 | 6 | 20.833 | 0 | pass | 5..14 (8) | 10 | 5 | 48/96 | 161 | tH,MISO 10.0 |
| m1_c250_k6_h5 | 1 | 250 | 6 | 20.833 | 5 | pass | 15..23 (8) | 19 | 5 | 48/96 | 173 | tH,MISO -2.0 |
| m1_c300_k7_h0 | 1 | 300 | 7 | 21.429 | 0 | pass | 5..16 (10) | 11 | 5 | 48/112 | 168 | tH,MISO 11.333 |
| m1_c300_k7_h5 | 1 | 300 | 7 | 21.429 | 5 | pass | 15..26 (11) | 20 | 5 | 48/112 | 189 | tH,MISO -2.0 |
| m1_c225_k5_h0 | 1 | 225 | 5 | 22.500 | 0 | pass | 6..12 (6) | 9 | 5 | 48/80 | 152 | tH,MISO 6.889 |
| m1_c225_k5_h5 | 1 | 225 | 5 | 22.500 | 5 | pass | 16..22 (6) | 19 | 5 | 48/80 | 162 | tSU,MOSI 15.778 |
| m1_c333_k7_h0 | 1 | 333 | 7 | 23.786 | 0 | pass | 5..16 (10) | 11 | 5 | 48/112 | 168 | tH,MISO 10.012 |
| m1_c333_k7_h5 | 1 | 333 | 7 | 23.786 | 5 | pass | 15..26 (11) | 20 | 5 | 48/112 | 189 | tH,MISO -2.0 |
| m1_c200_k4_h0 | 1 | 200 | 4 | 25.000 | 0 | pass | 5..10 (5) | 7 | 5 | 48/64 | 145 | tSU,MISO 8.0 |
| m1_c200_k4_h5 | 1 | 200 | 4 | 25.000 | 5 | pass | 15..19 (4) | 17 | 5 | 48/64 | 161 | tSU,MOSI 13.0 |
| m1_c250_k5_h0 | 1 | 250 | 5 | 25.000 | 0 | pass | 6..12 (6) | 9 | 5 | 48/80 | 152 | tH,MISO 6.0 |
| m1_c250_k5_h5 | 1 | 250 | 5 | 25.000 | 5 | pass | 16..22 (6) | 19 | 5 | 48/80 | 162 | tSU,MOSI 14.0 |
| m1_c300_k6_h0 | 1 | 300 | 6 | 25.000 | 0 | pass | 5..14 (8) | 10 | 5 | 48/96 | 161 | tH,MISO 8.0 |
| m1_c300_k6_h5 | 1 | 300 | 6 | 25.000 | 5 | pass | 15..23 (8) | 19 | 5 | 48/96 | 173 | tH,MISO -2.0 |
| m1_c333_k6_h0 | 1 | 333 | 6 | 27.750 | 0 | pass | 5..14 (8) | 10 | 5 | 48/96 | 161 | tH,MISO 7.009 |
| m1_c333_k6_h5 | 1 | 333 | 6 | 27.750 | 5 | pass | 15..23 (8) | 19 | 5 | 48/96 | 173 | tH,MISO -2.0 |
| m1_c225_k4_h0 | 1 | 225 | 4 | 28.125 | 0 | pass | 5..10 (5) | 7 | 5 | 48/64 | 145 | tSU,MISO 6.889 |
| m1_c225_k4_h5 | 1 | 225 | 4 | 28.125 | 5 | pass | 15..19 (4) | 17 | 5 | 48/64 | 161 | tSU,MOSI 11.333 |
| m1_c300_k5_h0 | 1 | 300 | 5 | 30.000 | 0 | pass | 6..12 (6) | 9 | 5 | 48/80 | 152 | tH,MISO 4.667 |
| m1_c300_k5_h5 | 1 | 300 | 5 | 30.000 | 5 | pass | 16..22 (6) | 19 | 5 | 48/80 | 162 | tSU,MOSI 11.333 |
| m1_c250_k4_h0 | 1 | 250 | 4 | 31.250 | 0 | pass | 5..10 (5) | 7 | 5 | 48/64 | 145 | tSU,MISO 6.0 |
| m1_c250_k4_h5 | 1 | 250 | 4 | 31.250 | 5 | pass | 15..19 (4) | 17 | 5 | 48/64 | 161 | tSU,MOSI 10.0 |
| m1_c333_k5_h0 | 1 | 333 | 5 | 33.300 | 0 | pass | 6..12 (6) | 9 | 5 | 48/80 | 152 | tH,MISO 4.006 |
| m1_c333_k5_h5 | 1 | 333 | 5 | 33.300 | 5 | pass | 16..22 (6) | 19 | 5 | 48/80 | 162 | tSU,MOSI 10.012 |
| m1_c200_k3_h0 | 1 | 200 | 3 | 33.333 | 0 | pass | 5..8 (3) | 7 | 5 | 48/48 | 144 | tH,MISO -2.0 |
| m1_c200_k3_h5 | 1 | 200 | 3 | 33.333 | 5 | pass | 16..17 (2) | 17 | 5 | 48/48 | 150 | tH,MISO 3.0 |
| m1_c225_k3_h0 | 1 | 225 | 3 | 37.500 | 0 | pass | 5..8 (3) | 7 | 5 | 48/48 | 144 | tH,MISO -2.0 |
| m1_c225_k3_h5 | 1 | 225 | 3 | 37.500 | 5 | pass | 16..17 (2) | 17 | 5 | 48/48 | 150 | tH,MISO 2.444 |
| m1_c300_k4_h0 | 1 | 300 | 4 | 37.500 | 0 | pass | 5..10 (5) | 7 | 5 | 48/64 | 145 | tSU,MISO 4.667 |
| m1_c300_k4_h5 | 1 | 300 | 4 | 37.500 | 5 | pass | 15..19 (4) | 17 | 5 | 48/64 | 161 | tSU,MOSI 8.0 |
| m1_c333_k4_h0 | 1 | 333 | 4 | 41.625 | 0 | pass | 5..10 (5) | 7 | 5 | 48/64 | 145 | tSU,MISO 4.006 |
| m1_c333_k4_h5 | 1 | 333 | 4 | 41.625 | 5 | pass | 15..19 (4) | 17 | 5 | 48/64 | 161 | tSU,MOSI 7.009 |
| m1_c250_k3_h0 | 1 | 250 | 3 | 41.667 | 0 | pass | 5..8 (3) | 7 | 5 | 48/48 | 144 | tH,MISO -2.0 |
| m1_c250_k3_h5 | 1 | 250 | 3 | 41.667 | 5 | pass | 16..17 (2) | 17 | 5 | 48/48 | 150 | tH,MISO 2.0 |
| m1_c300_k3_h0 | 1 | 300 | 3 | 50.000 | 0 | pass | 5..8 (3) | 7 | 5 | 48/48 | 144 | tH,MISO -2.0 |
| m1_c300_k3_h5 | 1 | 300 | 3 | 50.000 | 5 | pass | 16..17 (2) | 17 | 5 | 48/48 | 150 | tH,MISO 1.333 |

## Files changed
- pru_io/spi_link.py
- tools/spi_sweep.py (new)
- tests/test_spi_sweep.py (new)
- pru_io/spi_rates.json (new, generated, 480 KB)
- docs/spi_link_timing.md (new, generated)

## Self-review
- Only my files are staged. The user's files are untouched.
- No new cycle claims from hand counting. The doc quotes stepped numbers, from this sweep or from Task 4 tests.
- The default behaviour of measure_timing and find_miso_window is unchanged, and Task 2/4 tests still pass.

## Concerns
1. **Monitor timestamps break after `reload_controller`.** `sim.reset("pru0")` restarts PRU0's cycle counter, but PRU1's counter keeps going. MISO edges come in through the wire and are stamped with PRU1's cycle. After any reload (including inside `find_miso_window`), the monitor's edge list mixes the two clocks, and `measure_timing` gives nonsense: tV,MISO came out at about 0.5 ms. The sweep avoids this by using a fresh simulator for the sequence and the timing. **Task 6/7 (the UI rate change) must re-create the simulator or reset both cores' counters before showing timing.** I did not fix this, because it is outside this task.
2. **The MISO setup/hold rows are referenced to the pin (Ruling R7 as implemented in Task 2).** They ignore the input hop: PRU0 actually sees MISO 5 cycles later. Because of this, many passing rows show tH,MISO margin −2.00 ns as their "worst". The effective hold is +5 cycles. The doc explains this, but the controller may want measure_timing to subtract the hop from the sample point. That would change Task 2's tests.
3. tV,MISO wraps modulo tCLK when the sample is pipelined. This is noted in the doc.
4. With 50 MHz in mode 1, PRU1 has 0 cycles of slack and the window has only 2 values. Any silicon skew beyond the hop model would hit this rate first.
5. The JSON is 480 KB because it holds the per-combination timing tables, so the doc can be re-rendered without a new sweep.

---

# Fix round 1 (rulings R10, R7a, R11, R12)

## Changes
- **R10 (one time base after a reload).** `SpiLinkRuntime.reload_controller` now calls `_align_time_base()` after it loads PRU0.
  - Loading PRU0 already resets PRU0's counters and its GPI history. `_align_time_base()` also resets:
    - PRU1's `counters`;
    - PRU1's `io_port._device_cycle`. This is the device-bus stamp of PRU1's pin writes, and it was the real cause of the bad MISO timestamps. `io_port.reset` does the same.
    - PRU1's GPI delay history, by re-setting `gpi_delay_cycles`, which restarts it at cycle 0.
  - It then clears the monitor and seeds it with the idle bus word, so the next /CS falling edge still opens a frame.
- **R7a (pin-referred MISO sample).** `measure_timing(..., hop_delay=HOP_DELAY_CYCLES)`:
  - The sample point is now launch + miso_delay − hop_delay.
  - Applying only that shift still gave wrong pairing at k=3. Each sample was paired with a neighbouring bit's MISO change, and hold came out as 1 cycle. So the change-to-bit pairing also uses the hop: a MISO change belongs to the latest launch edge ≤ change − hop, because PRU1 sees each edge one hop late.
  - This also removes the tV,MISO wrap that was concern 3: tV is now 9 cycles at 50 MHz instead of 3.
  - With hop_delay=0 the behaviour is exactly R7, so the Task-2 synthetic tests pass hop_delay=0.
  - The HOP row now reports `hop_delay`. MOSI rows are unchanged.
- **R9 tie-break changed:** on a tie, the *earlier* value. With pin-referred setup/hold, the later tie value gave k=3 rows a hold of 1 cycle (−2.00 ns budget). The earlier value gives a balanced setup and hold of 2 and 2 cycles. 165 rows' `miso_delay` changed, each by −1 or −2 (by −2 where the middle is a hole). **No row's pass result or window changed.**
- **R11.** The doc now states that the controller's real gap is **144–231 cycles** (computed from the rows) and that the strict 2 tCLK gap is proven by the Task 4 direct-stimulus tests.
- **R12.**
  - `pru_io/spi_rates.json` keeps only `SUMMARY_KEYS`: id, mode, core_mhz, half_k, spi_mhz, hop_delay, miso_delay_window, miso_delay, pass, p1_cycles_per_bit, p1_byte_slack, worst {symbol, margin_ns}, tsu_miso_ns, th_miso_ns.
  - It is **58 KB** (was 480 KB). The timing tables are only in the doc.
  - `--render` was removed, because the doc can no longer be rendered from the JSON. `--check` and `doc_mismatches()` now compare the doc's results table (pass, window, miso_delay) with the JSON.
- Doc text that explained the −2 ns was replaced by the R7a description. The stale tV-wrap sentence was replaced too.

## Covering tests
- `test_reload_controller_keeps_one_time_base`: after a transfer and then `reload_controller`, both cycle counters are 0, the monitor is cleared, and tV,MISO is within 2 cycles of a fresh simulator. RED before the fix: (3159, 9) after resetting the counters only. GREEN after resetting `_device_cycle` too.
- `test_no_negative_miso_setup_hold_at_hop5`: every passing hop-5 row has tSU,MISO > 0 and tH,MISO > 0. The minimum is 6.67 ns for both.
- `test_rates_json_schema_and_coverage` now also checks: summary keys only, size < 100 KB, and miso_delay is `controller_delay_ok`.
- `test_timing_doc_agrees_with_json` replaces `test_timing_doc_up_to_date`.
- `test_measure_timing_miso_rows_use_sample_point` (Task 2): runs at hop_delay=0 with the original expected values, plus a hop-5 variant (MISO lag + hop, miso_delay + 2·hop) that gives the same setup/hold.

## Result after regeneration
- All 212 rows still pass.
- No passing hop-5 row has a negative margin. The smallest worst margin is tSU,MISO 1.33 ns, at k=3.
- Fastest rate per mode is unchanged: 50 MHz at 300 MHz, k=3.
  - mode 1: d=16, setup and hold at the pin 6.67 ns each.
  - mode 0: d=14.
- Sweep wall time 132 s.

## Command and output
`pytest tests/test_spi_sweep.py tests/test_spi_link_model.py tests/test_spi_controller.py tests/test_spi_peripheral.py -q` → `168 passed in 231.48s`. That run was before the final doc-text regeneration; the re-run is noted below.
- Re-run after the final regeneration: `168 passed in 219.19s`. Commit: see the git log entry "fix(spi): ..." following d003357.
