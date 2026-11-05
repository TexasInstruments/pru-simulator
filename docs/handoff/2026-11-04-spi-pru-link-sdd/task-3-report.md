# Task 3 report: PRU0 controller firmware + runtime transfer API

Status: DONE_WITH_CONCERNS. Commit `966929f` on feat/spi-pru-link.

## Implemented
- `source/spi/spi_controller.asm` (848 lines, lint_pru.py: 0 errors, 0 warnings).
  - Waits for go. Sets status busy, loads the TX frame image (cmd, addr, 0x00, 16 data bytes) into r5..r9, then drives /CS low. tLEAD = 2·HALF_K, 152 bits at exactly HALF_K per half period, tLAG = 2·HALF_K, /CS high. It then holds /CS high for 1+4·HALF_K cycles, realigns and stores C_RX_HDR/C_RX, increments frame_count, clears go and sets status done.
  - No polling, loads or stores between /CS low and /CS high. All 152 bits are unrolled as "units" of 2·HALF_K cycles: L (launch edge + MOSI, `and r30.w1, r1.w0, r2.w0`), C (capture, `xor r30.b2, r30.b2, r1.b2`), S (MISO sample, `and r3, r31.b1, 0x40`), plus P/A/B (next MOSI bit; shift the sample into one of seven 24-unit chunk registers).
  - HALF_K 3..5: inline units (18 variants, one per sample position). HALF_K ≥ 6: a 4-instruction unit plus `jal` into one shared skeleton that holds L/S/C and the delay loops. The skeleton exists because the simulator's macro preprocessor neither expands nested macros nor allows `.if` inside a macro body.
  - MISO is sampled exactly MISO_DELAY cycles after each launch edge, pipelined up to Q = MISO_DELAY/(2k) ≤ 6 bits. Sampling continues in tail units after the last CLK edge, with no extra clocks. Mode 0 bit 0 uses a virtual launch edge HALF_K before the first rising edge.
  - Abort (CTRL+8 u16 = N, 1..151): before /CS low the TX image is shifted right by E = 152−N bits, and the frame enters at unit E through a computed jump. The same path with E=0 is used for every frame, so the timing is identical. /CS goes high 2k after the N-th bit's last CLK edge, then the frame completes as normal.
  - R30 only ever has bits 13/16/17 non-zero (MOSI is masked, no dirty GPO bits).
- `pru_io/spi_link.py`: CTRL offsets/status constants, `controller_delay_ok(half_k, miso_delay)`, `IdealSpiPeripheral(mode, response, reaction_cycles)`, and `SpiLinkRuntime`:
  - `load_controller()` can be called again (monitor attached once, follows cfg.mode; GPI delay only set when it changes).
  - `load_peripheral()` raises NotImplementedError.
  - `wire()`, `transfer()` and `close()` as specified. `close()` restores the original drive masks and sets the GPI delay to 0 on both cores.

## Tests: tests/test_spi_controller.py (73 tests, all pass)
- mosi_bytes mode0/mode1 at 200/k=10 and 300/k=3: every PATTERNS entry.
- clock_exact: tCLK 40.0, tH/tL 20.0, tLEAD/tLAG ≥ 40 at 200/k4; tCLK 20.0 at 300/k3.
- every_half_period_is_exactly_k: k ∈ {3,4,5,6,7,8,11,16} × both modes × sample before/after C. All 303 half periods == k, tLEAD == tLAG == 2k.
- sample_point_is_launch_plus_delay: single-stepped. The R31 read for bit n happens exactly launch_n + d for all 152 bits. Covers 12 (k,d) cases × 2 modes, every unit class.
- reads_ideal_peripheral: modes 0/1 at 300/k3 and 200/k10; prints the window.
- miso_frame_matches_monitor, back_to_back_gap (tCSH ≥ 2 tCLK; frame_count 2), abort_after_bits (1/20/151 bits × 3 classes × 2 modes; exact edges, next frame normal), invalid_miso_delay_rejected, close_restores, load_peripheral_not_yet.
- Full suite `pytest -q`: 3377 passed, 3 failed (the 3 known pre-existing: test_clock_speed_endpoint ×2, test_server am263x), 2 xfailed.

## TDD evidence
- RED: `pytest tests/test_spi_controller.py -q -x` → `ImportError: cannot import name 'IdealSpiPeripheral' from 'pru_io.spi_link'` (collection error, no implementation).
- GREEN: `pytest tests/test_spi_controller.py tests/test_spi_link_model.py -q` → `93 passed`.
- One test of mine was wrong and was fixed rather than the code. With reaction 8 > k, the monitor's pin-level decode at the capture edge cannot see MISO, so that check now uses reaction 1.

## Measured cycle counts (single-stepped, via monitor edges)
| config | /CS low→high | transfer (go→done) | tCSH back-to-back | tLEAD/tLAG |
|---|---|---|---|---|
| m1 300 MHz k3 d14 | 921 (=307k) | 1074 | 150 | 20.0 ns |
| m0 300 MHz k3 d14 | 921 | 1071 | 147 | 20.0 ns |
| m1 200 MHz k4 d9 | 1228 | 1384 | 153 | 40.0 ns |
| m1 200 MHz k5 d23 | 1535 | 1710 | 172 | 50.0 ns |
| m1 200 MHz k6 d13/d19 | 1842 | 2024/2018 | 179/173 | 60.0 ns |
| m1/m0 200 MHz k10 d25 | 3070 | 3292/3282 | 219/209 | 100.0 ns |
| m1 333 MHz k16 d50 | 4912 | 5178 | 263 | 96.1 ns |

Program size is 1107–1734 instructions (inline, k 3..5) and about 800 (skeleton, k ≥ 6), within the 4 K-word IRAM.

## miso_delay passing windows (IdealSpiPeripheral reaction 8 = 5 hop + 3, PRU0 GPI delay 5)
- 300 MHz k=3, modes 0 and 1: [14, 16, 17, 19]. This is the full 2k-wide window 14..19, minus 15 and 18, which cannot be realised (sample on a CLK write).
- 200 MHz k=10, modes 0 and 1: [14..19, 21..29, 31..33]. This is 14..33, minus 20 and 30.
- The round trip is reaction + hop = 13, so the first passing value is 14.

## Self-review
- Spec §5.1: deterministic, no polling in the frame, pipelined sample delay, sampling continues after the last edge, tLEAD = tLAG = 1 tCLK, tCSH ≥ 2 tCLK. Every cycle number quoted in the asm comments is checked by a test.
- The sim-only `.if` limits were avoided. There is no nested `.if`, and every compound condition is computed with `.set` arithmetic. The long `qba idle` became `jmp` (10-bit branch range).
- Ruling R2: `load_controller` can be re-run. Wiring and the monitor are independent of it.

## Concerns
1. **Some miso_delay values cannot be realised:** `miso_delay % (2k)` ∈ {0, k}, or `miso_delay // (2k)` > 6. This includes the SpiConfig default `miso_delay=0`. `load_controller` raises ValueError for these values. The sweep and UI (later tasks) must skip them with `controller_delay_ok`. In practice the windows are 2k wide with two holes.
2. The unit list (160 lines) and the 18 inline variants follow a mechanical rule, documented in the asm comments. I generated them with a throwaway script that is not committed. Editing them by hand is error-prone.
3. Aborted frames shift the TX image with a bit-serial loop before /CS low. That costs up to about 2.3 k cycles at N=1, but is not timing-relevant.
4. `transfer()` steps one instruction at a time and polls status each step. That takes about 25–40 ms per frame, which is fine for tests.
5. Before the first instruction runs, R30 = 0, so /CS is low for 0 cycles until `set r30, r30, 17`. This is harmless for the monitor. Task 4 may want PRU0 started before PRU1 is wired.

---

# Fix round 1 (commit 2fd1a25)

## Changes
1. **Q = 4..6 now proven, no change to MISO_Q_MAX.** New test `test_miso_deep_pipeline[mode × k∈{3,6} × q∈{4,5,6}]` (12 cases). It sets reaction = 2k·Q − 6, which puts the whole passing window at depth Q, and sweeps from 2 below to 2 above that window. The test asserts the passing set equals every `controller_delay_ok` value in [reaction+6, reaction+5+2k], that all of them have depth Q, and that the window contains both merge classes (k=3: s=5 (MP 1) and s∈{1,2,4} (MP 0); k=6: S before C (class 1, MP 0) and after C (class 2, MP 1)). This assembles tail units 154..159 and the realign at RSH up to 7 and ACC6_SH down to 10.
   Measured windows (identical in modes 0 and 1):
   - k=3 (300 MHz): Q4 [25,26,28,29], Q5 [31,32,34,35], Q6 [37,38,40,41]. In each, 24/27, 30/33 and 36/39 are the holes where the sample would land on a CLK write.
   - k=6 (200 MHz): Q4 [49..53, 55..59], Q5 [61..65, 67..71], Q6 [73..77, 79..83]. Holes at 54, 66, 78.
   - Q depths now proven with c_rx data: 0, 1, 2, 3 (earlier tests), and 4, 5, 6.
2. `close()` sets `self.monitor = None`, so `load_controller()` attaches a new monitor. Covered by `test_close_then_reload_reattaches_monitor`: after close and reload, transfer() returns a frame with 152 bits.
3. /CS idle high on load:
   - `load_controller` writes R30 = 1<<17 (register and GPO) after the core reset and before `set_gpio_drive_mask`.
   - The firmware now starts with `ldi r30.w2, 0x0002` (/CS high) and then `ldi r30.w0, 0`, so no instruction drives /CS low.
   - Covered by `test_no_cs_glitch_on_load_and_reload`: with the wires attached, PRU1 GPI6 = 1 right after load and after a reload, and the monitor records only /CS-high levels over the first instructions.
   - Checked: the test fails when the R30 preset is removed from load_controller.
4. `tools/gen_spi_controller_units.py` added. It regenerates the `inline units` and `unit list` blocks between `; >>> generated` / `; <<< generated` markers; `--write` rewrites them in place, and the default mode is a check. `test_generated_blocks_match_generator` asserts that render(asm) == asm and that there are 160 m_unit invocations.
5. Small fixes:
   - The idle comment now says MOSI keeps the last bit sent.
   - The abort_after_bits error message now says 0 (full frame) is accepted.
   - The unreachable `halt` after `jmp r18.w0` is removed.
   - The load_controller ValueError now says to choose miso_delay with controller_delay_ok(). The holes and the SpiConfig default 0 are unchanged, per the ruling.

## Verification
`pytest tests/test_spi_controller.py tests/test_spi_link_model.py -q` gives `108 passed in 134.67s`. lint_pru.py reports 0 errors and 0 warnings, and `python tools/gen_spi_controller_units.py` exits 0.
