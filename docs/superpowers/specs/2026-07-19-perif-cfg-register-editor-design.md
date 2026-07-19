# Perif Panel: Editable RXCFG/TXCFG Registers — Design

**Date:** 2026-07-19
**Status:** Approved

## Problem

Running the perif clock-drift demo requires writing the shared clock
config registers (PRU0 `TXCFG @ 0x260E4`, PRU1 `RXCFG @ 0x26100`)
before starting the firmware. The UI has no way to do this directly:

- The memory window's block read (default length 1024) overruns the
  32-byte `ICSS_PERIF_CFG` region and errors "out of bounds", so users
  can't even navigate to the registers naively.
- The server exposes a `write_perif_register` websocket action
  (`ui/server.py`), but nothing in `app.js` uses it.
- The perif panel shows the decoded shared config read-only.

## Goal

Add editable raw-hex RXCFG/TXCFG fields to the perif panel so the
drift-demo register setup is done in-panel, per core, without the
memory window.

## Design

### Backend

`PerifRegisters.get_shared_config()` (`perif/perif_registers.py`)
gains three entries:

| Key         | Value                                        |
|-------------|----------------------------------------------|
| `rxcfg`     | raw u32 of RXCFG (`block+0x00`)              |
| `txcfg`     | raw u32 of TXCFG (`block+0x04`)              |
| `base_addr` | absolute block base (0x260E0 PRU0, 0x26100 PRU1) |

No other backend change: the dict already flows through the io-state
payload to the client, and the `write_perif_register` action already
handles masked 32-bit writes and re-sends state.

### Frontend (`ui/static/app.js`, perif panel)

New "CFG registers" row rendered above the existing R30 decode bar:

```
RXCFG @0x260E0: [0x00000000] [Apply]
TXCFG @0x260E4: [0x00000000] [Apply]
```

- Addresses computed from `base_addr` (`+0x00` / `+0x04`), so labels
  are correct for whichever core is selected (PRU0 or PRU1).
- Apply parses the input (hex `0x...` or decimal), and sends
  `{action: "write_perif_register", core: currentCore, addr, value}`.
  The server's state re-send updates the decoded fields immediately.
- Inputs are prefilled from state on each update **only when not
  focused**, so live state pushes don't clobber typing.
- Invalid input: small inline error next to the field; nothing sent.

### Out of scope (YAGNI)

- Per-channel CHnCFG0/1 editing (drift demo uses their defaults; the
  channel cards already show their decode).
- Per-field editors (dropdowns/spinners); raw hex matches the firmware
  headers, which document values as hex.

## Testing

- Unit test: `get_shared_config()` returns `rxcfg`, `txcfg`,
  `base_addr`, and the raw values round-trip after `write()`.
- Extend the existing UI-server test coverage so the state payload
  carries the raw values after a `write_perif_register` action.
- JS layer has no test harness; UI verified by driving the running app
  (enter `0x00070010` into TXCFG on PRU0, confirm decode bar updates
  and the register reads back via `sim.memory_read`).
