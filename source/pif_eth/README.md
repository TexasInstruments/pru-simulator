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
* **CRC-32:** firmware-generated (bit-serial, reflected poly `0xEDB88320`),
  matching `zlib.crc32` / Ethernet FCS.

## Data flow

```
 PRNG/preload payload ──▶ DRAM0 frame buffer (0x0500)
        │                        │
        │                  firmware CRC-32 ─▶ append 4-byte FCS
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
| `crc32.py`, `prng.py` | Ethernet FCS and xorshift32 references |
| `frames.py` | BERT and UDP frame builders |
| `decoder.py` | bit/symbol stream → frames |
| `pcap.py` | minimal classic-pcap writer (Ethernet link type) |
| `driver.py` | runs the firmware on the simulator, decodes, checks BER, writes pcap |
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

## Roadmap

* Broadside CRC accelerator for the FCS (currently firmware bit-serial).
* Striping across all three perif channels for aggregate throughput.
* A *robust* n=2 (125 Mbaud) firmware would need to cut the bit-packing
  loop's cycle cost further (or a multi-byte FIFO push, which the perif
  model/hardware spec does not currently support) rather than removing the
  FIFO-full check.
