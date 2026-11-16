# Handoff: electronica 2026 UART RX port (2026-11-02, afternoon)

This note follows on from
[2026-11-02-electronica2026-i2c-io-demo.md](2026-11-02-electronica2026-i2c-io-demo.md)
(same day, earlier session). Agent memory is stored per PC, so this note in
the repo is how the state reaches other machines.

The session ran from this repo. Its commits are in **open-pru**. Nothing in
pru_simulator changed apart from this note.

## Status

**Done and pushed:**
- open-pru branch `dev_tley_electronica2026_uart_rx` at `b06cf37a`, two
  commits on top of `main` (58cbf1a8). Open as
  [open-pru PR #189](https://github.com/TexasInstruments/open-pru/pull/189)
  into `main`.
- open-pru branch `dev_tley_electronica2026_i2c_io` at `4a8cad5c`: readme pin
  labels fixed (see "Not bugs" below). This updates PR #188.
- This note is in pru-simulator PR #102 (branch `docs/handoff-2026-11-02`),
  next to the morning's note.

**On the user's side:** both CCS projects import and build with no errors
(CCS 21, user, 2026-11-02). The hardware run with the PicoScope AWG as the
sender is the next step, not yet reported.

**What was built:** the simulator example `source/uart_rx_11frame.asm`
(4 Mbaud 8N1 bit-bang UART receiver, 11-byte frames) was ported to the
LP-AM243 as open-pru `examples/electronica2026/uart_rx_frame/uart_rx`:

- ICSSG0 PRU0 only. The R5F (FreeRTOS) clears PRU0 DRAM, sets GP_MUX_SEL and
  loads and starts PRU0. No other PRU core is loaded.
- RX is `PRG0_PRU0_GPI2` (R31 bit 2, pad `PRG0_PRU0_GPO2`, ball G1), on
  LaunchPad pin **BP.31** (header J4), with the pad pull-up on.
- SysConfig sets the PRU core clock to 200 MHz.
- Ring buffer of 372 frames at DRAM0 0x0000-0x0FFB, 16-bit frame count at
  0x0FFC, sticky framing error flag at 0x0FFE. R5F/CCS global address of
  DRAM0 is 0x30000000.

Entry points in that folder:
- `readme.md`: wiring, DRAM0 layout, build, run and validate steps.
- `docs/porting_report.md`: the prompt, model (Opus 5.5, effort high), time
  (12 min 04 s), tokens, every change, the checks and the open items.
- `test/helloworld_4mbaud_awg.csv` and `test/make_awg_csv.py`: the PicoScope
  AWG test frame (see "Hardware test sender" below).

## How the session went

1. **Running the source in the simulator.** The user asked how to run
   `uart_rx_11frame.asm`. The answer is `getting_started.md` Example 6 (IO
   panel → UART RX Inject), the MCP tool `pru_uart_inject`, or
   `tests/test_uart_rx*.py`. The headless runner can't inject UART frames.
2. **`HelloWorld!` run.** The live MCP tool failed (see open item 1), so the
   agent called `PRUSimulatorMCP.pru_uart_inject` directly in Python. With
   the local `memory.cfg` at 300 MHz nothing was received (framing error).
   With a 200 MHz copy it received `HelloWorld!`. The user confirmed it works
   at 200 MHz in the UI.
3. **Port to open-pru**, following the i2c_io port step by step. One question
   was put to the user (below).
4. **Pin label correction** by the user, then both branches pushed and
   PR #189 opened.
5. **CCS import failed** for the user, because the open-pru checkout was on
   the i2c_io branch (see "Not bugs"). After switching, import and build
   worked.
6. **PicoScope AWG test frame** for the hardware test, committed as
   `b06cf37a`.

## Decision made with the user

| Issue | User decision |
|---|---|
| The source never wraps the frame offset (`r1 += 11`). On silicon a continuous stream would write past PRU0 DRAM0, then PRU1 DRAM and shared RAM, and after about 0.3 s into the ICSSG INTC and CFG registers. | **Ring buffer**, wrap at 0x0FFC (372 frames) |

The agent also added the frame count at 0x0FFC without being asked, so a
reader can find the newest frame. It is one `sbco` line and is flagged in
the porting report.

## Key facts

- **The receiver only works at 200 MHz.** It is cycle-counted: 4 Mbaud is
  50 cycles per bit. `memory.cfg` is local state: the UI speed selector
  writes the clock into it, and on this PC it was at 300 MHz. Tests pin the
  clock through `tests/conftest.py`; ad-hoc scripts need their own 200 MHz
  config.
- **The receive path is unchanged.** Apart from the pin mask (1 → 4), every
  instruction from the start-bit poll to the stop-bit check is the same as
  in the source. The port and the source give identical results in
  pru-simulator: `HelloWorld!`, 3 frames with 0/1/2 idle bits between them,
  and a baud sweep.
- **Measured sample positions** (per-cycle trace of the built `.out`, 50-cycle
  bit cell, middle = 25): first data sample 75-77 cycles after the start
  edge; data samples at 25-33, or 25-41 for back-to-back frames; stop
  samples at 31-43.
- **Builds:** project makefile, and a CCS 21.0.1 headless import and build of
  both projects with 0 errors. The one R5F warning is the SysConfig
  part-number migration notice that the `empty` syscfg also gives.

## Hardware test sender (PicoScope 3416E AWG)

- `test/helloworld_4mbaud_awg.csv`: 1600 samples of -1/+1, 8 per bit. One
  period is 200 bit times: 45 idle bits, the 110-bit `HelloWorld!` frame,
  45 idle bits. PicoScope 7 imports AWG data as CSV with values between -1
  and +1, and the whole buffer repeats at the Frequency setting.
- Settings: Arbitrary, **20 kHz** (250 ns per bit), amplitude 1 V, offset
  +1 V, giving 0 V / 2 V.
- **Start order:** DC +2 V (line idle high), run the R5F, then switch to
  Arbitrary. A disabled AWG outputs 0 V, which looks like endless start
  bits. If the PRU starts mid-frame, every frame is shifted by a byte and
  the error flag sets; that was reproduced in pru-simulator.
- Expected: every 11-byte slot from 0x30000000 holds `HelloWorld!`, the count
  at 0x30000FFC climbs by 20,000 per second, and 0x30000FFE stays 0.
- **2 V is the AWG maximum** (the PicoScope MCP reports amplitude 0-2 V,
  offset narrowing with amplitude). That is at the bottom of the 3.3 V
  input-high range. If bytes are wrong, use a level shifter (e.g.
  SN74LVC1T45, VCCA = 1.8 V, VCCB = 3.3 V).
- The PicoScope MCP server has no tool to upload an arbitrary waveform:
  `configure_signal_generator` only sets the preset waveforms. Import the
  CSV in the GUI. The MCP can still read back a UART decode if the AWG
  output is also wired to a scope channel.

## Not bugs, so don't chase them

- **"J4.10" does not exist.** The first version of the readme said RX was
  on J4.10, from the `pru_eqep` readme's J-header numbering. The user
  pointed out that the LaunchPad headers are labelled with BoosterPack pin
  numbers. RX is BP.31 on J4. The source of truth for PRU0 GPO pins is slide
  6 of `examples/LCD_interface/documents/PRU IO - LCD Interface.pptx`
  (GPO0/1/2 = BP.33/32/31). The i2c_io readme had the same mistake (J4.8 /
  J4.9) and was fixed in `4a8cad5c`. This closes open item 3 of the
  morning's note.
- **CCS finds no uart_rx project.** The project files exist only on
  `dev_tley_electronica2026_uart_rx`. On the i2c_io branch, the
  `uart_rx_frame/uart_rx` folder still exists but holds only untracked build
  outputs, so CCS has nothing to import. Check `git branch --show-current`
  in open-pru first. The reverse holds for i2c_io.
- **No frames received in the simulator.** Check the PRU clock first. At
  300 MHz the receiver gets nothing and sets the framing error flag. This is
  expected, not a simulator bug.
- **The first byte is sampled early in a scripted run.** The simulator's GPI
  pin starts low, which the firmware sees as a start bit before the
  injection begins. Set the pin high from reset in test scripts. On silicon
  the pad pull-up does this.

## Branches (end of session)

- **pru_simulator:** `main`, plus `docs/handoff-2026-11-02` (PR #102) with
  both notes from today.
- **open-pru:** `dev_tley_electronica2026_i2c_io` (PR #188, at `4a8cad5c`),
  `dev_tley_electronica2026_uart_rx` (PR #189, at `b06cf37a`) and `main`.
  The checkout was left on the **uart_rx** branch, so the user's CCS can see
  that project.

## Open items

1. **pru-simulator: the `pru_uart_inject` MCP tool fails.** The running MCP
   server advertised `payload` as a string, and a call failed with
   `unsupported operand type(s) for >>: 'str' and 'int'`. `server.py` on
   `main` takes `payload: list[int]` and would reject a string with a
   different error, so the running server probably came from older code.
   Not verified. Next step: reconnect with `/mcp` and call it again. If it
   still shows a string, look at how `build_server()` derives the schema
   from the signature.
2. **Hardware run of uart_rx** on the LP-AM243 with the PicoScope AWG
   frame (see "Hardware test sender"). Check DRAM0 at 0x30000000 in the CCS
   memory browser, then record the result in the readme and porting report
   (both still say FIXME).
3. **Review and merge** open-pru PR #189.
4. **Merge conflict:** `examples/makefile` and `examples/readme.md` get an
   `electronica2026/...` entry on both open-pru branches. Whichever merges
   second needs a small resolution.
5. **Fast senders fail.** 4.05 Mbaud (+1.25 %) already gives framing errors:
   the stop bit of byte 11 is sampled at cycle 43 of 50, because the
   `qbeq store_bN` chain adds up to 11 cycles. The simulator source has the
   same limit. A jump table would centre the samples.
6. **No frame sync.** Frames are cut every 11 bytes from the first start bit
   seen. Start the PRU before the sender.
7. **uart_rx housekeeping:** board revision (FIXME), SysConfig GUI re-save,
   `examples/readme.md` portability FIXMEs, untracked build outputs
   (`pru0_load_bin.h`, `uart_rx.release.lnkxml`).
8. **Optional:** let the R5F print new frames on the UART console by polling
   the count at 0x0FFC.

