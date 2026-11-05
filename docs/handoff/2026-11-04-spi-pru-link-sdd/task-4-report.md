# Task 4 report: PRU1 edge-polling peripheral firmware

Status: DONE_WITH_CONCERNS. Commit `7da951a` on feat/spi-pru-link (base 2fd1a25).

## Implemented
- `source/spi/spi_peripheral.asm`: 1205 instructions in mode 1 and 1058 in mode 0. The mode is chosen at assembly time by `SPI_MODE`, the only header symbol. lint_pru.py reports 0 errors and 0 warnings.
  - **Start-up:** the firmware clears P_STATUS and writes ZERO_BLK, then waits for /CS high. Only after that does a /CS falling edge arm a frame.
  - **Edge polling:** every bit has a launch-edge poll and a capture-edge poll, fully unrolled. Nothing is timed from /CS.
  - **Capture poll:** `and SNAP, r31.b0, 0x46` followed by a branch on bit 1 of SNAP. This is one masked read of CLK|MOSI|/CS, and the read that shows the edge also keeps MOSI. SNAP is a byte field in one of four snapshot registers, so keeping MOSI costs 0 extra cycles. The 8 snapshots of a byte are then packed in 7 xor/shift instructions (the "gather"), spread into the gaps of the next byte.
    - The gather was checked for all 256 bytes (`gen.gather_check`).
    - In mode 0 the snapshot holds CLK=1, which inverts the gathered byte. Cmd and addr are corrected with `xor 0xFF` in the gaps. Data bytes are corrected with 4× `not` at commit.
  - **/CS abort check every bit:** the mode 1 launch poll and the mode 0 capture poll exit to `frame_abort` when /CS goes high. These are the polls that wait for CLK to rise. CLK idles low, so the falling-edge polls always end.
  - **Command decode without branches:** `M = (min((cmd>>1)^1,1)) - 1`. The pointer is `max(((addr<<4) ^ 0x800) & M, ZERO_BLK)`. This gives P_TX[addr] for READ and R/W with addr ≤ 0x7F, and ZERO_BLK (local 0x07F0, 16 zero bytes, a free DMEM1 area) otherwise. So MISO data is 0x00 for WRITE, unknown commands and bad addresses.
  - **Commit after /CS high:** frames +1. If the cmd or addr is bad, errors +1 and nothing is written. Otherwise the RX block is written for WRITE and R/W. last_cmd and last_addr are stored for every completed frame. Abort: aborts +1, RX is not committed, last_cmd/last_addr are unchanged.
- `tools/gen_spi_peripheral.py` generates the two frame bodies between `; >>> generated:` markers (`--write`, the default mode is a check, `--stats`).
  - It schedules gap work earliest deadline first, under the lateness bound `G' = max(g_next-1, G + w - K) ≤ K-1` with design target K=3.
  - It fails if an op misses its deadline.
- `pru_io/spi_link.py`:
  - `PERIPHERAL_SOURCE`
  - `load_peripheral()`
  - `reload_controller(cfg)` (Ruling R2). It raises if `cfg.mode` differs from the mode PRU1 was assembled for.
  - `preload_peripheral_tx`, `read_peripheral_rx`, `peripheral_status()`
  - `find_miso_window(delays=None, addr=0x7E, pattern="count")`, reusable for Task 5. It reloads PRU0 for each `controller_delay_ok` delay up to Q=6, runs one R/W frame, and returns the delays where all 19 MISO bytes are right. Afterwards it restores the original cfg.
- `tests/test_spi_controller.py`: the obsolete `test_load_peripheral_not_yet` is replaced by `test_transfer_without_peripheral_steps_pru0_only`.

## Tests (tests/test_spi_peripheral.py, 38 tests)
- Brief tests, in modes 0 and 1 at 200 MHz/k=10 with hop 5:
  - `test_write_lands_in_rx_block`: P_TX unchanged, only block 5 written, MISO all 0.
  - `test_read_returns_tx_block`: block 0x7F, header 00 00 00, monitor decode agrees.
  - `test_rw_full_duplex`
  - `test_unknown_command_counted`: cmd 0x04, 0x00, 0xFF.
  - `test_out_of_range_address`: R/W 0x80, READ 0xFF, WRITE 0x80. Each bad frame is followed by a good frame.
  - `test_abort_then_back_to_back_frame`: abort after 50 bits, not committed, aborts +1, then two back-to-back frames.
  - `test_peripheral_is_edge_polling`: PRU1 is loaded once (`sim.load` is wrapped and sees only "pru0"), and passes at k=10 and k=4.
- Extra tests:
  - header contains only SPI_MODE
  - `test_miso_window_contains_choice` (4 rates)
  - `test_fastest_rate_all_patterns`: 50 MHz, 300 MHz/k=3, every pattern with R/W and WRITE, both modes.
  - `test_miso_one_r30_write_is_harmless`
  - `test_per_bit_cycles`
  - `test_fetch_and_commit_cycles`
  - `test_waits_for_cs_idle_before_arming`
  - `test_helpers_validate`
  - generated blocks match the generator
- `pytest tests/test_spi_peripheral.py tests/test_spi_controller.py tests/test_spi_link_model.py`: all pass (144, before the 50 MHz test was added).
- Full `pytest -q`: 3430 passed, 3 failed (the known test_clock_speed_endpoint ×2 and test_server am263x), 2 xfailed.

## TDD evidence
- RED: `ImportError: cannot import name 'PERIPHERAL_SOURCE'` at collection, before any implementation.
- GREEN: protocol tests passed on the first firmware run.
- Two tests were wrong and were fixed, not the firmware:
  - The harmless-drive test counted PRU0's own /CS output on PRU0's GPI as a leak.
  - The per-bit test did not count the post-frame tail in bit 151.

## Single-stepped PRU1 cycles
"Work" means all cycles except poll iterations that looped back. The stepped count equals the generator's static count for every bit.

| | data bit | cmd/addr bit | idle bit | per data byte incl. gather | 
|---|---|---|---|---|
| mode 1 | 5 (poll 2 + MISO 1 + poll 2) | 4 | 3 | 47 |
| mode 0 | 4 (poll 2 + poll 1 + MISO 1) | 3 | 2 (3 at bit 23, which drives data bit 24) | 39 |

- At k=3 a data byte takes 48 cycles, so mode 1 has 1 cycle of slack per byte.
- Idle-byte TX fetch: 4 × `lbbo` 4 B at 3 cycles each, 12 cycles in total.
- Commit, from the read that sees /CS high back to `frame_wait`: 22 cycles in mode 1 and 26 in mode 0 for R/W. PRU1 needs /CS high for at least that long. The controller's gaps were 209/219 cycles at k=10.

## miso_delay per test rate
Windows come from `find_miso_window`; the chosen value is near the window middle (Ruling R9).

| rate | mode 0 (window) | mode 1 (window) |
|---|---|---|
| 200 MHz k=10 (10 MHz) | 22 (12..31, holes 20/30) | 23 (15..32, holes 20/30) |
| 200 MHz k=4 (25 MHz) | 17 (13..19, hole 16) | 17 (15..19, hole 16) |
| 300 MHz k=3 (50 MHz), all patterns | 16 ([14,16,17]) | 16 ([16,17]) |

## One-write MISO drive
It works. `lsl r30.w0, TXbyte, 4+i` puts TX bit 7-i on GPO11. The test confirms three things: PRU1's other R30 bits are set during a frame; no bit outside the SPI nets ever appears on either core's GPI; MISO data is exact. The drive mask is `1<<11`. R30 bits 16+ are never written.

## Self-review
- Anti-goal: PRU1 never counts cycles. Every bit waits for its own edges, and the header has no rate symbol. The same assembly passes at k=10, k=4 and k=3.
- The /CS idle wait comes before arming.
- RX is committed only at the end of a complete frame.
- The asm comments quote only stepped numbers (per bit, per byte, fetch, commit), and each is asserted by a test.
- Abort detection is per bit, which is stricter than the spec's byte-boundary minimum.

## Concerns
1. **Brief said lbco; I used lbbo.** I used 4× `lbbo &r1X, r19, 4j, 4` rather than one `lbco ... 16`. A single 16-byte load stalls 6 cycles and would miss a CLK level at k ≤ 4. lbbo with the pointer register as base gives immediate offsets at the same cost as lbco. The fetches of r11..r13 may fall after byte 2, up to the first MISO bit that uses each register.
2. **50 MHz has narrow margins.** Mode 1 has a 2-value miso_delay window and 1 cycle of slack per byte. Task 5 must sweep it. On silicon, any input skew beyond the 5-cycle hop model would cut into this.
3. **Errors and last_cmd defined by me.** `frames` counts error frames too: errors are a subset of frames. last_cmd and last_addr are not updated for aborted frames. Task 5 / UI should read the counters this way.
4. **Gap PRU1 needs between frames.** PRU1 needs /CS high for ≥ ~22–26 cycles plus the hop. At k=3, 2 tCLK is 12 cycles, so a controller using the strict 2 tCLK minimum would be too fast for PRU1. Our controller's gaps are about 150+ cycles.
5. **find_miso_window is slow.** It takes about 20 s per rate at k=10 because PRU0 is reassembled for every candidate. The four window tests cost about 50 s of suite time.

---

# Fix round 1 (commit 81d5506)

## Changes
1. **Short /CS-high gap no longer loses a frame (Important).** The old frame end took 22/26 cycles to commit, then went to `frame_wait`. That waited through a frame whose /CS had already fallen.
   - The commit at /CS high is now: `ldi r30,0`, frames += 1, errors += r29, last cmd/addr, and one `sbco` of the RX block to r28. That is **10 cycles** in both modes, from the R31 read that sees /CS high to the first /CS check (single-stepped).
   - The error flag r29 and the RX pointer r28 are computed branch-free during the frame. r28 is the P_RX block for a valid WRITE or R/W, and RX_DUMMY (local 0x1800) otherwise.
   - The status store now runs in `frame_idle`, one word at a time with a /CS check between words. If /CS has already fallen, it jumps straight to `frame_go`, the bit 0 polls; the /CS falling edge counts as already seen, and the cmd byte is still polled edge by edge.
   - Every frame repeats the 4 status stores (`st0..st3`) in its first gaps. This is idempotent: r20–r23 change only at frame end and abort.
   - Abort now jumps to `frame_idle` the same way. `frame_wait` (wait for /CS idle high) is used only at start-up.
   - Mode 0 now does its gather inversion as one `xor 0xFF` per byte inside the generator schedule, so the commit no longer needs 4× `not`.
   - Schedule at K=3 still fits. The generator places the r28/r29 ops after the fetches, so they do not take the gaps the 3-cycle `lbbo`s need.
2. **Spec §5.2 deviation (Minor).** The asm header now states that PRU1 samples MOSI at the capture-edge snapshot and does not time the sample from the previous edge. Reasons:
   - Re-arming for the next edge without counting cycles still needs a poll of the other CLK level, so timing from the previous edge would save no poll.
   - A sample a fixed number of cycles after the launch edge would sit next to a MOSI change that may be skewed on silicon (PRDO1, 2 ns).
   - That fixed delay would also have to fit the shortest half period of any controller.
   - At the capture edge, MOSI has been stable for half a period.
3. **Per-byte totals now asserted (Minor).** `test_per_bit_cycles` asserts the steady data byte (47 cycles in mode 1, 40 in mode 0; mode 0 went from 39 to 40 because of the per-byte xor). It also asserts that no byte costs more than 48 cycles (8 bits at k=3). The asm header quotes the same numbers.
4. **Address constants (Minor).** `P_ZERO_BLK = 0x27F0` and `P_RX_DUMMY = 0x3800` are in pru_io/spi_link.py, with comments, next to P_RX.
5. **CLK-high abort comment (Minor).** The comment no longer claims CLK always returns low. If a controller raises /CS with CLK high, PRU1 sees the abort at the next rising-edge poll after CLK falls. If CLK never falls, PRU1 stays in that poll.

## Covering tests (new)
These tests drive PRU1's GPI directly, cycle by cycle, with no PRU0. tLEAD = tLAG = 2k, the /CS-high gap is 4k (2 tCLK), and the hop delay is 5.
- `test_min_cs_gap_no_frame_lost[k=3,6 × mode 0,1]`: WRITE, R/W, WRITE. All three RX blocks are right and the status is frames=3.
- `test_min_cs_gap_after_error_and_abort[k=3,6 × mode 0,1]`: error frame, then a frame aborted after 50 bits, then two WRITEs, each at the minimum gap. Status is frames=3, errors=1, aborts=1. Neither the error frame nor the aborted frame writes.
- RED: against the old firmware (HEAD 7da951a asm), 6 of 8 fail. Only k=6 mode 1 passed.

## Stimulus note
`set_gpi_word` called between instructions stamps the delayed-GPI history with PRU1's cycle at the end of the running instruction. A pin change during a multi-cycle instruction (`sbco` 16 B is 5 cycles) therefore lands late, and two CLK edges inside one stall merge into one.
- The reviewer's script uses this pattern. With the new firmware it still showed a slip in mode 1 at k=3, caused by this artifact.
- The test helper `_gpi_at` writes the true change cycle into the history instead. It asserts that no R31 read has looked at that cycle yet, so the result is exact.
- **Concern for the simulator:** the same late stamping may apply to wire-driven GPI on a follower core that is in a stall during `step_paced_many`. It does not affect the PRU0↔PRU1 tests (controller gaps ≥ 150 cycles, PRU1 in-frame stalls ≤ 3 cycles), but it is a modelling limit.

## Command and output
`pytest tests/test_spi_peripheral.py tests/test_spi_controller.py tests/test_spi_link_model.py -q` → `154 passed in 178.14s`. lint_pru.py: 0 errors, 0 warnings. Generator check: clean.

## New commit cost and measured minimum /CS gap
- Commit, from the /CS-high read to the first frame_idle check: **10 cycles** in both modes (was 22 in mode 1 and 26 in mode 0).
- Minimum /CS-high gap with no frame lost (tLEAD = tLAG = 1 tCLK, 3 frames, swept with the exact-stamp stimulus):

| | k=3 | k=6 |
|---|---|---|
| mode 1 | 9 cycles | 1 cycle |
| mode 0 | 5 cycles | 1 cycle |
| spec minimum (2 tCLK) | 12 | 24 |

  Mode 1 is limited by the 6-op byte-18 gather after the last capture.
- The miso_delay choices are unchanged and still inside their measured windows (`test_miso_window_contains_choice` passes).
