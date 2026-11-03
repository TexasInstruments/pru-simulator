# electronica 2026 booth demos: how to run

This page is for booth staff. It covers the two PRU demos on the AM243x
LaunchPad (LP-AM243). Each demo needs the PicoScope set up differently, so
that setup gets its own section for each demo, and a side-by-side table
comes first.

| Demo | What visitors see | PicoScope is used as |
| ---- | ----------------- | -------------------- |
| **A. I2C IO expander** | A PRU bit-bangs 1 Mbit/s I2C. One LED runs along 8 LEDs, the PicoScope decodes the bus, and a PC window lights the same LED. | **Logic analyzer** (digital inputs D0/D1) |
| **B. UART RX frames** | A PRU receives 4 Mbaud UART frames. `HelloWorld!` fills the PRU memory, shown in CCS. | **Signal generator** (AWG output) |

Both demos run on the same board, but **only one at a time**: both use
ICSSG0 PRU0. To switch, load the other demo's R5F program (see "Switching
between demos").

The source code is in the open-pru repository, under
`examples/electronica2026/`:

| Demo | Folder | open-pru branch / PR |
| ---- | ------ | -------------------- |
| A | `i2c_io_expander/` (`i2c_io/` firmware, `visualizer/` PC window) | `dev_tley_electronica2026_i2c_io`, PR #188 |
| B | `uart_rx_frame/uart_rx/` | `dev_tley_electronica2026_uart_rx`, PR #189 |

Until both PRs are merged, each demo exists only on its own branch.

---

## PicoScope settings at a glance

| Setting | Demo A: I2C IO expander | Demo B: UART RX |
| ------- | ----------------------- | --------------- |
| Used for | Capture and decode | Send the test signal |
| Inputs | **D0 = SCL, D1 = SDA** (digital) | none needed |
| Output | none | **AWG** output → BP.31 |
| Signal generator | **Off** | **Arbitrary**, CSV `helloworld_4mbaud_awg.csv`, **20 kHz**, amplitude **1 V**, offset **+1 V** |
| Trigger | **Pulse width** on D0 (SCL), positive pulse **> 1 µs**, mode **Repeat**, about 20 % pre-trigger | not needed |
| Serial decoder | **I2C**, Data = D1, Clock = D0, **Fast-mode Plus (1 Mbit/s)**. The visualizer adds it if missing. | none |
| Timebase | About 100 µs across the screen (one I2C frame lasts 29 µs) | any |
| MCP server (beta) | **On**, port **manual 18123** (the LED window needs it) | not needed |
| What good looks like | One decoded frame per capture: `23 W 01 xx`, xx walks 01 → 80 | CCS shows `HelloWorld!` repeating in PRU memory |

**Tip:** once each setup works, save it as a PicoScope settings file
(for example `demoA_i2c` and `demoB_uart`), so switching later is just
"open settings".

---

## Wiring (both demos, can stay connected)

The demos use different pins, so all wires can stay connected. Demo A does
not touch BP.31, and Demo B does not touch BP.32/33.

The LaunchPad headers are labelled with **BoosterPack (BP) pin numbers**.
There is no "J4.10" or "J4.8"; use the BP numbers below.

| Signal | LaunchPad pin | Connect to | Demo |
| ------ | ------------- | ---------- | ---- |
| SCL | **BP.33** (J4) | TCA9538 SCL, PicoScope **D0** | A |
| SDA | **BP.32** (J4) | TCA9538 SDA, PicoScope **D1** | A |
| 3V3 | BP.1 (J1) | TCA9538 VCC | A |
| UART RX | **BP.31** (J4) | PicoScope **AWG** output (centre) | B |
| GND | BP.20 (J2) | TCA9538 GND, PicoScope digital GND, AWG ground | A + B |

TCA9538 board: A1 = A0 = high (address 0x23), RESET high, LEDs with series
resistors on P0 to P7. No I2C pull-ups needed.

---

## Before the booth opens (once per PC)

1. **CCS 21** with both projects of each demo imported. CCS copies the
   source files into its workspace on import, so after the import the
   projects no longer depend on which open-pru branch is checked out.
   - Demo A: `i2c_io_am243x-lp_icss_g0_pru0_fw_ti-pru-cgt` and
     `i2c_io_am243x-lp_r5fss0-0_freertos_ti-arm-clang`
   - Demo B: `uart_rx_am243x-lp_icss_g0_pru0_fw_ti-pru-cgt` and
     `uart_rx_am243x-lp_r5fss0-0_freertos_ti-arm-clang`
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

## Switching between demos

1. Stop the current demo:
   - Demo A: close the LED window.
   - Demo B: set the signal generator to **Off** or **DC +2 V**.
2. In PicoScope, switch to the other demo's settings (open the saved
   settings file, or use the table at the top).
3. In CCS, do a **system reset** of the board, or power-cycle it. Then load
   and run the other demo's **R5F** program. It loads its own PRU0 firmware.
4. For Demo B, keep the start order: **DC +2 V → run R5F → Arbitrary**.

The wiring can stay as it is.

---

## Optional: show the simulator first

Both demos started in TI's pru-simulator. To show that step:

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

---

## More detail

- Demo A: open-pru `examples/electronica2026/i2c_io_expander/readme.md`,
  `i2c_io/readme.md`, `visualizer/readme.md`
- Demo B: open-pru `examples/electronica2026/uart_rx_frame/uart_rx/readme.md`
- How each demo was ported: the `docs/porting_report.md` in each project
- Session notes: `docs/handoff/2026-11-02-*.md` and
  `docs/handoff/2026-11-03-uart-rx-hw-run-and-mcp-venv.md` (PR #102)
