# Handoff — TC7 on AM243x silicon: n=2 symbol errors (2026-10-09)

Snapshot of an **unfinished** hardware debug session. Agent memory is per-PC;
this note travels with the repo. Test plan:
[`docs/test-plans/2026-09-24-crc-accelerator-hw-test-plan.md`](../test-plans/2026-09-24-crc-accelerator-hw-test-plan.md)
(TC7 row still reads "Not run" — do not update it until n=2 passes).

## Status

| Rung | Result on AM243x LP (ICSSG0, PRU0 TX → PRU1 RX, 250 MHz) |
|---|---|
| n_tx = 4, 6, 8 (`pif_eth_tx_n2.asm`) | **PASS** — 100/100 frames, bit_errors 0, symbol_errors 0 |
| n_tx = 2 (`pif_eth_tx_n2_skipchecks.asm`) | **FAIL** — 48/100 ok. Every failing frame has *only* `symbol_errors=1`; crc_ok, prng_bit_errors, eof_status, tot_bits and the frame-buffer compare are all clean |

TC7 requires `symbol_errors = 0`, so n=2 fails as written.

## Findings (n=2)

* The all-checks `pif_eth_tx_n2.asm` deadlocks at n=2 on silicon, exactly as
  `source/pif_eth/PROJECT_REPORT.md` §8.3 says (TX frame counter stays 0). Use
  skipchecks for n=2 only. Run the n=2 rung **last** and power-cycle first: a
  deadlocked n=2 run left the TX channel stuck for later rungs.
* Capture of failing vs good frames (`cap_bytes` 347 vs 346). Decoding the tail
  bytes against the two comma codes (FA = `0011111010`, 305 = `1100000101`):
  the **final 4 bits of every burst are missing** — the byte `flush_pad` pushes
  (1380 bits/frame ⇒ last byte holds 4 data bits). Which comma is truncated
  depends on running disparity at end of payload (~50/50), and only one order
  leaves ≥10 leftover bits for the RX decoder to flag as an invalid symbol.
* Head bytes are identical in all frames (`FF C0 FF CC F0 03`), so RX start
  phase is not the issue.
* The simulator does **not** reproduce it: 0 symbol errors, and its TX FIFO sits
  at level 3 on almost every push (never near empty).
* Cause on silicon is **unknown**. Not yet distinguished: (a) hardware never
  serialises the last pushed byte, (b) an underrun/race in the tail path
  (`tc1`/`tc2`/`flush_pad`), (c) something n=2-specific in end-of-burst handling.

## Next steps

1. Rebuild the R5F project, power-cycle, re-run. The n=2 diagnostic lines now
   also print `tx_pushed` (T_BURST). Expect **173** for a full burst. 173 ⇒ PRU0
   pushed everything and hardware dropped the last byte; 172 ⇒ the push itself
   went wrong.
2. Firmware experiment (needs the PRU0 swap cycle below): append 1–2 zero bytes
   after `flush_pad` in the n=2 TX tail. Fixes it only if silicon always drops
   the last pushed byte; if it doesn't help, the cause is earlier in the tail.
3. Alternative: make the RX tail tolerant (don't count a trailing all-zero
   partial symbol after the last comma) — decide first whether `symbol_errors`
   is meant to be a real link metric here.

## Files

* `source/pif_eth/r5f/tc7_empty_example.c` — current R5F driver (copy of the CCS
  project's `empty_example.c`). `enc_lut.h` / `dec_lut.h` sit next to it;
  `source/pif_eth/gen_enc_lut.py` regenerates `enc_lut.h`.
* CCS workspace `~/workspace_ccstheia/` (local to the PC that ran the test):
  * `empty_am243x-lp_r5fss0-0_freertos_ti-arm-clang` — driver; `example.syscfg`
    has `coreClk=250 MHz` and PruGPIO PRU0 GPO0/1/2 + PRU1 GPI13.
  * `..._icss_g0_pru0_fw_ti-pru-cgt/main.asm` — `pif_eth_tx_n2.asm` with
    `pif_eth_crc32_hw.inc` inlined and a `main:` entry label.
  * `..._icss_g0_pru1_fw_ti-pru-cgt/main.asm` — `pif_eth_rx_o1_raw.asm`, same.
  * Rebuild a PRU main.asm: replace the `.include "pif_eth_crc32_hw.inc"` line
    by the file's contents, prepend `.retain/.retainrefs/.global main/
    .sect ".text"/main:`.
* The driver loads `PRU0Firmware_0` (tx_n2) for n=4/6/8 and `PRU0FirmwareSkip_0`
  from `pru0_skip_load_bin.h` for n=2. To regenerate the skip header: put the
  skipchecks source in the PRU0 project's main.asm, build in CCS, copy
  `pru0_load_bin.h` to the R5F project as `pru0_skip_load_bin.h` with the array
  renamed `PRU0FirmwareSkip_0`, restore the tx_n2 main.asm, rebuild PRU0.
  Use a **quoted** include for that header (angle brackets don't search the
  project dir).

## Gotchas

* clpru reserves `c0`–`c31`; labels `c1/c2/c3` in the TX firmware failed to
  assemble. Renamed to `tx_c1..tx_c3` in `4463d7b` (labels only).
* `G_MUX_EN` (ICSSG_SA_MX_REG / CFG+0x40 bit 7) is **not** set by the PRU
  firmware; the driver sets it via `PRUICSS_setSaMuxMode(..., SD_ENDAT)` and
  asserts the read-back.
* Loopback is a physical patch cable: PRU0 perif_out → PRU1 perif_in.
* The workspace `CLAUDE.md` forbids building CCS projects or editing `.syscfg`
  from Bash/Write; builds and SysConfig changes go through the user or the CCS
  MCP servers.
* `source/program.asm` is a scratch file; ignore its diff.
