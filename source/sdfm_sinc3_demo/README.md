# SDFM SINC3 Demo

Free-running SINC3 sigma-delta filter on SD channel 2, OSR=64.
Adapted from the AM261x ICSS-M firmware in
`workspace_ccstheia/empty_am261x-lp_icss_m0_pru1_fw_ti-pru-cgt/main.asm`.

## What it does

1. Configures SD channel 2 for SINC3 mode, OSR=64 (20 MHz SD clock → ~312 Hz sample rate at 200 MHz PRU clock).
2. Enables the SD channel and issues a re-initialise command.
3. Enters a free-running loop: polls R31 for a new shadow value, runs the
   3-stage comb filter (`M_ACC3_PROCESS`), and stores the 32-bit SINC3 result
   to DRAM0.
4. Wraps the write pointer after 256 samples (1024 bytes, 0x000–0x3FF in DRAM0).

## Files

```
source/sdfm_sinc3_demo/
    sdfm_sinc3_demo.asm        main program
    include/
        sdfm_defines.inc       register aliases and channel-select constants
        sdfm_macros.inc        M_ACC3_PROCESS comb-filter macro
    README.md                  this file
```

## How to run in the simulator

### Step 1 — Open the simulator

```
cd Projects/pru_simulator
python simulator.py        # CLI mode
# or
python ui/server.py        # Web UI on http://localhost:8765
```

### Step 2 — Load the program

**Web UI:**
- Click **Load** and select `source/sdfm_sinc3_demo/sdfm_sinc3_demo.asm`
- Include path: `source/sdfm_sinc3_demo`

**CLI / Python:**
```python
sim.load('pru0', open('source/sdfm_sinc3_demo/sdfm_sinc3_demo.asm').read(),
         include_paths=['source/sdfm_sinc3_demo'])
```

### Step 3 — Configure the SD pattern generator (sine wave)

In the Web UI, open the **SD Interface** panel and for channel 2 set:

| Parameter     | Value       | Notes                                  |
|---------------|-------------|----------------------------------------|
| Signal        | `sine`      | 2nd-order sigma-delta sine modulator   |
| SD clock      | `20.0` MHz  | Matches hardware firmware setting      |
| Amplitude     | `0.5`       | ±50 % of full scale, well within range |
| DC offset     | `0.0`       | Centred around zero                    |
| Period        | `1024`      | SD-clock samples per sine cycle        |
| Phase         | `0.0` °     |                                        |

**Python / scripting:**
```python
sim.set_sd_modulator('pru0', 2,
    signal='sine', amplitude=0.5, dc_level=0.0,
    period=1024, phase_deg=0.0, sd_clock_mhz=20.0)
```

### Step 4 — Run

Click **Run** (or `sim.run()`).  The program loops indefinitely.  After
~200 k PRU clock cycles, 256 filtered samples are in DRAM0.

To collect one complete buffer (~163 840 PRU ticks):
```python
# Each SINC3 sample takes OSR × (PRU_MHz / SD_MHz) = 64 × 10 = 640 ticks
# 256 samples × 640 ticks = 163 840 ticks minimum for one full buffer
for _ in range(200_000):
    core.step()
```

### Step 5 — Read results

**Memory panel**: set Base Address = `0x00000000`, Length = `1024` to view
all 256 samples in DRAM0.

**Python:**
```python
import struct
data, _ = sim.memory.read(0x00000000, 1024)
samples = struct.unpack('<256I', data)
print(samples)
```

## Expected output

With amplitude=0.5 (±50 % of OSR³) and OSR=64 the SINC3 output oscillates
between roughly:

| Measurement   | Value       |
|---------------|-------------|
| SINC3 maximum | ≈ 196 608   |
| SINC3 minimum | ≈  65 536   |
| Midpoint      | ≈ 131 072 (= OSR³/2 = 262144/2) |
| Peak-to-peak  | ≈ 131 072   |

The raw ACC3 shadow values (in R31[27:0]) span 0–262 144 (= 64³).

## Hardware differences from the original AM261x firmware

| Aspect | AM261x ICSS-M (original) | ICSS-G Simulator |
|---|---|---|
| SD_CFG_REG0 | `C4 + 0x90` | `C4 + 0x44` |
| CH2 SD_CLK_SEL | `C4 + 0xA4` | `C4 + 0x58` |
| CH2 SD_SAMPLE_SIZE | `C4 + 0xA8` | `C4 + 0x5C` |
| Reinit command | R31 write bit 23 | R31 write bit 23 (identical) |
| Shadow valid flag | R31[28], cleared by R31[24] write | R31[28], cleared by R31[24] write (identical) |
| Data buffer | `C16` (external RAM) | `C24` = DRAM0 (0x00000000) |
| GPCFG pin-mux | Required | Not needed (simulator config) |

## Algorithm overview

The `M_ACC3_PROCESS` macro implements the comb section of a SINC3 filter.
The hardware ACC3 integrator section runs inside the SD peripheral.

```
Hardware (SD peripheral):
  acc1[n] = acc1[n-1] + bit[n]
  acc2[n] = acc2[n-1] + acc1[n]
  acc3[n] = acc3[n-1] + acc2[n]          ← latched to shadow every OSR ticks

Firmware (M_ACC3_PROCESS, runs every OSR ticks):
  y1[k] = acc3[k] - acc3[k-1]            ; first  difference (DN0 → CN3, updates DN1)
  y2[k] = y1[k]   - y1[k-1]             ; second difference (CN3 → CN4, updates DN3)
  y3[k] = y2[k]   - y2[k-1]             ; third  difference (CN4 → CN5, updates DN5)
  output = y3[k] & 0x0FFFFFFF            ; 28-bit clamp → CN5 (R8)
```

This is equivalent to the 3rd-order FIR comb: `y[k] = acc3[k] - 3·acc3[k-1] + 3·acc3[k-2] - acc3[k-3]`.

Resolution: OSR³ counts full-scale → log₂(64³) ≈ 18 bits at OSR=64.
