# Sigma-Delta Filter Interface — Design Specification

## Overview

Add a sigma-delta (SD) filter peripheral to the PRU simulator, modeling the ICSS-G SCU SD hardware. The SD filter demodulates 1-bit PDM bitstreams from external sigma-delta ADC modulators. Configuration is through memory-mapped registers at real ICSS-G addresses. The IO panel switches from GPIO view to SD interface view when SD mode is active.

## Architecture

### Module Structure

```
Simulator
├── PRUCore (PRU0)
│   ├── IOPort (delegates to SD filter when sd_en=1)
│   └── SigmaDeltaFilter
│       ├── SigmaDeltaChannel[0]
│       ├── SigmaDeltaChannel[1]
│       └── SigmaDeltaChannel[2]
├── PRUCore (RTU0)
│   ├── IOPort (delegates to SD filter when sd_en=1)
│   └── SigmaDeltaFilter
│       ├── SigmaDeltaChannel[3]
│       ├── SigmaDeltaChannel[4]
│       └── SigmaDeltaChannel[5]
├── MemoryBus (hosts SD config registers)
├── SDModulator[0..5] (pattern generators, one per channel)
└── XFRBus (unchanged)
```

### Key Interactions

1. **PRU writes R30** → IOPort checks sd_en → routes to `SigmaDeltaFilter.process_r30(value)` decoding ch_sel[29:26], sd_en[25], snoop[24], data_sel[23]
2. **PRU reads R31** → IOPort checks sd_en → returns `SigmaDeltaFilter.get_r31_status()` packing {00, ovf, valid, data[27:0]} for selected channel
3. **Simulator.step()** → calls `sd_filter.tick()` advancing the filter by fractional SD clock ticks based on async clock model
4. **Memory writes to CFG registers** → MemoryBus intercepts writes at 0x26044–0x26060 → updates SigmaDeltaFilter configuration
5. **SDModulator** feeds sd_dat bitstream into channel inputs at the configured SD clock rate

## Channel Configuration

- 3 channels per core with load-sharing: PRU0 owns ch0–2, RTU0 owns ch3–5
- Load-share mode enabled via `SD_CFG_REG0.SHARE_EN[8]`
- Channel selection via R30[29:26] (ch_sel field)

## Accumulator Model

Each channel has 3 cascaded accumulators running continuously:

```
Per SD clock tick (when sd_en=1):
  acc1 += sd_dat_bit        (SINC1: running sum of bits)
  acc2 += acc1              (SINC2: running sum of acc1)
  acc3 += acc2              (SINC3: running sum of acc2)
  sample_counter++

  When sample_counter == OSR:
    shadow_acc1 = acc1
    shadow_acc2 = acc2
    shadow_acc3 = acc3
    valid = 1
    sample_counter = 0
    (accumulators keep running — NO auto-reset)
```

### Key behaviors

- **No auto-reset**: Accumulators never reset on their own. They accumulate continuously.
- **Shadow latch**: Every OSR ticks, current accumulator values are copied to shadow registers and valid flag is set.
- **Valid flag**: Set when shadow is latched, cleared when PRU reads R31.
- **Overflow**: Set when any accumulator exceeds 28-bit range. Cleared via clr_ovf command.
- **reinit command**: PRU writes reinit bit → resets acc1=acc2=acc3=0 and sample_counter=0.

### Firmware operation modes (both supported by same hardware model)

1. **Free-running**: PRU reads shadow every OSR period, does differentiation (comb stages) in firmware on consecutive snapshots. Accumulators never reset.
2. **Reset-and-measure**: PRU issues reinit, waits 3 OSR periods for acc3 pipeline to fill, reads result. Measurement timing controlled by reinit.

## Clock Model

Two independent asynchronous clock domains:

- **PRU clock (ocp_gclk)**: Configured in simulator config file (e.g., 200 MHz). Each `step()` = 1 PRU cycle.
- **SD clock**: Per-channel, configured in pattern generator UI (10–40 MHz). Asynchronous to PRU clock.

### Fractional accumulator approach

```python
# Per Simulator.step():
for channel in sd_channels:
    channel.sd_accumulator += channel.sd_freq / pru_freq
    while channel.sd_accumulator >= 1.0:
        channel.sd_accumulator -= 1.0
        channel.process_one_sd_tick(modulator.next_bit())
```

Example: PRU=200MHz, SD=20MHz → ratio=0.1 → one SD tick every 10 PRU steps.

## R30/R31 Register Interface

### R30 — Control (written by PRU)

| Bits | Field | Description |
|------|-------|-------------|
| [29:26] | ch_sel | Channel select (0–2 for PRU0, 3–5 for RTU0) |
| [25] | sd_en | SD interface enable (1=active, switches IO panel to SD mode) |
| [24] | snoop | Snoop mode (read other core's channel) |
| [23] | data_sel | Data source select |
| [22:0] | — | Unused by SD (pass-through to GPIO if needed) |

### R31 — Status (read by PRU)

| Bits | Field | Description |
|------|-------|-------------|
| [31:30] | — | Reserved (00) |
| [29] | ovf | Overflow flag (sticky, clear via clr_ovf) |
| [28] | valid | New sample ready (cleared on read) |
| [27:0] | data | Accumulator output (selected by ACC_SEL: acc1/acc2/acc3 shadow) |

### R31 — Commands (written by PRU, self-clearing)

Per the functional spec, commands are issued by writing to R31 with specific bit patterns:

| Bits | Field | Description |
|------|-------|-------------|
| [29] | clr_ovf | Write 1 to clear overflow flag for selected channel (self-clearing) |
| [28] | reinit | Write 1 to reset accumulators to zero for selected channel (self-clearing) |

## Memory-Mapped Configuration Registers

Base address: ICSS-G CFG space (e.g., 0x00026044 relative to PRU subsystem).

### SD_CFG_REG0 (Offset 0x26044, global)

| Bits | Field | R/W | Description |
|------|-------|-----|-------------|
| [8] | SHARE_EN | R/W | Load-share enable. 0=PRU owns all 0–8; 1=RTU:0–2, PRU:3–5, TX_PRU:6–8 |
| [14:11] | CH_SEL | R/W | Manchester SD channel select (not used in normal SD mode) |

### SD_CLK_SEL_REGn (Offset 0x26048 + n×8, per channel n=0–2)

| Bits | Field | R/W | Description |
|------|-------|-----|-------------|
| [5:4] | ACC_SEL | R/W | Accumulator output select: 0=acc3, 1=acc2, 2=acc1 |
| [2] | CLK_INV | R/W | Clock inversion: 0=rising edge, 1=falling edge |
| [1:0] | CLK_SEL | R/W | Clock source: 0=r31[16], 1=own sd_clk, 2=shared bank clk, 3=reserved |
| [22] | FD_ZERO_MAX | R/W1C | Fast Detect zero max threshold hit |
| [21:17] | FD_ZERO_MAX_LIMIT | R/W | Fast Detect zero max threshold (0–31 → 1–32) |
| [16] | FD_ZERO_MIN | R/W1C | Fast Detect zero min threshold hit |
| [15:11] | FD_ZERO_MIN_LIMIT | R/W | Fast Detect zero min threshold (0–31 → 1–32) |

### SD_SAMPLE_SIZE_REGn (Offset 0x2604C + n×8, per channel n=0–2)

| Bits | Field | R/W | Description |
|------|-------|-----|-------------|
| [7:0] | SAMPLE_SIZE | R/W | Over-sample rate: effective OSR = value + 1. Min valid=3 (OSR=4), max=255 (OSR=256) |
| [10:8] | FD_WINDOW_SIZE | R/W | Fast Detect window: 0=4, 1=8, ..., 7=32 samples |
| [23] | FD_EN | R/W | Fast Detect enable |
| [22] | FD_ONE_MAX | R/W1C | Fast Detect one max threshold hit |
| [21:17] | FD_ONE_MAX_LIMIT | R/W | Fast Detect one max threshold |
| [16] | FD_ONE_MIN | R/W1C | Fast Detect one min threshold hit |
| [15:11] | FD_ONE_MIN_LIMIT | R/W | Fast Detect one min threshold |

### Register access model

- Registers exist as a byte-addressable region in MemoryBus
- Writes trigger callbacks updating SigmaDeltaFilter configuration in real-time
- Reads return current register state
- UI config shortcuts write to these same memory addresses (single source of truth)
- W1C (write-1-to-clear) behavior for status bits (FD threshold flags)

## Fast Detect

Sliding-window monitor counting zeros and ones in the bitstream:

- Window size: 4–32 contiguous samples (configurable)
- Uses 32-bit shift register internally
- Compares zero/one counts against configurable thresholds
- Sets status flags (FD_ZERO_MAX, FD_ZERO_MIN, FD_ONE_MAX, FD_ONE_MIN)
- Flags cleared by writing 1 to the respective bit
- Enabled per-channel via FD_EN

## Pattern Generator (SDModulator)

Simulates an external sigma-delta ADC modulator providing sd_clk + sd_dat bitstream.

### 2nd-order sigma-delta modulator

```
Input x[n] → [+] → Integrator1 → [+] → Integrator2 → 1-bit Quantizer → sd_dat
              ▲                    ▲                          │
              └─── (-) feedback ───┴──────────────────────────┘
```

Output: 1-bit PDM stream where bit density encodes the input signal amplitude.

### Signal sources (selectable per channel)

**DC Level:**
- Range: -1.0 to +1.0 (maps to full-scale ADC range)
- Adjustable via UI slider

**Sine Wave:**
- Amplitude: 0–100% (default 80%)
- Period: samples per cycle (default 1024)
- Phase offset: 0–360° (default 0°)

### Per-channel parameters (live-adjustable from UI)

| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| Signal type | DC / Sine | DC | Input signal shape |
| SD Clock | 10–40 MHz | 20 MHz | Modulator clock frequency |
| DC Level | -1.0 to +1.0 | 0.0 | DC input value |
| Sine Amplitude | 0–100% | 80% | Peak amplitude relative to full-scale |
| Sine Period | 64–65536 samples | 1024 | Samples per full sine cycle |
| Sine Phase | 0–360° | 0° | Phase offset |

### Per-channel independence

Each of the 3 channels (per core) has its own SDModulator instance with independent signal source. Allows testing scenarios like:
- ch0=sine@0°, ch1=sine@120°, ch2=sine@240° (3-phase motor)
- ch0=DC(0.5), ch1=sine, ch2=DC(-0.3) (mixed)

## UI Design

### Mode switching

- IO panel shows **GPIO pin grid** (default) OR **SD interface** (when sd_en=1 in R30)
- Mode indicator at top: "SD MODE" badge + "Switch to GPIO" link
- Auto-switches when PRU writes sd_en=1 to R30
- Manual override available via UI link

### SD Panel layout (replaces GPIO grid)

```
┌─────────────────────────────────────────────────┐
│ [SD MODE] PRU0 owns Ch 0–2    [Switch to GPIO→] │
├─────────────────────────────────────────────────┤
│ R30 Control: [29:26]ch_sel=0 [25]sd_en=1 ...    │
├─────────────────────────────────────────────────┤
│ ┌─────────────┐ ┌───────────┐ ┌───────────┐    │
│ │ CH 0 ● sel  │ │ CH 1 ○    │ │ CH 2 ○    │    │
│ │ CLK:20M     │ │ CLK:20M   │ │ CLK:20M   │    │
│ │ OSR:64      │ │ OSR:64    │ │ OSR:128   │    │
│ │ ACC:sinc3   │ │ ACC:sinc3 │ │ ACC:sinc1 │    │
│ │             │ │           │ │           │    │
│ │ acc1:0x001A │ │ acc1:0x00 │ │ acc1:0x00 │    │
│ │ acc2:0x03F2 │ │ acc2:0x00 │ │ acc2:0x00 │    │
│ │ acc3:0xBC4A2│ │ acc3:0x00 │ │ acc3:0x00 │    │
│ │             │ │           │ │           │    │
│ │ R31:valid=1 │ │ R31:v=0   │ │ R31:v=0   │    │
│ │ data=0xBC4A2│ │ data=0x00 │ │ data=0x00 │    │
│ └─────────────┘ └───────────┘ └───────────┘    │
├─────────────────────────────────────────────────┤
│ Pattern Generator                                │
│ ┌─────────────┐ ┌───────────┐ ┌───────────┐    │
│ │CH0: Sine    │ │CH1: DC    │ │CH2: DC    │    │
│ │CLK: 20MHz   │ │CLK: 20MHz │ │CLK: 20MHz │    │
│ │Amp: 80%     │ │Level: 0.5 │ │Level: 0.0 │    │
│ │Period: 1024 │ │           │ │           │    │
│ │Phase: 0°    │ │           │ │           │    │
│ └─────────────┘ └───────────┘ └───────────┘    │
├─────────────────────────────────────────────────┤
│ Config: SD_CFG:SHARE_EN=1  Ch0:OSR=64...        │
└─────────────────────────────────────────────────┘
```

### State broadcast

WebSocket state message extended with SD data:
```json
{
  "io": {
    "mode": "sd",
    "sd": {
      "r30_control": {"ch_sel": 0, "sd_en": true, "snoop": false, "data_sel": false},
      "channels": [
        {"id": 0, "acc1": 26, "acc2": 1010, "acc3": 771234,
         "shadow_acc1": 26, "shadow_acc2": 1010, "shadow_acc3": 771234,
         "valid": true, "ovf": false, "selected": true,
         "config": {"osr": 64, "acc_sel": "sinc3", "clk_sel": "own", "clk_inv": false}},
        ...
      ],
      "pattern_gen": [
        {"signal": "sine", "sd_clock_mhz": 20, "amplitude": 0.8, "period": 1024, "phase_deg": 0},
        ...
      ]
    }
  }
}
```

## File Structure

New/modified files:

```
pru_io/
├── io_port.py          (modify: add SD mode delegation)
├── sd_filter.py        (new: SigmaDeltaFilter + SigmaDeltaChannel)
├── sd_modulator.py     (new: SDModulator pattern generator)
└── sd_registers.py     (new: register definitions + memory-mapped callbacks)

core/
└── pru_core.py         (modify: wire SD filter tick into step cycle)

simulator.py            (modify: create SD filters, wire to memory bus)
ui/server.py            (modify: broadcast SD state, handle SD UI actions)
ui/static/app.js        (modify: SD panel rendering + pattern gen controls)
ui/static/index.html    (modify: SD panel HTML structure)
config/
└── simulator.cfg       (modify: add pru_clock_freq entry)

tests/
├── test_sd_filter.py   (new: accumulator, timing, register interface)
├── test_sd_modulator.py(new: pattern generator output verification)
└── test_sd_registers.py(new: MMR read/write/callback behavior)
```

## Testing Strategy

1. **Unit: SigmaDeltaChannel** — accumulator math, shadow latch at OSR, no auto-reset, reinit behavior, overflow detection
2. **Unit: SDModulator** — DC produces expected bit density, sine produces correct modulated output, phase offset works
3. **Unit: SD Registers** — read/write at correct offsets, W1C behavior, callbacks trigger config updates
4. **Integration: Filter + Modulator** — DC input produces stable accumulator output matching expected value, sine produces periodic accumulator variation
5. **Integration: PRU + SD** — R30 write enables SD, R31 read returns correct status, channel switching works, reinit clears accumulators
6. **Clock model** — async tick advancement matches expected ratio, edge cases (sd_freq > pru_freq/4)

## Constraints and Limitations

- OSR valid range: 4–256 (SAMPLE_SIZE register values 3–255)
- Max SD clock: approximately pru_freq/4 (per spec timing constraint)
- Accumulator width: 28 bits (overflow when exceeded)
- Fast Detect window: 4–32 samples
- Channel select latency: 1 PRU cycle after R30 write before R31 reflects new channel
- No Manchester decode mode in v1 (SD_CFG_REG0 Manchester fields ignored)
