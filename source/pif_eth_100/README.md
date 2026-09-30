# pif_eth_100 — 100 Mbaud 8b/10b BERT TX (PRU0) + RX (PRU1) at 300 MHz

`pif_eth_100` is a variant of `source/pif_eth` (125 Mbaud at n = 2, 250 MHz) that runs the
same 8b/10b transmitter at n = 3 on a 300 MHz core clock, giving 100 Mbaud. PRU0 sends
200-byte BERT frames (xorshift32 payload) plus a 4-byte CRC-32 FCS computed on the CRC16/32
accelerator. PRU1 receives them over the perif loopback with a **fractional RX divider of
1.5**, which gives exactly 2x oversampling (5.000 ns samples for 10.000 ns bits), and checks
the CRC and the bit-error count against the same PRNG.

All results below are **simulator results, not silicon claims**.

* Design spec: [`docs/superpowers/specs/2026-09-30-pif-eth-100-design.md`](../../docs/superpowers/specs/2026-09-30-pif-eth-100-design.md) (section 17 addendum wins)
* Browser-UI walkthrough: [`USERS_GUIDE.md`](USERS_GUIDE.md)
* Development report: `DEVELOPMENT_REPORT.md` (not written yet; it is a later task of the plan)

## Clock plan

| Quantity | Value | Source |
|---|---|---|
| PRU0, PRU1 core clock | 300 MHz, T_core = 3.333 ns | `config/memory_pif_eth_100.cfg` |
| TX divider n_tx | 3 (div_factor 2, frac 0) | TXCFG |
| TX bit clock | 100.000 MHz, **T_bit = 10.000 ns** | 300 / 3 |
| RX divider | 1.5 (div_factor 0, frac 1) | RXCFG |
| RX sample clock | 200.000 MHz, **T_s = 5.000 ns**, exactly 2x | 300 / 1.5 |
| Core cycles per 10-bit symbol (TX) | 30 | 10 x 3 |
| Core cycles per TX FIFO byte | 24 | 8 x 3 |
| Samples per RX capture byte | 8 (sample_size = 7) | RXCFG |
| Core cycles per RX capture byte | **12** | 8 x 1.5 |

* **TXCFG (PRU0, `0x260E4`) = `0x00020010`**: div_factor = 2 (bits 31:16), frac = 0 (bit 15), clk_sel = 1 (bit 4, core clock).
* **RXCFG (PRU1, `0x26100`) = `0x0000801F`**: div_factor = 0, frac = 1 (bit 15), clk_sel = 1 (bit 4), sb_pol = 1 (bit 3), sample_size = 7 (bits 2:0).

Why not `n_rx = n_tx / 2`? The `pif_eth` rule uses integer dividers only. At n_tx = 3 it
would need n_rx = 1.5, which an integer divider cannot express. The FRAC bit adds a half
step (`n = (div_factor + 1) + 0.5 * frac`), so div_factor = 0 with frac = 1 gives 1.5.
`pif_eth/rx_driver.rxcfg_word()` has no frac bit, so `run_100.py` uses the literal word.

## Files

| File | Purpose |
|---|---|
| `README.md` | This file. |
| `USERS_GUIDE.md` | Step-by-step guide to run the TX + RX demo in the browser UI. |
| `pif_eth_100_tx.asm` | PRU0 firmware: BERT frame TX at n = 3, HW-CRC32 FCS, all FIFO-full checks kept from `pif_eth_tx_n2.asm`. |
| `pif_eth_100_rx.asm` | PRU1 firmware: realtime raw capture at 2x (Option 1), post-frame decimate + 8b/10b decode + CRC + BER. The proven baseline. |
| `pif_eth_100_rx_fast.asm` | PRU1 firmware: optional optimised RX. Copy of the baseline with a fast post-frame decode (see Optimised RX). |
| `pif_eth_crc32_hw.inc` | `crc32_core` on the ICSSG CRC16/32 accelerator (shared by TX and RX). |
| `run_100.py` | Headless driver: traced paced stepper, TX-only and loopback runs, seed sweep, throughput figures F1/F2/F3. |
| `seed_ui_100.py` | Client for the UI server: seed, arm and status of the shared simulator. |
| `ui_walkthrough_100.py` | Scripted execution of `USERS_GUIDE.md` over the UI server's HTTP/WebSocket endpoints. |
| `spike_frac_rx.py` | Task 0 spike: proves the 300 MHz clock plan and the fractional divider 1.5 with the unmodified `pif_eth` firmware. |
| `codec.py` | 8b/10b golden reference and decode LUT builder. |
| `decoder.py` | 8b/10b bit-stream decoder (golden). |
| `crc32.py` | Ethernet CRC-32 golden reference (`zlib.crc32`). |
| `prng.py` | xorshift32 payload golden reference. |
| `frames.py` | BERT frame builder (200 B payload + 4 B FCS). |
| `rx_reference.py` | Host-side reference for the RX path (expand, decimate, comma alignment, decode). |
| `__init__.py` | Package marker. |

Related files outside the folder: `config/memory_pif_eth_100.cfg` (300 MHz, DRAM timing) and
`tests/test_pif_eth_100.py` (the tests).

## Run it

Run from the repo root.

```bash
python3 source/pif_eth_100/run_100.py            # TX-only check, 9-run loopback sweep, throughput figures
python3 -m pytest tests/test_pif_eth_100.py      # unit and firmware tests
python3 source/pif_eth_100/spike_frac_rx.py      # Task 0 spike (clock plan + fractional divider)
```

To run the demo in the browser UI, follow [`USERS_GUIDE.md`](USERS_GUIDE.md). Its steps were
validated by a **scripted** walkthrough (`ui_walkthrough_100.py`, 2026-09-30, 2 frames, PASS)
that drives the same server endpoints the buttons use. A click-through in a real browser was
**not** performed (the browser automation was not connected).

## Measured results (simulator, 2026-09-30)

Output of `python3 source/pif_eth_100/run_100.py` at commit `e5f5444`, verbatim:

```text
TX only (seed 464371934, 4 back-to-back frames): PASS  period=10.000 ns  min transition spacing=10.000 ns  on 10 ns grid=True  burst_pushed=[263, 263, 263, 263]

  rx       seed  lat_ns frm  ovf symerr biterr crc eof  cap fifo  hot  post_cyc  result
base  464371934   0.833   3    0      0      0   1   1  526    1    8     44469  PASS
base  464371934   4.167   3    0      0      0   1   1  526    1    8     44469  PASS
base  464371934   7.500   3    0      0      0   1   1  526    1    8     44469  PASS
base          1   0.833   3    0      0      0   1   1  526    1    8     44481  PASS
base          2   0.833   3    0      0      0   1   1  526    1    8     44457  PASS
base          3   0.833   3    0      0      0   1   1  526    1    8     44462  PASS
base          4   0.833   3    0      0      0   1   1  526    1    8     44508  PASS
base          5   0.833   3    0      0      0   1   1  526    1    8     44470  PASS
base          6   0.833   3    0      0      0   1   1  526    1    8     44445  PASS

Throughput [base RX]  (simulator figures, not silicon)
  F1  line rate                  100.00 Mbaud
  F2  in-burst data rate          80.00 Mbit/s  (8b/10b coding rate)
  F2p in-burst payload rate       76.05 Mbit/s  (200 B per 21.04 us burst)
  F3  goodput, TX-limited         53.18 Mbit/s  (gap 9.05 us)
  F3  goodput, end-to-end          8.97 Mbit/s  (gap 157.35 us)
  T_burst 21040.0 ns   RX post-frame 44469 cycles = 148.2 us   host poll bound <= 160 ns

OVERALL: PASS
```

The Task 0 spike (`python3 source/pif_eth_100/spike_frac_rx.py`, same date and commit) ends
with `SPIKE PASS: 23/23 checks, base latency 0.0 ns`.

| Figure | Value | What it means |
|---|---|---|
| **F1 line rate** | 100.00 Mbaud | Smallest transition spacing on PRU0 ch0 is 10.000 ns, all on the 10 ns grid. |
| **F2 in-burst data rate** | 80.00 Mbit/s | 100 Mbaud x 8/10, the 8b/10b coding rate. This is the requested "80 Mbit/s net". Inside a burst the line has no gaps (T_burst = 21040 ns = 263 FIFO bytes x 8 x 10 ns). F2p = 76.05 Mbit/s is the payload alone within a burst (200 B per 21.04 us): commas, FCS and pad take the rest. |
| **F3 goodput, TX-limited** | 53.18 Mbit/s (gap 9.05 us) | Payload bits per frame start-to-start with TX running alone. The 9.05 us between bursts is PRU0 preparation (PRNG fill, CRC). |
| **F3 goodput, end-to-end** | 8.97 Mbit/s (gap 157.35 us) with the baseline RX; 19.48 Mbit/s with the optimised RX (see Optimised RX) | With RX in the loop, frame i+1 may only start after PRU1 has finished decoding frame i and re-armed. |

The end-to-end figure is far below 80 Mbit/s because it is bounded by RX Option 1: the
receiver captures in real time but decodes **after** the frame, and that post-frame work
takes 44469 cycles = 148.2 us in this run (about seven times the 21.04 us burst). The
100 Mbaud line itself is not the limit. F3 is the figure to quote for sustained throughput;
F2 describes only the time the burst is on the wire.

## Latency note

The loopback wire delay used by the harness (`run_100.LOOPBACK_LATENCY_NS`) is
**5/6 ns = 0.8333 ns**, not 0 ns. This was decided at the Task 5 second latency gate.

At 0 ns delay the edge-phase test failed at the +10/3 ns shift: `symbol_errors=71`,
`crc_ok=0`, `bit_err=547`, `cap_bytes=525` (one test failed, 34 passed). With a 5/6 ns base
delay all three edge phases (shifts 0, 10/3, 20/3 ns) decode clean, and so did seeds 1, 2, 3
across the three shifts and 2 frames each (9 of 9). Evidence:
`.superpowers/sdd/2026-09-30-pif-eth-100/task-5-report.md`.

**Known limit:** RX decode is not robust when a sample edge coincides exactly with a TX
edge, which is what happens at 0 ns wire delay in one of the three arm phases. The cause is
that the 300 MHz core period (10/3 ns) is inexact in floating point, so exact ties resolve
inconsistently once an inexact shift is added. The harness therefore uses a 5/6 ns wire
delay, which keeps every phase at least 0.83 ns from an edge. Whether a given single-frame
run at 0 ns fails depends on the arm phase: the UI's paced flow passed one frame at 0.0 ns
(session scratchpad `pif100_latency_probe.txt`, not committed), so that run is not evidence that 0 ns is safe.

## Memory maps

**DRAM0** (global address = PRU0 local address):

| Address | Size | Contents |
|---|---|---|
| `0x0000` | 1024 B | 8b/10b encode LUT, 256 x u32 |
| `0x0400` | 4 | num_frames |
| `0x0404` | 4 | mode (0 = PRNG) |
| `0x0408` | 4 | seed |
| `0x040C` | 4 | payload_len (200) |
| `0x0410` | 4 | frame_counter |
| `0x0414` | 4 | burst_pushed (263 per frame) |
| `0x0418` | 4 | go flag |
| `0x0500` | 204 B | core buffer: 200 B payload, then FCS at `0x05C8` |

**DRAM1** (host/global addresses; firmware local = global - `0x2000`). Unchanged from `pif_eth`:

| Global | Local | Size | Contents |
|---|---|---|---|
| `0x2000` | `0x0000` | 2048 B | 8b/10b decode LUT, 1024 x u16 |
| `0x2800` | `0x0800` | 1024 B | raw 2x capture (about 526 B used) |
| `0x2C00` | `0x0C00` | 512 B | **unused by the baseline RX**; only the fast RX builds its 256 B decimation LUT here (on the first frame's post-frame) |
| `0x2E00` | `0x0E00` | 256 B | reconstructed frame, 200 B payload + 4 B FCS |
| `0x2F00` | `0x0F00` | 64 B (32 used) | stats: +0 frames, +4 cap_bytes, +8 rx_ovf, +0xC symbol_errors, +0x10 crc_ok, +0x14 bit_err, +0x18 tot_bits (1600), +0x1C eof_status |
| `0x2F40` | `0x0F40` | 64 B (20 used) | control: +0 mode (0), +4 seed, +8 payload_len (200), +0xC go, +0x10 rxcfg (`0x0000801F`) |

Both cores read their control blocks once, at boot, so seed the DRAM before the first Run.

## Caveats

* **Simulator only.** No number here has been measured on silicon.
* The simulator assumes DRAM `write_latency = 1` and `jitter = 0`.
* The simulator samples at an even 5.000 ns. Silicon's fractional divider at 1.5 may space
  sample edges unevenly (1 and 2 core clocks alternating; not verified against the TRM).
  That would still give 2 samples per bit at zero drift, but with less edge margin
  (spec section 13, R6).
* Edge-tie finding (spec section 3.5): at 300 MHz the sample-to-edge offset is one of
  {0, 1.667, 3.333} ns, so in one third of arm phases samples land exactly on a TX edge. See
  the latency note above for how that was handled and what limit remains.

## Optimised RX (pif_eth_100_rx_fast.asm)

`pif_eth_100_rx_fast.asm` is a copy of the baseline RX with a faster post-frame decode; the
realtime capture loop (`poll` to `eof`) is byte-identical to the baseline's (checked with
`diff`). On the first frame's post-frame it builds a 256 B 2:1 decimation LUT at local
`0x0C00`; after the first comma anchors the symbol grid it decodes 4 capture bytes per load
and 4 line bits per LUT lookup instead of visiting the 8 samples of each byte one at a time.

Measured in the simulator at 300 MHz (`python3 source/pif_eth_100/run_100.py --rx both`,
seed 464371934, 3 frames, loopback latency 0.8333 ns; PRU1 cycles from the traced,
single-stepped eof to frame_loop span; 1 cycle = 3.333 ns):

| Quantity | Baseline RX | Fast RX |
|---|---|---|
| Post-frame, 3-frame mean (includes the one-off LUT build) | 44 469 cycles = 148.2 us | 14 905 cycles = 49.7 us |
| Post-frame, per frame | 44 484 / 44 444 / 44 480 | 17 652 / 13 513 / 13 549 |
| Post-frame, steady state (frames 2-3) | about 44 460 cycles | about 13 531 cycles = 45.1 us |
| F3 goodput, end-to-end, 3 frames | 8.97 Mbit/s (gap 157.35 us) | 19.48 Mbit/s (gap 61.09 us) |
| F3 goodput, end-to-end, steady state (frames 2-5 of a 5-frame run) | 8.97 Mbit/s | 21.25 Mbit/s |
| F3 goodput, TX-limited | 53.18 Mbit/s | 53.18 Mbit/s (unchanged) |
| Realtime loop, cycles per captured byte | 8 | 8 |

The one-off LUT build cost the first frame 17 652 - 13 531 = about 4.1 k cycles. The 5-frame
steady-state figures come from a separate `run_loopback(..., num_frames=5)` run (post-frame
per frame 17 652, 13 513, 13 549, 13 537, 13 513 for the fast RX; the 3-frame table rows are
the `run_100` output).

**Equivalence.** The fast RX consumes the same bit sequence with the same 10-bit tiling from
the same comma anchor, with the same decode, running-disparity and frame-buffer-guard
semantics. Tests (`tests/test_pif_eth_100.py`, all with `rx="fast"`): stats and frame bytes
are equal to the baseline's (`FrameStats` compared field by field) for the default seed over
3 frames, for seeds 464371934 and 1 at all three edge phases, and for payload lengths 128 and
252; zero BER, `crc_ok = 1`, `rx_ovf = 0`, `symbol_errors = 0`; the realtime loop keeps
8 cycles per byte and a maximum RX FIFO depth of at most 2; the LUT is empty at boot and
equals the expected table after the first frame; the UI flow with both go flags set before
either core runs passes; the post-frame is at least 2x faster and F3 end-to-end at least
1.5x higher than the baseline's.

The baseline `pif_eth_100_rx.asm` remains the proven reference and is unchanged. Everything
above is a simulator result, not measured on silicon.
