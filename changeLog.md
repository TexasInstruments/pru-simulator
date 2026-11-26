# Changelog

**v0.4.0**

- **Linked browser tabs** — open the same dashboard URL on multiple screens. Code, registers, signals, devices, memory and execution status come from one simulator. The execution-core list and Run/Stop are shared. Each view keeps its own page, inspected core and layout; resetting a layout does not change the execution cores. Opening another tab does not start another run.
- **Redesigned dashboard** — Simulator and I/O & Devices workspaces, persistent layouts, compact controls, consistent button motion, and dark/high-contrast themes.
- **CORES deck** — select which cores execute and which are shown. The default uses one shared source/register view; selected cores can be expanded into separate panes. Clock controls show the affected cores without claiming a hardware clock-domain layout.
- **Per-core assembly editing** — choose the load target in the editor, keep separate files and drafts for each core, and close files without affecting another core's editor or loaded program.
- **Device demos and diagnostics** — set up SSI model/reader and encoder-emulator demos with matching frame settings and timing, inspect TCA9538, UART, sigma-delta and Peripheral Interface state, and review device events and faults. Motor control follows in a separate release.
- **UI fixes** — repeated assembly loads (#53), UART recording controls (#55), UART injection-form reset (#56) and minimized-panel restoration (#61). See [dashboard instructions](docs/simulator_dashboard.md).

**v0.3.0**
- **SSI encoder simulation** — binary or Gray frames up to 64 bits, optional error fields and twelve documented RM08/SICK presets, with faults for excessive clock rate, incomplete frames and extra pulses.
- **Reader and emulator firmware** — generated configuration ABI, seqlock mailbox and host runtimes support model-to-reader and two-core firmware loopback tests. Separate Python decoding keeps validation independent; failed reader reloads preserve the last working configuration.
- **Generic MCP device tools** — discover, attach, detach and inspect SSI or TCA9538 devices, events and faults through the MCP SDK stdio transport. See [SSI setup and limitations](docs/ssi_device_model.md).

**v0.2.10**
- **Shared device bus** — extends the DeviceModel contract from #44 with GPIO drive ownership, pin release on detach, core-to-core wires and contention faults. TCA9538 uses the same device path; time-driven devices include stall cycles.
- **Coherent multi-core step-back** — restores all cores, shared memory, MAC/XFR, IEP and attached devices to one saved point, discarding later peer history.

**v0.2.9**
- **Exact shared IEP time** — one rational timebase includes memory stalls and preserves compare/capture behavior across different core clocks. Omitted core and external IEP rates default to 200 MHz; explicit rates remain configurable.
- **RTU1 and paced execution** — AM243x/AM64x profiles gain RTU1. Multi-core stepping follows elapsed time, reports actual progress when catch-up is bounded, and keeps Peripheral Interface timestamps aligned after a core reset.
- **IEP-aware step-back** — snapshots restore the shared timer, reject unsafe undo after a peer core progresses, and clear all core histories on configuration changes.
- **Per-core constant addresses** — C10/C11/C22 selection follows the executing core where the target defines it; corrected addresses do not add the corresponding register models.

**v0.2.8**
- **Core faults and wait instructions** — memory faults are available in simulator, MCP and dashboard state; `WBS`/`WBC` accept implicit R31 and explicit register forms, with immediate bit numbers limited to 0–31.
- **Hardware reset** — resets IEP counter, compare and capture state to the documented register defaults. See the IEP reset notes above for increments and capture limits.
- **Configuration and MCP safety** — invalid configurations are rejected before writing, copied configurations can locate their constants file, and MCP calls validate argument types.

**v0.2.7**
- **CRC16/32 broadside accelerator** (XFR `device_id=1`) — modeled on AM64x/AM243x TRM (SPRUIM2H) §6.4.6.2.2.1 / Table 6-429: `R25` CRC_CFG, `R27` CRC_DATA_8_BFLIP (read), `R28` CRC_SEED / CRC_DATA_32_BFLIP (read), `R29` CRC_DATA (destructive read resets the accumulator to the seed). CRC-16 (standard/mod) and CRC-32, matched to AM243x silicon: `R29` is the non-reflected accumulator and `R28` the reflected (zlib/Ethernet) value; CCITT mode feeds bytes MSB first. Byte, half-word and word writes give the same result. Shares the broadside window with the MAC accelerator, selected by `device_id`. The dashboard shows a live CRC status widget (mode and accumulator) in the single-core and multi-core views. Backend in `xfr/crc_accelerator.py`; functional spec of the underlying RTL in `references/icss_m_xfr_crc_functional_spec.md`.
- **CRC examples** — `source/crc_example.asm` (CRC-16 with half-word writes, CRC-32 with word writes, using the TRM's NOP-based programming model) and `source/crc_bitswap_example.asm` (reads R27 byte flip, R28 32-bit flip and R29, with R29 read last). Both are checked end-to-end in `tests/test_crc_example.py` and `tests/test_crc_bitswap_example.py`.
- **pif_eth computes the Ethernet FCS on the CRC accelerator** — new `pif_eth_crc32_hw.inc` is a drop-in `crc32_core` with the same contract as the bit-serial routine: 32-bit words go through CRC_DATA in a zero-overhead `LOOP`, and 1–3 trailing bytes run as a byte-wide session re-seeded via CRC_SEED (the TRM requires one write width per session). All four firmware images use it, and the n2 TX variants drop their inlined bit-serial copies. `pif_eth_crc32.inc` stays as the software fallback. A 128-octet FCS goes from ~9.5k to 181 cycles on TX and 187 on RX (~50x), and is now data-independent. `rx_driver.py` still passes all 28 rung/seed combinations. README and PROJECT_REPORT (+PDF) updated.
- **IEP follows simulated time on its own clock** — the IEP used to tick once per instruction on every core, so memory stall cycles were lost and two cores stepping together ran it at twice the rate. Each core now reports its elapsed time (cycles including stalls, over its own clock), and the shared IEP follows the core that is furthest ahead, emitting one edge per IEP clock period. A core whose cycle counter is reset rejoins at the IEP's current time instead of freezing the counter. The IEP clock is independent of the PRU clocks: `iep_clock_mhz` under `[device]` (default `pru_clock_mhz`), or `Simulator.set_iep_clock_mhz()` at runtime without a count jump.
- **Hardware test plan for the CRC changes** — [`docs/test-plans/2026-09-24-crc-accelerator-hw-test-plan.md`](docs/test-plans/2026-09-24-crc-accelerator-hw-test-plan.md) (TC1–TC7, results table to fill in on silicon). The CRC results in this release are from the simulator and tested on silicon.

**v0.2.6**
- **Peripheral Interface TX test patterns** (`source/perif_tx_patterns.asm`) — the existing `perif_tx_pattern.asm` streams an 8-bit counter, which is ~90% zeros MSB-first and reads as sparse noise on the Signal Graph. New self-configuring firmware transmits the classic bit patterns instead, selected by a `PATTERN .set` at the top of the file: `0x00`, `0xFF`, `0xAA`, `0x55`, walking 1, walking 0, counter-only, or (the default) one byte of each followed by the counter — `00 FF AA 55 | 01 02 04 08 10 20 40 80 | FE FD FB F7 EF DF BF 7F | 00 01 02 …`. Twenty bytes, about 1280 graph samples, so the 2048 window catches the whole sequence in one single-shot capture. Patterns come from a table the prologue writes to DRAM at `0x1F00`, so changing them is five `ldi` pairs. Keeps the same pre-shifted start-bit framing, so the receiver still byte-aligns; every `PATTERN` value is checked end-to-end over the ch0 loopback in `tests/test_perif_tx_patterns.py`. `perif_tx_pattern.asm` is untouched — `tests/test_perif_drift_experiment.py` depends on its exact counter output. Cross-machine [handoff note](docs/handoff/2026-07-28-perif-tx-test-patterns.md).

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
- **PRU core speed selector** — controls-bar dropdown (200/225/250/300/333 MHz) sets the single `pru_clock_mhz` (all cores share one frequency) via a new `GET`/`PUT /config/clock_speed` endpoint, which patches `memory.cfg` in place (preserving formatting/comments — no `configparser` round-trip) and reloads the simulator. One frequency applies to every core; the earlier per-core drift demo was removed. Design: [`2026-07-20-pru-core-speed-selector-design.md`](docs/superpowers/specs/2026-07-20-pru-core-speed-selector-design.md).
- **IEP timer clock** — IEP0 now runs on its own clock, set with `iep_clock_mhz` under `[device]` (default: `pru_clock_mhz`) or at runtime with `Simulator.set_iep_clock_mhz()`. It is driven by simulated time, not instruction count: memory stall cycles advance it, and two cores stepping together advance it once, not twice. See the TIMING notes in `perif/iep.py`.
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
