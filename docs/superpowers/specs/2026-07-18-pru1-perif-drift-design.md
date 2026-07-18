# PRU1 Core + Peripheral-Mode Loopback RX with Clock-Drift Experiment — Design

**Date:** 2026-07-18
**Status:** Approved

## 1. Overview

Add a third core `pru1` to the simulator, give it its own clock frequency
(configurable in `memory.cfg`), move the second Peripheral Interface block to it
(hardware-accurate per TRM), and build a TX/RX firmware pair that streams a
byte pattern over the ch0 loopback. A pytest experiment then measures at what
pattern length the receiver stops reconstructing the data for a given
PRU0↔PRU1 clock offset.

Physical basis: the perif RX oversampler detects the start bit **once** per
arming and then free-runs (`PerifChannel._rx_started` never resets during a
stream). Sampling-point slip therefore accumulates over the whole pattern at
`ppm × bits`, and reception fails once the slip reaches ~half a bit period:

```
failure_length_bits ≈ 0.5 / (offset_ppm × 1e-6)
e.g. +500 ppm → ~1000 bits ≈ 125 bytes
```

## 2. Configuration (`memory.cfg`)

One new **optional** key in `[device]`:

```ini
[device]
pru_clock_mhz = 200        ; PRU0 and RTU0, as today
pru1_clock_mhz = 200.1     ; NEW — PRU1 clock; defaults to pru_clock_mhz if absent
```

No other config changes. This works because `PeripheralInterface` converts
cycles→ns via its own `pru_clock_mhz` (`advance_cycles`), so PRU1's ns-timeline
diverges physically from PRU0's when both cores step the same cycle count.
The `Loopback` maps between the two absolute ns-timelines already; its
`drift_ppm` parameter (±100 ppm clamp) remains available *on top* and is not
needed for this experiment.

## 3. Core & Wiring Changes (`simulator.py`)

### 3.1 New core

- `cores` gains `"pru1": PRUCore("PRU1", memory, xfr, io_pru1, constant_table)`.
- `pru1_clock_mhz = [device].get("pru1_clock_mhz", pru_clock_mhz)`.
- PRU1's `SigmaDeltaFilter` and `PeripheralInterface` are constructed with
  `pru1_clock_mhz`; PRU0/RTU0 keep `pru_clock_mhz`.
- PRU1's SD filter registers stay `None` (SD register sharing remains a
  PRU0/RTU0 slice-0 concern, unchanged).

### 3.2 Perif moves from RTU0 to PRU1 (hardware-accurate)

Per TRM, GPCFG1_REG (0x2600C) and the second perif block (0x26100) belong to
PRU1:

- `core_order = ["pru0", "pru1"]`; perif bases `{"pru0": 0x260E0, "pru1": 0x26100}`.
- `rtu0.io_port.perif = None` (R31 routing already handles the None case).
- `Loopback(self._perif["pru0"], self._perif["pru1"])` — PRU0 TX ch-N → PRU1 RX ch-N.
- RTU0's SD filter + shared-register wiring is untouched.

### 3.3 GPCFG core mapping

`gpcfg_write` / `gpcfg_state` map `{"pru0": 0, "pru1": 1}`. For `rtu0` (which
has no GPCFG mux on real hardware): `gpcfg_state` returns `{"mux_sel": 0}` and
`gpcfg_write` is a no-op — keeps the WebSocket server robust if the UI sends a
stale core name. The UI hides the GP-Mux row when RTU0 is selected.

## 4. Firmware Examples (`source/`)

Host/UI prerequisites for both (same pattern as the existing `perif_tx_demo`):
GPCFG mux=1 on pru0 and pru1; ch0 `tx_frame_size`, `rx_frame_size`,
`rx_sample_size` and clock-select registers configured; loopback ch0 enabled.
Exact register values are fixed in the implementation plan from
`perif_registers.py`.

### 4.1 `perif_tx_pattern.asm` (PRU0)

- Selects ch0, sets **continuous clock mode** so frames stream back-to-back
  (this is what makes the single-start-bit free-running RX experiment valid).
- Loop: read R31 ch0 TX status byte (FIFO count in bits [4:2]); while the
  4-deep FIFO has room, push the next byte of an **incrementing counter
  pattern** (0x00, 0x01, … wrap at 0xFF). Counter pattern → the first
  corrupted byte index is directly readable from the received data.
- Issues TX go once after pre-filling the FIFO.

### 4.2 `perif_rx_capture.asm` (PRU1)

- Arms RX ch0 (R30 byte3, bit 24).
- Loop: poll R31 bit 24 (ch0 rx_valid) → read R31 byte0 (RX FIFO head) →
  store to a DRAM1 buffer (base 0x2000, incrementing pointer) → write R31
  bit 24 (clr_val, pops FIFO) → increment a received-byte counter at a fixed
  DRAM1 location (0x3FF8) so host/tests can watch progress.
- DRAM1 is used to avoid clashing with PRU0 data in DRAM0.

## 5. Drift Experiment

### 5.1 Pytest (`tests/test_perif_drift_experiment.py`)

- Builds a temp config file per case with `pru1_clock_mhz` offset
  (0, +200, +500, +1000 ppm; +100 ppm optionally marked slow), constructs
  `Simulator(config_path)`.
- Loads TX on pru0, RX on pru1, applies register prerequisites, enables
  loopback ch0, steps both cores in lockstep chunks until the RX byte counter
  reaches the target length or a cycle budget expires.
- Finds the **first mismatch index** against the counter pattern.
- Assertions:
  - 0 ppm: a long pattern (≥ 2× the 1000 ppm failure length) arrives intact.
  - For each offset: failure length within a sanity band around
    `0.5 / ppm` bits (band generous — ±50% — the point is the scaling law,
    not an exact constant).
  - Failure length decreases monotonically with increasing offset.

### 5.2 Report script (`tools/perif_drift_report.py`)

Standalone script printing a `ppm → first-failing byte index` table over a
finer offset sweep, for interactive experimentation beyond the test's fixed
points.

## 6. UI Changes

- **Core select** (`index.html`): add `PRU1` option.
- **Multi-core view** stays dual: PRU0 + a selectable partner core (RTU0 or
  PRU1; default RTU0 for compatibility). A small partner dropdown appears
  next to the Multi-core button when MC mode is active. Run/Step/Reset in MC
  mode drive PRU0 + the selected partner. The `mc*` state dicts in `app.js`
  gain `pru1` keys.
- Perif panel loopback section label → "Loopback → PRU1 RX".
- GP-Mux row hidden when RTU0 is selected (see 3.3).

## 7. Testing

- Existing perif tests referencing `rtu0` (5 files) update to `pru1`.
- New: drift experiment test (5.1); a server test that `pru1` state includes
  perif data and `rtu0` no longer does; a config test that `pru1_clock_mhz`
  defaults to `pru_clock_mhz` when absent.
- Full suite must stay green; SD tests must be unaffected by the rtu0 perif
  removal.

## 8. Risks / Notes

- **Sim speed:** the +100 ppm case needs ~5000 bits ≈ 625 bytes; at the perif
  bit-clock divider this can be over a million core steps per core. Keep it
  out of the default sweep (mark slow) if it pushes suite runtime.
- **Lockstep semantics:** stepping both cores the same cycle count with
  different clocks means slightly different elapsed ns — that *is* the
  physics, and the loopback maps absolute ns correctly.
- **Snapshot/step-back:** cores are iterated generically, but plan must verify
  step-back, hard reset, and MCP server core-name lists cover `pru1`.
