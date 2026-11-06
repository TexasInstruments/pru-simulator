# electronica 2026 booth demos: how to run

This page is for booth staff. It covers the three PRU demos on the AM243x
LaunchPad (LP-AM243) and two demos that run in the simulator only (PDM
audio and the SPI link). Each board demo needs the PicoScope set up differently, so
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

Two more demos run in TI's pru-simulator alone, with no board and no
PicoScope, on their own laptop or screen:

| Demo | What visitors see |
| ---- | ----------------- |
| **D. PDM audio** | PRU1 synthesises two PDM microphones from audio clips, PRU0 decodes them into 16 kHz audio. Two live spectrograms show speech and a chirp. See "Demo D: PDM audio (simulator)". |
| **SPI link** | PRU0 and PRU1 talk SPI, frame by frame, in a loop that runs by itself. See "SPI link (simulator, runs unattended)". |

Both are in pru-simulator. Until their PRs are merged, use the branch
`electronica2026-demos` on the demo laptop: it merges the SPI branch
(`feat/spi-pru-link`, PR #108) and the audio branch (`feat/pdm-audio-demo`,
PR #106) into `main`, which already has the simulator versions of Demos A,
B and C. This branch exists only locally on the demo laptop; it is not
pushed.

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

## BoosterPack pin map (all demos)

Every pin any demo uses, by BoosterPack number. Two more setups share the
header with Demos A, B and C:

- **PDM loopback (board):** the 8-channel PDM demo from open-pru
  (`examples/electronica2026/pdm_audio/`, PR #191; also the base of the
  PDM-over-Ethernet demo). Nine jumpers connect PRU1 outputs to PRU0 SD
  inputs. Demo D above is the simulator version of this demo.
- **SPI link:** runs in the simulator only. The SPI pane's pin table plans
  these LP pins for a later board version; nothing is wired today.

All pads are ICSSG0 (`PRG0_...`). "out" = the demo drives the pin, "in" =
it only reads it.

| BP pin | PRU pad | A · I2C | B · UART RX | C · UART print | PDM loopback (board) | SPI link (planned) | Clash |
| ------ | ------- | ------- | ----------- | -------------- | -------------------- | ------------------ | ----- |
| BP.1 | 3V3 | TCA9538 VCC | | | | | |
| BP.7 | PRU0_GPO16 | | | | clock in (GPI16, SD8_CLK), from BP.11 | PRU0 CLK out | PDM / SPI |
| BP.11 | PRU1_GPO0 | | | | clock out, to BP.7 | | |
| BP.12 | PRU0_GPO15 | | | | ch6 in (GPI15, SD7_D), from BP.59 | | |
| BP.13 | PRU0_GPO5 | | | | ch2 in (GPI5, SD2_D), from BP.69 | | |
| BP.14 | PRU0_GPO14 | | | | | PRU0 MISO in | |
| BP.15 | PRU0_GPO13 | | | | ch5 in (GPI13, SD6_D), from BP.71 | PRU0 MOSI out | PDM / SPI |
| BP.18 | PRU0_GPO17 | | | | ch7 in (GPI17, SD8_D), from BP.57 | PRU0 /CS out | PDM / SPI |
| BP.19 | PRU0_GPO3 | | | | ch1 in (GPI3, SD1_D), from BP.68 | | |
| BP.20 | GND | GND | GND | GND | GND (scope reference) | | shared, fine |
| BP.31 | PRU0_GPO2 | | UART RX in (GPI2), from the AWG | | | | |
| BP.32 | PRU0_GPO1 | **SDA out** (GPO1), to TCA9538 + D1 | | | **ch0 in** (GPI1, SD0_D), from BP.67 | | **A / PDM** |
| BP.33 | PRU0_GPO0 | SCL out (GPO0), to TCA9538 + D0 | | UART TX out (GPO0), to D0 | | | A + C share it on purpose |
| BP.43 | PRU0_GPO11 | | | | ch4 in (GPI11, SD5_D), from BP.72 | | |
| BP.44 | PRU0_GPO7 | | | | ch3 in (GPI7, SD3_D), from BP.70 | | |
| BP.57 | PRU1_GPO16 | | | | ch7 out, to BP.18 | | |
| BP.59 | PRU1_GPO15 | | | | ch6 out, to BP.12 | | |
| BP.67 | PRU1_GPO1 | | | | **ch0 out**, to BP.32 | PRU1 CLK in | **A / PDM** (via the jumper), PDM / SPI |
| BP.68 | PRU1_GPO2 | | | | ch1 out, to BP.19 | PRU1 MOSI in | PDM / SPI |
| BP.69 | PRU1_GPO6 | | | | ch2 out, to BP.13 | PRU1 /CS in | PDM / SPI |
| BP.70 | PRU1_GPO11 | | | | ch3 out, to BP.44 | PRU1 MISO out | PDM / SPI |
| BP.71 | PRU1_GPO13 | | | | ch5 out, to BP.15 | | |
| BP.72 | PRU1_GPO12 | | | | ch4 out, to BP.43 | | |

What clashes:

- **BP.32: Demo A and the PDM loopback.** Demo A drives SDA push-pull on
  BP.32, and the ch0 jumper connects BP.32 to PRU1_GPO1 (BP.67), which the
  PDM demo drives push-pull. While PRU1 still runs, two outputs fight on one
  wire. This is the only clash between the PDM loopback and Demos A, B and C.
- **BP.33: Demos A and C** use the same pin for the same kind of signal (a
  PRU0 output to PicoScope D0). Only one runs at a time, so this is fine.
- **SPI link vs PDM loopback:** 7 of the 8 planned SPI pins are PDM jumper
  pins. It does not matter today, because the SPI link runs only in the
  simulator. A board version would need the PDM jumpers off.
- Demos B and C share no signal pin with the PDM loopback.

### From the PDM loopback to another demo

Starting point: the nine PDM jumpers are fitted and the PicoScope probes the
PDM clock and ch0.

Always first: press **SW1 (MCU PORZ)** to reset the SoC (this stops PRU1),
then reconnect CCS to the target. Loading another demo's R5F program alone
does **not** stop PRU1.

| To run | Wiring | PicoScope |
| ------ | ------ | --------- |
| **A · I2C** | **Remove the ch0 jumper BP.67 to BP.32.** Connect the TCA9538: SCL to BP.33, SDA to BP.32, VCC to BP.1, GND to BP.20. | D0 to **BP.33**, D1 to **BP.32**. If your ch0 probe sits on BP.32 (the PRU0 end of the jumper), it is already the D1 position. The clock probe moves to BP.33. |
| **B · UART RX** | No pin in common. AWG output to BP.31, ground to BP.20. | Digital probes not used; only the AWG output. |
| **C · UART print** | No pin in common. | D0 to **BP.33**. The clock probe moves there. |
| **D · PDM audio (simulator)**, **SPI link** | None: laptop only. | Not used. |

The other eight jumpers share no pin with Demos A, B and C. After the SW1
reset nothing drives them, so they should be harmless left in place. That is
reasoned, not tested; the PDM readme's rule is to pull all nine jumpers
before another demo.

### Back to the PDM loopback
1. Press **SW1**, reconnect CCS.
2. Fit the ch0 jumper BP.67 to BP.32 again (and any other jumper you
   pulled).
3. Disconnect the TCA9538's **SDA wire from BP.32**. Left on, the TCA9538
   sits on ch0's data line. (A precaution; not tried.)
4. The AWG on BP.31 can stay. BP.31 is SD1_CLK, which the PDM demo does not
   use (all channels take the clock from BP.7).
5. PicoScope: clock probe on BP.11 or BP.7, ch0 probe on BP.67 or BP.32.
6. SW6 must still be in its default position (LD17 off) for ch4 to ch7.

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
4. **pru-simulator** (Demo D, SPI link, and "show the simulator first"):
   check out branch `electronica2026-demos` and run
   `pip install -r requirements.txt` once.
5. **AWG file** (Demo B): copy
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

## Demo D: PDM audio (simulator)

PRU1 synthesises two PDM microphones from audio clips (two 2nd-order
sigma-delta modulators, 3.072 MHz PDM clock). PRU0 decodes them through the
ICSS sigma-delta hardware (SINC3 filter, then a 63-tap FIR) into 16 kHz PCM.
The dashboard's **Audio** view shows both channels as live spectrograms.
Every PRU cycle is simulated, so the picture builds up slowly: about 1.5 ms
of audio per second.

### Run
1. Start the dashboard from the pru-simulator folder (branch
   `electronica2026-demos`):
   ```
   python3 ui/server.py      # then open http://127.0.0.1:8080/
   ```
2. Open the **Audio** tab.
3. Pick **Gen L** `speech_librivox.wav` and **Gen R**
   `chirp_lin_100_7000.wav`. **Swap L↔R** exchanges them.
4. Click **Run**. The first setup sets both PRU clocks to 200 MHz and shows
   a notice; that is expected.
5. Click **Booth** for the large view: only the clip pickers, Run/Stop and
   the two spectrograms. **Exit booth** or **Esc** leaves it.

### What good looks like
- The first spectrogram column appears after about 11 s (it needs 16 ms of
  audio). After that a new column (8 ms of audio) follows about every 5 s.
- **Left:** speech formants. **Right:** the chirp ridge climbing from 100 Hz
  to 7 kHz.
- The progress bar moves and **Simulation speed** reads about
  1.5 ms audio / s.
- The 450 ms speech clip takes about 5 minutes. The 250 ms chirp ends after
  about 2 min 50 s, so the right side stays blank from then on while the left
  keeps going. Then the run stops; click **Run** to start again from 0.

### Talking points
- **Two PRU cores, one data line:** both microphones share one PDM data
  line. The left mic is valid on the rising clock edge, the right mic on the
  falling edge, like a real stereo PDM pair.
- **Exact clock:** 3.072 MHz from a 200 MHz core is 65 + 5/48 cycles per
  period. PRU1 mixes short and long periods so there is no drift, only 5 ns
  jitter.
- **Headroom:** the PRU0 receiver needs at most 1009 of the 6250 cycles per
  sample window for 2 channels (16 %). 8 channels are estimated at about 64 %.
- **Every cycle simulated:** that is why it is slow. The same receiver code
  was then ported to the LP-AM243 (open-pru PR #191).

### If something is wrong

| You see | Do this |
| ------- | ------- |
| The tab says "Running in another tab" and Run is greyed out | A second browser tab only watches. Use the tab that started the run, or close the other one. |
| The page was reloaded and the run stopped | Normal. The spectrograms come back from the server; click **Run** to continue without a gap. |
| **Reset** is greyed out | Reset only works while the run is stopped. Click **Stop** first. |
| The pictures start again from 0 | Someone used the Simulator view (Load, Reset, a scenario, a clock change) or set up the SPI demo. That hands the simulator back; the next **Run** sets the audio demo up again from 0. |
| No new column for a long time | Check **Simulation speed**. At about 1.5 ms audio / s a column takes about 5 s. A slower laptop takes longer. |

### SPI link and audio on two screens
One dashboard runs one simulator: setting up the SPI demo ends the audio
run, and an audio **Run** ends the SPI demo. To show both at the same time,
start a second dashboard on another port, for example
`python3 ui/server.py --port 8081`, and open `http://127.0.0.1:8081/` on the
second screen. Checked on the development PC: the SPI test sequence and
an audio run worked side by side from one folder.

---

## SPI link (simulator, runs unattended)

PRU0 is an SPI controller and PRU1 an SPI peripheral, both bit-banged and
wired pin to pin in TI's pru-simulator. Visitors watch frames go across a
scope bit by bit: walking 1, walking 0, 0xA5 / 0x5A and 00 / FF, about
7 s per frame and about 30 s per loop, repeating. No board or PicoScope
is needed, so it can run on its own laptop or screen next to the board
demos.

The code is in pru-simulator, branch `feat/spi-pru-link` (PR #108). Until
the PR is merged, use branch `electronica2026-demos` (see the top of this
page).

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

The simulator demos (D and SPI link) do not touch the board; see "SPI link
and audio on two screens" for switching between them.

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
- Demo D: pru-simulator `source/pdm/README.md` (branch `feat/pdm-audio-demo`,
  PR #106), and `docs/reports/2026-11-03-pdm-audio-demo-report.md`
- SPI link: pru-simulator `docs/spi_link.md` (branch `feat/spi-pru-link`,
  PR #108), and `docs/handoff/2026-11-05-spi-sequence-play.md`
- PDM loopback (board): open-pru `examples/electronica2026/pdm_audio/pdm_audio/readme.md`
  (jumper table, reset rule) and `pdm_audio_eth/pdm_audio_eth/docs/demo_howto.md` (PR #191)
- How each demo was ported: the `docs/porting_report.md` in each project
- Session notes: `docs/handoff/2026-11-02-*.md` and
  `docs/handoff/2026-11-03-*.md` (PR #102)
