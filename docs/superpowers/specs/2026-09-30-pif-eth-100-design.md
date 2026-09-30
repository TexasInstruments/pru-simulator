# pif_eth_100 — 100 Mbaud 8b/10b TX+RX at 300 MHz (n_tx = 3) — Design Spec

**Date:** 2026-09-30
**Status:** Approved 2026-09-30, with the user decisions in
**§17 Addendum** (RX post-frame optimisation, loopback latency,
existing-file touches, branch/commit policy). The plan is
`docs/superpowers/plans/2026-09-30-pif-eth-100.md`.
**Related:** [`2026-07-21-pif-eth-n2-125mbaud-design.md`](2026-07-21-pif-eth-n2-125mbaud-design.md) (TX n2 loop),
[`2026-07-21-pif-eth-pru1-rx-design.md`](2026-07-21-pif-eth-pru1-rx-design.md) (RX Option 1),
[`2026-07-20-pru-core-speed-selector-design.md`](2026-07-20-pru-core-speed-selector-design.md),
`docs/handoff/2026-07-22-pif-eth-rx-o1.md`

All figures in this document are **simulator figures**. Nothing here is a
silicon claim. Hand-tallied cycle counts are marked *est.*; they are
hypotheses until the simulator confirms them (project rule, see §13 R2).

## 1. Purpose and intent

**What the user asked for (verbatim):** "the pif_eth project in the source
folder goes up to 125 Mbit with n=2. I need another variant with 8b/10b line
coder CRC32 ussing the hardware widget and 200 bytes frame with 80 Mbit net
data rate and 100 Mbit line rate. PRU frequency should be 300 MHz to make N=3.
Use superpower planing with opus 5.5 and implementation with Sonnet 5.5. Put
the project in a separate folder e.g. pif_eth_100. Document whole development
in a report including prompt, interaction, tokens, models, time spent. Also
generate a users guide on how to run the receive and transmitter in the UI."

**How this spec reads it (decided with the user, not re-opened here):**

| Request term | Interpretation |
|---|---|
| 100 Mbit line rate | 100 Mbaud: TXCFG n = 3 at a 300 MHz core, 10 ns per line bit |
| 8b/10b line coder | Same IBM/ANSI 8b/10b and DRAM0 LUT as `pif_eth` |
| CRC32 on the hardware widget | FCS on the ICSSG CRC16/32 broadside accelerator (`pif_eth_crc32_hw.inc`, copied) |
| 200 bytes frame | BERT frame: 200 B xorshift32 payload + 4 B FCS = **204 octets** before 8b/10b, bracketed by K28.5 commas |
| 80 Mbit net data rate | **8b/10b coding rate**: 100 Mbaud × 8/10 = 80 Mbit/s of octets while a burst is on the wire. Commas, FCS and the inter-frame gap lower the frame-averaged goodput. That figure is measured and reported next to the other two (§8). It is not hidden. |
| receive and transmitter | PRU0 transmits, PRU1 receives over the existing zero-drift perif loopback (channel 0) |
| separate folder | `source/pif_eth_100/`, self-contained. `source/pif_eth/` is not modified. |

## 2. Scope and non-goals

**In scope:** a BERT-only TX (PRU0) and RX (PRU1) pair at 100 Mbaud. The
package holds its own Python golden models, a headless loopback driver with
throughput measurement, a UI seeding script, tests, README, a user's guide, a
development report, and PDFs of the guide and the report.

**Non-goals (YAGNI):**
- No UDP frame and no pcap/Wireshark path. RX only implements BERT, same as `pif_eth`.
- No striping across perif channels 1 and 2.
- No non-zero drift or jitter on the loopback.
- No RX Option 2/3 (realtime decode).
- No work to raise the frame-averaged goodput. The deliverable measures it;
  it does not optimise it. §15 lists candidate improvements as future work.
  **Superseded in part by §17.1:** a separate, post-baseline RX post-frame
  optimisation (`pif_eth_100_rx_fast.asm`) is now in scope.
- No `_skipchecks`-style TX. The all-checks loop self-adapts and has headroom at n = 3.
- No change to any existing file: `source/pif_eth/*`, `memory.cfg`,
  `readme.md`, simulator or UI code. New files only. **Amended by §17.3:**
  a changelog entry in the top-level `readme.md` and a new handoff note
  are allowed. `memory.cfg` may only change transiently in the UI run and
  must be restored.

## 3. Corrections to the brief (and to older docs)

These were checked against the code.

1. **Divider formula.** Older docs (`2026-07-21-pif-eth-pru1-rx-design.md`
   §4 and the n2 spec §1) state `n = (frac+1)·(div_factor+1)`. That is stale.
   The model is `perif/perif_channel.py::_divider`:
   `n = (div_factor + 1) + 0.5·frac`, so `FRAC` adds a **half step**. The
   brief is right: div_factor = 0 with frac = 1 gives 1.5. RXCFG FRAC is
   bit 15 and DIV_FACTOR is bits 31:16 (`perif/perif_registers.py`).
2. **The fallback decoder is not new work.** The brief says 3x oversampling
   "needs a new 3x symbol-grid decoder". In fact
   `rx_reference.decimate(samples, factor)` and
   `decode_capture(raw, oversample=…)` already take the ratio. The firmware
   change is a mod-3 sample selector in `pf_bit` in place of the XOR toggle.
   The EOF rule still holds at 3x: two zero capture bytes are 16 zero
   samples, and the longest legal run is 5 bits = 15 samples. The fallback
   still has **zero headroom**: 8 cycles per captured byte against an
   8-cycle loop.
3. **Capture region.** The brief says "0x0800..0x0E00". The Option-1 capture
   buffer is `0x0800–0x0BFF` (1024 B). `0x0C00–0x0DFF` is the unused
   Option-2/3 symbol buffer. About 528 B are needed at 2x (§7.4), so the
   buffer fits without using the symbol region.
4. **TX risk is lower than implied.** The n = 3 budget in core cycles
   (24 per FIFO byte) is the same at 250 and 300 MHz. `pif_eth_tx_n2.asm`
   (all checks) was already measured clean at n = 3 on 250 MHz with the same
   DRAM latency config. At 300 MHz only the wall-clock scale changes.
5. **Sampling on TX edges (not in the brief).** At 300 MHz the core period is
   10/3 ns, which is not exact in floating point. The RX sample grid is set
   by PRU1's arm time and the TX edges by PRU0's go time, both multiples of
   10/3 ns. The sample-to-edge offset is therefore (Δcycles · 10/3) mod 5 ns,
   which is one of {0, 1.667, 3.333} ns. In one third of the arm phases,
   samples land **exactly on a TX edge** (a float tie in `tx_line_at`'s
   `tt <= t`). A float probe (6000 random phases, 2200-bit frames) found the
   tie always resolved the same way within a frame, so decimation stays
   correct. This rests on float-rounding behaviour, so the spike must cover
   all three residues by construction (§13, S0.4).
6. **UI speed selector side effects.** The 300 MHz dropdown exists
   (`ALLOWED_CLOCK_MHZ = {200,225,250,300,333}`). It **rewrites the tracked
   root `memory.cfg`** and **rebuilds the Simulator**, which drops loaded
   programs, the loopback and DRAM contents. It must be the first UI step,
   and `memory.cfg` must be restored afterwards.
7. **UI Open-Project gotcha (not in the brief).** `app.js::openProject`
   concatenates tabs **only when a `project.json` manifest exists**. So
   `source/pif_eth_100/` must **not** get a `project.json`. Without one,
   each file loads alone, and the tab path `pif_eth_100/<file>.asm` makes
   the server add `source/pif_eth_100/` to the include path, which
   `.include "pif_eth_crc32_hw.inc"` needs. A file opened through the OS
   file picker has no folder in its path, so the `.include` fails. The
   guide must use the in-UI source browser.
8. **Models on record.** The session transcript shows the orchestrating
   session running `claude-sonnet-5-5`, with this planning subagent on Opus
   5.5. The development report takes model IDs from the transcripts, not
   from the brief.

## 4. Architecture and files

```
PRU0 (300 MHz)                                     PRU1 (300 MHz)
prng_fill 200 B ─▶ crc32_core (XFR 1) ─▶ FCS        go_wait ─▶ arm RX ch0
     └─▶ 8b/10b LUT (DRAM0) ─▶ fixed-shift pack        │ realtime: poll/zrun, store raw bytes
          ─▶ ch0 TX FIFO (n=3, 10 ns/bit) ══loopback══▶ ch0 RX (div 1.5, 5 ns/sample, 2x)
                                                       │ EOF (2 zero bytes)
                                                       └▶ post_frame: decimate, align, LUT decode,
                                                          crc32_core, BER ─▶ DRAM1 stats
```

New files (all new; nothing existing is edited):

| Path | Origin | Purpose |
|---|---|---|
| `source/pif_eth_100/__init__.py` | new | package marker, one-line docstring |
| `source/pif_eth_100/pif_eth_100_tx.asm` | copy of `pif_eth/pif_eth_tx_n2.asm` | PRU0 TX, TXCFG n = 3 (§6) |
| `source/pif_eth_100/pif_eth_100_rx.asm` | copy of `pif_eth/pif_eth_rx_o1_raw.asm` | PRU1 RX Option 1, RXCFG div 1.5 (§7) |
| `source/pif_eth_100/pif_eth_crc32_hw.inc` | verbatim copy | shared `crc32_core` (the `.include` resolves in the same folder) |
| `source/pif_eth_100/codec.py` | verbatim copy | 8b/10b encode/decode, DRAM0 LUT, DRAM1 decode LUT |
| `source/pif_eth_100/crc32.py`, `prng.py` | verbatim copies | FCS and xorshift32 references |
| `source/pif_eth_100/frames.py` | copy, trimmed | `BERT_PAYLOAD_LEN = 200`, `build_bert_frame` only (UDP removed) |
| `source/pif_eth_100/decoder.py`, `rx_reference.py` | verbatim copies | bit/symbol decode, capture → frames (relative imports stay valid) |
| `source/pif_eth_100/run_100.py` | new, modelled on `pif_eth/rx_driver.py` | headless TX→RX loopback, seed sweep, stats, throughput (§10.1) |
| `source/pif_eth_100/seed_ui_100.py` | new, modelled on `pif_eth/seed_ui.py` | arms both cores in the browser UI (§10.2) |
| `source/pif_eth_100/spike_frac_rx.py` | new | Task 0 spike, kept as evidence (§13) |
| `source/pif_eth_100/session_stats.py` | new | transcript → tokens/models/time tables for the report (§12.3) |
| `source/pif_eth_100/build_pdfs.py` | new | markdown-it-py + headless Chrome → PDFs (§12.4) |
| `source/pif_eth_100/README.md` | new | project README (§12.1) |
| `source/pif_eth_100/USERS_GUIDE.md` / `.pdf` | new | step-by-step UI guide (§12.2) |
| `source/pif_eth_100/DEVELOPMENT_REPORT.md` / `.pdf` | new | prompt, interaction, models, tokens, time, results (§12.3) |
| `source/pif_eth_100/pif_eth_100_rx_fast.asm` | copy of `pif_eth_100_rx.asm` + fast decode | optimised RX, §17.1 (built after the baseline) |
| `source/pif_eth_100/ui_walkthrough_100.py` | new | scripted run of the user-guide steps over the UI server's HTTP/WS endpoints (§17.4) |
| `config/memory_pif_eth_100.cfg` | copy of `config/memory_pif_eth_rx.cfg` | both cores at 300 MHz (§9) |
| `tests/test_pif_eth_100.py` | new | §11 |

Not copied: `pcap.py`, `driver.py`, `gen_dec_lut.py`, `dec_lut.h`,
`pif_eth_crc32.inc` (the software CRC), `pif_eth_tx.asm`, `_skipchecks`.

Imports: scripts put `<root>` and `<root>/source` on `sys.path`, the same way
`rx_driver.py` does, and import `from pif_eth_100 import …`. Nothing under
`source/pif_eth_100/` may import `pif_eth`. The one exception is the Task 0
spike (§13): it *reads* (never edits) `source/pif_eth/pif_eth_tx_n2.asm` and
`pif_eth_rx_o1_raw.asm` to test the clock plan before the new firmware
exists. Its Python models come from the `pif_eth_100` copies, which are
therefore made in Task 0.

## 5. Clock plan and register words

| Quantity | Value | Source |
|---|---|---|
| PRU0, PRU1 core clock | 300 MHz, T_core = 3.333 ns | `config/memory_pif_eth_100.cfg` |
| TX divider n_tx | 3 (div_factor 2, frac 0) | TXCFG |
| TX bit clock | 100.000 MHz, **T_bit = 10.000 ns** | 300 / 3 |
| RX divider | 1.5 (div_factor 0, frac 1) | RXCFG |
| RX sample clock | 200.000 MHz, **T_s = 5.000 ns**, exactly 2x | 300 / 1.5 |
| Core cycles per 10-bit symbol (TX) | 30 | 10 × 3 |
| Core cycles per TX FIFO byte | 24 | 8 × 3 |
| Samples per RX capture byte | 8 (sample_size = 7) | RXCFG |
| Core cycles per RX capture byte | **12** | 8 × 1.5 |

**TXCFG (PRU0, `0x260E4`) = `0x00020010`**: div_factor = 2 (bits 31:16),
frac = 0 (bit 15), clk_sel = 1 (bit 4, core clock).

**RXCFG (PRU1, `0x26100`) = `0x0000801F`**: div_factor = 0, frac = 1
(bit 15), clk_sel = 1 (bit 4), sb_pol = 1 (bit 3), sample_size = 7
(bits 2:0).

The `pif_eth` rule `n_rx = n_tx / 2` (integer dividers only) cannot give
exact 2x oversampling at n_tx = 3. The half step is what makes 2x possible
here. `pif_eth/rx_driver.rxcfg_word()` cannot express it (no frac bit), so
`run_100` uses the literal word.

`CH0CFG0 = 0` on PRU0 (continuous TX mode) and GPCFG0/1 mux_sel = 1, as in
`pif_eth`.

Float exactness: 300/3 = 100.0 and 300/1.5 = 200.0, so both perif periods
are exact (10.0 ns and 5.0 ns). Only the core period 10/3 ns is inexact
(see §3.5).

## 6. TX design (`pif_eth_100_tx.asm`, PRU0)

**Changes from `pif_eth/pif_eth_tx_n2.asm`.** Everything else stays
byte-for-byte, to keep the validated timing.
1. A new header comment: the 100 Mbaud variant, TXCFG n = 3, 300 MHz, BERT
   200 B, and a pointer to this spec.
2. The self-config TXCFG write becomes `ldi r0, 0x0010` / `ldi r0.w2, 0x0002`,
   giving `0x00020010`.
3. All five FIFO-full checks stay in place, which keeps the push rate
   self-adapting.

Nothing else changes. The DRAM0 map, go-flag handshake, `prng_fill`,
`crc32_compute` → `crc32_core`, the 4 leading and 2 trailing commas,
fixed-shift packing, `flush_pad` and `drain_wait` all stay as they are.
Precondition: `core_len % 4 == 0`, and 204 = 4 × 51 holds.
`crc32_core` sees 200 B = 50 words and takes no byte-tail path.

**DRAM0 map (unchanged, global = PRU0 local):** LUT `0x0000` (256 × u32),
`num_frames 0x0400`, `mode 0x0404` (0 = PRNG), `seed 0x0408`,
`payload_len 0x040C` (= 200), `frame_counter 0x0410`, `burst_pushed 0x0414`,
`go 0x0418`, core buffer `0x0500–0x05CB` (200 B payload, then FCS at
`0x05C8`).

**Burst composition:**

| Part | Symbols | Line bits | FIFO bytes |
|---|---|---|---|
| Leading commas (c1 before go, c2–c4) | 4 | 40 | 5 |
| Payload + FCS | 204 | 2040 | 255 (51 groups × 5) |
| Trailing commas | 2 | 20 | 2 (+ 4 bits left over) |
| `flush_pad` | — | 4 (zero pad) | 1 |
| **Total** | **210** | **2104** | **263** (`burst_pushed`) |

**Cycle budget (FIFO byte = 24 core cycles).** The n2 spec hand tally
(*est.*) puts the data loop at ≈ 15.6 cycles per FIFO byte on average and
16.0 at worst, which leaves ≈ 8 cycles of headroom per byte (≈ 33 %). The
FIFO holds 4 bytes, so 96 cycles of slack on top. Continuous mode ends the
burst if the FIFO is empty at a byte boundary. Running dry would therefore
show up as a short burst and decode failures, not as a flag. The pass
criterion is `T_burst == 263 × 8 × 10 ns` (§8) plus a clean decode.

## 7. RX design (`pif_eth_100_rx.asm`, PRU1)

### 7.1 Changes from `pif_eth/pif_eth_rx_o1_raw.asm`
1. A new header: 100 Mbaud, 2x oversampling with the fractional divider,
   the 12-cycle budget, and a pointer to this spec.
2. **Default RXCFG.** After `lbbo &r0, r2, 16, 4` (the control-block
   `rxcfg`), a zero word is replaced by `0x0000801F` before the `sbbo` to
   `0x26100` (for example `qbne` past an `ldi r0, 0x801F`). The implementer
   must confirm by test that the upper half ends up 0. The host still
   writes `0x0000801F` explicitly. The default only exists so a forgotten
   seed fails visibly on data, not on clock config.
3. Nothing else changes: the 7-instruction `poll`/`zrun` hot path,
   2-zero-byte EOF, `captured_bytes = ptr − (base+1)`, `post_frame`
   (XOR-toggle 2→1 decimation, bit-sliding first-comma anchor, LUT decode,
   RD check), `pf_symbol`'s frame-buffer guard (`eof_status = 2`),
   `rx_crc_check` (`crc32_core`) and `rx_ber_check` (PRNG state kept across
   frames).

The `base+1` length quirk was "verified empirically for 60–128 octets" in
its original comment. A 200 B payload is outside that range, but it is
already covered by `tests/test_pif_eth_rx.py::test_o1_clean_at_payload_len_200`
(n_tx = 8). The new tests re-check it at the new rate.

### 7.2 Realtime budget

| Path | Instructions | Cycles (*est.*) | Budget | Headroom |
|---|---|---|---|---|
| `poll` byte (qbbc, and, mov r31, sbbo, add, qbeq, jmp) | 7 | 8 (sbbo has +1 write stall; `write_latency = 1`) | 12 | 4 cycles (33 %) |
| `zrun` byte | 7 | 8 | 12 | 4 |
| FIFO slack (4 deep) | — | — | 48 cycles | — |

The 125 Mbaud rung passed with this loop at zero headroom (8 of 8 cycles).
Here the loop gets 12 cycles. This is still a claim near a limit, so §13 R2
requires simulator confirmation. The loop cost comes from traced single
steps, and the maximum RX FIFO occupancy is recorded (*expected* ≤ 1).

### 7.3 SOF/EOF and phase
- SOF: hardware start bit (sb_pol = 1) on the first `1` sample. The RD−
  K28.5 `0011111010` starts with two zeros, so capture begins about 2 bits
  in. Alignment comes from comma search, never from the capture start.
- EOF: two consecutive all-zero capture bytes = 16 zero samples = 8 line
  bits, which is more than 8b/10b's 5-bit maximum run.
- Decimation is phase-insensitive at zero drift: every bit gets exactly 2
  samples, including in the edge-tie case (§3.5), provided the tie
  resolves the same way throughout. S0.4 checks this.

### 7.4 Capture sizing and the DRAM1 map

Samples ≈ (2104 − 2) × 2 = 4204, which is ≈ 526 capture bytes, plus 2 EOF
bytes ≈ **528 B** (`cap_bytes` reports one fewer). Host estimate, kept from
`rx_driver`: `(payload_len + 4 + 6) × 10 × 2 / 8 = 525` for 200 B. The limit
is 1024 B. The binding limit on `payload_len` stays the 256 B frame buffer,
so `payload_len ≤ 252`. `run_100` also requires `payload_len % 4 == 0`
(the TX precondition). The default is 200.

The DRAM1 map is **unchanged** from `pif_eth`, so the UI Memory-panel
addresses are the same. Firmware addresses are core-local; host addresses
are global (DRAM1 base `0x2000`).

| Local | Global | Size | Contents |
|---|---|---|---|
| `0x0000` | `0x2000` | 2048 B | 8b/10b decode LUT, 1024 × u16 (`c24`) |
| `0x0800` | `0x2800` | 1024 B | raw oversample capture (≈ 528 B used) |
| `0x0C00` | `0x2C00` | 512 B | unused by the baseline RX; the fast RX (§17.1) builds its 256 B decimation LUT at `0x0C00–0x0CFF` |
| `0x0E00` | `0x2E00` | 256 B | reconstructed frame, 200 B payload + 4 B FCS |
| `0x0F00` | `0x2F00` | 64 B (32 used) | stats: +0 frames, +4 cap_bytes, +8 rx_ovf, +C symbol_errors, +10 crc_ok, +14 prng_bit_errors, +18 tot_bits (= 1600), +1C eof_status |
| `0x0F40` | `0x2F40` | 64 B (20 used) | control: +0 mode (0), +4 seed, +8 payload_len (200), +C go, +10 rxcfg (`0x0000801F`) |

## 8. Frame timeline and the three throughput figures

Definitions: L = payload_len = 200 B, N_push = `burst_pushed` = 263,
T_bit = 10 ns. `t_go[i]` is the rising edge and `t_end[i]` the falling edge
of PRU0 ch0 `tx_out_en` for burst i. They come from
`channels[0].tx_out_en_transitions`, which is exact ns and uncapped.

**Per-frame timeline, TX-limited (*est.*, except where fixed by construction):**

| Phase | Core cycles | Time |
|---|---|---|
| hs_wait exit, flag clear | ≈ 8 | 0.03 µs |
| `prng_fill` 200 B (11 instr + 1 write stall per octet) | ≈ 2 400 | 8.0 µs |
| `crc32_compute` 50 words × 5 + ≈ 25 setup | ≈ 275 | 0.9 µs |
| c1 setup, `tx_go` | ≈ 12 | 0.04 µs |
| **Burst on the wire** (263 B × 24) | **6 312 (exact)** | **21.04 µs** |
| RX realtime capture (overlaps the burst) | ≈ 528 × 12 | ≈ 21.1 µs |
| RX `post_frame` (decimate ≈ 18 cyc/bit × 2104, decode, CRC ≈ 280, BER ≈ 3.4 k) | ≈ 40–50 k | ≈ 135–170 µs |

**F1: line rate.** F1 = 1 / T_bit. It is measured as the smallest spacing
between transitions in PRU0 ch0 `tx_transitions` for one burst (the comma's
`…010` gives isolated bits). All spacings must be integer multiples of
10.000 ns within 1e-6 ns. The check is `tx_clock_period_ns() == 10.0`.
**Expected: 100.00 Mbaud.**

**F2: in-burst data rate (8b/10b coding rate).**
F2 = F1 × 8/10 = **80.00 Mbit/s**. It applies to every symbol on the wire,
payload, FCS and commas alike. Measurement confirms the burst had no gaps:
`T_burst = t_end − t_go` must equal `N_push × 8 × T_bit = 21 040 ns`
(± 1e-6). Then F2 = 8 × (N_push × 8 / 10) / T_burst, which reports 80.00
for a gapless burst. Also reported:
**F2p: in-burst payload rate** = 8·L / T_burst = 1600 / 21.04 µs =
**76.05 Mbit/s**. The 4.9 % gap to F2 is 6 commas, 4 FCS octets and 4 pad
bits.

**F3: frame-averaged goodput.**
F3 = 8·L·(N−1) / (t_go[N−1] − t_go[0]). This is start-to-start over N ≥ 3
frames and includes every gap. It is reported for two regimes:
- **F3-TX (TX-limited).** TX runs alone, with PRU1 not loaded. The host sets
  `go` back to 1 as soon as TX clears it, during the running burst, so
  every frame starts with no host latency. Each burst is decoded from the
  line history (the `driver.py::_capture_burst` pattern) before the next
  burst overwrites it: `tx_transitions` is capped at 4096 entries, about
  1.9 bursts. *Est.* 1600 / (21.04 + ≈ 9.0) µs ≈ **53 Mbit/s**.
- **F3-E2E (TX→RX, RX-gated).** Frame i+1 may only start after PRU1 has
  finished `post_frame` for frame i and re-armed; otherwise the frame is
  lost. When `S_FRAMES == i+1`, the host writes `C_GO = 1` and then
  `T_GOFLAG = 1`, polling at ≤ 16 lead instructions. That bounds host
  latency to ≤ 16 PRU0 instructions (≈ 0.1 µs); report the bound.
  *Est.* 1600 / (21.04 + ≈ 150 + ≈ 9.0) µs ≈ **9 Mbit/s**. RX Option 1's
  post-frame decode dominates.

`run_100.py` prints all figures, plus the breakdown behind them: T_burst,
TX prep (PRU0 cycles from leaving `hs_wait` to `tx_go`), RX post-frame
(PRU1 cycles from label `eof` back to `go_wait`, read from the traced
steps), and the host-latency bound. The README and the report state
plainly that "80 Mbit/s" is F2 and that F3-E2E is far lower, with the
reason.

## 9. Configuration and UI interaction

`config/memory_pif_eth_100.cfg` = `config/memory_pif_eth_rx.cfg` with
`pru_clock_mhz = 300` and `pru1_clock_mhz = 300`. All memory regions and
latencies stay the same (DRAM `read_latency = 2`, `write_latency = 1`,
`jitter = 0`). `iep_clock_mhz` and `uart_clock_mhz` keep their defaults and
are unused here. `run_100.py` and the tests pass this file by absolute
path. They **never** use or modify the root `memory.cfg`, which on this
branch is the 250 MHz RX config and is the default for other tests.

The UI server always loads the root `memory.cfg`
(`ui/server.py: config_path`). There are two ways to reach 300 MHz, and
both rewrite that tracked file:
- (a) back up `memory.cfg`, copy `config/memory_pif_eth_100.cfg` over it,
  then start the server; or
- (b) with the server running, pick **300 MHz** in the controls-bar
  dropdown (`PUT /config/clock_speed`). This patches both clock keys and
  **rebuilds the Simulator**, so it must come before any Load, loopback or
  seed step. It also needs DRAM1 to exist in the current `memory.cfg`
  (true on this branch).

The guide documents (a) as primary and (b) as the alternative, and always
ends with restoring `memory.cfg`.

## 10. Drivers

### 10.1 `run_100.py`
Constants: `CONFIG_PATH`, `TXCFG_100 = 0x00020010`, `RXCFG_100 = 0x0000801F`,
`N_TX = 3`, `RX_DIV = 1.5`, `PAYLOAD_LEN = 200`, `LOOPBACK_LATENCY_NS`
(default 0.0, final value set by the spike, §13), the DRAM0/DRAM1 addresses
from §6/§7.4, `SEEDS = (DEFAULT_SEED, 1, 2, 3, 4, 5, 6)`.

API (names are binding for the tests):
- `validate_payload_len(n)`: raises `ValueError` unless `4 ≤ n ≤ 252` and
  `n % 4 == 0`.
- `build_sim(seed, num_frames, payload_len, latency_ns) -> Simulator`: seeds
  the DRAM0 LUT and control block, the DRAM1 decode LUT, control and zeroed
  stats; gpcfg both cores; enables loopback ch0 (`latency_ns`, jitter 0,
  drift 0); loads TX and RX; runs the prologues (`step("pru0", 60)`,
  `step("pru1", 20)`). **No host TXCFG override**: the firmware is the
  source of truth.
- `run_loopback(seed, num_frames, payload_len, latency_ns, trace=False) -> LoopResult`:
  runs the E2E regime (§8). It collects per-frame stats (frames, cap_bytes,
  rx_ovf, symbol_errors, crc_ok, bit_err, tot_bits, eof_status), compares
  the frame buffer with the expected slice of the whole-burst PRNG stream
  (`prng_bytes(L·N)[(N−1)L : NL] + fcs`, the harness lesson from the RX
  spec §14), records `t_go`/`t_end`, and with `trace=True` also records
  max RX FIFO occupancy, per-byte hot-loop cycles and RX post-frame cycles.
- `_step_paced_traced(sim, n)`: a local mirror of `Simulator.step_paced`
  (same loop, same `guard_ns = 20`). It records `(pc, cycles, len(rx_fifo))`
  after every PRU1 instruction. Labels come from
  `sim.cores["pru1"]._parser.labels`; the implementer must confirm whether
  those are instruction indices comparable to `pc`. A test checks that
  traced and untraced runs give identical stats.
- `run_tx_only(seed, num_frames) -> TxResult`: runs the F3-TX regime and
  decodes each burst with the package's `decoder`.
- `throughput(loop: LoopResult, tx: TxResult) -> dict`: returns F1, F2,
  F2p, F3-TX, F3-E2E and the breakdown.
- `main()`: runs the seed sweep × 3 frames, prints a per-row table (seed,
  frames, ovf, symerr, biterr, crc, eof, cap_bytes, max_rx_fifo, PASS/FAIL,
  anchor-risk flag when `crc_ok = 0` and `symerr = 0`), then the
  throughput block. Exits non-zero on any FAIL. CLI flags: `--seeds`,
  `--frames`, `--latency-ns`.

### 10.2 `seed_ui_100.py`
Subcommands: `seed` (the default), `arm`, `status`. Options: `--ws`
(default `ws://localhost:8080/ws`) and `--http` (default
`http://localhost:8080`).
- `seed`: `GET /config/clock_speed` must return `{"mhz": 300}`; if not,
  abort with a message pointing to guide step 1. Enable loopback ch0 through
  the WS action `perif_loopback` (`latency_ns = LOOPBACK_LATENCY_NS`).
  Write the DRAM0 LUT and control block (`num_frames = 100`, `mode = 0`,
  seed, `payload_len = 200`, counters 0), then the DRAM1 decode LUT,
  control block (with `rxcfg = 0x0000801F`) and zeroed stats. Write
  `T_GOFLAG = 1` and then `C_GO = 1` last. Must run after Load on both cores
  and before the first Run, because both firmwares read their control
  blocks once at boot.
- `arm`: writes only `T_GOFLAG = 1` and `C_GO = 1`, to arm the next frame.
- `status`: reads the stats block via WS `read_memory` and prints it with
  PASS/FAIL against `crc_ok = 1`, `rx_ovf = 0`, `symbol_errors = 0`,
  `bit_err = 0`, `tot_bits = 1600`, `eof_status = 1`.
- The write list is built by a pure function, `build_writes() -> list[(addr, bytes)]`,
  so it can be tested without a server.

## 11. Tests (`tests/test_pif_eth_100.py`)

**Pure Python:**
- Register words: `TXCFG_100 == 0x00020010` and `RXCFG_100 == 0x0000801F`.
  Written into a `PerifRegisters`, they give div/frac/clk_sel/sb_pol/
  sample_size as in §5, and `PerifChannel` periods of 10.0 and 5.0 ns at a
  300 MHz core.
- Frame geometry: 204 octets, 210 symbols, 2104 bits, 263 FIFO bytes (build
  it with the copied `codec`); FCS equals `zlib.crc32`.
- Copied golden models: 8b/10b round-trip for all octets and both RD; DRAM1
  decode LUT properties; `rx_reference.decode_capture` at every skew for
  `oversample=2`, and also `oversample=3` if the fallback is taken.
- `validate_payload_len`: 200 and 252 accepted; 253, 202 (not % 4) and 300
  rejected.
- `seed_ui_100.build_writes()`: addresses and values match §6/§7.4, and the
  go flags come last.

**Simulator (config `memory_pif_eth_100.cfg`):**
- Clock plan: `cores["pru0"].clock_mhz == cores["pru1"].clock_mhz == 300`;
  after the prologues, TXCFG reads `0x00020010`, RXCFG reads `0x0000801F`,
  and the periods are 10.0 and 5.0 ns.
- RX default: with control `rxcfg = 0`, RXCFG still becomes `0x0000801F`.
- TX only: a 200 B frame decodes to payload + FCS, `burst_pushed = 263`,
  `T_burst == 21 040 ns`.
- Loopback clean: 2 frames, all stats clean, frame buffer matches, `eof = 1`.
- **Edge-phase coverage:** parametrise loopback `latency_ns ∈ {0, 10/3, 20/3}`.
  This shifts the sample-to-edge offset through all three residues of §3.5
  whatever the base phase is. Each must be clean.
- Budget: a traced run gives hot-loop cost == 8 cycles per stored byte,
  max RX FIFO occupancy ≤ 2 (the measured value is pinned in a comment),
  and `rx_ovf = 0`.
- Seed sweep (`@pytest.mark.slow` if runtime exceeds about 60 s): 7 seeds × 2 frames.
- Throughput: F1 == 100.0, F2 == 80.0 (± 0.01), F2p == 76.05 (± 0.01),
  0 < F3-E2E < F3-TX < F2p.
- Regression gate (not a new test): `pytest tests/test_pif_eth.py tests/test_pif_eth_rx.py`
  must still pass with no edits.

## 12. Documentation deliverables

### 12.1 `README.md`
What it is, a files table, how to run (`run_100.py`, pytest), the §5 clock
table, §6/§7 maps, the measured run table and throughput block pasted from
`run_100.py` output with the date, the simulator-only disclaimer, the
fractional-divider silicon caveat (§13 R6), and links to the guide and the
report.

### 12.2 `USERS_GUIDE.md` (+ PDF)
Modelled on `source/pif_eth/README.md` §"Run the 125 Mbaud TX+RX demo in the
browser UI". The steps:
0. Prerequisites: `pip install -r requirements.txt`; `websockets` present.
1. `cp memory.cfg memory.cfg.bak && cp config/memory_pif_eth_100.cfg memory.cfg`,
   then `python3 ui/server.py`. Alternative: the 300 MHz dropdown, **before**
   anything else (§9).
2. Open `http://localhost:8080` and confirm the dropdown shows 300 MHz.
3. Turn on Multi-core and set the partner to PRU1.
4. Open `pif_eth_100/pif_eth_100_tx.asm` **from the in-UI source browser**
   and load it to PRU0. Do the same for `pif_eth_100_rx.asm` to PRU1. Warn
   against the OS file picker and against loading the same file twice
   (§3.7; the drift-demo failure mode).
5. `python3 source/pif_eth_100/seed_ui_100.py`, then check that the Loopback
   card shows ch0 enabled with the latency set by the spike.
6. With PRU0 as the current (lead) core, click Run. Stop after the frame
   completes. Optionally check the Peripheral panel: PRU0 TXCFG
   `0x00020010`, PRU1 RXCFG `0x0000801F`.
7. `seed_ui_100.py status`, or a Memory panel at `0x2F00` (length 64) and
   the frame at `0x2E00` (length 204). Give the expected values.
8. For the next frame: `seed_ui_100.py arm`, then Run.
9. Restore: `mv memory.cfg.bak memory.cfg`.

Troubleshooting table: empty capture (loopback off, or TX loaded twice);
`crc_ok = 0` / garbage (seeded after PRU1 had already run → reload PRU1 and
reseed); programs gone (the speed dropdown was used after Load); `.include`
not found (file opened via the OS picker). The Signal Graph only traces the
lead core.

**The guide must actually be run.** Use the real browser through the
Chrome extension if it is available. Otherwise drive every step through the
same HTTP and WS endpoints the buttons use, from a script. Record which
method was used, the observed values and the date in the guide's
"Validated" note and in the report.

### 12.3 `DEVELOPMENT_REPORT.md` (+ PDF)
Sections: the prompt (verbatim); a decisions and interaction log (every
user message with its timestamp, and the decisions of §1); the process
(brainstorm → spec (Opus 5.5) → plan → implementation subagents (Sonnet
5.5) → reviews), with time per phase; a models table (per agent: role,
model ID taken from the transcript, input / cache-write / cache-read /
output tokens, wall time); totals; results (the `run_100` tables and all
throughput figures); deviations from this spec; lessons learned.

Data comes from `session_stats.py`, which reads
`~/.claude/projects/-home-thomas-GitHub-pru-simulator/<session>.jsonl` and
`<session>/subagents/agent-*.jsonl`:
- **Tokens.** Take assistant records with `message.usage`, **deduplicated
  by `message.id`** (streamed chunks repeat the usage). Sum
  `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`
  and `output_tokens` per model.
- **Time.** First and last `timestamp` per file. The session started at
  2026-09-30T13:19:49+02:00.
- **User messages.** Records with `type == "user"` that are not `isMeta`
  and not tool results.

The report is a snapshot taken at generation time; its header states the
cut-off. Regenerate it as the last step.

### 12.4 PDFs
`build_pdfs.py` renders markdown with `markdown_it.MarkdownIt('gfm-like', {'linkify': False})`,
wraps it in a small styled HTML template (tables, code, blockquote), then
runs `google-chrome --headless --disable-gpu --no-sandbox --print-to-pdf=<out> --no-pdf-header-footer file://<html>`.
It writes `USERS_GUIDE.pdf` and `DEVELOPMENT_REPORT.pdf` next to their
`.md` files. The machine has no pandoc and no sudo.

## 13. Risks and Task 0 spike

### Task 0: spike `spike_frac_rx.py` (runs before any other task)

It uses config `memory_pif_eth_100.cfg` (created first) and reads the
existing `source/pif_eth/pif_eth_tx_n2.asm` and `pif_eth_rx_o1_raw.asm`
without modifying them. Settings: host TXCFG override `0x00020010` (as
`rx_driver` does), `payload_len = 200`. The script prints a PASS/FAIL line
per check and exits non-zero on failure.

| # | Check | Pass criterion |
|---|---|---|
| S0.1 | Clock plan | both cores `clock_mhz == 300`; the PRU1 perif channel reports `core_clock_mhz == 300` |
| S0.2 | Fractional RX divider | after writing `0x0000801F`: `get_rx_div_factor() == 0`, `get_rx_div_factor_frac() == 1`, `rx_clock_period_ns() == 5.0`; TX `tx_clock_period_ns() == 10.0` |
| S0.3 | TX at n = 3 / 300 MHz | seeds {DEFAULT, 1, 2}: `burst_pushed == 263`, `T_burst == 21 040 ns`, line-history decode == payload + FCS |
| S0.4 | Python-armed RX, all edge phases | for latency ∈ {0, 10/3, 20/3} ns × seeds {DEFAULT, 1, 2}: RX armed from the host (the `capture_python_rx` pattern), `rx_ovf` false, batch ≤ 24 PRU0 instructions, which is half the 48-cycle FIFO fill time; `rx_reference.decode_capture(oversample=2)` → `invalid_symbols == 0` and payload + FCS found. For latency 0, also count RX samples within 1e-6 ns of a TX edge and report it (ties expected in one residue) |
| S0.5 | Firmware RX at 12-cycle budget | `pif_eth_rx_o1_raw.asm`, rxcfg `0x0000801F`, the same 3 latencies × 3 seeds × 2 frames: `rx_ovf = 0`, `symbol_errors = 0`, `bit_err = 0`, `crc_ok = 1`, `eof = 1`, frame matches; traced hot loop == 8 cycles per byte; max RX FIFO occupancy recorded |

**Outcomes:**
- **All pass:** go ahead with the main design and set `LOOPBACK_LATENCY_NS = 0.0`.
- **Only the latency-0 residue with ties fails (S0.4/S0.5):** set
  `LOOPBACK_LATENCY_NS = 5/6 ns` (0.8333). That puts every residue at least
  0.83 ns from an edge; offsets become {0.83, 2.5, 4.17} ns. Re-run S0.4
  and S0.5 with base latency 5/6 plus the same 0, 10/3 and 20/3 shifts.
  Document it as a modelled wire propagation delay, not drift. The UI guide
  then sets that value.
- **S0.2 fails (frac ignored / wrong period) or S0.4 still fails:** stop
  and report to the user. The simulator stays unmodified without user
  approval. With approval, take the **contingency**: 3x oversampling at
  n_rx = 1 (RXCFG `0x0000001F`, a 300 MHz sample clock, 8 cycles per byte
  = **zero headroom**, the same margin as the proven 125 Mbaud rung). It
  needs the mod-3 selector in `pf_bit`, `oversample=3` in the Python
  reference, and a capture of ≈ 789 B for 200 B (still < 1024 B; for
  `payload_len` near 252 about 983 B, which is tight).
- **S0.3 or S0.5 fails on budget (underrun / `rx_ovf`):** stop and report.
  The 3x fallback would make RX worse. This result would contradict the
  n = 3 evidence from 250 MHz.

The spike's checks move into `tests/test_pif_eth_100.py` once the copies
exist (§11).

### Risk register

| # | Risk | Mitigation / owner step |
|---|---|---|
| R1 | Fractional RX divider never used in this project | Task 0 (S0.2, S0.4, S0.5) |
| R2 | 12-cycle RX budget and 24-cycle TX budget rest on hand tallies | traced single steps (`_step_paced_traced`), hot-loop == 8 cycles and max FIFO occupancy asserted in tests; `T_burst` exactness for TX |
| R3 | Buffer ceilings | frame 204 ≤ 256 B; capture ≈ 528 ≤ 1024 B; host `validate_payload_len`; firmware `eof_status = 2` guard kept |
| R4 | PRNG fill and setup set the gap | measured and reported (F3-TX, F3-E2E, breakdown), not optimised. User expectation: F3-E2E ≈ 9 Mbit/s *est.* (§16 Q1) |
| R5 | UI at 300 MHz | dropdown order, `memory.cfg` backup/restore, no `project.json`, in-UI browser for includes, `seed_ui_100` checks the clock; the guide is executed |
| R6 | Simulator vs silicon | The simulator samples at an even 5.0 ns. Silicon `div16fr` at 1.5 may space sample edges unevenly (1/2 core clocks, not TRM-verified): still 2 samples per bit at zero drift, but less edge margin. DRAM `write_latency = 1`, `jitter = 0` assumed. Stated in the README and the report |
| R7 | Float edge ties (§3.5) | covered by construction in S0.4 and the tests (latency shifts) |
| R8 | First-comma anchor risk in `post_frame` (no scoring) | 7-seed sweep; anchor-risk flag printed per row |
| R9 | Runtime (≈ 45 k PRU1 instructions per frame in post-frame) | seeds × 3 frames in `run_100`; keep default pytest short, mark the sweep slow if needed |

## 14. Success criteria (simulator only, not a silicon claim)

1. Task 0 passes (or the documented latency outcome applies), with the
   output recorded in the report.
2. `run_100.py`: 7 seeds × 3 frames, every row clean (`rx_ovf = 0`,
   `symbol_errors = 0`, `bit_err = 0`, `crc_ok = 1`, `eof = 1`, frame
   matches), and all three edge residues clean.
3. F1 = 100.00 Mbaud and T_bit = 10.000 ns confirmed from the perif line
   history; both cores at 300 MHz; F2 = 80.00 Mbit/s with a gapless burst;
   F2p, F3-TX and F3-E2E measured and published with their breakdown.
4. RX hot loop measured at 8 cycles against 12; max RX FIFO occupancy
   recorded; `rx_ovf = 0`.
5. `pytest tests/test_pif_eth_100.py tests/test_pif_eth.py tests/test_pif_eth_rx.py`
   is green, and `git status` shows no modified tracked files (only new
   files; `memory.cfg` restored).
6. The UI walkthrough has been executed end to end, with observed stats
   recorded.
7. README, USERS_GUIDE and DEVELOPMENT_REPORT are written, with PDFs
   regenerated last.

## 15. What each deliverable needs from the implementer (plan input)

Suggested task order: **T0** config + verbatim golden-model copies
(`__init__`, `codec`, `crc32`, `prng`, `decoder`, `rx_reference`,
`pif_eth_crc32_hw.inc`) + spike → **T1** `frames.py` (BERT 200) + pure tests → **T2** TX asm + TX-only tests →
**T3** RX asm + loopback tests → **T4** `run_100.py` (trace, throughput,
sweep) + budget tests → **T5** `seed_ui_100.py` + unit test → **T6** UI
validation run + USERS_GUIDE → **T7** README with measured tables →
**T8** `session_stats.py` + DEVELOPMENT_REPORT → **T9** `build_pdfs.py` +
PDFs → **T10** final verification (§14).

| Deliverable | Needs |
|---|---|
| `memory_pif_eth_100.cfg` | copy + 2 keys (§9) |
| `pif_eth_100_tx.asm` | 2-line TXCFG change + header (§6); no other edits |
| `pif_eth_100_rx.asm` | header + rxcfg default (§7.1); no hot-loop edits |
| golden models | verbatim copies; `frames.py` trimmed to BERT 200 |
| `run_100.py` | §10.1 API; traced pacing mirror; throughput formulas §8; spike outcome → `LOOPBACK_LATENCY_NS` |
| `seed_ui_100.py` | §10.2; clock guard; `build_writes()` pure |
| tests | §11 list; config by absolute path; never touch `memory.cfg` |
| USERS_GUIDE | §12.2 steps; executed; observed values |
| README | §12.1; paste measured output with date |
| DEVELOPMENT_REPORT | §12.3; `session_stats.py`; generated last |
| PDFs | §12.4 pipeline |

**Amended by §17.5:** a branch/commit task comes first, and the RX
optimisation, readme changelog/handoff and final report/PDF tasks are
inserted before T10. The binding task list is the plan.

Future work (not in this project): overlap TX PRNG/CRC prep with RX
post-frame; RX Option 2/3; double-buffered RX capture. The word-wise
decimator is no longer future work: it is §17.1.

## 16. Open questions for the user

*Answered 2026-09-30. See §17.*

1. **Goodput expectation.** "80 Mbit net" is met as F2 (in-burst). The
   frame-averaged figures are estimated at about 53 Mbit/s (TX-limited)
   and about 9 Mbit/s (end-to-end, bounded by RX Option 1's post-frame
   decode). Is reporting that enough, or should a follow-up raise F3?
2. **Loopback latency.** Zero by default (like `pif_eth`), or a fixed 5/6 ns
   modelled wire delay even if the spike passes at 0?
3. **Existing files.** The rule is new files only. Should the top-level
   `readme.md` changelog and a `docs/handoff/2026-09-30-pif-eth-100.md` note
   (a new file, per the cross-PC convention) be added?
4. **Commits.** Should implementation commit per task on `feat/sd-sweep`,
   or leave everything uncommitted for review?

---

## 17. Addendum 2026-09-30 (user decisions)

The user approved §1–§16 as written, with the decisions below. Where they
differ from the body, this section wins.

### 17.1 RX post-frame optimisation (new, separate deliverable)

**Why.** Baseline `post_frame` costs **44.5 k PRU1 cycles** for a 200 B
frame. This was measured in the simulator with the existing
`pif_eth_rx_o1_raw.asm` on the 250 MHz rig (n_tx = 4). At 300 MHz that is
≈ 148 µs, and it is why F3-E2E is ≈ 9 Mbit/s. The realtime loop is not the
bottleneck (max RX FIFO depth 1).

**Chosen approach: fast aligned decode.** Options 2/3 of the 2026-07-21 RX
spec move work *into* the realtime loop. That spec estimated them at 15–25
cycles per byte, which does not fit the 12-cycle budget, so they are
rejected here. The cost is in the post-frame bit loop: about 20 cycles per
line bit, spent visiting each of the 8 samples per byte one at a time.
- **First frame's post-frame** (`r14 == 0`): PRU1 builds a 256 B **2:1
  decimation LUT** at local `0x0C00` (global `0x2C00`):
  `nib[b] = b7<<3 | b5<<2 | b3<<1 | b1`. These are the samples the baseline
  XOR toggle keeps (1st, 3rd, 5th and 7th, MSB first). The phase is the
  same at every byte boundary because there are 8 samples per byte. This
  costs ≈ 4.4 k cycles, once. **Not at boot:** in the UI both go flags are
  set before either core runs, so RX must arm within TX's ≈ 2.7 k-cycle
  frame prep. A boot-time build missed the frame start in the planner's UI
  test (`cap_bytes` 410, `crc_ok` 0).
- **Post-frame:** the unchanged baseline bit-slide scan runs only until the
  first comma anchors the grid (a few bytes). At the next byte boundary it
  hands over to `pf_fast`. `pf_fast` loads 4 capture bytes per `lbbo`, maps
  each byte to 4 bits with one LUT load, shifts them into the accumulator,
  and cuts a symbol whenever ≥ 10 bits are held:
  `sym = (acc >> (nbits − 10)) & 0x3FF`. Decode, RD check, commas, the
  frame-buffer guard (`eof_status = 2`) and error counting match
  `pf_symbol`'s aligned branch. CRC and BER are unchanged.
- **Equivalence by construction.** The fast path consumes the same bit
  sequence with the same 10-bit tiling from the same anchor. Stats and the
  frame buffer must be **identical** to the baseline for the same input.
  This is the main test.

**Expected savings (measured by the planner in scratch copies, not yet in
the repo).** The table below is from the first prototype at 250 MHz
(n_tx = 4). Afterwards, the plan's exact code was re-run at 300 MHz:
post-frame is 13.5 k cycles per frame in steady state, 14.9 k as a 3-frame
mean including the one-off LUT build; F3-E2E went from 8.97 to 19.48 Mbit/s
over 3 frames (≈ 21.3 in steady state); all stats were identical to the
baseline.
The prototype ran on the 250 MHz rig (n_tx = 4) with the traced
per-instruction stepper. Across 4 seeds × payload 128/200/252 × 3
latencies, stats and frame bytes were identical to the baseline.

| Payload | Baseline post-frame | Fast post-frame | Saving |
|---|---|---|---|
| 128 B | 29.2 k cycles | 9.0 k cycles | −69 % |
| 200 B | 44.5 k cycles | 13.5 k cycles | −70 % (≈ −103 µs at 300 MHz) |
| 252 B | 55.5 k cycles | 16.8 k cycles | −70 % |

*Est.* F3-E2E rises from ≈ 9 to ≈ 21 Mbit/s: 1600 bits / (21.04 + 45 + 9)
µs. The number must be **measured** at 300 MHz and reported next to the
baseline figure. The measurement uses the traced per-instruction stepper
(eof → frame_loop PRU1 cycles), which is the single-step validation
required by §13 R2.

**Rules.**
- `pif_eth_100_rx.asm` stays the proven baseline and is not edited by this
  work.
- The fast RX lives in `pif_eth_100_rx_fast.asm` with its own tests. It is
  built only **after** the baseline loopback, tests, UI guide and report
  skeleton are done.
- Mandatory for the fast RX: zero BER, `crc_ok = 1`, `rx_ovf = 0`,
  `symbol_errors = 0` over the seed sweep and all three edge phases, and
  equivalence to the baseline.
- If it cannot be made to pass, the baseline remains the delivered result,
  and README and report say so.
- The realtime `poll`/`zrun` loop is unchanged in the fast RX.
- `run_100.py` gains `rx="base"|"fast"` and `--rx base|fast|both`.

**Harness consequence.** A long PRU1 prologue must never break the drivers,
so they stop using `step("pru1", 20)`. They run PRU1 to `go_wait` with
the paced traced stepper, which detects "an instruction at label `go_wait`
executed". The prototype showed that polling `pc == label` after
multi-instruction paced steps can alias and never match. Stepping PRU1
alone would also let it run ahead of PRU0's time.

### 17.2 Loopback latency
The default is **0 ns**. All three sample-to-edge residues are tested by
shifting the latency by 0, 10/3 and 20/3 ns (§11, S0.4). A fixed 5/6 ns
latency is **only** the fallback from §13's spike outcomes.

**Planner evidence (scratch copy of the plan's code).** The spike passes at
0 ns, because in its harness the edge-tie phase falls at an exact latency
of 0. In the `run_100` harness the tie phase falls at the +10/3 ns shift.
Adding an inexact float latency to exact edge ties makes the ties resolve
inconsistently, so single samples slip: frame 0 shows ≈ 50–70 symbol
errors and `crc_ok = 0`. With base 5/6 ns every residue is ≥ 0.83 ns from
an edge, and all phases, seeds and tests are clean. The fallback condition
is therefore expected to be met. The plan puts a **second latency gate** in
Task 5, which switches to 5/6 ns only after confirming exactly this
signature.

### 17.3 Existing files that may change
- Top-level `readme.md`: one changelog entry under
  `**Unreleased — feat/pif-eth-100**`, above v0.2.6. No version bump, since
  that would mean editing `ui/static/index.html`, which is not allowed.
- New `docs/handoff/2026-09-30-pif-eth-100.md` in the style of the
  existing handoff notes.
- `memory.cfg`: changed only transiently by the UI run (copy, or the
  dropdown), and restored before any commit. No other existing file may
  change.

### 17.4 UI validation method
`ui_walkthrough_100.py` drives the running server through the same
endpoints the buttons use:
- `GET/PUT /config/clock_speed`;
- WS `load` with `filename` = `pif_eth_100/<file>.asm`, so the `.include`
  path resolves as it does in the browser;
- WS `perif_loopback`, `write_memory`, `run_multicore` with
  `core = "pru0"` and `partner = "pru1"`, and `read_memory`.

After loading, it sends `reset` for PRU0 and PRU1, because Load keeps a
core's old PC and registers. The planner reproduced the failure: re-loading
the fast RX over a previously run baseline, without Reset, resumed mid-code
and wrote LUT bytes over the stats block. The guide's step "click Reset"
exists for the same reason. It is always run, and its output goes into the
guide and the report. The
browser walkthrough through the Chrome MCP tools is run in addition when
`tabs_context_mcp` answers. If the browser is not reachable, the scripted
run is the validation of record, and the guide and report say so.

### 17.5 Branch, commits, task order
- First create branch `feat/pif-eth-100` off `feat/sd-sweep`. The first
  commit holds this spec and the plan.
- One commit per task, plus the docs. **No push, no PR.**
- Stage explicit paths only; never `git add -A` or `git add .`. The user's
  untracked scratch files (`debug_*.py`, `run_fir_filter.py`,
  `source/fir_filter.asm`, …) must never be staged.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Task order: branch + spec/plan commit → T0 spike → T1 → T2 → T3 → T4 → T5
  → T6 (UI) → T7 README → T8 report skeleton + `session_stats.py` →
  **RX-fast** → readme changelog + handoff → report final + PDFs → T10
  final verification.
- Report facts to carry: planning (the spec) ran on **Opus 5.5**, a
  subagent using ≈ 209 k tokens over ≈ 12.6 min. Implementation runs on
  **Sonnet 5.5**.

### 17.6 Resolved open items (verified in code)
- `LDI` writes the full 32-bit register in the simulator
  (`core/pru_core.py`: `LDI` → `_write_operand`), so `ldi r0, 0x801F`
  yields `0x0000801F`.
- `cores[...]._parser.labels` maps label → instruction index, the same unit
  as `pc`. The prototype used it successfully.
- LBBO/LBCO with a register offset and byte-field operands (`r4.b1`) are
  supported.
