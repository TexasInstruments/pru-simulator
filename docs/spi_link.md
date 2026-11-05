# SPI link: PRU0 controller and PRU1 peripheral

## What it is

PRU0 is an SPI controller and PRU1 is an SPI peripheral. Both are bit-banged on ICSSG0 in direct GPO/GPI mode and wired pin to pin. PRU1 is a real edge-polling peripheral: it waits for every CLK edge and does not count cycles from /CS, so it works with any controller that meets the timing, not only PRU0. The link runs SPI mode 0 and mode 1 (CPOL = 0) at supported rates from 10 to 50 MHz. Each 19-byte frame writes, reads, or does both on 16-byte blocks of PRU1 memory. The design is in [the SPI design spec](superpowers/specs/2026-11-04-spi-pru0-pru1-design.md).

## Run the demo in the dashboard

Start the dashboard from the repository root:

```
python -m ui.server
```

Open http://127.0.0.1:8080/ and go to **I/O & Devices → SPI**. Use `--port` to change the port.

1. Click **Set up SPI demo**. This replaces the programs, devices and wiring with the scenario "SPI multi-core (controller + peripheral)".
2. Pick **Mode** (0 or 1) and **SPI CLK**. The list offers only the rates that pass for that mode. Each rate uses the lowest PRU core clock that passes, and the view shows the core clock it picked. A core clock change applies to this session only: the simulator is rebuilt from a temporary copy of the config, and `memory.cfg` is not written. Leaving the SPI demo restores the clocks from `memory.cfg`.
   **MISO sample delay** lists the realisable PRU0 sample points (core cycles from each MISO launch edge; values that land on a CLK write are left out). Values in the passing window found by the sweep are marked "passes", the others "fails"; the sweep's choice is marked "(default)". Picking a value outside the window shows PRU0 reading the wrong bit. Changing the mode or rate goes back to the default.
3. Pick the **Command** (WRITE 01, READ 02, READ/WRITE 03, or Unknown 04), an **Address** (0 to 255, shown as "= byte N"; addresses above 0x7F take the error path), and a **Pattern**.
4. **Send frame** runs one frame. **Run test sequence** runs the full test sequence described under Protocol and reports "passed" or "FAILED" with the byte error count.
5. After a test sequence, a row of **step chips** appears above the frame strip, one per step (21), each marked ✓ or ✗. A failed chip is red and lists its notes as a tooltip. Click a chip to show that step's recorded frame in the scope, frame strip, memory panels and timing table. A line above the frame strip names the step, command, address and pattern. The /CS-high gap (t<sub>CSH</sub>) needs two frames, so it reads "—" for a single step; the `back_to_back` step reports the measured gap.
6. **Play** replays the booth loop bit by bit: the read/write frames for walking 1, walking 0, 0xA5 / 0x5A and 00 / FF, repeating until **Pause**. The trace draws left to right, the current byte is outlined in the frame strip, the PRU1 RX and PRU0 RX panels fill byte by byte, and each frame ends on its compare result. **Speed** *booth* (the default) takes about 7 s per frame (about 30 s per loop); *slow* and *fast* halve and triple it. Play replays the recorded traces and does not run the simulator. If no sequence has been recorded, Play runs it first.
   While Play runs, every SPI control except **Pause** and **Speed** is locked, including the step chips. The dashboard's global toolbar is not locked. The recorded steps are cleared, and Play stops in every open tab, by a change of mode, rate or MISO sample delay, **Send frame**, **Set up SPI demo**, Reset, HW Reset, or leaving the SPI demo.

**Booth setup:** click **Set up SPI demo**, then **Play**.

What the view shows:

- **Scope.** /CS, CLK, MOSI and MISO come from the simulator's real pin trace. Byte boxes over MOSI and MISO show each byte's hex value and field name. Triangles mark the capture edges for the selected mode. A cursor at one bit shows the tCLK, tLEAD, setup and hold arrows. Click the scope to move the cursor.
- **Frame strip.** The 19 bytes (CMD, ADR, IDLE, D0 to D15), MOSI on top and MISO below, with the current byte outlined.
- **Memory panels.** PRU0 TX (sent on MOSI), PRU1 RX at the addressed byte (written), PRU1 TX at the addressed byte (read), and PRU0 RX (got on MISO). The compare line reads "16/16 bytes match both ways" when the data agrees; mismatching bytes are marked in red.
- **Timing table.** The parameters for the selected mode, rate and core clock. "Sim" values are measured from the pin trace of the last frame. Shaded cells are AM243 datasheet data (SPRSP65J Tables 6-112 and 6-113: input slew, output load, PRDO1 output skew); every other row is a design value. The input setup and hold rows are design budgets, not datasheet limits, because the datasheet has no direct-input timing table. t<sub>LEAD</sub>, t<sub>LAG</sub> and t<sub>H</sub>/t<sub>L</sub> have no AM243 limit and no margin: they are the controller's own design values (1 t<sub>CLK</sub>, 1 t<sub>CLK</sub>, ½ t<sub>CLK</sub>), met exactly by construction; on silicon PRDO1 pad skew can shift them by up to 2 ns. The worst-margin row (the tightest margin at this rate) is flagged. The line above the table shows the GPI delay in use.

MISO is decoded at PRU0's sample point (launch edge + `MISO_DELAY` − hop delay), not at the nominal capture edge. A scope that decodes MISO at the nominal capture edge shows it shifted by the round-trip delay. The frame strip and the MISO byte boxes use the sample point.

## Run from Python

The API is in `pru_io/spi_link.py`. The simulator's core clock must equal the row's `core_mhz`. The repo's `memory.cfg` follows the dashboard clock selector, so the example builds a throw-away copy of it with both PRU clocks set to the row's value (`tools/spi_sweep.make_config`, which also copies the `config/` folder). Run it from the repository root; it does not change `memory.cfg`.

```python
import tempfile

from simulator import Simulator
from pru_io.spi_link import (CMD_READ, CMD_WRITE, PATTERNS, SpiConfig,
                             SpiLinkRuntime, load_supported, run_test_sequence)
from tools.spi_sweep import make_config

# Mode 1 at 25 MHz. The row gives the core clock, half period and MISO delay.
row = next(r for r in load_supported()[1] if abs(r["spi_mhz"] - 25.0) < 1e-3)
cfg = SpiConfig(mode=row["mode"], core_mhz=row["core_mhz"],
                half_k=row["half_k"], miso_delay=row["miso_delay"])

workdir = tempfile.mkdtemp(prefix="spi-")
sim = Simulator(make_config(cfg.core_mhz, workdir))   # both PRU clocks = cfg.core_mhz
assert sim.cores["pru0"].clock_mhz == sim.cores["pru1"].clock_mhz == cfg.core_mhz

rt = SpiLinkRuntime(sim, cfg)
rt.load_controller()     # PRU0 controller
rt.load_peripheral()     # PRU1 peripheral
rt.wire()                # the four SPI nets
try:
    rt.preload_peripheral_tx(0x05, PATTERNS["walking1"][1])   # PRU1 TX block 5
    rt.transfer(CMD_WRITE, 0x02, PATTERNS["a5_5a"][0])        # PRU0 TX -> PRU1 RX block 2
    print(rt.read_peripheral_rx(0x02).hex())
    res = rt.transfer(CMD_READ, 0x05)                         # PRU1 TX block 5 -> MISO
    print(res["miso"][3:].hex())                              # 16 data bytes (first 3 are 0x00)
    print(rt.peripheral_status())
    print(run_test_sequence(rt)["ok"])
finally:
    rt.close()
```

- `SpiConfig(mode, core_mhz, half_k, miso_delay)` checks its arguments. SPI CLK is `core_mhz / (2 × half_k)`. Core clocks are 200, 225, 250, 300 and 333 MHz.
- `load_supported()` returns `{0: [...], 1: [...]}`: one row per supported rate, at the lowest passing core clock, sorted by rate. Each row has `mode`, `core_mhz`, `half_k`, `miso_delay` and `spi_mhz`. The `miso_delay` is the value the sweep chose for that rate; `miso_delay_pass` lists every passing value (`miso_delay_window` is its range). `p1_cycles_per_bit` is PRU1's cheapest data bit and `p1_bit_cycles_max` the limiting one (a data bit that also carries gap work); `p1_fetch_cycles` is the idle-byte TX fetch.
- The firmware files `source/spi/spi_controller.asm` and `spi_peripheral.asm` hold a default-config block (mode 1, 25 MHz at a 200 MHz core) between `; >>> spi config` and `; <<< spi config`, so they assemble as the editor shows them. `render_source(cfg, role)` returns the file with that block replaced by `render_header(cfg, role)`; the runtime loads that.
- `SpiLinkRuntime(sim, cfg)` uses the calibrated hop delay (5 cycles) by default. Call `load_controller()`, `load_peripheral()` and `wire()` in that order, as the dashboard does, before `transfer()`.
- `transfer(cmd, addr, data=bytes(16))` runs one frame. It returns a dict with `miso` (19 bytes), `c_rx`, `cycles` and `frame` (the decoded frame from the bus monitor).
- `preload_peripheral_tx(addr, data)` and `read_peripheral_rx(addr)` access PRU1's TX and RX blocks. `peripheral_status()` returns the PRU1 counters.
- `run_test_sequence(runtime, patterns=None, record=False)` returns `ok`, `byte_errors` and `steps`. `patterns` defaults to all of `PATTERNS`: `a5_5a`, `walking1`, `walking0`, `count` and `toggle`. Every frame step names the `pattern` it sent. With `record=True`, each frame step also has a `trace`: its bus `frame` (`start`, `end`, `bits`, `aborted`, `mosi`, `miso` and the `edges` to draw it, starting with the idle edge before it) and the `memory` panels right after it (`c_tx`, `c_rx`, `p_rx`, `p_tx`; the PRU1 blocks are None above address 0x7F). The `back_to_back` gap check has `trace` None. The dashboard serves these steps with the `spi_steps` request.
- `close()` removes the wires and the bus monitor and resets the GPI delays.

## Protocol

One frame is /CS low, 19 bytes (152 SPI clocks), /CS high. MSB first.

| Byte | MOSI (PRU0 → PRU1) | MISO (PRU1 → PRU0) |
|---|---|---|
| 0 | command | 0x00 |
| 1 | address (block index) | 0x00 |
| 2 | idle 0x00 | 0x00. During this byte PRU1 fetches the read data. |
| 3–18 | 16 data bytes (WRITE, R/W) or 0x00 (READ) | 16 data bytes (READ, R/W) or 0x00 (WRITE) |

Commands (bit 0 = write, bit 1 = read):

| Command | Value | Effect |
|---|---|---|
| WRITE | 0x01 | The 16 MOSI data bytes go to PRU1 RX block `addr`. |
| READ | 0x02 | PRU1 TX block `addr` goes out on MISO. |
| READ/WRITE | 0x03 | Full duplex: MOSI goes to RX block `addr` and TX block `addr` goes out on MISO. |

Any other command is an error. PRU1 ignores the data, drives 0x00 on MISO, writes nothing and counts an error.

Address: the byte offset is `addr × 16`. Valid addresses are 0x00 to 0x7F, which gives 128 blocks (2 KB) per buffer. An address above 0x7F is treated like an unknown command.

Timing rules:

- The idle byte gives PRU1 time to load the read data before the first data bit is due. The fetch fits in one byte time at the fastest rate.
- /CS must be high for at least 2 t<sub>CLK</sub> between frames. PRU1 resynchronises on every /CS falling edge.
- /CS going high in the middle of a frame aborts it. PRU1 discards the partial data (RX is not committed) and counts an abort.

PRU1 samples MOSI in the R31 read that shows the capture edge, not a fixed time after the previous edge as spec §5.2 suggested. What: each bit has two polls, one per CLK edge, and the capture poll's single masked R31 read holds CLK, MOSI and /CS together. Why: timing the sample from the launch edge would still need a poll of the other CLK level to re-arm without counting cycles, so it saves no poll; it would also sample a fixed number of cycles after an edge whose MOSI change may be skewed by up to 2 ns (PRDO1), and that number would have to suit the shortest half period of any controller. At the capture edge MOSI has been stable for half a period. Cost: two polls per bit (4–5 cycles per data bit, up to 6 with gap work, single-stepped), which still reaches 50 MHz at a 300 MHz core.

PRU1 status counters: frames (completed, valid or not), errors (unknown command or bad address), aborts, and the last command and address.

Test sequence (**Run test sequence** in the dashboard, `run_test_sequence()` in Python). For each pattern: WRITE a block, READ a preloaded TX block, and READ/WRITE full duplex. Then an unknown command, an out-of-range address, a mid-frame /CS abort, and two back-to-back frames with the controller's shortest /CS-high gap. Each step checks the MISO bytes PRU0 received, the MOSI bytes on the bus, every PRU1 RX block and the PRU1 status counters.

## Memory map

Addresses are global ICSSG addresses: PRU0 DMEM0 starts at 0x0000 and PRU1 DMEM1 at 0x2000. The names match `pru_io/spi_link.py`.

| Name | Address | Size | Contents |
|---|---|---|---|
| `CTRL` | 0x0000 | 16 | PRU0 control block: +0 go (host sets 1, PRU0 clears it when /CS is high again), +1 cmd, +2 addr, +3 status (0 idle, 1 busy, 2 done), +4 frame count (u32), +8 abort-after-bits (u16, 0 for a full frame) |
| `C_TX` | 0x0100 | 16 | PRU0 data bytes sent on MOSI |
| `C_RX` | 0x0110 | 16 | PRU0 data bytes captured on MISO (bytes 3–18) |
| `C_RX_HDR` | 0x0120 | 3 | MISO bytes 0–2 (expected 0x00) |
| `P_STATUS` | 0x2000 | 16 | PRU1 status: +0 frames u32, +4 errors u32, +8 aborts u32, +12 last_cmd u8, +13 last_addr u8 |
| `ZERO_BLK` | 0x27F0 | 16 | All-zero block: PRU1's MISO source for frames that do not read (WRITE, unknown command, bad address). Firmware scratch; do not write it from the host. |
| `P_TX` | 0x2800 | 2048 | PRU1 read source, block n at +16·n |
| `P_RX` | 0x3000 | 2048 | PRU1 write target, block n at +16·n |
| `P_RX_DUMMY` | 0x3800 | 16 | Commit target for READ, unknown-command and bad-address frames, so they never write `P_RX`. Firmware scratch; do not write it from the host. |

`spi_peripheral.asm` writes the same two scratch blocks as PRU1-local addresses 0x07F0 and 0x1800 (global = local + 0x2000).

## Pins (LP-AM243)

From the design spec §7 (SPRUJ12F). All pins are on PRG0 (ICSSG0).

| Signal | Core | PRU pin (R30/R31 bit) | Ball | Header |
|---|---|---|---|---|
| CLK out | PRU0 | PRG0_PRU0_GPO16 | N3 | J1.7 |
| MOSI out | PRU0 | PRG0_PRU0_GPO13 | N1 | J2.15 |
| MISO in | PRU0 | PRG0_PRU0_GPI14 | N2 | J2.14 |
| /CS out | PRU0 | PRG0_PRU0_GPO17 | E1 | J2.18 |
| CLK in | PRU1 | PRG0_PRU1_GPI1 | J2 | J7.67 |
| MOSI in | PRU1 | PRG0_PRU1_GPI2 | M2 | J7.68 |
| /CS in | PRU1 | PRG0_PRU1_GPI6 | F5 | J7.69 |
| MISO out | PRU1 | PRG0_PRU1_GPO11 | P1 | J7.70 |

- PRU0 uses site 1's standard BoosterPack SPI positions.
- PRU1 uses four adjacent pins on J7 (J7.67 to J7.70). The step 2 hardware link is therefore four jumper wires: J1.7 to J7.67 (CLK), J2.15 to J7.68 (MOSI), J2.18 to J7.69 (/CS), and J7.70 to J2.14 (MISO).
- J2.15 needs the Servo-BoosterPack switch (SW6, Rev A boards) in its default position.
- None of these balls is among the outputs gated on E1/E2 boards (R21, T19, U18, U20, V20).

## Timing and limits

- **Hop delay: 5 core cycles.** The simulator adds a 5-cycle GPI input delay to both PRUs (`HOP_DELAY_CYCLES = 5` in `pru_io/spi_link.py`). This is calibrated to one silicon measurement: at 333 MHz (about 3 ns per cycle), a `wbs` on an input followed by a `set` on an output took 18 ns (6 cycles) from input edge to output edge on an external scope. The simulator with no delay shows 1 cycle for the same code, so the silicon path adds 5 cycles (15 ns). The split between input synchroniser, pads and output path is unknown. Putting the whole delay on the input side is equivalent for this design, since every SPI net has one output and one input. At core clocks below 333 MHz, 5 cycles is more than 15 ns, so the model is cautious there.
- **Supported** means zero byte errors on every test with the 5-cycle hop delay on both cores. Rates that pass only at zero delay are reported but not offered.
- **Fastest supported rate:** 50.000 MHz at 300 MHz (k = 3) in mode 0, and 50.000 MHz at 300 MHz (k = 3) in mode 1.
- Board trace and jumper-wire delay are not modelled. A few centimetres of wire is well under 1 ns.
- AM243 limits come from SPRSP65J §6.11.5.16.1 (Table 6-113, PRDO1 output skew of 2 ns max; Table 6-112 slew and load conditions).
- Full results are in [SPI link timing](spi_link_timing.md): every (mode, rate, core clock) pair with its pass or fail result, the `miso_delay` and the timing table for each supported combination. Do not edit that file by hand.

To regenerate the timing doc and the rate table used by the UI, run the sweep from the repository root:

```
python tools/spi_sweep.py            # print the sweep result only
python tools/spi_sweep.py --write    # rewrite pru_io/spi_rates.json and docs/spi_link_timing.md
python tools/spi_sweep.py --check    # confirm the doc's results table matches the JSON
```

`--jobs N` sets the number of worker processes. The committed doc records its sweep wall time in its header (about two minutes).
