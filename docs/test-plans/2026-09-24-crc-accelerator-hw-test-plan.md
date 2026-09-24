# CRC Accelerator Hardware Test Plan

Sep 24, 2026 · @Thomas

## Scope

The pull request from `dev` stays closed until the CRC16/32 accelerator changes pass on AM243x/AM64x silicon. Everything so far is verified only in the pru-simulator, whose CRC model follows TRM SPRUIM2H §6.4.6.2.2.1, Table 6-429.

| Commit | Change | What silicon must confirm |
| --- | --- | --- |
| `f01aae7` | pif\_eth FCS moved to the CRC16/32 accelerator (`pif_eth_crc32_hw.inc`), used by all 4 firmware images | Correct FCS, safe register use, real cycle cost |
| `aaf7a72` | `crc_bitswap_example.asm`: bit-mirrored reads via R27 (byte flip) and R28 (32-bit flip) | Mirror values; reads of R27/R28 are non-destructive |
| `1f1604f` | The simulator's CRC accelerator model itself | Model behaviour matches silicon in every case below |

Out of scope: the 8b/10b line code, the perif TX/RX timing, and the 125 Mbaud ladder. Those are unchanged by this work and are covered by earlier reports.

## Setup

Run on an AM243x or AM64x ICSSG, with PRU0 as TX and PRU1 as RX, both at 250 MHz. That matches `config/memory_pif_eth_rx.cfg` in the simulator.

| Item | What to use |
| --- | --- |
| Branch | `dev` at `aaf7a72` or later |
| TX firmware | `pif_eth_tx.asm`, `pif_eth_tx_n2.asm`, `pif_eth_tx_n2_skipchecks.asm` (PRU0) |
| RX firmware | `pif_eth_rx_o1_raw.asm` (PRU1) |
| CRC routine | `source/pif_eth/pif_eth_crc32_hw.inc` (included by all 4) |
| Software reference | `source/pif_eth/pif_eth_crc32.inc`, and `zlib.crc32` on the host |
| Standalone examples | `source/crc_example.asm`, `source/crc_bitswap_example.asm` |
| Host reference values | `tests/test_pif_eth.py`, `tests/test_crc_bitswap_example.py` |

**Cycle measurement.** Enable the PRU cycle counter (PRU\_CTRL `CTR_EN`) and read `CYCLE` right before the `jal` into `crc32_compute` / `rx_crc_check` and again right after it returns. The difference is directly comparable with the simulator figures in the pass criteria below, which cover the same span.

**Result readout.** Halt the core, or have it spin, and read registers or DRAM through CCS or the debugger of your choice. DRAM0 addresses are in `source/pif_eth/README.md`.

**Open question:** the sources were written for the simulator's assembler. Confirm that they build unchanged with `clpru`, in particular `ldi32`, `zero`, `.set` and `.include`, before starting the test cases.

## Test cases

Run the cases in order. TC1–TC3 check the accelerator on its own, and TC4–TC7 check the pif\_eth firmware that uses it.

| ID | Case | Procedure | Expected |
| --- | --- | --- | --- |
| TC1 | Basic CRC-16 / CRC-32 | Run `crc_example.asm` to `halt` | `r10 = 0x0000EB93`, `r11 = 0xB12B5C1C` |
| TC2 | Bit-swap reads | Run `crc_bitswap_example.asm` to `halt` | `r10 = r13 = 0x8DD43A38` (R27), `r11 = r14 = 0x383AD48D` (R28), `r12 = r15 = 0xB12B5C1C` (R29) |
| TC3 | R27/R28 reads don't reset | Covered by TC2: R29 is read after R27 and R28 | `r12` is the real CRC, not the seed `0xFFFFFFFF` |
| TC4 | `crc32_core` contract and tail path | Build the `_CRC_HARNESS` program from `tests/test_pif_eth.py` with `pif_eth_crc32_hw.inc`. Run it for lengths 0, 1, 2, 3, 4, 5, 7, 60, 128 and 201, using the test's data pattern `(i*37+11) & 0xFF` | FCS at `0x0404` = `zlib.crc32(data)`; end pointer = `0x0500 + len`; `r28 = 0x1234`, `r29 = 0x5678` |
| TC5 | NOP margin before XIN | Repeat TC4 at lengths 4 and 7 with 2, 1 and 0 `NOP`s before each `XIN` | 2 and 1 pass; record whether 0 passes. Firmware keeps 2 |
| TC6 | Cycle cost | Read `CYCLE` around each CRC call (see Setup) for the payloads in the pass criteria | Within the pass-criteria bounds |
| TC7 | TX → RX end to end | PRU0 `pif_eth_tx_n2*.asm` into PRU1 `pif_eth_rx_o1_raw.asm` over the perif loopback, 100 BERT frames at each of n\_tx = 2, 4, 6, 8 | Every frame: `crc_ok = 1`, `symbol_errors = 0`, `prng_bit_errors = 0`, `rx_ovf = 0` |

Lengths 1, 2, 3, 5, 7 and 201 in TC4 exercise the byte-wide tail session. That path relies on a `CRC_SEED` write between two sessions of different write widths, which only the TRM text and the simulator support so far.

## Pass criteria

TC1–TC5 and TC7 must match exactly, with no tolerance. TC6 passes if each silicon count is no more than 2× the simulator value and is the same across frames of the same length.

| Routine | Payload | Simulator (hardware CRC) | TC6 limit | Old software CRC, simulator |
| --- | --- | --- | --- | --- |
| TX `crc32_compute` | 128 B (BERT) | 181 cycles | ≤ 362 | 9,488–9,552 |
| TX `crc32_compute` | 60 B (UDP) | 96 cycles | ≤ 192 | 4,456 |
| RX `rx_crc_check` | 128 B (BERT) | 187 cycles | ≤ 374 | 9,526–9,558 |
| TX / RX | 201 B | 283 / 289 cycles | ≤ 566 / 578 | 14,864 / 14,873 |

The simulator models DRAM `read_latency = 2` and one cycle per `XOUT`/`XIN`. Its word loop costs 5 cycles per 4 bytes: `LBBO` 1 + 2 stall, `XOUT` 1, `ADD` 1. A silicon count above that per word points to DRAM or broadside stalls the model lacks. Record the per-word figure, (count − 20) / (len / 4), so the model can be corrected.

Any mismatch in TC1–TC5 is a model bug as well as a firmware bug. Fix `xfr/crc_accelerator.py` first, then the firmware.

## Results and exit criteria

Open the pull request from `dev` once every row reads Pass. If a case fails, fix it on `dev`, re-run TC1–TC7, and put the silicon cycle counts into `source/pif_eth/PROJECT_REPORT.md` §9.3 next to the simulator figures.

| ID | Result | Measured | Board / date | Notes |
| --- | --- | --- | --- | --- |
| TC1 | Not run |  |  |  |
| TC2 | Not run |  |  |  |
| TC3 | Not run |  |  |  |
| TC4 | Not run |  |  |  |
| TC5 | Not run |  |  |  |
| TC6 | Not run |  |  |  |
| TC7 | Not run |  |  |  |
