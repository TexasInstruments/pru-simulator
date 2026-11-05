# electronica 2026 booth demos: how to run

This page is for booth staff. It covers the three PRU demos on the AM243x
LaunchPad (LP-AM243). Each demo needs the PicoScope set up differently, so
that setup gets its own section for each demo, and a side-by-side table
comes first.

| Demo | What visitors see | PicoScope is used as |
| ---- | ----------------- | -------------------- |
| **A. I2C IO expander** | A PRU bit-bangs 1 Mbit/s I2C. One LED runs along 8 LEDs, the PicoScope decodes the bus, and a PC window lights the same LED. | **Logic analyzer** (digital inputs D0/D1) |
| **B. UART RX frames** | A PRU receives 4 Mbaud UART frames. `HelloWorld!` fills the PRU memory, shown in CCS. | **Signal generator** (AWG output) |
| **C. UART print** | A PRU sends `Hello 2026` as 115200 baud UART, every 1.87 ms. The PicoScope decodes the text. | **Logic analyzer** (digital input D0) |

All three demos run on the same board, but **only one at a time**: all use
ICSSG0 PRU0. To switch, load the other demo's R5F program (see "Switching
between demos").

The source code is in the open-pru repository, under
`examples/electronica2026/`:

| Demo | Folder | open-pru branch / PR |
| ---- | ------ | -------------------- |
| A | `i2c_io_expander/` (`i2c_io/` firmware, `visualizer/` PC window) | `dev_tley_electronica2026_i2c_io`, PR #188 |
| B | `uart_rx_frame/uart_rx/` | `dev_tley_electronica2026_uart_rx`, PR #189 |
| C | `uart_print/uart_print/` | `dev_tley_electronica2026_uart_print`, PR #190 |

Until the PRs are merged, each demo exists only on its own branch.

A fourth screen can run the **SPI link** demo in the simulator alone, with
no board and no PicoScope. It loops by itself; see "SPI link (simulator,
runs unattended)" below.

---

## PicoScope settings at a glance

| Setting | Demo A: I2C IO expander | Demo B: UART RX | Demo C: UART print |
| ------- | ----------------------- | --------------- | ------------------ |
| Used for | Capture and decode | Send the test signal | Capture and decode |
| Inputs | **D0 = SCL, D1 = SDA** (digital) | none needed | **D0 = UART TX** (digital, same wire as Demo A's SCL) |
| Output | none | **AWG** output → BP.31 | none |
| Signal generator | **Off** | **Arbitrary**, CSV `helloworld_4mbaud_awg.csv`, **20 kHz**, amplitude **1 V**, offset **+1 V** | **Off** |
| Trigger | **Pulse width** on D0 (SCL), positive pulse **> 1 µs**, mode **Repeat**, about 20 % pre-trigger | not needed | **Pulse width** on D0, positive pulse **> 500 µs**, mode **Repeat**, about 10 % pre-trigger |
| Serial decoder | **I2C**, Data = D1, Clock = D0, **Fast-mode Plus (1 Mbit/s)**. The visualizer adds it if missing. | none | **UART/RS-232** on D0, **115200** baud, 8 data bits, no parity, 1 stop bit, LSB first, idle high |
| Timebase | About 100 µs across the screen (one I2C frame lasts 29 µs) | any | About 1 ms across the screen (the string lasts 871 µs) |
| MCP server (beta) | **On**, port **manual 18123** (the LED window needs it) | not needed | not needed |
| What good looks like | One decoded frame per capture: `23 W 01 xx`, xx walks 01 → 80 | CCS shows `HelloWorld!` repeating in PRU memory | `Hello 2026` decoded in every capture, no framing errors |

**Tip:** once each setup works, save it as a PicoScope settings file
(for example `demoA_i2c`, `demoB_uart` and `demoC_uart_tx`), so switching later is just
"open settings".

---

## Wiring (all demos, can stay connected)

All wires can stay connected. Demo A does not touch BP.31, Demo B does not
touch BP.32/33, and Demo C uses only BP.33, the wire that already goes to
PicoScope D0 for Demo A.

The LaunchPad headers are labelled with **BoosterPack (BP) pin numbers**.
There is no "J4.10" or "J4.8"; use the BP numbers below.

| Signal | LaunchPad pin | Connect to | Demo |
| ------ | ------------- | ---------- | ---- |
| SCL / UART TX | **BP.33** (J4) | TCA9538 SCL, PicoScope **D0** | A + C |
| SDA | **BP.32** (J4) | TCA9538 SDA, PicoScope **D1** | A |
| 3V3 | BP.1 (J1) | TCA9538 VCC | A |
| UART RX | **BP.31** (J4) | PicoScope **AWG** output (centre) | B |
| GND | BP.20 (J2) | TCA9538 GND, PicoScope digital GND, AWG ground | A + B + C |

TCA9538 board: A1 = A0 = high (address 0x23), RESET high, LEDs with series
resistors on P0 to P7. No I2C pull-ups needed. During Demo C the TCA9538
sees the UART signal on its SCL pin; ignore the LEDs then.

---

## Before the booth opens (once per PC)

1. **CCS 21** with both projects of each demo imported. CCS copies the
   source files into its workspace on import, so after the import the
   projects no longer depend on which open-pru branch is checked out.
   - Demo A: `i2c_io_am243x-lp_icss_g0_pru0_fw_ti-pru-cgt` and
     `i2c_io_am243x-lp_r5fss0-0_freertos_ti-arm-clang`
   - Demo B: `uart_rx_am243x-lp_icss_g0_pru0_fw_ti-pru-cgt` and
     `uart_rx_am243x-lp_r5fss0-0_freertos_ti-arm-clang`
   - Demo C: `uart_print_am243x-lp_icss_g0_pru0_fw_ti-pru-cgt` and
     `uart_print_am243x-lp_r5fss0-0_freertos_ti-arm-clang`
   - Build each **PRU project first**, then its R5F project.
2. **PicoScope 7** (7.2.30 or later) with the **MCP server** feature
   switched on, for Demo A's LED window. It is hidden behind a flag; see
   `i2c_io_expander/visualizer/readme.md` › "PicoScope 7 setup", step 1.
   A PicoScope update resets the flag.
3. **LED window** (Demo A) built once:
   `cd i2c_io_expander/visualizer && cargo build --release`.
4. **AWG file** (Demo B): copy
   `uart_rx_frame/uart_rx/test/helloworld_4mbaud_awg.csv` to a folder
   **outside the repository** (for example `~/booth/`) and import it from
   there. A PicoScope save onto the repository file once replaced it with
   zeros.

---

## Demo A: I2C IO expander

### PicoScope setup
1. Signal generator: **Off**.
2. Digital inputs: enable **D0** (SCL) and **D1** (SDA).
3. Trigger: **Pulse width**, source **D0**, positive pulse **longer than
   1 µs**, mode **Repeat**, pre-trigger about 20 %. SCL only stays high
   that long in the 100 ms pause between frames, so every capture starts
   with one frame.
4. Timebase: about 100 µs across the screen.
5. Preferences → **MCP server (beta)**: **On for current session**, port
   **manual 18123**.
6. Serial decoder: **I2C**, Data = **D1**, Clock = **D0**,
   **Fast-mode Plus (1 Mbit/s)**. If you skip this, the LED window adds one
   named `TCA9538`.

### Run
1. In CCS, load and run the R5F program
   `i2c_io_am243x-lp_r5fss0-0_freertos_ti-arm-clang`.
   The UART console prints `I2C IO expander: ICSSG0 PRU0 running`.
2. Start the LED window:
   ```
   cd examples/electronica2026/i2c_io_expander/visualizer
   ./target/release/i2c_led_visualizer
   ```
   (`cargo run --release` also works; run `source ~/.cargo/env` first if
   `cargo` is not found.) Place it next to PicoScope; **F11** = fullscreen.

### What good looks like
- **LEDs:** one LED runs P0 → P7, one step every 100 ms (0.8 s per round).
- **PicoScope:** one frame per capture, decoded as `23 W 01 xx`, with xx
  walking 01, 02, 04 … 80. The SCL period is about 1 µs.
- **LED window:** status `last frame: addr 0x23 · reg 0x01 · data 0x08`
  (the value changes), with the same LED lit as on the board.

### If something is wrong

| You see | Do this |
| ------- | ------- |
| Window says `waiting for PicoScope 7 …` | The PicoScope MCP server is off or on another port. Check Preferences → MCP server: On, port 18123. Run `ss -ltnp \| grep PicoScope.GTK` to find the port; pass `--url http://127.0.0.1:<port>/mcp`. |
| No "MCP server" entry in Preferences | The hidden flag is off (for example after a PicoScope update). See setup step 2. |
| Window says `waiting for I2C frames …` or `no I2C frames` | The board is not running, or D0/D1 are swapped or not enabled. Check the PicoScope screen first. |
| LED window sometimes holds, then jumps | Normal. PicoScope itself pauses now and then, and the window mirrors it. |
| PicoScope shows several frames or a jumping picture | The trigger is not set as above (pulse width on D0, > 1 µs, Repeat). |
| No LEDs, but the PicoScope decodes fine | TCA9538 power, address pins (A0 = A1 = high) or RESET. The firmware does not check ACK, so a missing chip does not stop the bus. |

---

## Demo B: UART RX frames

**Important:** this receiver is timed for a 4 Mbaud sender that is exact or
slightly slow. The AWG file is exact.

### PicoScope setup
Set the values **in this order**, because the allowed offset range depends
on the amplitude:

1. Signal generator: waveform **Arbitrary**. Import
   `helloworld_4mbaud_awg.csv` from the copy outside the repository, and
   **apply it in the editor** before you close it.
2. **Amplitude 1 V** (set this first).
3. **Offset +1 V**. The output now swings between 0 V and 2 V.
4. **Frequency 20 kHz.** Check the unit: **kHz, not MHz.** At 20 MHz the
   output looks like a sine wave and nothing is received.

⚠ **Do not connect BP.31 while the settings are wrong.** With offset 0 V
the output goes below 0 V, which the LaunchPad input is not rated for.

### Run (the start order matters)
The firmware has no frame sync. It cuts frames every 11 bytes from the first
start bit it sees, so the line must be idle when the PRU starts.

1. Switch the signal generator to **DC, +2 V** (line idle high).
2. In CCS, load and run the R5F program
   `uart_rx_am243x-lp_r5fss0-0_freertos_ti-arm-clang`.
   The UART console prints
   `UART RX frame: ICSSG0 PRU0 running, frames at PRU0 DRAM0 (0x30000000)`.
3. Switch the signal generator to **Arbitrary** (the settings above).

### Show the result in CCS
Open a **Memory Browser**:

- **Received frames:** address **0x30000000** from the R5F, or address
  **0** in **Data_Memory** of the PRU0 debug view. Set format
  **Packed Char**. It shows `He ll oW or ld !H el lo Wo rl d! …`, which is
  `HelloWorld!` repeating, starting with `H` at the first byte.
- **Frame count:** 16-bit value at **0x30000FFC**. It goes up by about
  20,000 per second, so it rolls over every few seconds. It only needs to
  change between two refreshes.
- **Error flag:** byte at **0x30000FFE**. It must be **0**.

### If something is wrong

| You see | Do this |
| ------- | ------- |
| AWG output looks like a sine | The frequency is in MHz. Set **20 kHz**. If it still looks wrong, import the CSV again and apply it in the editor. |
| Offset cannot be set to +1 V | Lower the amplitude to 1 V first. |
| Text shifted, for example `elloWorld!H`, and 0x30000FFE = 1 | The PRU started in the middle of a frame. Restart: DC +2 V, run the R5F program again, then Arbitrary. |
| Memory all zeros, count not moving | The AWG is off or not connected to BP.31, or there is no ground connection to BP.20. |
| Error flag 1, but the text looks right | The flag is sticky. It may have been set during the DC → Arbitrary switch. Restart with the start order above. |
| Wrong bytes, at random | 2 V is at the bottom of the 3.3 V input-high range. Check the ground lead. If needed, add a level shifter (for example SN74LVC1T45, VCCA = 1.8 V, VCCB = 3.3 V). |

---

## Demo C: UART print

PRU0 bit-bangs a UART transmitter: 115200 baud, 8N1, on BP.33. It sends
`Hello 2026`, keeps the line high for 1 ms, and sends it again, forever.
There is no receiver on the board; the PicoScope is the receiver.

### PicoScope setup
1. Signal generator: **Off**.
2. Digital inputs: enable **D0**. D1 can stay on; Demo C does not use it.
3. Serial decoder: **UART/RS-232** on **D0**, **115200** baud, 8 data bits,
   no parity, 1 stop bit, LSB first, idle high.
4. Trigger: **Pulse width**, source **D0**, positive pulse **longer than
   500 µs**, mode **Repeat**, pre-trigger about 10 %. The only high level
   that long is the 1 ms gap after each string, so every capture starts at
   the first character. Inside the string the line is never high for more
   than 78 µs.
5. Timebase: about 1 ms across the screen. The string lasts 871 µs.

If the digital inputs are not available, channel A on BP.33 works the same
way (DC, ±5 V, decoder and trigger on A, threshold about 1.65 V).

### Run
1. In CCS, load and run the R5F program
   `uart_print_am243x-lp_r5fss0-0_freertos_ti-arm-clang`.
   The UART console prints
   `UART print: ICSSG0 PRU0 running, 115200 8N1 TX on PRG0_PRU0_GPO0 (BP.33)`.

The start order does not matter: the transmitter repeats, and the trigger
waits for the gap.

### What good looks like
- **PicoScope:** every capture decodes as `48 65 6C 6C 6F 20 32 30 32 36`,
  shown as `Hello 2026`, with no framing errors. One character lasts
  about 87 µs; the string repeats every 1.87 ms.
- **CCS Memory Browser:** address **0x30000000**, format **Packed Char**,
  shows `Hello 2026` followed by a 0 byte. The PRU reads the text from
  there on every pass.

### Optional: change the text live
Because the PRU reads the string from memory on every pass, you can type a
new one in the CCS Memory Browser at 0x30000000. The PicoScope shows it
from the next pass on (within 2 ms).

- End the text with a **0 byte**, or the PRU keeps sending what follows in
  memory.
- At most 255 bytes.
- Not yet tried on hardware. If it does not work, reload the R5F program to
  get `Hello 2026` back.

### If something is wrong

| You see | Do this |
| ------- | ------- |
| Flat line on D0 | The R5F program is not running, or a different demo is loaded. Reload `uart_print_am243x-lp_r5fss0-0_freertos_ti-arm-clang`. |
| Signal there, but the decoder shows garbage or framing errors | Check the decoder: 115200 baud, 8N1, idle high, LSB first. The decoder must be on D0 (or on A, if you use channel A). |
| Capture starts in the middle of the text | The trigger is not set as above (pulse width on D0, positive, > 500 µs). |
| All characters decode, but slightly off in time | Normal: the PRU sends 0.37 % slow (114,780 baud). That is well within UART tolerance. |
| Wrong baud rate, about 172 kbaud | The PRU clock is not 200 MHz. Do a system reset and reload the R5F program; it sets 200 MHz. |

---

## SPI link (simulator, runs unattended)

PRU0 is an SPI controller and PRU1 an SPI peripheral, both bit-banged and
wired pin to pin in TI's pru-simulator. Visitors watch frames go across a
scope bit by bit: walking 1, walking 0, 0xA5 / 0x5A and 00 / FF, about
7 s per frame and about 30 s per loop, repeating. No board or PicoScope
is needed, so it can run on its own laptop or screen next to the board
demos.

The code is in pru-simulator, branch `feat/spi-pru-link` (PR #108). Until
the PR is merged, check out that branch.

### Run (two clicks)
1. Start the dashboard from the pru-simulator folder:
   ```
   python -m ui.server      # then open http://localhost:8080
   ```
2. Go to **I/O & Devices → SPI**.
3. Click **Set up SPI demo**.
4. Click **▶ Play**.

Play runs the SPI test sequence once (about a second), then replays its
frames in a loop. While it plays, every SPI control except **Pause** and
**Speed** is greyed out, so a visitor clicking around cannot stop it.

### What good looks like
- A row of 21 green ✓ chips above the frame strip. The 4 loop frames have
  a thicker green border, and the one playing is outlined in yellow.
- The line above the frame strip names the step, for example
  `Step 6/21 · rw:walking1 · READ/WRITE (03) @ 0x41 · pattern walking1`.
- The scope trace draws left to right, the byte boxes fill in as each
  byte completes, and the PRU1 RX and PRU0 RX panels fill byte by byte.
  The compare badge reads **Transferring…**, then
  **16/16 bytes match both ways** at the end of each frame.
- **Speed**: *booth* is the default. *slow* halves the pace and *fast*
  triples it.

### Talking points
- **Real pin trace:** what is drawn is the simulator's recorded pins
  (/CS, CLK, MOSI, MISO), not an animation.
- **Two PRU cores:** PRU0 drives SPI CLK at 25 MHz on a 200 MHz core
  (5 ns per cycle). PRU1 follows every clock edge.
- **Timing table:** below the scope, with the AM243 datasheet values
  marked. The tightest margin (MISO setup) is flagged.
- **Tested up to 50 MHz:** the link was swept from 10 to 50 MHz in both
  SPI modes. 50 MHz works at a 300 MHz core.

### If something is wrong

| You see | Do this |
| ------- | ------- |
| The pane shows only **Set up SPI demo**, and the frames and chips are gone | Someone used the top toolbar (Config, loading another example, …), which ends the SPI demo. Click **Set up SPI demo**, then **▶ Play**. |
| The loop is not moving and the button reads **▶ Play** | It was paused, or someone changed a setting after pausing. Click **▶ Play**. |
| A red ✗ chip | That step failed. The loop still plays it, so visitors see the real result. Hover over the chip for the reason, and note it for the team. |
| **Play** pressed, but nothing happens for a long time | Click **❚❚ Pause**, then **▶ Play** again. If that does not help, reload the page (Ctrl+Shift+R) and do the two clicks again. |
| The page cannot connect | The dashboard is not running. Start `python -m ui.server` again. |

---

## Switching between demos

1. Stop the current demo:
   - Demo A: close the LED window.
   - Demo B: set the signal generator to **Off** or **DC +2 V**.
   - Demo C: nothing to stop.
2. In PicoScope, switch to the other demo's settings (open the saved
   settings file, or use the table at the top).
3. In CCS, do a **system reset** of the board, or power-cycle it. Then load
   and run the other demo's **R5F** program. It loads its own PRU0 firmware.
4. For Demo B, keep the start order: **DC +2 V → run R5F → Arbitrary**.

The wiring can stay as it is.

---

## Optional: show the simulator first

All three demos started in TI's pru-simulator. To show that step:

```
pip install -r requirements.txt
python ui/server.py      # then open http://localhost:8080
```

- **Demo A:** load `source/i2c_tca9538_running_led.asm`, click **Attach
  TCA9538 @ 0x23**, set the step interval to 0.01 s and click Run. One LED
  walks P0 → P7 in the I2C · TCA9538 panel (`getting_started.md`,
  Example 10).
- **Demo B:** load `source/uart_rx_11frame.asm` and use **IO panel → UART
  RX Inject** with `HelloWorld!` (`getting_started.md`, Example 6).
  **The PRU0 clock must be 200 MHz** (speed selector). At other clocks
  nothing is received.
- **Demo C:** load `source/uart_print.asm`, set the Signal Graph window to
  2048, click **● REC** and **Run**, then **▶ Decode** in the IO panel's
  UART Decoder. It shows `Hello 2026` (`getting_started.md`, Example 11).
  The simulator source sends the string once and halts; the board version
  repeats it.

---

## More detail

- Demo A: open-pru `examples/electronica2026/i2c_io_expander/readme.md`,
  `i2c_io/readme.md`, `visualizer/readme.md`
- Demo B: open-pru `examples/electronica2026/uart_rx_frame/uart_rx/readme.md`
- Demo C: open-pru `examples/electronica2026/uart_print/uart_print/readme.md`
- SPI link: pru-simulator `docs/spi_link.md` (branch `feat/spi-pru-link`,
  PR #108), and `docs/handoff/2026-11-05-spi-sequence-play.md`
- How each demo was ported: the `docs/porting_report.md` in each project
- Session notes: `docs/handoff/2026-11-02-*.md` and
  `docs/handoff/2026-11-03-*.md` (PR #102)
