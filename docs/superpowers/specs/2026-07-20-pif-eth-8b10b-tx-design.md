# pif_eth — 8b/10b line-coded Ethernet TX over the Peripheral Interface (design)

Date: 2026-07-20

## Goal

Transmit Ethernet frames as an **8b/10b** line-coded serial stream over the PRU
**Peripheral Interface**, single-core **PRU0**, **channel 0**, at a target
125 Mbaud line rate (100 Mbit/s raw Ethernet).  Verify the stream on the host
and render it to a Wireshark trace, running ~100 frames while keeping a frame
counter.

## Decisions (confirmed with the requester)

1. **Single channel (ch0)** — one continuous bit stream, not striped across the
   three channels.
2. **True 8b/10b with running disparity** — the single DRAM0 LUT holds both the
   RD- and RD+ symbol plus the resulting disparity; firmware tracks RD.
3. **BERT frame = 132 octets** — 128 PRNG payload octets + 4-octet CRC-32.
4. **Both frame types to pcap** — BERT (malformed Ethernet, expected) and the
   valid UDP frame.

## Line code

IBM/ANSI 8b/10b (Widmer & Franaszek): 5b/6b on the low 5 bits + 3b/4b on the
high 3 bits, transmitted `a b c d e i f g h j` (a first on the wire).  Running
disparity is carried between symbols and sub-blocks and is always ±1 at symbol
boundaries; per-symbol disparity is −2/0/+2.  The alternate D.x.A7 3b/4b code
breaks runs of five.  `codec.py` is the golden reference; it is validated by
strong invariants (RD bounded, no run > 5, bijective decode) rather than by
transcribing the ISO tables, and it is the sole definition the firmware and the
decoder must agree with (hardware interop is out of scope for the simulator).

The **K28.5** comma is used as idle fill and inter-frame delimiter, giving the
host decoder symbol alignment.

## Line rate

Bit clock = source / ((frac+1)·(div+1)); from a 200 MHz core clock the exact
125 MHz is not integer-divisible.  The model uses `TXCFG` div = 7 → 25 MHz,
which (a) is representable and (b) leaves the single core comfortable headroom
to keep the 4-deep FIFO fed.  Because the encoder and the host decoder both use
the configured period, the absolute rate does not affect the byte stream or the
BER; 125 Mbaud is the documented physical target.

## Frames

* **BERT** — no preamble, no header. Payload = 128 octets from a firmware
  xorshift32 PRNG (defined seed, state persists across frames). FCS = CRC-32
  over the 128 payload octets, appended as octets 129–132.
* **UDP** — Ethernet/IPv4/UDP, payload `"Hello World Text"`, padded to the
  60-octet Ethernet minimum, FCS appended (64 octets on the wire).

CRC-32 is firmware-generated (bit-serial, reflected `0xEDB88320`, init/final
`0xFFFFFFFF`), identical to `zlib.crc32`.  A broadside CRC accelerator is a
future stage.

## Firmware data flow (per frame / "burst")

1. Fill payload: PRNG (BERT) or use the host-preloaded payload (UDP).
2. Compute CRC-32 over the payload and append the 4-octet FCS in DRAM0.
3. For each octet: `LBCO` the LUT word from DRAM0 (`c24 + octet*4`), pick the
   RD- or RD+ half by the current disparity, extract the 10-bit symbol and the
   next RD, pack the symbol MSB-first into a bit accumulator, and push whole
   bytes to the ch0 TX FIFO, spinning while the FIFO is full.
4. Bracket the frame with K28.5 commas; flush the partial trailing byte; wait
   for the burst to fully serialise (busy clear); publish the frame counter.

Because payload/CRC generation does not feed the FIFO, each frame is its own
continuous-mode burst (FIFO drains between frames) rather than one unbroken
stream — this avoids FIFO under-run during generation.

## Host verification and capture

The perif TX timeline advances as PRU0 steps, recording the serial line's
timestamped transitions.  A go-flag handshake releases one frame at a time; the
driver steps until the frame counter increments, then reconstructs that burst's
bits by sampling the recorded line at bit centres (`go + (k+½)·period`), 8b/10b-
decodes them, checks them against the golden reference (zero BER), and writes
the decoded frames (FCS stripped) to a classic-format pcap (Ethernet link type).

## Testing

`tests/test_pif_eth.py`: codec round-trip and 8b/10b invariants; CRC/PRNG
references; frame builders; firmware self-configuration; firmware PRNG + CRC in
DRAM; zero-BER round trips for both frame types; valid-Ethernet check on the
decoded UDP frame; pcap output; and driving the firmware through the MCP server
wrapper.  A `driver.py` entry point runs 100 frames of each type and emits the
traces.

## Out of scope (first implementation)

Broadside CRC accelerator; 3-channel striping; hardware-exact 8b/10b table
interop; RX/receive path (the host decoder stands in for a receiver).
