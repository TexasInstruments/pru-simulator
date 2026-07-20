# pif_eth — 8b/10b line-coded Ethernet TX over the Peripheral Interface

Single-core **PRU0** firmware that transmits Ethernet frames as an **8b/10b**
line-coded serial stream on **channel 0** of the 3-channel Peripheral Interface,
plus a Python golden-reference/decoder layer that verifies the stream and
renders it to a **Wireshark** trace.

* **Line code:** true IBM/ANSI 8b/10b with running-disparity tracking, driven
  from a single 256-entry lookup table held in **DRAM0** and read with `LBCO`
  from `c24`.  RD is bounded to ±1 and no run exceeds 5 bits (verified).
* **Line rate:** target 125 Mbaud → 100 Mbit/s raw Ethernet (8b/10b overhead).
  The simulator models the bit clock at a scaled, integer-divisible rate
  (`TXCFG` div = 7 → 25 MHz) so the single core reliably keeps the 4-deep TX
  FIFO fed; because the encoder and decoder share the configured period this
  does not affect the byte stream or the measured BER.
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

## Roadmap

* Broadside CRC accelerator for the FCS (currently firmware bit-serial).
* Striping across all three perif channels for aggregate throughput.
