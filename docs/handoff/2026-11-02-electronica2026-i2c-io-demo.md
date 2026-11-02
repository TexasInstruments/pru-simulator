# Handoff: electronica 2026 I2C IO expander demo (2026-11-02)

This note follows on from
[2026-10-09-tc7-silicon-debug.md](2026-10-09-tc7-silicon-debug.md). Agent
memory is stored per PC, so this note in the repo is how the state reaches
other machines.

The session ran from this repo, but all of its commits are in **open-pru**,
branch `dev_tley_electronica2026_i2c_io`. Nothing in pru_simulator changed
apart from this note.

## Status

**Done and pushed:**
- Branch pushed to `origin` at `f4be861b`, 11 commits on top of `main`.
- No Pull Request yet. The user chose "push the branch" and stopped there.

**Built in this session:** the simulator example
`source/i2c_tca9538_running_led.asm` became a booth demo with four stages:

| Stage | Where (open-pru `examples/electronica2026/i2c_io_expander/`) | State |
|---|---|---|
| Simulator | pru_simulator `source/i2c_tca9538_running_led.asm` (unchanged) | Reference for the port |
| LP-AM243 target | `i2c_io/`: PRU firmware plus MCU+ SDK R5F FreeRTOS project | Built and run from CCS 21 by the user |
| PicoScope trace | PicoScope 3416E MSO plus PicoScope 7.2.30 I2C decoder | Decodes `23 W 01 xx`, one frame per capture |
| Rust LED window | `visualizer/` | Runs; user confirmed "LEDs keep up with the scope" |

Entry points:
- `readme.md` in that folder runs the demo in order, with the flow image
  `docs/development_flow.png`.
- `i2c_io/docs/porting_report.md` records the port: the prompt, model, tokens,
  time and decisions.
- `visualizer/docs/design.md` and `plan.md` hold the visualizer's design and
  implementation plan.

## Commits (open-pru, oldest first)

| SHA | What |
|---|---|
| `248ce997` | Port: i2c_io project, ICSSG0 PRU0 only, SysConfig 200 MHz, GPO0 = SCL, GPO1 = SDA |
| `e98966a5` | 100 ms idle pause after each LED write (`PAUSE_COUNT`, r7) |
| `f517d536` | Visualizer design and plan |
| `c09be788` … `a84bfaa5` | Visualizer, 6 tasks, built test-first |
| `cdb430a7` | Fixes from the final review |
| `f4be861b` | Demo readme and flow image (SVG/PNG plus generator `.py`) |

## Key facts

### Firmware port
- **SDA is push-pull on AM243x.** A pad is muxed either as GPO or as GPI,
  never open-drain. The firmware therefore drives SDA low during the ACK
  clock and does not sample ACK (`ldi r20, 0`). NACK detection is inactive
  on silicon.
- **1 Mbit/s is kept on purpose.** The TCA9538 is rated for 400 kHz. The
  demo only shows the bus on a scope.
- **Simulator checks:** the SCL edges of the port match the simulator source
  (671 of 671). One LED step is 100.03 ms with the pause.

### PicoScope 7 MCP server
- The MCP server is hidden behind `PicoMCPEnabled` in the root-owned
  `/opt/picoscope/lib/PicoScope.GTK.dll.config`.
- On the Linux desktop it was set to `true`, and the original is kept as
  `.orig`. A PicoScope package update resets it.
- Then enable it in Preferences → MCP server (beta).
- The tools return no raw decode rows. `get_serial_decoder_summary` with
  `groupBy:"Data"` returns the data bytes, e.g. `"01 08 "`.

### Visualizer
- Rust crate `i2c_led_visualizer`, built on eframe/egui 0.37 and ureq 3.
- It polls PicoScope 7 every 50 ms over MCP.
- 46 tests; clippy is clean.
- Measured on the hardware: about 10 changes per second, a few skipped steps
  per 10 s, all during PicoScope stalls.

## Not bugs, so don't chase them

- **CCS did not show the pause in `main.asm`.** open-pru projectspecs import
  sources with `action="copy"`, so the CCS workspace
  (`~/workspace_ccstheia/<project>/`) holds a snapshot. Copy changed files
  over, or import the project again.
- **No "MCP server" entry in the PicoScope Preferences.** This is the hidden
  feature flag above, not a broken install.
- **The first flag edit did nothing.** The `cp` ran but the `sed` did not.
  `sudo sed -i '/PicoMCPEnabled/s/false/true/' …` worked.
- **The LED window sometimes skips a step or pauses.** PicoScope 7 itself
  stalls and misses captures, and its own screen does the same; the user saw
  this. The window mirrors it on purpose. Polling faster than 50 ms made
  PicoScope skip more.
- **The repo-root `make` dirties `examples/pru_i2s`.** It regenerates tracked
  `*_array.h` files. Revert them with `git restore`.

## Environment on the Linux desktop

- **open-pru build:** `imports.mak` defaults don't match this PC. Run
  `make DEVICE=am243x MCU_PLUS_SDK_PATH=$HOME/ti/mcu_plus_sdk_am243x_12_01_00_33 SYSCFG_NODE=$HOME/ti/ccs2101/ccs/tools/node/node`.
- **Git identity:** the open-pru checkout has no git identity configured.
  Commit with `git -c user.name="Thomas Leyrer" -c user.email="t-leyrer@ti.com" commit …`.
- **Rust:** installed in `~/.cargo` but not on `PATH` by default. Run
  `source ~/.cargo/env`.
- **PicoScope MCP port:** auto-assigned 18123. The visualizer defaults to
  18123, so set the port to manual 18123 in Preferences to make it stable.
- **Excluded workspace:** `.superpowers/` is excluded through open-pru's
  `.git/info/exclude`, which is local only.

## Open items

1. **Pull Request:** open one for `dev_tley_electronica2026_i2c_io`, when wanted.
2. **Board revision:** record the LP-AM243 revision in `i2c_io/readme.md`
   (currently FIXME).
3. **J-header positions:** check J4.8 / J4.9 against the board silkscreen.
4. **Portability:** the `examples/readme.md` FIXMEs for am261x, am263px,
   am263x, am62x and am64x.
5. **SysConfig:** open `example.syscfg` in the SysConfig GUI and save, to
   clear the part-number migration warning.
6. **Untracked build outputs:** `i2c_io/firmware/am243x-lp/pru0_load_bin.h`
   and `i2c_io/.../i2c_io.release.lnkxml` are not in any `.gitignore`.
7. **Visualizer minors deferred from the final review:**
   - `--interval-ms 0` is accepted;
   - a `+` sign is accepted in hex keys;
   - add `rust-version = "1.88"`;
   - setup is re-run every 50 ms after a non-Success reply;
   - `poller::run` has no loop test;
   - `--no-gui` prints the same line again when only the address changes;
   - `design.md` status line is stale.
8. **Not tested on hardware:** closing and reopening PicoScope 7 while the
   visualizer runs. It is covered by fake-server tests only.
