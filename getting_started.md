# Getting Started

This guide walks through installing the PRU Simulator and running each example program in `source/` from scratch.

## 1. Install

**Prerequisites:** Python 3.9 or later.

```bash
cd pru_simulator
pip install -r requirements.txt
```

## 2. Start the Dashboard

```bash
python ui/server.py
```

Open `http://localhost:8080` in your browser. You should see the PRU Simulator dashboard.

> Hover over **PRU SIM** in the top-left to confirm the version.

## 3. Dashboard Overview

The dashboard is divided into resizable panels. You can drag panel title bars to rearrange them or split the window. Layouts are saved automatically per session mode (Single-core / Multi-core).

| Panel | Purpose |
|---|---|
| **Source / Disassembly** | Paste assembly source; shows assembled instructions with addresses |
| **Editor** | Text editor for writing or loading `.asm` files |
| **Registers** | R0–R31 live values; double-click any value to edit inline |
| **Memory** | Hex dump of DRAM0/DRAM1/ICSS_SHARED; double-click a cell to edit |
| **IO** | GPO (R30) and GPI (R31) pin controls; loopback strip; UART decoder; SD filter panel |
| **Signal Graph** | Logic analyzer + analog scope; records GPO/GPI and memory channels |
| **Memory Graph** | Analog waveform view for memory buffer channels |
| **Counters** | Cycle count, instruction count, stall cycles, IPC |

**Toolbar buttons:**

| Button | Action |
|---|---|
| Step | Execute one instruction |
| Step Back | Reverse the last instruction (full machine state restored) |
| Run | Execute continuously at the configured interval |
| Reset | Reset PC and registers to 0, and clear this core's Peripheral Interface status/FIFOs |
| HW Reset | Full hardware reset — clears all SPAD banks, all cores, and every core's Peripheral Interface state (TX/RX FIFOs, overrun/underrun, RX valid/overflow, busy). Perif config registers, GP Mux and loopback settings are kept |
| Multi-core | Toggle dual-core (PRU0 + RTU0) debug view |

---

## Example 1 — Running LED (`running_led.asm`)

**What it does:** Shifts a single bit through the GPO pins on each LOOP iteration — simulates a running LED on a GPIO port.

**Source:**
```asm
start:
     ldi r30, 1
     loop endloop, 19
     lsl r30, r30, 1
endloop:
     qba start
```

**Steps:**

1. Open the Editor panel and paste the source above (or click **Open** and select `source/running_led.asm`).
2. Click **Load & Assemble**. The Source / Disassembly panel shows the assembled instructions.
3. Open the **IO** panel. GPO pins 0–19 are shown as indicators.
4. Click **Step** repeatedly and watch bit 0 of R30 shift left through the GPO pins on each loop iteration.
5. Set the **SIM step interval** to `0.1` (seconds) and click **Run** to see the LED pattern animate automatically.
6. Click **Reset** to stop and return to the start.

**What to observe:**
- R30 in the Registers panel updates with each step: `0x00000001` → `0x00000002` → `0x00000004` → …
- GPO pin 0 lights, then pin 1, then pin 2, and so on across the row.
- After pin 19 the program jumps back to `start` and resets to pin 0.

---

## Example 2 — Memory Copy (`mem_copy.asm`)

**What it does:** Writes values 1–100 into DRAM0 using SBCO, then copies them to ICSS_SHARED using LBCO+SBCO. Demonstrates the constant table addressing for data memory access.

**Source:**
```asm
start:
    ldi   r2, 1
    loop endloop, 100
    sbco &r2, c28, r2.b0, 1      ; write r2 → DRAM0[r2.b0]
    add  r2, r2, 1
endloop:
    ldi   r2, 1
    loop endloop1, 25
    lbco &r3, c28, r2.b0, 1      ; read  DRAM0[r2.b0] → r3
    sbco &r3, c24, r2.b0, 1      ; write r3 → ICSS_SHARED[r2.b0]
    add  r2, r2, 1
endloop1:
    halt
```

> `c28` = DRAM0 base, `c24` = ICSS_SHARED base (PRU constant table).

**Steps:**

1. Paste the source (or open `source/mem_copy.asm`) and click **Load & Assemble**.
2. Click **Reset** to clear memory to zero.
3. Open the **Memory** panel. Select **DRAM0** from the region selector.
4. Click **Run** and let the program reach `halt` (the Run button stops automatically on HALT).
5. Observe DRAM0: bytes at offsets 0x01–0x64 should read `01 02 03 … 64`.
6. Switch the Memory panel region to **ICSS_SHARED**: offsets 0x01–0x19 should be a copy of DRAM0[0x01–0x19].

**What to observe:**
- R2 counts from 1 upward in the Registers panel during execution.
- The write loop fills DRAM0; the copy loop copies the first 25 values to ICSS_SHARED.
- Counters panel shows ~130 instruction cycles to complete.

---

## Example 3 — XFR Scratchpad (`xfr_test.asm`)

**What it does:** Writes a value (`0xCAFE`) into PRU scratchpad bank 0 via XOUT, clears the register, reads it back via XIN, then distributes the value to all three SPAD banks. Demonstrates the XFR transfer mechanism.

**Source:**
```asm
    fill &r2, 32
    ldi r2, 0xCAFE
    xout 10, &r2, 32        ; write r2 → SPAD Bank0  (device 10)
    zero &r2, 32            ; clear r2
    xin  10, &r2, 32        ; read  SPAD Bank0 → r2
    xout 11, &r2, 32        ; copy → Bank1 (device 11)
    xout 12, &r2, 32        ; copy → Bank2 (device 12)
    xout 15, &r2, 32        ; copy → system events (device 15)
    mov  r10, r2
    ldi  r0.b0, 4
    xout 10, &r2, 32
    ldi  r0.b0, 0
    xin  10, &r2, 32
    halt
```

> Device IDs: 10 = SPAD Bank0, 11 = Bank1, 12 = Bank2, 15 = System Events.

**Steps:**

1. Open `source/xfr_test.asm` and click **Load & Assemble**.
2. Click **Reset**.
3. Click **Step** once — `fill &r2, 32` sets R2–R9 to `0xFFFFFFFF`.
4. Step again — `ldi r2, 0xCAFE` sets R2 = `0x0000CAFE`.
5. Step through `xout 10, &r2, 32` — R2's value is latched into SPAD Bank0.
6. Step through `zero &r2, 32` — R2 returns to `0x00000000`.
7. Step through `xin 10, &r2, 32` — R2 restores to `0x0000CAFE`. XFR round-trip confirmed.
8. Continue stepping to HALT. Final state: R2 = `0x0000CAFE`, R10 = `0x0000CAFE`.

**What to observe:**
- After `zero`, R2 = 0 — the scratchpad is the only copy of the value.
- After `xin`, R2 = `0x0000CAFE` — retrieved from SPAD Bank0.
- R10 = `0x0000CAFE` after `mov r10, r2`.

---

## Example 4 — MAC Accelerator (`mac_example.asm`)

**What it does:** Demonstrates the ICSSG broadside multiplier (device_id=0) in two modes:
1. **MPY mode** — single multiply: 50 × 25 = 1250 (`0x000004E2`)
2. **MAC mode** — dot product: (1,2,3) · (4,5,6) = 4 + 10 + 18 = 32 (`0x00000020`)

The result is read back via XIN into R26 (low 32 bits) and R27 (high 32 bits).

**Register map used:**

| Register | Role |
|---|---|
| R25 | MAC_CTRL_STATUS (bit0 = MAC_MODE, bit1 = clear ACC_CARRY) |
| R26 | Result low 32 bits |
| R27 | Result high 32 bits |
| R28 | Operand A (auto-sampled on each XOUT R25) |
| R29 | Operand B (auto-sampled on each XOUT R25) |

**Steps:**

1. Open `source/mac_example.asm` and click **Load & Assemble**.
2. Click **Reset**.

**Part 1 — MPY mode:**

3. Step through the `mpy_demo` section (lines up to the first `xin DEVICE_ID, &r27, 4`).
4. After these steps, check the Registers panel:
   - R26 = `0x000004E2` (1250 = 50 × 25)
   - R27 = `0x00000000`
   - R28 = `0x00000032` (50)
   - R29 = `0x00000019` (25)

**Part 2 — MAC mode:**

5. Continue stepping through `mac_demo`. Watch R28/R29 load successive vector elements.
6. Each `xout DEVICE_ID, &r25, 1` triggers an accumulate: the hardware adds R28×R29 to the running total.
7. After the final `xin DEVICE_ID, &r27, 4` (just before HALT), check:
   - R26 = `0x00000020` (32 = dot product)
   - R27 = `0x00000000`

8. Click **Run** (or continue stepping) to reach HALT.

**Dashboard tip:** The Registers panel shows a **MAC** indicator row (near the Carry flag) that displays the current MAC mode and whether ACC_CARRY is set — useful for tracking accumulator state without stepping through every instruction.

---

## Example 5 — UART Transmitter + Decoder (`uart_tx.asm`)

**What it does:** Bit-bangs a single UART byte (`'A'` = 0x41) on GPO pin 0 at 115200 baud (8N1 framing). The IO panel's UART decoder reconstructs the byte from the recorded GPO transitions.

**Steps:**

1. Open `source/uart_tx.asm` and click **Load & Assemble**.
2. Open the **Signal Graph** panel and click **REC** to arm recording.
3. Click **Run** — the program completes and halts automatically.
4. Open the **IO** panel. In the **UART Decoder** section, verify:
   - **GPO** selector is set to `GPO0`
   - **Mode** is `8N1`
5. Click **▶ Decode**. The decoded result appears as: `41  A`

**What to observe:**
- The Signal Graph shows the start bit (LOW), 8 data bits LSB-first, and stop bit (HIGH) on the GPO0 lane.
- The UART decoder auto-detects the bit period from the recorded transitions.
- Edit `TX_CHAR` in the source to transmit any ASCII character.

---

## Example 6 — UART Receiver + Frame Injection (`uart_rx_11frame.asm`)

**What it does:** Receives 11-byte UART frames at 4 Mbaud on GPI pin 0 (R31 bit 0). The IO panel's **UART RX Inject** section generates the stimulus waveform. Received bytes are stored to DRAM0, verifiable in the Memory panel.

**Steps:**

1. Open `source/uart_rx_11frame.asm` and click **Load & Assemble**.
2. Open the **IO** panel. Scroll down to the **UART RX Inject** section (below UART Decoder).
3. Enter a payload in the hex field, e.g. `48 65 6C 6C 6F 57 6F 72 6C 64 21` (or switch to ASCII and type `HelloWorld!`).
4. Verify settings: **Pin** = GPI0, **Baud** = 4.00 Mb, **Frames** = 1.
5. Click **▶ Inject**. The status line shows: `✓ Armed: 11 bytes × 1 frame · trigger cycle N · run to receive`.
6. Click **Run** (or step ~8000 instructions). The firmware detects the start bit, samples all 11 bytes, and stores them via SBCO.
7. Open the **Memory** panel. Set address to `0x00000000` and refresh. The 11 injected bytes appear at offset 0.

**What to observe:**
- The firmware polls GPI0 for a falling edge (start bit), then samples at mid-bit intervals.
- Each received byte is accumulated LSB-first and packed into registers R2–R4.
- After 11 bytes, a single `SBCO &R2, c24, R1, 11` stores the frame to DRAM0.
- R20 (frame counter) increments by 1 after each successful frame.
- If a framing error occurs (invalid stop bit), DRAM0[0x0FFE] is set to 1 and the frame is discarded.

**Variations:**
- Change baudrate to test tolerance (receiver handles down to ~3.75 Mbaud).
- Set **Frames** > 1 to inject multiple consecutive frames — each is stored sequentially in DRAM0.
- Use the MCP tool `pru_uart_inject` for automated testing from Claude.

---

## Example 7 — MVI GPIO Loopback (`mvi_gpio_loopback.asm`)

**What it does:** Drives a walking-bit pattern on GPO (R30.b0) via MVIB register-file indirect, then reads the looped-back value from GPI (R31.b0) into a capture buffer. Demonstrates MVIB addressing and the IO panel's loopback feature.

**Setup:**

1. Open `source/mvi_gpio_loopback.asm` and click **Load & Assemble**.
2. Open the **IO** panel. Between the GPO and GPI rows you will see five labeled loopback toggle buttons: `3:0  7:4  11:8  15:12  19:16`.
3. Enable groups **3:0** and **7:4** (both amber).

**Steps:**

4. Click **Run** (or step through the inner loop 16 times).
5. After the program completes, check the Registers panel:
   - R2–R5 hold the TX pattern (walking bits)
   - R10–R13 hold the RX capture and should mirror R2–R5

**What to observe:**
- Each MVIB write drives one byte of R30 (GPO), and the loopback immediately feeds it back to R31 (GPI).
- The next MVIB read captures the GPI byte into the RX buffer.
- Without loopback enabled, R10–R13 will remain zero (GPI pins not driven).

---

## Example 8 — SDFM SINC3 Demo (`source/sdfm_sinc3_demo/`)

**What it does:** Free-running SINC3 (3rd-order CIC) sigma-delta filter, adapted from real AM261x ICSS-M firmware. Configures the SD peripheral via memory-mapped registers, then reads filtered accumulator values in a loop.

See `source/sdfm_sinc3_demo/README.md` for full setup instructions, expected register values, and notes on differences from hardware.

**Quick steps:**

1. Open the project: click **Project** in the Editor panel and select `source/sdfm_sinc3_demo/`.
2. Click **Load & Assemble**.
3. Open the **IO** panel. The SD panel appears automatically once the firmware writes `sd_en=1` to R30.
4. Set the step interval to `0.01` s and click **Run**.
5. In the SD panel, watch the **acc3** (SINC3) shadow values update every OSR ticks. With the default DC=0.5 input and OSR=64, acc3 should stabilise around `0x200000`.

**What to observe:**
- The SD panel shows R30 decode fields (ch_sel, sd_en, data_sel) updating as firmware runs.
- Each channel card shows live accumulator values and the shadow latch with the valid flag.
- The pattern generator controls let you switch between DC and sine inputs in real time.

---

## Signal Graph

The Signal Graph panel records GPO/GPI pin states and optional memory addresses over time.

**To record a running_led trace:**

1. Load `running_led.asm` and click Reset.
2. Open the Signal Graph panel. The **GPO** and **GPI** channels are added by default.
3. Set the step interval to `0.05` s and click **Run**.
4. The graph draws a digital lane per pin — pin 0 through pin 7 shift in sequence.
5. Click **Export CSV** to save the trace for further analysis.

**To add a memory channel:**

1. Click **+ Mem Channel** in the Signal Graph panel.
2. Enter a memory address (e.g. `0x0001` for DRAM0 offset 1) and click Add.
3. Run `mem_copy.asm` — the graph plots the byte value at that address as an analog lane.

---

## Tips

- **Edit a register directly:** Double-click any value in the Registers panel, type a new hex value (without `0x`), and press Enter. R30 edits update GPO pins immediately; R31 edits update GPI pins.
- **Edit a memory cell:** Double-click any cell in the Memory panel and type a new hex byte.
- **Toggle a GPI pin:** Click any pin indicator in the IO panel to set it high; click again to clear. The R31 register updates in real time.
- **GPIO loopback:** The five toggle buttons between GPO and GPI rows wire 4-bit groups of GPO directly to GPI. Useful for firmware that reads back what it writes without physical wiring.
- **Step back:** Click **Step Back** (or press `←`) to reverse the last instruction. The full machine state — registers, memory, SD filter — is restored exactly.
- **Layout:** Drag a panel title bar onto another panel to split the window. Layouts persist across sessions.
- **Reset Layout:** Click the **⊞ Reset Layout** button in the toolbar to restore the default arrangement.
