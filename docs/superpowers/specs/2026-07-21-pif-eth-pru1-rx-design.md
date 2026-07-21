# pif_eth PRU1 RX over Loopback — Design Spec

**Date:** 2026-07-21
**Status:** Approved, not yet implemented
**Related:** [`2026-07-21-pif-eth-n2-125mbaud-design.md`](2026-07-21-pif-eth-n2-125mbaud-design.md)
(TX side), [`2026-07-20-pif-eth-8b10b-tx-design.md`](2026-07-20-pif-eth-8b10b-tx-design.md)

## 1. Goal

Add a receive path to `pif_eth`: PRU1 receives the 8b/10b line-coded Ethernet
frames that PRU0 transmits, over the existing PRU0→PRU1 perif loopback
(`perif/loopback.py`), with **no clock drift** (latency, jitter and drift all
zero). The RX FIFO runs at **2x the line rate** (250 MHz sample clock against
125 Mbaud) so each line bit is sampled twice.

How much of the reconstruction can happen in realtime is bounded by PRU1's
cycle budget. Three processing depths are specified; the deliverable includes a
**measured** table of the maximum line rate each one sustains.

## 2. Decisions

| Question | Decision |
|---|---|
| Scope | Implement all three options; characterize the max sustained rate of each |
| Post-frame processing | **In PRU1 firmware**, publishing frame + stats to DRAM1 |
| Integration | **Fully wired** — `rx_driver.py` + pytest coverage, running in CI |

## 3. What the RX hardware actually does

From `perif/perif_channel.py` (`rx_sample_edge` / `_capture_byte`):

- Samples the input line every RX clock period into an 8-bit shift register,
  newest sample in bit 0 (so reading a captured byte MSB→LSB gives the samples
  in chronological order).
- Waits for a **start bit**: the first sample equal to `sb_pol` arms capture.
  That start-bit sample is consumed by the detector and not counted.
- Thereafter captures the shift register into the FIFO every
  `sample_size + 1` samples. With `sample_size = 7`, that is **one FIFO byte
  per 8 samples**.
- The capture is the **raw shift register** — the hardware does *not* decimate
  oversampled bits. At 2x oversampling each captured byte therefore holds
  **8 raw samples = 4 line bits**.
- The RX FIFO is **4 bytes deep**; `rx_ovf` latches on overflow.

## 4. Clock plan and the oversample ladder

Both cores at 250 MHz (`pru_clock_mhz = pru1_clock_mhz = 250`). Loopback
channel 0 enabled with `latency_ns = jitter_ns = drift_ppm = 0`.

The perif derives both clocks from the core clock as
`clock = core / ((frac+1)·(div+1))`. Writing `n_tx` and `n_rx` for those
divisors, `TX baud = core/n_tx` and `RX sample = core/n_rx`. Exact 2x
oversampling requires `core/n_rx = 2·core/n_tx`, i.e.

> **`n_rx = n_tx / 2` — only *even* TX dividers permit exact 2x oversampling.**

This fixes the characterization ladder:

| `n_tx` | Line rate | `n_rx` | RX sample clock | Core cycles per captured byte |
|---|---|---|---|---|
| 2 | 125.00 Mbaud | 1 | 250.00 MHz | **8** |
| 4 | 62.50 Mbaud | 2 | 125.00 MHz | 16 |
| 6 | 41.67 Mbaud | 3 | 83.33 MHz | 24 |
| 8 | 31.25 Mbaud | 4 | 62.50 MHz | 32 |

Cycles per captured byte is `8 · n_rx` (8 samples per capture, `n_rx` core
cycles per sample). This is the **hard average throughput budget** for the
realtime service loop at each rung. The 4-deep FIFO provides only
`4 · 8 · n_rx` cycles of total slack — 32 cycles at `n_tx = 2`.

**RXCFG** (PRU1, `0x26100`): `sample_size = 7`, `sb_pol = 1`,
`clk_sel = 1` (core clock), `div_factor = n_rx - 1`, `frac = 0`.

At `n_tx = 2` the TX side must be `pif_eth_tx_n2_skipchecks.asm` (the only
variant that sustains n=2); at `n_tx ≥ 4` use `pif_eth_tx_n2.asm`, which keeps
all FIFO checks and self-adapts.

**This promotes both `pif_eth_tx_n2*.asm` files from standalone reference
artifacts to firmware exercised by CI.** Their hard constraints still hold and
must be enforced by `rx_driver.py`: `_skipchecks` is valid *only* at
`pru_clock_mhz = 250` with `n_tx = 2`, and silently corrupts data at any other
divider. The driver must select the TX variant from `n_tx` rather than letting
the caller pair them freely.

## 5. Two simplifications that make this tractable

### 5.1 Decimation is phase-insensitive at zero drift

With no drift, both samples of a bit are identical. Taking every second sample
from an *arbitrary* starting phase therefore still yields the correct bit
sequence: if sampling starts mid-bit, `s1, s3, s5, …` gives `b0, b1, b2, …`
exactly as `s0, s2, s4, …` does, because `s2 == s3` and so on.

Consequence: the firmware never needs to hunt for the sample phase within a
bit. Only the **symbol** boundary (mod 10) has to be recovered, by comma
correlation. This is what makes Option 2's fixed-shift unrolled extraction
possible.

This simplification is valid *only* because drift is zero. It must be revisited
before this design is reused with a non-zero `drift_ppm`.

### 5.2 SOF and EOF are cheap proxies, not comma correlation

True K28.5 correlation in realtime is not affordable — it needs a sliding
window across byte boundaries that are not symbol-aligned. Instead:

- **SOF** = the RX hardware's own start-bit detection. The line idles at 0
  (`tx_line_value()` returns 0 when `tx_out_en` is clear), so with `sb_pol = 1`
  the hardware auto-starts on the first 1 bit of the burst. The firmware just
  observes that capture has begun.
- **EOF** = **two or more consecutive all-zero captured bytes**. 8b/10b bounds
  the run length at 5 bits, which at 2x oversampling is at most 10 consecutive
  zero samples; two zero bytes are 16 zero samples and therefore cannot occur
  inside a valid frame. **One zero byte is not sufficient** and must not be
  used — a legal 5-bit zero run can produce one.

The RD− comma is `0011111010`, which begins with two zeros, so the hardware
start-bit can begin capture up to 2 bits into the first symbol. **Symbol
alignment must always be recovered by comma correlation, never assumed** from
the capture start.

## 6. Firmware variants

Three separate PRU1 programs under `source/pif_eth/`, sharing the DRAM1 map and
the post-frame routines.

### 6.1 Option 1 — `pif_eth_rx_o1_raw.asm`

Realtime work: SOF/EOF detection only; every captured byte is stored verbatim.
Steady-state loop, targeting the 8-cycle budget:

```
poll:   qbbc poll, r31, 24      ; wait ch0 rx_valid
        and  r4, r31, 0xFF      ; FIFO head
        mov  r31, r5            ; pop FIFO
        sbbo r4, r1, 0, 1       ; store raw oversample byte
        add  r1, r1, 1
        qbeq zero_run, r4, 0    ; all-zero byte -> candidate EOF
        jmp  poll
```

`zero_run` counts consecutive zero bytes and ends the burst at 2. All
decimation, alignment, decode, CRC and BER happen post-frame.

Capture sizing: a BERT frame is 132 octets → 1320 line bits → 330 captured
bytes; UDP is 64 octets → 640 line bits → 160 bytes. Six commas (leading, three
idle, two trailing) add 60 line bits → 15 bytes. Worst case ≈ 345 bytes.

**`payload_len` ceiling: 252 octets.** The post-frame decode stage writes
each decoded octet into the 256 B reconstructed-frame buffer at `0x0E00`
(`payload_len + 4`-byte FCS must fit), so `payload_len <= 252`.
`rx_driver.run_rx`/`build_sim` reject anything larger with `ValueError`
rather than let the firmware overrun into the stats/control blocks that
immediately follow. The realtime `poll`/`zero_run` loop above has no room in
its 8-cycle budget for a bounds check of its own, so `pf_symbol` (the
post-frame decode routine) carries a matching guard: on reaching the end of
the frame buffer it stops storing, sets `eof_status = 2` (overflow abort,
§8), and abandons the rest of that frame's octets rather than corrupting
what follows. The raw capture buffer (`0x0800`, 1024 B) has no equivalent
firmware guard for the same reason the realtime loop can't afford one; it is
sized generously enough (§ above) that the host-side `payload_len` check
keeps it from overrunning in practice, and `rx_driver` also rejects any
`payload_len` whose estimated capture size would exceed it.

### 6.2 Option 2 — `pif_eth_rx_o2_bits.asm`

Realtime work: decimate 2→1 and store packed 10-bit symbols. Each captured byte
yields 4 bits; `LCM(4, 10) = 20`, so a **5-byte unrolled phase cycle emits
exactly 2 symbols** with compile-time shift amounts — the same fixed-shift
technique used in the TX n2 rewrite, and legitimate here only because of §5.1.
Estimated 15–25 cycles/byte, i.e. expected to sustain `n_tx = 4` or 6.

### 6.3 Option 3 — `pif_eth_rx_o3_decode.asm`

Realtime work: Option 2 plus 8b/10b decode. Each completed 10-bit symbol is
looked up with `LBCO` from the decode LUT and the running disparity is checked.
Symbols complete every 2.5 captured bytes. Expected to sustain `n_tx = 6` or 8.

## 7. Decode LUT

**1024 entries × u16 = 2048 bytes**, a single table (not two RD sub-tables).

A 10-bit 8b/10b codeword identifies its octet **unambiguously without knowing
the running disparity**: codewords with disparity ±2 are legal in only one RD
context, and disparity-neutral codewords decode to the same octet in both. RD
is needed only to *validate* the stream, not to decode it.

Entry layout:

| Bits | Meaning |
|---|---|
| `[7:0]` | decoded octet |
| `[8]` | valid (1 = legal codeword) |
| `[9]` | disparity-neutral (1 = codeword disparity is 0, RD unchanged) |
| `[10]` | resulting RD when *not* neutral (0 = negative, 1 = positive) |
| `[11]` | is-comma (K28.5) |

Bit `[9]` is required: a disparity-neutral codeword leaves RD **unchanged**, so
there is no absolute "RD after this symbol" to store. The firmware updates RD
only when `[9] == 0`.

RD validation: a non-neutral codeword is legal only if it *flips* RD, so
`[9] == 0 && [10] == current_rd` is an RD violation and increments
`symbol_errors`.

Built host-side by a new `codec.build_dram1_decode_lut()`, validated in tests
against `codec.py`'s existing encoder as a round-trip bijection.

**Constant-table note (corrected 2026-07-21):** on AM243x ICSSG each PRU sees
its **own** DRAM at core-local `0x0000`, so PRU1 reaches DRAM1 through **`c24`**
at local `0x0000` — the memory mapping is swapped between the cores. The
simulator originally applied no per-core translation, which made PRU1 firmware
non-portable; that is fixed in `core/pru_core.py::_map_data_addr`, which swaps
the two 8 KB DRAM banks for PRU1 (`addr ^ 0x2000` below `0x4000`).

All firmware addresses in §8 are therefore **core-local**; host-side code
continues to use global addresses, where DRAM1 starts at `0x2000`.

## 8. DRAM1 memory map

DRAM1 is 8 KB. PRU1 addresses it at **core-local** `0x0000`–`0x1FFF`; the host
sees the same bytes at **global** `0x2000`–`0x3FFF`. Both columns below refer to
the same storage.

| Local (firmware) | Global (host) | Size | Contents |
|---|---|---|---|
| `0x0000` | `0x2000` | 2048 B | 8b/10b decode LUT, 1024 × u16 (`c24` offset 0) |
| `0x0800` | `0x2800` | 1024 B | raw oversample capture buffer (Option 1) |
| `0x0C00` | `0x2C00` | 512 B | packed symbol buffer (Options 2/3) |
| `0x0E00` | `0x2E00` | 256 B | reconstructed frame buffer (payload + FCS) |
| `0x0F00` | `0x2F00` | 64 B | stats block (below) |
| `0x0F40` | `0x2F40` | 64 B | control block (below) |

**Stats block** (local `0x0F00` / global `0x2F00`, all u32):

| Offset | Field |
|---|---|
| `+0x00` | `frame_counter` |
| `+0x04` | `captured_bytes` (this frame) |
| `+0x08` | `rx_ovf_count` |
| `+0x0C` | `symbol_errors` (invalid codewords / RD violations) |
| `+0x10` | `crc_ok` (1 = FCS matched) |
| `+0x14` | `prng_bit_errors` (BER numerator) |
| `+0x18` | `total_bits_checked` (BER denominator) |
| `+0x1C` | `eof_status` (0 = running, 1 = clean EOF, 2 = overflow abort) |

`eof_status = 2` is set by Option 1's `pf_symbol` overrun guard (§6.1) when
the reconstructed-frame buffer fills before decode finishes -- reachable
whenever `payload_len` is large enough to overrun it, and exercised directly
in `tests/test_pif_eth_rx.py::test_o1_overrun_sets_eof_status_2_and_preserves_control_block`.

**Control block** (local `0x0F40` / global `0x2F40`, all u32):

| Offset | Field |
|---|---|
| `+0x00` | `mode` (0 = PRNG/BERT, 1 = preloaded) |
| `+0x04` | `seed` |
| `+0x08` | `payload_len` |
| `+0x0C` | `go` flag (host→firmware) |
| `+0x10` | `rxcfg` — the RXCFG word the firmware writes at startup |

`rxcfg` is host-supplied rather than hardcoded because `n_rx` changes at every
rung of the ladder (§4); the firmware must not need reassembling per rate.

## 9. Post-frame processing (PRU1 firmware)

Runs after EOF, off the critical path:

1. **Comma-align** — correlate for K28.5 to recover the symbol boundary.
2. **Decimate** 2→1 (Option 1 only; Options 2/3 already did this).
3. **LUT decode** (Options 1/2 only; Option 3 already did this), accumulating
   `symbol_errors`.
4. Strip commas, extract the payload into the frame buffer at `0x2E00`.
5. **CRC32** over payload+FCS using the bit-serial reflected-`0xEDB88320`
   routine reused verbatim from `pif_eth_tx.asm`; set `crc_ok`.
6. **BER check** (BERT mode) — regenerate the payload with the same xorshift32
   PRNG and seed, compare bitwise, accumulate `prng_bit_errors` and
   `total_bits_checked`.
7. Publish stats, increment `frame_counter`, clear `go`, return to polling.

## 10. Host driver

New `source/pif_eth/rx_driver.py`:

- Seeds the decode LUT and control block into DRAM1, TX LUT/control into DRAM0.
- Loads the chosen TX variant on `pru0` and the chosen RX variant on `pru1`.
- Enables loopback channel 0 with zero latency/jitter/drift.
- Drives the pair with `sim.step_paced("pru0", "pru1", …)`, which keeps PRU1's
  perif clock behind PRU0's so RX only samples line history TX has recorded.
- Reads the DRAM1 stats block and cross-checks it against the Python golden
  reference (`codec`, `crc32`, `prng`) — the firmware's own verdict is never
  the sole authority.
- Emits the rate-characterization table over the (option, `n_tx`) matrix.

## 11. Testing

Extends `tests/test_pif_eth.py`.

**Pure Python (no simulator)**
- decode LUT round-trips against `codec.py` for all 256 octets in both RD
  contexts; invalid codewords flagged invalid.
- decimation helper is phase-insensitive (§5.1) over random bit sequences.
- comma-alignment helper recovers the boundary from an arbitrary offset.

**Firmware on the simulator**
- PRU1 self-configures GPCFG1 + RXCFG correctly for a given `n_rx`.
- SOF fires on burst start; EOF fires on a 2-zero-byte run and **not** on a
  single zero byte produced by a legal 5-bit run.
- Per option, at its **rated divider**: `crc_ok = 1`, `symbol_errors = 0`,
  `prng_bit_errors = 0`, `rx_ovf_count = 0` over a multi-frame run, for both
  BERT and UDP.
- Ladder characterization across (option, `n_tx`) as a parametrized test.

"Rated divider" is the smallest `n_tx` at which an option is *measured* to run
clean. The ladder test determines it; the value is then recorded as a constant
per option in `rx_driver.py`, and the per-option regression test above pins
that constant. Order of work therefore matters: characterize first, pin second.

## 12. Validation requirements and risks

1. **No sustained-rate claim may rest on a hand cycle-tally.** Every entry in
   the rate table is confirmed by single-stepping the simulator and asserting
   `rx_ovf == False` — the TX n2 work established that a loop can pass a
   hand-count and still lose a 2-cycle race in practice.
2. **Option 1's 8-cycle loop is genuinely marginal** (~7 instructions plus
   `sbbo` write latency). It may not reach `n_tx = 2`; that is an acceptable
   measured outcome, not a failure of the design.
3. Options 2 and 3 are *expected* not to reach `n_tx = 2`. The ladder exists so
   this is quantified rather than assumed.
4. The 4-deep FIFO gives only 32 cycles of slack at `n_tx = 2`; any jitter in
   the service loop beyond that overflows.
5. §5.1 and §5.2 both depend on zero drift and on 8b/10b's 5-bit run bound.
   Both must be re-derived before reuse under drift.

## 13. Out of scope

- Non-zero drift/jitter/latency on the loopback (the drift demo covers that
  separately for the simple pattern firmware).
- Striping across perif channels 1 and 2.
- Broadside CRC accelerator (still a TX-side future item).
- Replacing `pif_eth_tx.asm` as the production TX firmware.

## 14. Measured results (Task 8, 2026-07-21)

`python3 source/pif_eth/rx_driver.py o1` runs `characterize("o1")`: every
ladder rung (`n_tx = 2, 4, 6, 8`), each swept over 7 seeds
(`DEFAULT_SEED, 1, 2, 3, 4, 5, 6`), `num_frames=2` per run. Full raw output:

```
 opt  n_tx       seed    Mbaud   ovf  symerr  biterr  crc  result
  o1     2  464371934   125.00     0       0       0    1  PASS
  o1     2          1   125.00     0       0       0    1  PASS
  o1     2          2   125.00     0       0       0    1  PASS
  o1     2          3   125.00     0       0       0    1  PASS
  o1     2          4   125.00     0       0       0    1  PASS
  o1     2          5   125.00     0       0       0    1  PASS
  o1     2          6   125.00     0       0       0    1  PASS
  o1     4  464371934    62.50     0       0       0    1  PASS
  o1     4          1    62.50     0       0       0    1  PASS
  o1     4          2    62.50     0       0       0    1  PASS
  o1     4          3    62.50     0       0       0    1  PASS
  o1     4          4    62.50     0       0       0    1  PASS
  o1     4          5    62.50     0       0       0    1  PASS
  o1     4          6    62.50     0       0       0    1  PASS
  o1     6  464371934    41.67     0       0       0    1  PASS
  o1     6          1    41.67     0       0       0    1  PASS
  o1     6          2    41.67     0       0       0    1  PASS
  o1     6          3    41.67     0       0       0    1  PASS
  o1     6          4    41.67     0       0       0    1  PASS
  o1     6          5    41.67     0       0       0    1  PASS
  o1     6          6    41.67     0       0       0    1  PASS
  o1     8  464371934    31.25     0       0       0    1  PASS
  o1     8          1    31.25     0       0       0    1  PASS
  o1     8          2    31.25     0       0       0    1  PASS
  o1     8          3    31.25     0       0       0    1  PASS
  o1     8          4    31.25     0       0       0    1  PASS
  o1     8          5    31.25     0       0       0    1  PASS
  o1     8          6    31.25     0       0       0    1  PASS

n_tx=2: PASS (all 7 seeds)
n_tx=4: PASS (all 7 seeds)
n_tx=6: PASS (all 7 seeds)
n_tx=8: PASS (all 7 seeds)
```

All 28 (rung × seed) combinations pass: `rx_ovf=0`, `symbol_errors=0`,
`prng_bit_errors=0`, `crc_ok=1`, and the reconstructed frame in DRAM1 matches
the expected PRNG+FCS payload byte-for-byte. No row triggered the anchor-risk
flag (`crc_ok=0` with `symbol_errors=0`), so no phase-shifted false-comma
lock-on was observed across any of the 28 runs.

Contrary to §12.2's expectation that `n_tx=2` was "genuinely marginal" and
"may not reach" full rate, the measured result is that Option 1's 7-instruction
realtime service loop **does** sustain full line rate (125.00 Mbaud, 8 core
cycles per captured FIFO byte at 250 MHz) with zero FIFO overflows across
every seed tried. It does so at **zero headroom**: 7 instructions plus a
1-cycle DRAM write stall on the `sbbo` exactly fills the 8-cycle budget, with
none of the 4-deep FIFO's 32 cycles of total slack to spare if that per-byte
cost grows even by one cycle. The result depends on this simulator's modelled
timing for DRAM1 -- `config/memory_pif_eth_rx.cfg` sets `write_latency = 1`
and `jitter = 0`; adding either back would drop this rung to FAIL. This is a
simulator measurement of the modeled realtime loop's cycle cost against the
modeled perif timing, not a claim about real silicon.

**Rated divider:** `RATED_DIVIDER["o1"] = 2` — the smallest (fastest) rung on
the ladder, since it is the smallest `n_tx` measured to pass cleanly. Pinned
by `test_o1_clean_at_rated_divider` in `tests/test_pif_eth_rx.py`.

**Harness bug found and fixed during characterization:** the initial
`characterize()` draft (matching the plan's literal code) compared the
firmware's reconstructed frame at every rung against
`prng_bytes(BERT_PAYLOAD_LEN, seed)` — the payload of *frame 0* of a burst —
regardless of `num_frames`. With `num_frames=2`, DRAM1's `FRAME_ADDR` holds
the *last* received frame, and the BERT PRNG runs continuously across a
multi-frame burst (the same convention already established in
`driver.py`'s `expected_stream`), so frame 1's payload is
`prng_bytes(BERT_PAYLOAD_LEN * 2, seed)[128:256]`, not
`prng_bytes(BERT_PAYLOAD_LEN, seed)`. The unfixed comparison produced a
uniform FAIL across all four rungs while `ovf/symerr/biterr` were all clean
and `crc_ok=1` — a harness defect in the oracle, not an RX firmware failure.
Fixed in `rx_driver.characterize()` by slicing the correct final-frame window
out of the whole-burst PRNG stream before comparing.
