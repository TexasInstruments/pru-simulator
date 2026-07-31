# Handoff — perif reset, test hermeticity, Signal Graph clock lanes (2026-07-27)

Cross-machine snapshot (agent memory is per-PC; this file travels with the
repo so any PC sees it after `git pull`). Continues from
[`2026-07-22-pif-eth-rx-o1.md`](2026-07-22-pif-eth-rx-o1.md).

## Status

**Complete, committed and pushed** — three commits on `main`, released as
**v0.2.3** (UI logo tooltip + readme changelog both bumped):

| Commit | What |
|---|---|
| `307fbec` | `fix(perif): clear Peripheral Interface state on reset` |
| `c4bef7b` | `test: stop inheriting the UI's core-speed selection` |
| `e093dce` | `feat(ui): add perif clock lanes to the Signal Graph` |

Suite green: 1198 passed, 2 xfailed.

## 1. Reset left peripheral status bits latched

`PeripheralInterface` had **no reset path at all**, and `PRUCore.reset()`
only touched registers/counters/PC/accelerators. So the dashboard's *HW
Reset* (and the per-core *Reset*) left a TX overrun/underrun, `rx_valid` /
`rx_ovf`, `busy`, or a half-full FIFO from the previous run standing — still
visible in the Peripheral panel and still readable in R31.

- New `PerifChannel.reset()` — the wider sibling of `tx_reinit()` (which is
  the TX-only R31 bit19 soft reset). Also clears the RX side, the recorded
  `tx_transitions` line history, and the ns timeline. Leaves
  `rx_line_source` connected, or the loopback would silently unwire itself.
- New `PeripheralInterface.reset()` — all 3 channels + `ch_sel` + timeline,
  and drives the TXCFG busy bits low.
- New `IOPort.reset()`, called from `PRUCore.reset()`, so both reset buttons
  get the behavior. Zeroes GPO (R30 is already zeroed by the register-file
  reset); **leaves GPI alone** — that models external stimulus (UI input
  pins, injected UART frames), not core state.
- **Config deliberately survives a reset**: perif config registers (clock
  dividers, frame sizes, wire/Tst delays), the GP Mux selection, and the
  loopback parameters. Those are the setup the user entered in the UI, and
  wiping them on every reset would be hostile. Documented in
  `Simulator.hard_reset()`'s docstring, the HW Reset tooltip, the in-app
  help table, and `getting_started.md`.
- New tests: `tests/test_perif.py::TestHardwareReset` (4),
  `tests/test_perif_integration.py` (2, incl. `get_r31_status() == 0` after
  reset and config survival).

## 2. Six "pre-existing failures" were the UI's speed selector

Worth knowing before anyone re-debugs this: at the start of the session the
suite showed **6 failures on a clean `main`**, and none of them were code
bugs. The v0.2.1 core-speed selector **rewrites `memory.cfg` in place**, and
both `Simulator()` and `PRUSimulatorMCP()` default to that file — so any
test constructing one with no arguments silently ran at whatever clock was
last picked in the dashboard. The local checkout was at 250 MHz.

Casualties: the bit-bang UART receiver tests (×4 — its sampling loop is
tuned to ~51 cycles/bit at 200 MHz / 4 Mbaud, so at 250 MHz it receives
nothing), `test_pru1_clock_from_config` (overrode only `pru1_clock_mhz`,
then asserted PRU0 was at 200 MHz), and `test_channel_switching` (hardcoded
a 200 MHz modulator clock for what was meant to be a 1:1 SD:core ratio).

- New `tests/conftest.py`: `sim_config(**device_overrides)` writes a
  throw-away `memory.cfg` into `tmp_path`; `nominal_config` is the 200 MHz
  convenience wrapper. **It copies `config/` alongside** — `_load_constants()`
  resolves `constants_am243x.cfg` relative to the config file, and without
  it the constant table is empty and the UART firmware's
  `sbco &r2, c24, r1, 11` stores nowhere. That one cost a debugging cycle.
- The SD test now matches its modulator clock to `sim._pru_clock_mhz`.
- Verified green at **200, 250 and 333 MHz** — the check to repeat if this
  class of failure reappears.

## 3. Signal Graph: perif clock lanes

Triggered by two "this looks wrong" reports that both turned out correct —
see §4. The graph recorded `perifN_out` (data line) but **not its bit
clock**, so a correct capture was genuinely unreadable: the perif serializer
sends raw MSB-first bits with no framing, leaving nothing on screen to mark
bit boundaries.

- `graphSample` samples `tx_clk_pin` into a new `perifClk[3]` field;
  `drawDigitalGraph` draws `perifN_clk` immediately after its `perifN_out`
  (muted same-hue color, `GRAPH_PERIF_CLK_COLORS`); CSV export gains three
  matching columns. Samples predating the change lack `perifClk` — both
  paths fall back to 0.
- One clock edge per bit; at the usual `TXCFG` `div=7` (core/8) a bit is
  8 samples wide, since the graph takes one sample per instruction (Run
  sends `count: 1`).
- Guarded by `tests/test_perif_server.py::test_state_carries_tx_clk_pin_for_graph_lanes`
  — asserts the key exists in the websocket state payload **and** that it
  actually toggles while transmitting, since a lane that never transitions
  is filtered out as inactive and the feature would silently do nothing.
  There is no JS test infra, so this payload-contract test is the only
  automated guard on the feature.

## 4. Two reported "bugs" that were correct behavior

Both are counter-intuitive enough that they will very likely be re-reported
from another PC. Recording the analysis so it isn't re-derived.

**(a) `perif_tx_pattern.asm` pushes `0x80 0x00 0x81 0x01`, not the counter.**
Correct. The RX consumes the first `1` as its start bit, so the firmware
pre-shifts the whole payload right by one bit
(`push_k = carry | (p_k >> 1)`, `carry_0 = 0x80`). Concatenating the pushed
bytes MSB-first gives `1 | 00000000 | 00000001 | 00000010 | …`. The
`0x8x`/`0x0x` alternation is just each odd counter value's LSB landing in
the next byte's MSB. `tests/test_perif_drift_experiment.py:48` already
asserts the RX recovers `range(32)`.

**(b) The captured `perif0_out` waveform looks like noise.** Also correct —
collapsing the samples back to bits yields exactly `1` + `0x00 0x01 0x02 …`.
It reads as strange because an MSB-first small-counter pattern is ~90% zeros
(narrow pulses, long flat stretches), the start bit offsets every byte
boundary by one bit, each bit is 8 samples wide, and — before §3 — there was
no clock lane to count against.

**Known cosmetic artifact:** the *first* data pulse renders 7 samples
instead of 8; every later high run is an exact multiple of 8. The R31 go
strobe is processed against the perif timeline as it stood before that
instruction's own cycle was added (`PRUCore.step()` calls
`perif.advance_cycles(counters.cycles)` *before* `counters.tick()`), so the
recorded go timestamp sits one core cycle off the capture grid. Harmless —
the RX samples at bit centres and decodes cleanly.

## How to reproduce

```bash
python3 -m pytest tests/ -q                    # 1198 passed, 2 xfailed
python3 -m pytest tests/test_perif.py::TestHardwareReset -v
python3 ui/server.py                           # dashboard on :8080
```

To see the clock lanes: load `source/perif_tx_pattern.asm` (self-configuring
— no register setup needed), open **Signal Graph**, click **● REC**, then
**Run**. `perifN_clk` stays hidden until the first `tx_go`, since a lane is
only drawn once it has transitioned.

## Open items

- **The speed-selector endpoint does not preserve line endings.** The
  committed `memory.cfg` is CRLF; the endpoint rewrites it as LF, so a
  2-line change shows as a 79-line diff. Cosmetic but noisy in `git diff`,
  and it means the file is *always* dirty on a machine where the selector
  has been used. Not fixed — the endpoint's stated contract is that it
  patches in place preserving formatting, so the fix belongs there.
- **The clock lanes were never viewed in a browser.** Verified via
  `node --check`, the websocket payload test, and by replaying the exact
  sampling the JS does — but the on-screen rendering and the chosen colors
  are unconfirmed by eye.
- `PerifRegisters` has `set_busy()` but no getter, so the reset test reads
  the TXCFG busy bits by unpacking the raw register. A `get_busy(ch)` would
  be tidier if that pattern recurs.
- Unchanged future work from earlier handoffs: pif_eth Options 2/3, the
  `PROJECT_REPORT.md` RX section, channel-1/2 striping, broadside CRC.
