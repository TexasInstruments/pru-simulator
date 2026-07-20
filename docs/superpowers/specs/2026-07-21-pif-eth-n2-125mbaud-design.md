# pif_eth n=2 (125 Mbaud) TX Firmware — Design Note

**Date:** 2026-07-21
**Status:** Experimental — not integrated into `pif_eth_tx.asm` / `driver.py` / test suite

## 1. Goal

Push the `pif_eth` PRU0 TX firmware (`source/pif_eth/pif_eth_tx.asm`) from its
documented 25 MHz line rate (TXCFG div=7 @ 200 MHz core) toward the
architecture's 125 Mbaud target, using the higher core clock speeds added by
the [[pru-core-speed-selector]] work. 125 Mbaud requires divider n=2 at a
250 MHz core clock (`n = (frac+1)*(div_factor+1)`, `bit_clock = core_clock/n`,
`perif/perif_channel.py`).

## 2. Files

- **`source/pif_eth/pif_eth_tx_n2.asm`** — `pif_eth_tx.asm` with TXCFG set to
  div=1 (n=2) and `send_frame`'s bit-packing rewritten: 4-octet batched
  `LBBO` loads (core_len assumed a multiple of 4 — true for both BERT=132 and
  UDP=68), and fixed-shift unrolled packing (the bit-remainder cycles
  0→2→4→6→0 deterministically every 4 pushes, so the shift amount at each
  push is a compile-time constant, not a runtime `nbits`-tracked value).
  Applied to **both** the data loop and the leading/trailing K28.5 commas —
  the commas still used the old `push_symbol` initially, and were the first
  thing to underrun (they can't be skipped: fixing only the data loop but
  leaving slow commas still starves the FIFO right at burst start). All 5
  FIFO-full checks (`and r6,r31,0x1C; qbeq ...,r6,0x10`) present.
- **`source/pif_eth/pif_eth_tx_n2_skipchecks.asm`** — identical, except the
  FIFO-full check is removed on the data loop's phase 0 and phase 1 (the two
  single-byte-emit phases); phase 2 (single-emit) and phase 3 (the
  double-byte-emit) keep their checks.

## 3. Results (measured in-simulator, `Simulator(config_path=...)` with
`pru_clock_mhz=250`, BERT + UDP frames via the `codec`/`decoder` golden path)

| Firmware | n | Bit rate | Result |
|---|---|---|---|
| `pif_eth_tx_n2.asm` (all checks) | 2 | 125.00 MHz | **FAIL** — underrun, then deadlock (FSM goes idle after the premature `_finish_frame()`, later pushes spin forever waiting for a drain that never resumes) |
| `pif_eth_tx_n2.asm` (all checks) | 3–8 | 83.33–31.25 MHz | **PASS**, BER=0 at every step |
| `pif_eth_tx_n2_skipchecks.asm` | 2 | 125.00 MHz | **PASS**, BER=0 over 100 BERT + 100 UDP frames |
| `pif_eth_tx_n2_skipchecks.asm` | 3 | 83.33 MHz | **FAIL** — BER≈0.68, `tx_overrun=True` |
| `pif_eth_tx_n2_skipchecks.asm` | 4 | 62.50 MHz | **FAIL** — BER≈0.68, `tx_overrun=True` |

## 4. The n=2 underrun, and why removing 2 checks fixes it

At n=2 the perif channel drains the TX FIFO deterministically every
`8*n = 16` core cycles/byte (exact integer, no jitter — same-clock sampling,
`perif/perif_channel.py`). The fully-checked, fully-unrolled loop's
production cost is right at that boundary (hand-analysis put it at ~15.6
avg / 16.0 worst-case cycles/byte); single-stepped in the simulator, the
actual failure is a **2-cycle race**: the FIFO-full check passes (not full),
but the drain's next bit-edge — landing deterministically in the gap between
the check and the following `mov r30.b0,r5` — pops the FIFO before the byte
lands, finds it empty, and ends the burst early. Removing the check on
phases 0 and 1 saves 2 cycles per push at exactly the two points that
mattered, closing that gap.

## 5. Critical caveat — this is not a general speedup, it is a single-divider hack

**The FIFO-full check is not just a safety margin — it is the mechanism that
makes the firmware's push rate automatically track whatever the configured
drain rate is.** Removing it fixes the firmware at a constant production
rate tuned to *just barely* match n=2. The moment the drain is slower
(n=3, n=4 — both tested), nothing throttles the firmware anymore, and it
pushes into an already-full FIFO every few bytes: `tx_overrun=True`,
~68% BER, frames silently truncated to a fraction of their expected length.

**`pif_eth_tx_n2_skipchecks.asm` must only ever be run with `pru_clock_mhz =
250` and TXCFG div_factor = 1 (n=2).** If `memory.cfg`'s `pru_clock_mhz` or
the firmware's TXCFG divider ever changes, this firmware will not error or
hang loudly — it will **silently produce wrong data** (overrun, not a crash)
unless re-validated at the new clock/divider combination first.

## 6. Recommendation

- For a **robust** speedup that stays correct across clock/divider changes,
  use `pif_eth_tx_n2.asm` (all checks) at n≥3 — a genuine ~4x improvement
  over the original 25 MHz design (83.33 MHz @ n=3), with the same
  self-adapting safety margin the original design relies on.
- `pif_eth_tx_n2_skipchecks.asm` is kept as a validated, working example of
  hitting the full 125 Mbaud architectural target — but it is hard-pinned to
  n=2 and should be treated as a one-off, not a drop-in replacement for
  `pif_eth_tx.asm`.
- Neither file is wired into `driver.py` or `tests/test_pif_eth.py`; both are
  standalone artifacts under `source/pif_eth/` for reference/future work.
