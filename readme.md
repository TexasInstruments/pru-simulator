# PRU Simulator

A cycle-accurate PRU assembly simulator with an interactive HTML dashboard. No
hardware required: Runs entirely on a PC, with the option for your AI to use the
simulator through an MCP server.

## Supported features

The simulator currently models the PRU subsystems on the **AM243x** (PRU_ICSSG,
V4 ISA) and **AM263x** (PRU-ICSS, V3 ISA).

### Simulator features

These are simulator-side capabilities, not features of the PRU hardware itself.

- **Interactive dashboard** — register panel, memory panel, IO pin control, disassembly view
- **ELF binary loading** — load compiled .out files directly (TI PRU CGT ELF32) with automatic disassembly
- **Step-back** — reverse any instruction; full machine state including SD filter is restored
- **Signal graph** — digital logic analyzer for GPO/GPI pin transitions
- **Memory graph** — analog scope for memory buffer waveform visualization
- **GPIO loopback** — wire GPO groups directly to GPI for firmware loopback testing without hardware. Can specify loopback latency/jitter/clock-drift
- **Device bus** — attach external device models (`DeviceModel`) to any core's GPIO pins and wire pins between cores. A shared `DeviceBus` resolves open-drain nets as wired-AND and reports push-pull contention as a fault instead of last-writer-wins; devices expose `events()` and `faults()` for checking firmware
- **SSI encoder model and reader** — independent binary/Gray encoder `DeviceModel` with up to 64-bit frames, error fields and 12 encoder presets, clock-rate and monoflop faults, generated shared-memory ABI, and PRU1 reader and PRU0 emulator examples. See [SSI device model and reader](docs/ssi_device_model.md)
- **UART decoder** — bit-bang UART decode in the IO panel (8N1, auto-detect bit period)
- **Multi-core simulation & debug view** — PRU_ICSSG supports simultaneous simulation & debugging of up to 3 PRU cores; PRU-ICSS supports simultaneous simulation & debugging of both PRU cores
- **Tiling window manager** — drag, split, collapse/expand, and persist panel layouts
- **MCP server** — AI assistant integration via Model Context Protocol


### PRU features

Status per processor:
- ✅ implemented in the simulator
- ❌ present in silicon, not currently simulated
- N/A not present on that processor (per the [PRU Subsystem Features Comparison](https://www.ti.com/lit/sprac90) app note)

| Category | Feature | AM243x | AM263x |
|---|---|---|---|
| Core | Full PRU ISA (ALU, branches, bit ops, LOOP, LBCO/SBCO, LBBO/SBBO, XIN/XOUT/XCHG, MVI) | ✅ | ✅ |
| General PRU Specs | PRU cores | ✅ | ✅ |
| General PRU Specs | RTU_PRU (Auxiliary PRU) cores | ✅ | N/A |
| General PRU Specs | TX_PRU (Transmit PRU) cores | ❌ | N/A |
| General PRU Specs | INTC | ❌ | ❌ |
| Accelerators: Data Processing | MPY/MAC | ✅ | ✅ |
| Accelerators: Data Processing | Scratchpad | ✅ | ✅ |
| Accelerators: Data Processing | IPC Scratch Pad | ✅ | N/A |
| Accelerators: Data Processing | Broadside RAM | ❌ | N/A |
| Accelerators: Data Processing | BSWAP | ✅ | N/A |
| Accelerators: Data Processing | CRC16/32 | ❌ | ❌ |
| Accelerators: Data Processing | SUM32 | ❌ | N/A |
| Accelerators: Data Processing | Task Manager | ❌ | N/A |
| Accelerators: Data Processing | Spinlock | ❌ | N/A |
| Accelerators: Data Processing | Filter Data Base (FDB) | ❌ | N/A |
| Accelerators: Data Movement | XFR2VBUS | ❌ | ❌ |
| Accelerators: Data Movement | PSI TX & RX | ❌ | N/A |
| Accelerators: Data Movement | XFR2TR | ❌ | N/A |
| Peripherals | Sigma-delta filter (3 ch, CIC accumulators, Fast Detect, pattern generators) | ✅ | ✅ |
| Peripherals | Peripheral Interface / 3 ch EnDAT (protocol-agnostic serializer/deserializer, used for EnDat/BiSS/HDSL/line-code) | ✅ | ✅ |
| Peripherals | UART | ❌ | ❌ |
| Peripherals | eCAP | ❌ | ❌ |
| Peripherals | IEP (Industrial Ethernet Peripheral: timer/compare/capture) | ✅ | ✅ |
| Peripherals | MII_RT / MII_G_RT (RGMII) | ❌ | ❌ |
| Peripherals | MDIO | ❌ | ❌ |
| Peripherals | SGMII | ❌ | N/A |
| Peripherals | PWM | ❌ | N/A |

### PRU I/O modes

| Mode Class | Specific I/O Mode | Core Registers Used | Primary Use Case / Description | AM243x | AM263x |
|---|---|---|---|---|---|
| GPI (Input) | Direct Input Mode | `R31[31:0]` | Lowest latency direct pin sampling and software bit-banging | ✅ | ✅ |
| GPI (Input) | 16-Bit Parallel Capture | `R31` | Captures 16 bits of parallel data synchronous to a sampling clock | ❌ | ❌ |
| GPI (Input) | 28-Bit Shift In Mode | `R31` | Automatically deserializes incoming high-speed serial streams | ❌ | ❌ |
| GPO (Output) | Direct Output Mode | `R30[31:0]` | Direct software-driven pin control for custom low-latency protocols | ✅ | ✅ |
| GPO (Output) | Shift Out Mode | `R30` | Automatically serializes data written to the register over a clock pin | ❌ | ❌ |
| Interface | Peripheral IF Mode | Slice Hardware Co-processor | Industrial motor position encoder feedback (EnDAT, BiSS-C, Tamagawa) | ✅ | ✅ |
| Interface | SD Mode | Internal SDFM Blocks | Connects to external Sigma-Delta ADCs for isolated current/voltage sensing | ✅ | ✅ |
| Interface | MII / RGMII Mode | ICSSG Real-Time Ethernet Switch | Real-time industrial communication hardware layer (EtherCAT, PROFINET, EtherNet/IP) | ❌ | ❌ |


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
├── pru_io/             GPO/GPI port model, SD filter (R30/R31 interface), DeviceModel/DeviceBus, device models (SSI encoder, TCA9538), generated ABIs
├── perif/              3-channel Peripheral Interface (SCU), GPCFG mux, TX→RX loopback
├── schema/             Shared-memory ABI schemas (source for generated .py/.inc files)
├── source/             Example PRU assembly programs
├── tests/              Pytest test suite
├── tools/              Headless runner, ABI code generators, perif drift report
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
| `memory_am261x.cfg` | AM261x | MSRAM burst timing measured on LP-AM261; selected by cfg file, not by target |

Switch config by editing `memory.cfg` or copying one of the `config/` files over it.

Clock keys in the `[device]` section:

| Key | Applies to | Default if absent |
|---|---|---|
| `pru_clock_mhz` | PRU0, RTU0, and the IEP OCP clock | 200 |
| `pru1_clock_mhz` | PRU1, RTU1 | inherited core |
| `iep_clock_mhz` | IEP while ICSS CFG `IEPCLK` bit 0 is clear | 200 |

Omitting `pru_clock_mhz` preserves the simulator's 200 MHz core default; omitting `pru1_clock_mhz` inherits the first rate. Explicitly configured rates, including 250 MHz, remain unchanged. The external IEP defaults independently to 200 MHz, the rate the TRM gives for ICSSG_IEP_CLK (SPRUIM2J §6.4.13.2.4). Core and IEP clocks remain independently configurable, including positive fractional MHz values, and changing the core speed leaves the external IEP clock unchanged. Firmware's IEPCLK register selects which configured source drives the timer.

With both rates omitted, 1000 unstalled core cycles produce 1000 IEP ticks with `CNT_ENABLE=1` and `DEFAULT_INC=1` while IEPCLK bit 0 is clear. The supplied `memory.cfg` explicitly selects 250 MHz cores; with the omitted external IEP rate of 200 MHz, it produces 800 ticks for the same 1000 core cycles. See [clock sources and configuration](getting_started.md) for the cited TI firmware examples. RTU1 is available only on the AM243x/AM64x profiles; an omitted target selects AM243x.

The simulator's IEP `GLOBAL_CFG` reset value, `0x550`, follows the TRM register defaults (IEP_GLOBAL_CFG_REG, Table 14-10907: `CMP_INC` and `DEFAULT_INC` reset to 5h, `CNT_ENABLE` to 0h). `DEFAULT_INC=5` counts nanoseconds at the default 200 MHz IEP clock, where one tick is 5 ns, so firmware that enables the counter with a read-modify-write (`GLOBAL_CFG | 1`) gets a nanosecond count. If you set a different IEP clock, for example 250 MHz (a 4 ns tick), firmware must set `DEFAULT_INC` itself (4 at 250 MHz) to keep a nanosecond count. A full-word write of `1` leaves `DEFAULT_INC=0`, and the counter does not advance. The SSI runtime (`pru_io/ssi_runtime.py`) is the in-repo example: it enables a stopped IEP with `DEFAULT_INC=1` written explicitly and sizes its timeouts from the active IEP clock.

All cores share one IEP timer. Each core's elapsed cycles, including stalls,
are converted to time with exact rational clock periods, so cores at different
clocks stay aligned without float drift.

Count-register writes preserve the running IEP clock phase, including byte and
64-bit writes. Enabling the counter or changing its increment or effective clock
starts a new phase.

Dashboard core and load selectors follow the configured target, including RTU1
on AM243x/AM64x. Each core has an independent constant table. Fixed C10,
C11 and C22 addresses follow the executing core on AM243x/AM64x (SPRUIM2J,
Table 6-65, pp. 581–582); AM263x PRU0/PRU1 use their documented C11/C22
addresses. Other configured entries are preserved. AM263x's legacy simulated
RTU0 has no hardware counterpart and keeps the common table; this change does
not add hardware support for that core. Correct address selection does not
implement the task manager, control or RAT register behavior. Accesses still
require the corresponding memory region/model in the chosen configuration.
The dashboard constant-table panel shows the common sidecar reference, not
the executing core's corrected table.

Dashboard step-back uses one ordered history across all cores. Undo restores
all cores, shared memory, the IEP and devices to the same saved point, and
removes later history entries so peer state stays consistent.

Paced stepping limits each follower to 1000 catch-up instructions per lead step.
`step_paced` and `step_paced_many` return `complete`, actual `lead_steps`, and the selected `targets`
(in exact IEP time units). An unfinished catch-up returns `complete=False` with
`reason="pacing_incomplete"`; MCP reports the same reason and dashboard Run stops
with a message. Another call continues execution from the actual core state;
elapsed time is never skipped. A follower that reaches HALT or the end of its
program finishes normally, while a recorded CPU fault remains an error. The
guard target is fixed for that catch-up even if firmware changes the mux.
Unfinished targets are retained per lead and follower set: retries finish them
before advancing that lead again, so `lead_steps` can be zero during catch-up.
Other follower selections do not execute or discard that saved work. A core
reset discards pending targets; dashboard step-back restores them with time.

Batched callers must count `lead_steps`, not the requested batch size: a
bounded catch-up may return before the batch finishes. Retry `pacing_incomplete`;
report `pacing_catchup_failed` instead of treating it as a completed batch.
Peripheral Interface timestamps use the same core-time origin as the IEP,
including after resetting one core. A full hardware reset starts both at zero.

### IEP reset and capture limits

`Simulator.hard_reset()` now calls `IepTimer.hardware_reset()`, so the counter, compare and capture state no longer survive a full reset. Register defaults follow [AM64x/AM243x TRM SPRUIM2J](https://www.ti.com/lit/pdf/spruim2), Tables 14-10907 and 14-10925: the counter stays disabled, with `GLOBAL_CFG` defined fields `0x550` and `CAP_CFG` defined fields `0x1FC00`. Reset leaves `DEFAULT_INC=5`, so enabling only `CNT_ENABLE` with a read-modify-write advances the counter by five per IEP tick; firmware must set `DEFAULT_INC` explicitly when another increment is required. Capture configuration follows Table 14-10925: bits 9:0 enable slots, bits 17:10 configure synchronization, and bits 23:18 select external sources. The simulator latches the first event until its valid bit is cleared; input synchronization, source routing and continuous overwrite are not modelled. The earlier synthetic first/last bits have been removed because they overlapped these fields.

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
| `ssi_generic_reader/ssi_generic_reader.asm` | SSI encoder reader (1-64 bit frames) and generated shared-memory mailbox; see [timing and manual 300 MHz instructions](docs/ssi_device_model.md) |
| `ssi_generic_emulator.asm` | SSI encoder emulator on PRU0 (1-64 bit frame read from a shared-memory block at every frame start); pairs with the reader in a board loopback, see [docs/ssi_device_model.md](docs/ssi_device_model.md) |
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

v1.0.0 — hover over **PRU SIM** in the dashboard header to confirm.

### Changelog

**v1.0.0**
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
