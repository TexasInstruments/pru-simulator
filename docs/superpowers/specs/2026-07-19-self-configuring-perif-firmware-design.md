# Self-Configuring Perif Demo Firmware — Design

**Date:** 2026-07-19
**Status:** Approved

## Problem

The perif drift demo requires four manual host steps before Run: set
GP-Mux to 1 on both cores, write `TXCFG`/`RXCFG` (and rely on
`CH0CFG0 = 0`), and enable loopback. Missing any of them yields an
empty capture with no obvious error (observed twice on 2026-07-19:
unset registers, then wrong firmware per core). The user wants the
firmware itself to configure its peripheral registers, and the IO
window to follow the mode the firmware selects.

## Findings (verified before design)

- Firmware `sbbo` stores already reach the memory-mapped config
  registers: `PerifRegisterRegion`/`GpcfgRegion` sit on the memory
  bus, and writes fire the existing config/mux callbacks.
- The IO window already switches on `io.mode` in state pushes, and
  `mode` follows the GPCFG mux — a firmware GPCFG write flips the
  panel on the next state push. **No UI or server changes needed.**

## Design

### `source/perif_tx_pattern.asm` (PRU0)

Config prologue before the existing `start:` code (r0 = value,
r1 = address, both already free at entry):

```
        ldi  r0, 0x0000          ; GPCFG0: mux_sel=1 (bits 29:26)
        ldi  r0.w2, 0x0400
        ldi  r1, 0x6008
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4
        ldi  r0, 0x0010          ; TXCFG = 0x00070010 (core clk, div=7)
        ldi  r0.w2, 0x0007
        ldi  r1, 0x60E4
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4
        ldi  r0, 0                ; CH0CFG0 = 0 (continuous mode)
        sbbo r0, r1, 4, 4         ; full 32-bit clear (tx_frame_size is bits 15:11)
```

Header: "Host prerequisites" shrinks to "enable loopback ch0".

### `source/perif_rx_capture.asm` (PRU1)

Prologue (r0/r1 free at entry; r1 is re-initialized to the buffer
pointer right after):

```
        ldi  r0, 0x0000          ; GPCFG1: mux_sel=1 (bits 29:26)
        ldi  r0.w2, 0x0400
        ldi  r1, 0x600C
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4
        ldi  r0, 0x001F          ; RXCFG = 0x0007001F
        ldi  r0.w2, 0x0007
        ldi  r1, 0x6100
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4
```

### Unchanged

- Loopback enable stays a host/UI step (harness construct, not a
  memory-mapped register).
- Existing host register writes in tests/tools stay (redundant but
  harmless — same values).
- Perif panel CFG fields simply display the firmware-written values.

## Testing

- New test in `tests/test_perif_drift_experiment.py`: full roundtrip
  with **no host register setup** — only `perif_loopback(0, True)` —
  firmware self-configures, capture is the clean pattern.
- New test in `tests/test_perif_server.py`: load a GPCFG-writing
  program via ws, `run`, assert the state push reports
  `io.mode == "perif"` (locks in the window-switch behavior).
- All existing tests must keep passing unchanged.
