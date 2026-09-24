# PRU Simulator

A cycle-accurate PRU assembly simulator with an interactive HTML dashboard. Targets the both AM261/AM263/AM62x ICSS_M PRU core (V3 ISA) and AM243x/AM64x ICSSG PRU core (V4 ISA) and runs entirely on a PC — no hardware required.

## Features

- **Full PRU ISA** — ALU, branches, bit operations, LOOP, LBCO/SBCO, LBBO/SBBO, XIN/XOUT/XCHG, MVI (MVIB/MVIW/MVID)
- **ELF binary loading** — load compiled .out files directly (TI PRU CGT ELF32) with automatic disassembly
- **Sigma-delta filter** — full ICSS-G SCU SD peripheral (3 channels, CIC accumulators, Fast Detect, memory-mapped registers, pattern generators)
- **Peripheral Interface** — 3-channel ICSS-G SCU serializer/deserializer (protocol-agnostic: EnDat/BiSS/HDSL/line-code). Per-channel TX FIFO/FSM/clock + RX FIFO/oversampler, selected via `GPCFG.PRU_GP_MUX_SEL`, with a timed PRU0-TX → core-1-RX loopback (latency / jitter / clock-drift). See `references/endat/ENDAT_INTERFACE_SPEC.md`.
- **GPIO loopback** — wire GPO groups directly to GPI for firmware loopback testing without hardware
- **UART decoder** — bit-bang UART decode in the IO panel (8N1, auto-detect bit period)
- **Step-back** — reverse any instruction; full machine state including SD filter is restored
- **Interactive dashboard** — register panel, memory panel, IO pin control, disassembly view
- **Signal graph** — digital logic analyzer for GPO/GPI pin transitions
- **Memory graph** — analog scope for memory buffer waveform visualization
- **MAC accelerator** — ICSSG broadside multiply-accumulate unit (device_id=0)
- **Multi-core** — simultaneous PRU0 + RTU0 debug view
- **Tiling window manager** — drag, split, collapse/expand, and persist panel layouts
- **MCP server** — AI assistant integration via Model Context Protocol

## PRU I/O Modes

| Mode Class | Specific I/O Mode | Core Registers Used | Primary Use Case / Description | Supported |
|---|---|---|---|---|
| GPI (Input) | Direct Input Mode | `R31[31:0]` | Lowest latency direct pin sampling and software bit-banging | ✅ |
| GPI (Input) | 16-Bit Parallel Capture | `R31` | Captures 16 bits of parallel data synchronous to a sampling clock | ❌ |
| GPI (Input) | 28-Bit Shift In Mode | `R31` | Automatically deserializes incoming high-speed serial streams | ❌ |
| GPO (Output) | Direct Output Mode | `R30[31:0]` | Direct software-driven pin control for custom low-latency protocols | ✅ |
| GPO (Output) | Shift Out Mode | `R30` | Automatically serializes data written to the register over a clock pin | ❌ |
| Interface | Peripheral IF Mode | Slice Hardware Co-processor | Industrial motor position encoder feedback (EnDAT, BiSS-C, Tamagawa) | ✅ |
| Interface | SD Mode | Internal SDFM Blocks | Connects to external Sigma-Delta ADCs for isolated current/voltage sensing | ✅ |
| Interface | MII / RGMII Mode | ICSSG Real-Time Ethernet Switch | Real-time industrial communication hardware layer (EtherCAT, PROFINET, EtherNet/IP) | ❌ |


## Requirements

- Python 3.9 or later
- Dependencies:

```
pip install -r requirements.txt
```

## Quick Start

```bash
python ui/server.py
```

Open `http://localhost:8080` in your browser.

## Project Structure

```
pru_simulator/
├── config/             Memory layout configs (AM243x, AM263x)
├── core/               PRU ISA implementation (ALU, parser, disassembler, ELF loader, …)
├── mem/                Memory bus and region model
├── mcp_server/         MCP server for AI tool integration
├── pru_io/             GPO/GPI port model, SD filter (R30/R31 interface)
├── perif/              3-channel Peripheral Interface (SCU), GPCFG mux, TX→RX loopback
├── source/             Example PRU assembly programs
├── tests/              Pytest test suite
├── ui/
│   ├── server.py       FastAPI WebSocket server
│   └── static/         Dashboard HTML/JS (index.html, app.js, layout.js)
├── xfr/                XFR scratchpad + MAC accelerator
├── memory.cfg          Default memory configuration (AM243x)
├── requirements.txt
└── simulator.py        Simulator entry point
```

## Memory Configuration

The simulator loads `memory.cfg` at startup. Three built-in configs are provided in `config/`:

| File | Target | Notes |
|---|---|---|
| `memory_am243x.cfg` | AM243x | Default |
| `memory_am263x.cfg` | AM263x | |

Switch config by editing `memory.cfg` or copying one of the `config/` files over it.

## Example Programs

See [getting_started.md](getting_started.md) for step-by-step walkthroughs of all examples in `source/`.

| File | What it demonstrates |
|---|---|
| `running_led.asm` | GPO pin toggle, LOOP instruction |
| `mem_copy.asm` | SBCO/LBCO data memory read/write |
| `xfr_test.asm` | XFR scratchpad (XOUT/XIN, SPAD banks) |
| `mac_example.asm` | MAC accelerator (MPY mode + accumulate mode) |
| `crc_example.asm` | CRC16/32 accelerator: CRC-16 (half-word writes) and CRC-32 (word writes) over one frame |
| `crc_bitswap_example.asm` | CRC16/32 accelerator bit-mirrored reads: byte-wide flip (`R27`) and 32-bit flip (`R28`), with byte-wide and 32-bit-wide data writes |
| `uart_tx.asm` | Bit-bang UART TX (115200 baud, 8N1) with UART decoder |
| `uart_rx_11frame.asm` | Bit-bang UART RX (4 Mbaud, 8N1) with frame injection from IO panel |
| `mvi_gpio_loopback.asm` | MVIB register-indirect + GPIO loopback (walking-bit pattern) |
| `sdfm_sinc3_demo/` | Free-running SINC3 filter adapted from AM261x ICSS-M firmware |
| `perif_duty_cycle_sweep.asm` | Peripheral Interface TX: 125 Mbit 0%→100% duty-cycle pulse sweep on PRU0 ch0 (needs `memory_perif_125mbit_demo.cfg`) |
| `pif_eth/` | 8b/10b line-coded Ethernet TX over the Peripheral Interface (PRU0 ch0): firmware PRNG, CRC-32 on the CRC16/32 broadside accelerator, running-disparity 8b/10b via DRAM0 LUT, pcap output. See [PROJECT_REPORT.md](source/pif_eth/PROJECT_REPORT.md) ([PDF](source/pif_eth/PROJECT_REPORT.pdf)) · [handoff note](docs/handoff/2026-07-20-pif-eth.md). Experimental higher-clock variants `pif_eth_tx_n2*.asm` (not wired into the test suite): [design note](docs/superpowers/specs/2026-07-21-pif-eth-n2-125mbaud-design.md). PRU1 RX over the PRU0→PRU1 loopback (`pif_eth_rx_o1_raw.asm` + `rx_driver.py`), CI-exercised: [design note](docs/superpowers/specs/2026-07-21-pif-eth-pru1-rx-design.md) · [handoff note](docs/handoff/2026-07-22-pif-eth-rx-o1.md) |

## Headless JSON runner

CI and agent workflows can execute an assembly source or a linked TI PRU ELF
without starting the web UI.  The command writes exactly one deterministic JSON
object to stdout; load errors exit 2, unmet conditions or exhausted budgets exit
3, and unexpected runner errors exit 4.

```bash
python -m tools.headless_runner --json --assembly source/example.asm --core pru0 --max-steps 10000
python -m tools.headless_runner --json --elf build/firmware.out --until 'reg:r0==0x55' --max-cycles 5000
```

`--until` accepts `halt` (the default), `cycles>=N`, register equality or
inequality, 32-bit little-endian memory comparisons, and GPI/GPO pin equality.
Both `--max-steps` and `--max-cycles` are hard budgets.  JSON remains the only
stdout format when `--json` is omitted; the flag makes the CI contract explicit.

## Running Tests

```bash
python -m pytest tests --tb=short -q
```

The maintained test suite lives under `tests/`.  Files under `references/` are
standalone experiments and may require optional packages (for example,
`matplotlib`); they are not part of the CI test suite.

## Version

v0.2.6 — hover over **PRU SIM** in the dashboard header to confirm.

### Changelog

**v0.2.6**
- **Peripheral Interface TX test patterns** (`source/perif_tx_patterns.asm`) — the existing `perif_tx_pattern.asm` streams an 8-bit counter, which is ~90% zeros MSB-first and reads as sparse noise on the Signal Graph. New self-configuring firmware transmits the classic bit patterns instead, selected by a `PATTERN .set` at the top of the file: `0x00`, `0xFF`, `0xAA`, `0x55`, walking 1, walking 0, counter-only, or (the default) one byte of each followed by the counter — `00 FF AA 55 | 01 02 04 08 10 20 40 80 | FE FD FB F7 EF DF BF 7F | 00 01 02 …`. Twenty bytes, about 1280 graph samples, so the 2048 window catches the whole sequence in one single-shot capture. Patterns come from a table the prologue writes to DRAM at `0x1F00`, so changing them is five `ldi` pairs. Keeps the same pre-shifted start-bit framing, so the receiver still byte-aligns; every `PATTERN` value is checked end-to-end over the ch0 loopback in `tests/test_perif_tx_patterns.py`. `perif_tx_pattern.asm` is untouched — the drift experiment and `tools/perif_drift_report.py` depend on its exact counter output. Cross-machine [handoff note](docs/handoff/2026-07-28-perif-tx-test-patterns.md).

**v0.2.5**
- **Signal Graph: Run now captures at the signal's own rate** — the graph was fed only by the state push at the *end* of each Run chunk, so it sampled once per 100+ instructions. That is orders of magnitude coarser than a Peripheral Interface bit (2 core cycles at the channel-0 `N=2` divider), so a Run capture of `perif_duty_cycle_sweep.asm` aliased away to nothing while the same firmware traced correctly under SIM, which steps one instruction per message. The server now samples inside its run loop and ships the batch as a `capture` message: every instruction while peripheral mode is active, every 100th otherwise — GP traces are firmware-paced (a 115200-baud bit-bang bit is ~1736 cycles) and the UART decoder's bit-period detection depends on the wider time span. The stride is decided per instruction, since firmware enables peripheral mode from inside the run. Peripheral captures are single-shot — at full rate a run fills the window in milliseconds, so it fills once and REC switches itself off, the way a logic analyzer does; GP captures keep rolling as before. Cross-machine [handoff note](docs/handoff/2026-07-28-run-mode-graph-capture.md).

**v0.2.4**
- **Signal Graph: peripheral mode no longer plots GPO/GPI** — in perif mode the GP Mux hands the pads to the Peripheral Interface, so the `GPO n` / `GPI n` lanes the graph kept drawing from R30/R31 showed pins that do not exist on the wire, next to the perif lanes that do. The digital graph now keys off `io.mode` (recorded per sample): in perif mode only the peripheral lanes are drawn, and each active channel contributes three of them — the new `perifN_out_en` joins `perifN_out` and `perifN_clk`, so it is visible when the channel actually drives the pad. A channel qualifies as active if *any* of its three signals toggles, and then all three lanes are drawn, so a steady `out_en` still appears beside the data it qualifies. GP mode is unchanged. CSV export gains `mode` and `perifN_out_en` columns. Cross-machine [handoff note](docs/handoff/2026-07-28-perif-mode-graph-lanes.md).

**v0.2.3**
- **Signal Graph: Peripheral Interface clock lanes** — the graph recorded `perifN_out` (the serial data line) but not its bit clock, so a correct capture was unreadable: the perif serializer sends raw MSB-first bits with no framing, leaving no reference for where a bit starts. New `perifN_clk` lanes (one clock edge per bit, paired with the data lane and included in the CSV export) make the bit grid visible. Note that firmware prefixing a start bit — e.g. `source/perif_tx_pattern.asm` — shifts the payload right by one bit, so wire octets begin one bit after the first rising edge.
- **Tests no longer depend on the UI's core-speed selection** — the speed selector rewrites `memory.cfg` in place, and any test constructing `Simulator()` / `PRUSimulatorMCP()` with no arguments inherits that clock. Picking anything other than 200 MHz in the dashboard therefore broke six cycle-timed tests (bit-bang UART RX ×4, PRU1 clock config, SD channel switching). New `tests/conftest.py` provides a `sim_config` / `nominal_config` fixture that builds a throw-away `memory.cfg` with the clock pinned; the SD test now matches its modulator clock to the configured core clock instead of a hardcoded 200 MHz. Suite verified green at 200, 250 and 333 MHz.
- **Reset now clears Peripheral Interface state** — the UI's *HW Reset* (and the per-core *Reset*) left the perif's latched status bits standing, so a TX overrun/underrun, `rx_valid`/`rx_ovf`, `busy` or a half-full FIFO from the previous run stayed visible in the Peripheral panel and in R31 after the reset. `PRUCore.reset()` now resets its IO port, which resets the attached `PeripheralInterface`: per-channel TX/RX FIFOs, overrun/underrun, RX valid/overflow/EOF, capture progress, FSM/pins, recorded line history and the ns timeline, plus the R30 channel select and the TXCFG busy bits. Config is deliberately preserved — perif config registers (clock dividers, frame sizes, delays), the GP Mux selection and the loopback parameters all survive a reset, as does GPI (external stimulus, not core state). Cross-machine [handoff note](docs/handoff/2026-07-27-perif-reset-and-graph-clock-lanes.md) covering all three v0.2.3 changes.

**v0.2.2**
- **pif_eth — PRU1 RX over the PRU0→PRU1 loopback (Option 1)** — new `pif_eth_rx_o1_raw.asm` + `rx_driver.py` receive the 8b/10b line-coded frames PRU0 transmits, at 2x-oversampled RX sample clock (a hardware requirement — the RX shift register doesn't decimate), with SOF from the RX hardware's own start-bit detection and EOF from a 2-consecutive-zero-byte proxy (one zero byte is a legal 5-bit run, not EOF). Post-frame comma-align, 8b/10b decode, CRC-32 and PRNG BER-check happen off the realtime path. Measured **zero BER across the full n_tx ladder (2/4/6/8 → 125.00/62.50/41.67/31.25 Mbaud), 7 seeds each** — including `n_tx=2`, which the design's own risk section expected might not be reachable by the 8-cycle realtime budget; it passes, but at *zero* headroom (7 instructions + a 1-cycle DRAM write stall exactly fill the budget). This also promotes `pif_eth_tx_n2*.asm` from reference-only artifacts (v0.2.1) to firmware `rx_driver.py` actually selects and exercises by divider. Design: [`2026-07-21-pif-eth-pru1-rx-design.md`](docs/superpowers/specs/2026-07-21-pif-eth-pru1-rx-design.md); cross-machine [handoff note](docs/handoff/2026-07-22-pif-eth-rx-o1.md).
- **fix(pif_eth): bound the RX frame buffer** — the post-frame decode stage wrote decoded octets into the 256 B reconstructed-frame buffer with no bound, so `payload_len > 252` walked writes into the adjacent stats/control blocks, poisoning `go`/`seed`/`payload_len`/`rxcfg` for later frames. Fixed with a firmware-side backstop (`eof_status = 2` on overrun) plus a host-side `ValueError` for oversized `payload_len` in `rx_driver.py`.

**v0.2.1**
- **PRU core speed selector** — controls-bar dropdown (200/225/250/300/333 MHz) sets `pru_clock_mhz` and `pru1_clock_mhz` together via a new `GET`/`PUT /config/clock_speed` endpoint, which patches `memory.cfg` in place (preserving formatting/comments — no `configparser` round-trip) and reloads the simulator. Always syncs all three cores; the [PRU0→PRU1 perif clock-drift demo](docs/superpowers/specs/2026-07-18-pru1-perif-drift-design.md) remains a separate manual `pru1_clock_mhz` edit, applied *after* picking a base speed from the dropdown. Design: [`2026-07-20-pru-core-speed-selector-design.md`](docs/superpowers/specs/2026-07-20-pru-core-speed-selector-design.md).
- **pif_eth experimental 125 Mbaud TX firmware** (`source/pif_eth/pif_eth_tx_n2*.asm`) — batched 4-octet loads and fixed-shift unrolled 8b/10b bit-packing get the TX loop's own cost down to the perif drain-rate boundary. The all-checks build is a robust ~4x speedup (83.33 MHz @ n=3, zero BER) over the original 25 MHz design; a second build with 2 of the loop's FIFO-full checks removed hits the full 125 Mbaud (n=2) target with zero BER, but is hard-pinned to that exact divider — it silently corrupts frames at any other clock/divider combination. Neither file replaces `pif_eth_tx.asm` in the test suite. Caveat and full results: [`2026-07-21-pif-eth-n2-125mbaud-design.md`](docs/superpowers/specs/2026-07-21-pif-eth-n2-125mbaud-design.md); cross-machine [handoff note](docs/handoff/2026-07-21-speed-selector-and-pif-eth-n2.md).

**v0.2.0**
- **pif_eth — 8b/10b line-coded Ethernet TX over the Peripheral Interface** (PRU0, ch0): self-configuring firmware with an xorshift32 PRNG, bit-serial CRC-32 FCS, true 8b/10b (running disparity) via a 256-entry DRAM0 LUT (`LBCO`/`c24`), K28.5 inter-frame commas, and a Python decoder/driver that checks BER=0 and writes Wireshark pcaps. Two example frames — BERT (132 B) and UDP "Hello World Text" (64 B). Full write-up: [`source/pif_eth/PROJECT_REPORT.md`](source/pif_eth/PROJECT_REPORT.md) ([PDF](source/pif_eth/PROJECT_REPORT.pdf)); cross-machine [handoff note](docs/handoff/2026-07-20-pif-eth.md).


**v0.1.9**
- **TX continuous mode (live FIFO streaming)** — corrected against the AM243x TRM (Table 6-424): `tx_frame_size = 0` now pops one byte at a time from a *live* FIFO as each finishes shifting out (instead of snapshotting the queue at go-time), so software can push the next byte while the current one transmits. R31's `tx_fifo_sts0` (bits `[4:2]`) reports true live occupancy.
- **Half-empty refill** — `source/perif_duty_cycle_sweep.asm` now streams its whole 9-byte sweep as one continuous, gapless transmission (verified: exactly 576 ns / 9×64 ns, zero dead time between bytes), refilling whenever occupancy drops to the TRM-specified 2-byte level, instead of one preload-and-go frame per byte.
- **R31 TX status corrected for channel 0** against TRM Table 6-424: `ovr0[0]`/`unr0[1]`/`tx_fifo_sts0[4:2]`/`busy0[5]` confirmed bit-exact. Channels 1/2 are flagged in the spec as a known simplification (still byte-aligned, not yet TRM-verified) pending the remaining register field table.

**v0.1.8**
- **Peripheral Interface (3-channel SCU)** — protocol-agnostic serializer/deserializer modeled on the validated `references/endat` RTL. Per-channel TX FIFO + wire/Tst-delay FSM + MSB-first serializer (frame size, bit-swap, clock modes 0–3, overrun/underrun) and RX FIFO + start-bit/oversample capture + frame-size EOF. Backend in `perif/`.
- **GP-Mux mode select** — `GPCFG.PRU_GP_MUX_SEL[29:26] == 1` (GPCFG0 `0x26008` / GPCFG1 `0x2600C`) routes R30/R31 to the peripheral and switches the IO window to the Peripheral panel; selectable from the new "GP Mux" dropdown.
- **Timed PRU0→core-1 loopback** — per-channel serial-sample loopback with configurable latency, jitter, and clock drift (±100 ppm) on independent per-core nanosecond timelines; large drift produces real RX bit-slip.
- **Peripheral UI panel** — per-channel TX/RX FIFO, FSM/status flags and config, plus a loopback control block; step-back captures peripheral + line state. GPO recording via the existing Signal Graph.
- MMR block per core at the AM243x `EDPRU0`/`EDPRU1` offsets (`0x260E0` / `0x26100`).

**v0.1.7**
- **UART RX receiver** — 4 Mbaud bit-bang UART receiver assembly (`uart_rx_11frame.asm`): polls GPI0 for start bit, assembles 11-byte frames into register buffer, stores via single SBCO to DRAM0, framing error detection with sticky flag at DRAM0+0x0FFE
- **UART frame injection UI** — new "UART RX Inject" panel in the IO section: hex/ASCII payload toggle, configurable pin/baudrate (Mbaud with 2 decimals)/frame count, arms UARTFrameGenerator at current cycle for step-through reception
- **UARTFrameGenerator peripheral** — pre-computes bit-level UART waveform timeline on GPI pins; supports multi-frame, configurable baudrate, framing error injection for testing
- **MCP `pru_uart_inject` tool** — end-to-end UART RX testing via MCP server (load assembly, inject frames, verify DRAM0 contents)
- Baudrate tolerance tested: receiver handles 3.75–4.00 Mbaud (−6.25% to nominal)

**v0.1.6**
- **Disassembler validation** — compiled reference assembly (`pru_encoding_test.asm`) with TI CGT clpru v2.3.3; 150 parametrized tests verify all instruction formats decode correctly
- **ISA execution validation** — 97-test firmware (`isa_execution_test.asm`) runs on real PRU hardware and in simulator; byte-for-byte DRAM comparison confirms execution match
- **Pseudo-instruction reconstruction** — disassembler now shows NOP, MOV, ZERO, FILL instead of raw AND/XIN encodings
- Fix: WBS/WBC were swapped in disassembler (QBBS+offset=0 is WBC, not WBS)
- Fix: LBCO/SBCO constant table resolution from ELF-loaded code (C24 was treated as raw address 24)
- Fix: Carry/borrow convention corrected to match hardware (carry=1 when no borrow, ARM-like)
- Fix: SUC semantics corrected to a - b - ~C (subtracts NOT-carry as borrow)

**v0.1.5**
- **MVI instructions** — MVIB/MVIW/MVID with all 16 addressing modes (direct, register-file indirect, pre-decrement, post-increment)
- **GPIO loopback** — compact toggle strip in IO panel wires GPO groups to GPI combinatorially; guarded against SD mode
- `mvi_gpio_loopback.asm` — walking-bit GPO→GPI demo using MVIB register-file indirect

**v0.1.4**
- **Sigma-delta filter peripheral** — full ICSS-G SCU SD model: 3-channel CIC filter (SINC3/SINC2/SINC1), Fast Detect sliding window, 2nd-order ΣΔ modulator pattern generator, memory-mapped registers at real ICSS-G addresses
- **SD UI panel** — R30 decode bar, per-channel accumulator display, pattern generator controls, config register shortcuts
- **Step-back** — full machine snapshot/restore including SD filter integrators and modulator state
- **SDFM SINC3 demo** — free-running SINC3 filter adapted from real AM261x ICSS-M firmware (`source/sdfm_sinc3_demo/`)
- Preprocessor `.asg` directive support (TI alias syntax, reversed arg order vs `.set`)

**v0.1.3**
- **UART decoder** — ▶ Decode / Clear buttons in IO panel; auto-detects bit period, displays decoded bytes as hex + ASCII

**v0.1.2**
- **ELF binary loading** — Open > Browse > select .out file; auto-disassembles and loads
- **Disassembler** — decodes all PRU instruction formats (PDSP v2.4.8 spec)
- Signal graph and memory graph split into separate, independent tileable panels
- IO panel now displays only GPO/GPI pin indicators (compact)
- Collapse/expand button (−/+) on every panel title bar for quick window management
- Resizable divider between Registers and C-Table sections
- Updated default layouts for single-core and multi-core modes
- Fix: LBBO/SBBO/XFR byte-address offset (RxByteAddr) now correctly handled
- Fix: Partial register writes preserve untouched bytes (byte-level write strobes)
- Fix: LSL/LSR/CLR/SET mask shift/bit amount to 5 LSBs per hardware spec

**v0.1.1**
- Signal graph with digital and analog zones
- Memory fill modal (pattern, sequence, waveform)
- Tiling window manager with drag-to-split

**v0.1.0**
- Initial release: full PRU ISA, dashboard, multi-core, MCP server
