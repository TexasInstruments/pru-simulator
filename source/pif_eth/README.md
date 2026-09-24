# pif_eth — 8b/10b line-coded Ethernet TX over the Peripheral Interface

Single-core **PRU0** firmware that transmits Ethernet frames as an **8b/10b**
line-coded serial stream on **channel 0** of the 3-channel Peripheral Interface,
plus a Python golden-reference/decoder layer that verifies the stream and
renders it to a **Wireshark** trace.

* **Line code:** true IBM/ANSI 8b/10b with running-disparity tracking, driven
  from a single 256-entry lookup table held in **DRAM0** and read with `LBCO`
  from `c24`.  RD is bounded to ±1 and no run exceeds 5 bits (verified).
* **Line rate:** target 125 Mbaud → 100 Mbit/s raw Ethernet (8b/10b overhead).
  The production firmware (`pif_eth_tx.asm`) models the bit clock at a scaled,
  integer-divisible rate (`TXCFG` div = 7 → 25 MHz) so the single core
  reliably keeps the 4-deep TX FIFO fed; because the encoder and decoder share
  the configured period this does not affect the byte stream or the measured
  BER. **Update (2026-07-21):** the full 125 Mbaud target (TXCFG n=2 @
  250 MHz core) has since been demonstrated experimentally — see
  [125 Mbaud (n=2) follow-up](#125-mbaud-n2-follow-up-2026-07-21) below.
* **Frames:**
  * **BERT (option 1)** — no preamble, no header. 128 PRNG payload octets
    (firmware xorshift32) + 4-octet CRC-32 = 132 octets. For bit-error-rate
    testing.
  * **UDP (option 2)** — Ethernet/IPv4/UDP carrying `"Hello World Text"`,
    padded to 60 octets + 4-octet FCS = 64 octets. Dissects cleanly in
    Wireshark.
* **CRC-32:** computed on the ICSSG **CRC16/32 broadside accelerator** (XFR
  device 1, reflected poly `0xEDB88320`), matching `zlib.crc32` / Ethernet
  FCS — see [CRC-32 on the broadside accelerator](#crc-32-on-the-broadside-accelerator).
  The original bit-serial firmware routine is kept as a fallback.

## Data flow

```
 PRNG/preload payload ──▶ DRAM0 frame buffer (0x0500)
        │                        │
        │                  CRC16/32 accel ─▶ append 4-byte FCS
        ▼                        ▼
 per octet: LBCO LUT[octet] (DRAM0, c24) ─▶ 10-bit symbol (RD tracked)
        ▼
 pack MSB-first into bytes ─▶ ch0 TX FIFO (refill while not full)
        ▼
 perif serialiser ─▶ 8b/10b line ─▶ host decoder ─▶ frames ─▶ pcap
```

Frames are bracketed by **K28.5** commas (idle fill / inter-frame delimiters),
which also give the decoder symbol alignment.

## Files

| File | Purpose |
|------|---------|
| `pif_eth_tx.asm` | PRU0 firmware (self-config, PRNG, CRC-32, 8b/10b encode, stream) |
| `codec.py` | 8b/10b encode/decode + DRAM0 LUT builder (golden reference) |
| `pif_eth_crc32_hw.inc` | shared `crc32_core`: Ethernet FCS on the CRC16/32 broadside accelerator (used by all firmware) |
| `pif_eth_crc32.inc` | same `crc32_core` contract, bit-serial software fallback (no longer included by default) |
| `crc32.py`, `prng.py` | Ethernet FCS and xorshift32 references |
| `frames.py` | BERT and UDP frame builders |
| `decoder.py` | bit/symbol stream → frames |
| `pcap.py` | minimal classic-pcap writer (Ethernet link type) |
| `driver.py` | runs the TX firmware on the simulator, decodes, checks BER, writes pcap |
| `rx_driver.py` | drives PRU0 TX → PRU1 RX over the perif loopback, characterizes the clock ladder |
| `seed_ui.py` | seeds the browser UI's shared simulator (WebSocket) to run TX (and TX+RX) demos interactively |
| `pif_eth_tx_n2.asm` | experimental: batched/unrolled bit-packing, all FIFO checks intact — validated n=3..8, fails at n=2 |
| `pif_eth_tx_n2_skipchecks.asm` | experimental: as above, 2 FIFO checks removed — hits full 125 Mbaud (n=2), hard-pinned to it |

## Run it

```bash
# 100 frames of each type -> BER report + traces/pif_eth_{bert,udp}.pcap
python3 source/pif_eth/driver.py 100

# inspect the UDP trace
tshark -r source/pif_eth/traces/pif_eth_udp.pcap
```

Tests (`tests/test_pif_eth.py`) cover the codec invariants, firmware
self-configuration, firmware PRNG/CRC in DRAM, zero-BER round trips for both
frame types, the pcap output, and driving the firmware through the **MCP
server** wrapper.

## DRAM0 memory map

| Address | Contents |
|---------|----------|
| `0x0000` | 8b/10b encode LUT, 256 × u32 |
| `0x0400` | `num_frames` (u32) |
| `0x0404` | `mode` (u32): 0 = PRNG payload, 1 = preloaded payload |
| `0x0408` | `prng_state` / seed (u32) |
| `0x040C` | `payload_len` (u32, octets before FCS) |
| `0x0410` | `frame_counter` (u32, published) |
| `0x0414` | `burst_pushed` bytes (u32, published) |
| `0x0418` | go flag (u32, host→firmware handshake) |
| `0x0500` | frame core buffer (payload followed by 4-byte FCS) |

### LUT entry (u32, little-endian) for octet `b`

| Bits | Meaning |
|------|---------|
| `[9:0]` | 10-bit symbol to send when running disparity is negative |
| `[10]` | RD after the symbol (0 = negative, 1 = positive) |
| `[25:16]` | 10-bit symbol to send when running disparity is positive |
| `[26]` | RD after the symbol |

## CRC-32 on the broadside accelerator

All four firmware images (`pif_eth_tx.asm`, `pif_eth_tx_n2.asm`,
`pif_eth_tx_n2_skipchecks.asm`, `pif_eth_rx_o1_raw.asm`) include the same
`crc32_core` from `pif_eth_crc32_hw.inc`. The routine keeps the software
routine's contract (`r21` = buffer, `r8` = length → `r20` = FCS, `r21` =
end of buffer, return via `r26`), so only the `.include` line changed in
`pif_eth_tx.asm` and the RX firmware. The two n2 variants used to inline
their own copy of the bit-serial loop; they now call the shared routine too.

How it drives the hardware (TRM SPRUIM2H §6.4.6.2.2.1, Table 6-429):

1. `XOUT 1, &r25, 1` with `CRC_CFG = 0x01` selects CRC-32 and seeds `0xFFFFFFFF`.
2. A zero-overhead `LOOP` pushes the buffer one 32-bit word at a time:
   `LBBO &r29` then `XOUT 1, &r29, 4`.
3. Two `NOP`s, then `XIN 1, &r29, 4` reads the result. The read is
   destructive. The engine applies no final XOR, so the firmware does
   `NOT`.
4. A session must keep one write width. So when the length is not a
   multiple of 4, the 1–3 trailing bytes run as a second, byte-wide
   session, seeded through `CRC_SEED` (`R28`) with the word session's
   result. BERT (128) and UDP (60) are both multiples of 4 and never take
   this path. RX tests with `payload_len=201` do.

The broadside window is fixed at `R25`–`R29`, and the firmware already
uses `r28`/`r29` as return-address registers (RX: both are live inside
`post_frame`). So `crc32_core` saves them in `r22`/`r24` and restores
them before returning.

### Cycle savings (measured on the simulator)

Cycles from the `jal` into `crc32_compute` / `rx_crc_check` to its return,
taken by single-stepping the simulator (default memory config for
`driver.py`; `config/memory_pif_eth_rx.cfg` for the TX+RX runs; both have
DRAM `read_latency = 2`):

| Routine | Payload | Software (bit-serial) | Accelerator | Saving |
|---|---|---|---|---|
| TX `crc32_compute` | 128 B (BERT) | 9 488 – 9 552 | **181** | ~52× (−9.3k cycles) |
| TX `crc32_compute` | 60 B (UDP) | 4 456 | **96** | ~46× |
| RX `rx_crc_check` | 128 B (BERT) | 9 526 – 9 558 | **187** | ~51× |
| TX / RX | 201 B (byte tail) | 14 864 / 14 873 | **283 / 289** | ~52× |

* **Per byte:** the software loop costs about 74 cycles per octet, and the
  exact figure depends on the data (one extra `LDI`/`XOR` pair per `1`
  shifted out). That is why it varies from frame to frame. The accelerator
  loop costs 5 cycles per 4-octet word (`LBBO` 1 + 2 DRAM stall, `XOUT` 1,
  `ADD` 1; `LOOP` adds nothing), about 1.25 cycles/octet. The fixed setup
  and readout (save/restore, `CRC_CFG`, 2 `NOP`s, `XIN`, `NOT`) is about
  20 cycles. The cost is now deterministic. The inner loop is bound by
  DRAM read latency, not by the CRC.
* **What it buys on TX:** the FCS is computed between bursts, not inside
  the 8b/10b streaming loop. So it doesn't change the per-bit budget (the
  n=2 zero-headroom analysis above is untouched). It shrinks the dead time
  before each frame. At n=2 / 250 MHz, a BERT burst is 138 symbols × 10
  bits × 2 cycles ≈ 2 760 core cycles on the wire. The software CRC
  (≈9.5k cycles, 38 µs) took about 3.4× longer than the frame it was
  protecting. The accelerator (181 cycles, 0.72 µs) takes about 7% of it.
  The PRNG fill (≈1.5k cycles for 128 octets: 11 instructions + 1 write stall per octet) is now the larger per-frame
  software cost.
* **What it buys on RX:** `rx_crc_check` runs post-frame, before PRU1
  re-arms for the next burst. Its ≈9.4k-cycle cut goes straight into the
  minimum inter-frame gap RX can accept. The realtime capture loop is
  unchanged, and `rx_driver.py o1` still passes all 28 ladder/seed
  combinations, including n_tx=2.

These are simulator figures. `XOUT`/`XIN` to the CRC model cost one cycle
each, and the `NOP` count follows the TRM's "1–2 NOPs" rule. They are
**not silicon measurements**.

## 125 Mbaud (n=2) follow-up (2026-07-21)

Reaching the architecture's 125 Mbaud target requires TXCFG divider n=2 at a
250 MHz core clock (`bit_clock = core_clock/n`). Two experimental firmware
variants explore this (neither wired into `driver.py` or
`tests/test_pif_eth.py` — both are standalone reference artifacts):

| Firmware | n | Bit rate | Result |
|---|---|---|---|
| `pif_eth_tx_n2.asm` (all FIFO checks) | 2 | 125.00 MHz | **FAIL** — underrun, then deadlock |
| `pif_eth_tx_n2.asm` (all FIFO checks) | 3–8 | 83.33–31.25 MHz | **PASS**, BER=0 — a genuine ~4x speedup over the 25 MHz baseline, self-adapting |
| `pif_eth_tx_n2_skipchecks.asm` | 2 | 125.00 MHz | **PASS**, BER=0 over 100 BERT + 100 UDP frames — hits the full 125 Mbaud target |
| `pif_eth_tx_n2_skipchecks.asm` | 3 or 4 | 83.33 / 62.50 MHz | **FAIL** — BER≈0.68, `tx_overrun=True` |

Both variants add batched 4-octet `LBBO` loads and fixed-shift unrolled
8b/10b bit-packing (compile-time shift amounts instead of a runtime
`nbits`-tracked value), applied to both the data loop and the K28.5 comma
emission. The `_skipchecks` variant additionally drops the FIFO-full check on
the data loop's two single-byte-emit phases, closing a 2-cycle race at n=2
(confirmed by single-stepping the simulator: the check passes, then the
drain's next bit-edge pops the FIFO before the byte lands).

**Caveat:** the FIFO-full check is what lets the firmware's push rate track
whatever drain rate is configured. Removing it (`_skipchecks`) fixes
production at a constant rate tuned to exactly match n=2 — the same firmware
silently corrupts data (overrun, not a crash/hang) at any other divider. It
must only be run with `pru_clock_mhz=250` and TXCFG div_factor=1, and
re-validated if either ever changes.

**Recommendation:** for a robust speedup that stays correct across
clock/divider changes, use `pif_eth_tx_n2.asm` at n≥3. Treat
`pif_eth_tx_n2_skipchecks.asm` as a validated proof that the 125 Mbaud
architectural target is reachable, not as a drop-in replacement for
`pif_eth_tx.asm`.

Full results and root-cause analysis:
`docs/superpowers/specs/2026-07-21-pif-eth-n2-125mbaud-design.md`.

## PRU1 RX (Option 1 — realtime capture, post-frame decode)

`pif_eth_rx_o1_raw.asm` runs on **PRU1** and receives PRU0's 8b/10b stream
over the same perif loopback, at exactly **2x oversampling** (`n_rx =
n_tx/2`). The realtime service loop only drains the 4-deep RX FIFO and
detects SOF/EOF (2 consecutive zero capture bytes); 8b/10b decode, comma
alignment, CRC-32 verification and PRNG bit-error counting all happen
**post-frame**, in firmware, after capture completes. Results are published
to DRAM1 (`rx_driver.STATS_ADDR`, `rx_driver.FRAME_ADDR`).

**`payload_len` ceiling: 252 octets.** The reconstructed-frame buffer at
`0x0E00` is 256 B, holding `payload_len + 4` (FCS) bytes, so `payload_len`
must be `<= 252`. `rx_driver.run_rx`/`build_sim` validate this and raise
`ValueError` rather than let the firmware overrun. The realtime `poll`/`zrun`
capture loop is deliberately left with no guard of its own — see below — so
the post-frame decode stage (`pf_symbol`) carries a matching firmware-side
backstop: on overrun it stops storing, sets `eof_status = 2` (overflow
abort), and abandons the rest of that frame's octets, rather than walking
into the stats/control blocks that immediately follow the frame buffer.

`rx_driver.py` drives PRU0 TX → PRU1 RX end to end and exposes:

```bash
python3 source/pif_eth/rx_driver.py o1
```

`characterize("o1")` runs every ladder rung and reports which ones stay
clean (`rx_ovf=0`, `symbol_errors=0`, `prng_bit_errors=0`, `crc_ok=1`, and
the reconstructed frame matches the expected PRNG+FCS payload). Task 8 swept
7 seeds per rung to bound a known anchor risk — Option 1 locks its symbol
grid onto the *first* comma it finds with no scoring, so an unlucky bit
stream could in principle lock onto a phase-shifted false comma (this exact
defect was previously found and fixed in the host-side reference decoder).

**Measured (2026-07-21), 7 seeds × 4 rungs, all 28 combinations PASS:**

| n_tx | line rate | cycles/captured byte | result |
|---|---|---|---|
| 2 | 125.00 Mbaud | 8 | **PASS** |
| 4 | 62.50 Mbaud | 16 | **PASS** |
| 6 | 41.67 Mbaud | 24 | **PASS** |
| 8 | 31.25 Mbaud | 32 | **PASS** |

Option 1's 7-instruction realtime loop sustains full line rate (`n_tx=2`)
cleanly in this simulator — no FIFO overflow, no symbol errors, no anchor-risk
lock-on, across every seed tried. **The `n_tx=2` (125 Mbaud) row passes at
*zero* headroom**: 7 instructions plus a 1-cycle DRAM write stall exactly
fills the 8-cycle-per-captured-byte budget, with none of the 4-deep FIFO's
slack (32 cycles) left unused if that budget grows by even one cycle. That
result depends on this simulator's modelled timing —
`config/memory_pif_eth_rx.cfg` sets DRAM1 `write_latency = 1` and `jitter =
0`; any added jitter or write latency on that path drops this rung. This is a
simulator measurement of modelled cycle cost, **not a silicon claim**.
**Rated divider: `RATED_DIVIDER["o1"] = 2`**
(the smallest `n_tx` measured to pass), pinned by
`test_o1_clean_at_rated_divider` in `tests/test_pif_eth_rx.py`. Full raw
output and a harness bug found/fixed during characterization:
`docs/superpowers/specs/2026-07-21-pif-eth-pru1-rx-design.md` §14.

## Run the 125 Mbaud TX+RX demo in the browser UI

This runs the full loopback demo interactively — `pif_eth_tx_n2_skipchecks.asm`
(PRU0, n=2/250 MHz, the full 125 Mbaud rung) transmitting into
`pif_eth_rx_o1_raw.asm` (PRU1, Option 1 realtime capture) — inside `ui/server.py`'s
browser UI, instead of via `rx_driver.py`. Same firmware, same DRAM1 addresses;
only the driver changes.

1. **Config.** The server always loads the project-root `memory.cfg`. This
   demo needs both cores at 250 MHz and a DRAM1 region, i.e. the same config
   `rx_driver.py` uses — copy it in before starting the server:
   ```bash
   cp config/memory_pif_eth_rx.cfg memory.cfg
   python ui/server.py
   ```
   (If the server is already running, paste `memory_pif_eth_rx.cfg`'s contents
   into the browser's Config editor and apply instead of restarting.)
2. **Open the UI** at `http://localhost:8080` and turn on **Multi-core mode**.
3. **Load PRU0** with `pif_eth_tx_n2_skipchecks.asm`. This firmware
   hardcodes `TXCFG` for n=2 at boot — no register poke needed for the 125
   Mbaud rung specifically.
4. **Load PRU1** with `pif_eth_rx_o1_raw.asm`.
5. **Enable loopback.** Open the Peripheral Interface panel's Loopback card,
   check channel 0 enabled (0 ns latency/jitter/drift for an ideal wire), and
   Apply. This is required — the simulator does not wire TX to RX by default,
   and without it PRU1's RX line source is `None`.
6. **Seed DRAM before the first Run/Step on PRU1** — its control-block reads
   (mode/seed/payload_len/rxcfg) happen once at boot:
   ```bash
   python3 source/pif_eth/seed_ui.py bert125
   ```
   This writes PRU0's TX control block (DRAM0), PRU1's RX control block and
   decode LUT (DRAM1 @0x2000, per the map below), zeroes the RX stats block,
   and arms one frame on both cores.
7. **Click Run.** Multi-core Run steps PRU0 (lead) and paces PRU1 (follow) to
   stay within the perif clock, so both cores advance together; Stop halts
   both. A single BERT frame at n=2 finishes in a few thousand instructions.
8. **Read results** in a Memory panel pointed at DRAM1's stats block
   (addresses below) or the reconstructed frame at `0x2E00`. Re-run
   `seed_ui.py bert125` to arm the next frame.

Note: the Signal Graph traces only the multi-core *lead* (PRU0) during a
paced Run; to watch RX FIFO/valid/overflow live, switch the current-core
selector to PRU1 and read its Peripheral Interface channel card, or just read
the DRAM1 stats after the run completes.

### DRAM1 (PRU1 RX) memory map

| Address | Contents |
|---------|----------|
| `0x2000` | 8b/10b decode LUT, 1024 × u16 |
| `0x2800` | raw oversample capture buffer (1024 B) |
| `0x2E00` | reconstructed frame (payload + 4-byte FCS), 256 B |
| `0x2F00` | stats: `frames` (u32) |
| `0x2F04` | stats: `cap_bytes` (u32) |
| `0x2F08` | stats: `rx_ovf` (u32) — nonzero = FIFO overflowed |
| `0x2F0C` | stats: `symbol_errors` (u32) |
| `0x2F10` | stats: `crc_ok` (u32) — 1 = FCS matched |
| `0x2F14` | stats: `prng_bit_errors` (u32) |
| `0x2F18` | stats: `tot_bits` (u32) |
| `0x2F1C` | stats: `eof_status` (u32) — 1 = clean EOF, 2 = frame-buffer overflow abort |
| `0x2F40` | control: `mode` (u32) — RX only implements BERT (0); no UDP path |
| `0x2F44` | control: `seed` (u32) — must match PRU0's TX seed |
| `0x2F48` | control: `payload_len` (u32) — must match PRU0's TX `payload_len` |
| `0x2F4C` | control: `go` (u32, host→firmware handshake, re-armed per frame) |
| `0x2F50` | control: `rxcfg` (u32) — `((n_rx-1)<<16) \| 0x1F`; n_rx = n_tx/2 = 1 at the 125 Mbaud rung |

Only BERT (mode=0) is supported end-to-end through RX — reconstruction and
bit-error counting both depend on the PRNG stream, which the UDP frame
doesn't have. To try other ladder rungs (n_tx=4/6/8) in the UI, load
`pif_eth_tx_n2.asm` on PRU0 instead (has all FIFO checks, not hard-pinned to
n=2) and pass a different `rxcfg_word(n_tx//2)` — `seed_ui.py bert125` only
wires up the n=2/125 Mbaud rung; see [125 Mbaud (n=2) follow-up](#125-mbaud-n2-follow-up-2026-07-21)
and [PRU1 RX](#pru1-rx-option-1--realtime-capture-post-frame-decode) above for
the full ladder and its results.

## Roadmap

* Striping across all three perif channels for aggregate throughput.
* A *robust* n=2 (125 Mbaud) firmware would need to cut the bit-packing
  loop's cycle cost further (or a multi-byte FIFO push, which the perif
  model/hardware spec does not currently support) rather than removing the
  FIFO-full check.
