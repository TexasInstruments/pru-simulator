# PRU Simulator

A cycle-accurate PRU assembly simulator with an interactive HTML dashboard. No
hardware required: Runs entirely on a PC, with the option for your AI to use the
simulator through an MCP server.

## Supported features

The simulator models **AM243x/AM64x** (PRU_ICSSG) and **AM261x/AM263x**
(PRU-ICSSM; V3 cores, while ICSSG is V4). **AM62x** is also selectable; its profile is copied from
AM261x and not verified on hardware. Integrated accelerators differ by target; see
[device capabilities](docs/device_capabilities.md) for the hardware profiles,
unsupported-transfer diagnostics and silicon evidence.

### Simulator features

These are simulator-side capabilities, not features of the PRU hardware itself.

- **Interactive dashboard** — register panel, memory panel, IO pin control, disassembly view; Simulator, Devices and Events views with persistent panel visibility and dark/high-contrast themes. See [dashboard controls](docs/simulator_dashboard.md)
- **ELF binary loading** — load compiled .out files directly (TI PRU CGT ELF32) with automatic disassembly
- **Step-back** — reverse any instruction; full machine state including SD filter is restored
- **Signal graph** — digital logic analyzer for GPO/GPI pin transitions
- **Memory graph** — analog scope for memory buffer waveform visualization
- **GPIO loopback** — wire GPO groups directly to GPI for firmware loopback testing without hardware. Can specify loopback latency/jitter/clock-drift
- **Device bus** — attach external device models (`DeviceModel`) to any core's GPIO pins and wire pins between cores. A shared `DeviceBus` resolves open-drain nets as wired-AND and reports push-pull contention as a fault instead of last-writer-wins; devices expose `events()` and `faults()` for checking firmware
- **SSI encoder model and reader** — independent binary/Gray encoder `DeviceModel` with up to 64-bit frames, error fields and 12 encoder presets, clock-rate and monoflop faults, generated shared-memory ABI, and PRU1 reader and PRU0 emulator examples. See [SSI device model and reader](docs/ssi_device_model.md)
- **UART decoder** — bit-bang UART decode in the IO panel (8N1, auto-detect bit period)
- **Multi-core simulation & debug view** — PRU_ICSSG supports simultaneous simulation & debugging of up to 4 PRU cores (PRU0, RTU0, PRU1, RTU1), shown as PRU0 plus a selected partner; PRU-ICSS supports simultaneous simulation & debugging of both PRU cores
- **Tiling window manager** — drag, split, collapse/expand, and persist panel layouts
- **MCP server** — AI assistant integration via Model Context Protocol


### PRU features

Status per processor:
- ✅ implemented in the simulator
- ❌ present in silicon, not currently simulated
- N/A not present on that processor (per the [PRU Subsystem Features Comparison](https://www.ti.com/lit/sprac90) app note)

| Category | Feature | AM243x, AM64x | AM261x, AM263x | AM62x |
|---|---|---|---|---|
| Core | Full PRU ISA (ALU, branches, bit ops, LOOP, LBCO/SBCO, LBBO/SBBO, XIN/XOUT/XCHG, MVI) | ✅ | ✅ | ✅ |
| General PRU Specs | PRU cores | ✅ | ✅ | ✅ |
| General PRU Specs | RTU_PRU (Auxiliary PRU) cores | ✅ | N/A | N/A |
| General PRU Specs | TX_PRU (Transmit PRU) cores | ❌ | N/A | N/A |
| General PRU Specs | INTC | ❌ | ❌ | ❌ |
| Accelerators: Data Processing | MPY/MAC | ✅ | ✅ | ✅ |
| Accelerators: Data Processing | Scratchpad | ✅ | ✅ | ✅ |
| Accelerators: Data Processing | IPC Scratch Pad | ✅ | N/A | N/A |
| Accelerators: Data Processing | Broadside RAM | ❌ | N/A | N/A |
| Accelerators: Data Processing | BSWAP | ✅ | N/A | N/A |
| Accelerators: Data Processing | CRC16/32 | ✅ | ✅ | ✅ |
| Accelerators: Data Processing | SUM32 | ❌ | N/A | N/A |
| Accelerators: Data Processing | Task Manager | ❌ | N/A | N/A |
| Accelerators: Data Processing | Spinlock | ❌ | N/A | N/A |
| Accelerators: Data Processing | Filter Data Base (FDB) | ❌ | N/A | N/A |
| Accelerators: Data Movement | XFR2VBUS | ❌ | ❌ | ❌ |
| Accelerators: Data Movement | PSI TX & RX | ❌ | N/A | N/A |
| Accelerators: Data Movement | XFR2TR | ✅ | N/A | N/A |
| Peripherals | Sigma-delta filter (3 ch, CIC accumulators, Fast Detect, pattern generators) | ✅ | ✅ | N/A |
| Peripherals | Peripheral Interface / 3 ch EnDAT (protocol-agnostic serializer/deserializer, used for EnDat/BiSS/HDSL/line-code) | ✅ | ✅ | N/A |
| Peripherals | UART | ❌ | ❌ | ❌ |
| Peripherals | eCAP | ❌ | ❌ | ❌ |
| Peripherals | IEP (Industrial Ethernet Peripheral: timer/compare/capture) | ✅ | ✅ | ✅ |
| Peripherals | MII_RT / MII_G_RT (RGMII) | ❌ | ❌ | N/A |
| Peripherals | MDIO | ❌ | ❌ | N/A |
| Peripherals | SGMII | ❌ | N/A | N/A |
| Peripherals | PWM | ❌ | N/A | N/A |

### PRU I/O modes

| Mode Class | Specific I/O Mode | Core Registers Used | Primary Use Case / Description | AM243x, AM64x | AM261x, AM263x | AM62x |
|---|---|---|---|---|---|---|
| GPI (Input) | Direct Input Mode | `R31[31:0]` | Lowest latency direct pin sampling and software bit-banging | ✅ | ✅ | ✅ |
| GPI (Input) | 16-Bit Parallel Capture | `R31` | Captures 16 bits of parallel data synchronous to a sampling clock | ❌ | ❌ | ❌ |
| GPI (Input) | 28-Bit Shift In Mode | `R31` | Automatically deserializes incoming high-speed serial streams | ❌ | ❌ | ❌ |
| GPO (Output) | Direct Output Mode | `R30[31:0]` | Direct software-driven pin control for custom low-latency protocols | ✅ | ✅ | ✅ |
| GPO (Output) | Shift Out Mode | `R30` | Automatically serializes data written to the register over a clock pin | ❌ | ❌ | ❌ |
| Interface | Peripheral IF Mode | Slice Hardware Co-processor | Industrial motor position encoder feedback (EnDAT, BiSS-C, Tamagawa) | ✅ | ✅ | N/A |
| Interface | SD Mode | Internal SDFM Blocks | Connects to external Sigma-Delta ADCs for isolated current/voltage sensing | ✅ | ✅ | N/A |
| Interface | MII / RGMII Mode | ICSSG Real-Time Ethernet Switch | Real-time industrial communication hardware layer (EtherCAT, PROFINET, EtherNet/IP) | ❌ | ❌ | N/A |


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
├── config/             Memory layout configs (AM243x, AM64x, AM261x, AM263x, AM62x)
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
│   └── static/         Dashboard HTML/JS (index.html, app.js, layout.js, workspace.js)
├── xfr/                XFR scratchpad + MAC accelerator
├── memory.cfg          Default memory configuration (AM243x)
├── requirements.txt
└── simulator.py        Simulator entry point
```

## Memory Configuration

The simulator loads `memory.cfg` at startup. Five built-in configs are provided in `config/`:

| File | Target | Notes |
|---|---|---|
| `memory_am243x.cfg` | AM243x | Default |
| `memory_am64x.cfg` | AM64x | Same PRU_ICSSG as AM243x (the A53 cores are not modeled); uses `constants_am243x.cfg` |
| `memory_am263x.cfg` | AM263x | |
| `memory_am261x.cfg` | AM261x | MSRAM burst timing measured on LP-AM261; selected by cfg file, not by target |
| `memory_am62x.cfg` | AM62x | Copied from AM261x; MS_RAM sized to the 64 KB OCRAM at 0x70000000 (SPRUIV7C). Timing not verified on hardware |

Switch config by editing `memory.cfg` or copying one of the `config/` files over it.

Clock keys in the `[device]` section:

| Key | Applies to | Default if absent |
|---|---|---|
| `pru_clock_mhz` | every PRU/RTU core, and the IEP OCP clock | 200 |
| `iep_clock_mhz` | IEP while ICSS CFG `IEPCLK` bit 0 is clear | 200 |

Omitting `pru_clock_mhz` preserves the simulator's 200 MHz core default; all PRU and RTU cores always share this one frequency (the removed `pru1_clock_mhz` key is rejected with an error). Explicitly configured rates, including 250 MHz, remain unchanged. The external IEP defaults independently to 200 MHz, the rate the TRM gives for ICSSG_IEP_CLK (SPRUIM2J §6.4.13.2.4). Core and IEP clocks remain independently configurable, including positive fractional MHz values, and changing the core speed leaves the external IEP clock unchanged. Firmware's IEPCLK register selects which configured source drives the timer.

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
addresses. Other configured entries are preserved. Correct address selection does not
implement the task manager, control or RAT register behavior. Accesses still
require the corresponding memory region/model in the chosen configuration.
C24-C31 follow the executing core's CTBIR0/1 and CTPPR0/1 registers (CTRL block
+0x20, +0x24, +0x28, +0x2C; PRU0 0x22000, PRU1 0x24000, and on AM243x/AM64x RTU0
0x23000 and RTU1 0x23800). A write from the PRU (SBBO, or SBCO through C11) or from
the host (`Simulator.ctrl_write`, or a memory write to the CTRL address) updates
that core's table at once, as measured on LP-AM243 and LP-AM261: C24 = CTBIR0[7:0]
<< 8, C25 = 0x2000 | CTBIR0[23:16] << 8, C26 = 0x2E000 | CTBIR1[7:0] << 8,
C27 = 0x32000 | CTBIR1[23:16] << 8, C28 = CTPPR0[15:0] << 8, C29 = 0xD0000000 |
CTPPR0[31:16] << 8, C30 = 0xE0000000 | CTPPR1[15:0] << 8, C31 = 0xF0000000 |
CTPPR1[31:16] << 8. The registers are 0 out of reset, so **C28 is 0 until firmware
programs CTPPR0** (`blk_ptr = 0x100` gives 0x10000, Shared RAM); firmware that
uses C28 without doing so would also fail on silicon. The registers' power-on
value was not measured, and neither were register bits outside the fields above
(read-back returns what was written). The dashboard constant-table panel reads the
executing core's live table.

AM263x has its own `config/constants_am263x.cfg`, with the same values as
`constants_am261x.cfg` (same ICSS-M; measured on LP-AM261). AM62x has `config/constants_am62x.cfg`, also copied from
AM261x and not verified on hardware. C10 and C12-C14 are reserved in
the TRM; on an LP-AM261 the table holds non-zero pointers there (C10 0x2A000 on
PRU0 / 0x2A200 on PRU1, C12 0x27000, C13 0x2C000, C14 0x24800) and the window
behind each reads zero. The simulator shows those pointers and maps the reserved
ranges (0x24800-0x24BFF, 0x27000-0x27FFF, 0x2A000-0x2DFFF) as read-as-zero
windows, so a load through them returns zeros. These ranges share the ICSS-M
absent-block model in `mem/icssm_absent.py` (see
[docs/icssm_absent_blocks.md](docs/icssm_absent_blocks.md)), including the
`icssg_only_blocks = strict` option. A 32-bit store at the start of
PA_STATS_QRAM, TM_CFG_PRU0 and PA_STATS_CRAM was dropped on silicon; other
writes were not measured. Writes are ignored and logged once. Tracked in
[#79](https://github.com/TexasInstruments/pru-simulator/issues/79).

Dashboard step-back uses one ordered history across all cores. Undo restores
all cores, shared memory, the IEP and devices to the same saved point, and
removes later history entries so peer state stays consistent.

Step-back does not yet restore XFRDMA queues, XFR2VBUS transfers or external
VBUS memory, or task-ring queues and pending work. Do not use undo to validate
programs that use those accelerators; reset and rerun them instead.

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

The MCP `pru_step_multicore` response includes total `lead_steps` on success,
incomplete catch-up and core faults, so callers can account for partial batches.

Multicore pacing executes complete instructions before catching up peer cores;
it does not order all shared effects cycle by cycle. A lagging core's firmware
write to IEPCLK takes effect at the already-advanced shared IEP time, rather
than retroactively at that core's earlier time. Configuring the clock before
advancing the cores avoids this case. Chronological multicore execution,
including this clock-write limitation, is tracked in
[#57](https://github.com/TexasInstruments/pru-simulator/issues/57).

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

v0.4.0 — hover over **PRU SIM** in the dashboard header to confirm.

See [changeLog.md](changeLog.md) for the full changelog.
