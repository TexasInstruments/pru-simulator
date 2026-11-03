# Handoff: uart_rx hardware run and MCP server venv (2026-11-03)

This note follows on from
[2026-11-02-electronica2026-uart-rx-port.md](2026-11-02-electronica2026-uart-rx-port.md).
Agent memory is stored per PC, so this note in the repo is how the state
reaches other machines.

The session ran from this repo. Its commits are in **open-pru**. No
pru_simulator source changed. The MCP fix was a package upgrade in the local
venv, which is outside the repository (see "MCP server").

## Status

**Done and pushed:**
- **uart_rx passes on hardware.** LP-AM243 **Rev A**, sender PicoScope 3416E
  AWG. The CCS memory browser (Packed Char) shows `HelloWorld!` back to back
  from 0x30000000, frame-aligned (the first slot starts with `H`). The count
  at 0x30000FFC climbs, and the error flag at 0x30000FFE is 0. The 0 V / 2 V
  AWG levels were enough; no level shifter was needed.
- open-pru branch `dev_tley_electronica2026_uart_rx` at **`d9250549`**, two
  new commits, pushed to
  [open-pru PR #189](https://github.com/TexasInstruments/open-pru/pull/189)
  (now 4 commits):
  - `e6b8982b` hardware run passes: uart_rx `readme.md` (Rev A, run recorded
    in the Validated HW & SW table, three AWG pitfalls) and
    `docs/porting_report.md` (hardware row in the verification table, two
    open items closed).
  - `d9250549` portability: the uart_rx row in `examples/readme.md` is
    `Yport` for am261x, am263px, am263x, am62x and am64x (user decision).
- **pru-simulator MCP server works again** on this PC (venv upgraded to
  mcp 2.3.0). This closes open item 1 of the previous note.
- This note is on the pru-simulator branch `docs/handoff-2026-11-02`
  (PR #102), next to the two notes from 2026-11-02.

No FIXME is left in the uart_rx readme, its porting report or
`examples/readme.md`.

## How the session went

1. **Handoff read-back.** The newest note was on the PR #102 branch, not on
   `main`.
2. **AWG CSV overwritten.** `test/helloworld_4mbaud_awg.csv` held 32768 lines
   of `0.00000000`, written at 09:13. The cause was not verified; a PicoScope
   save onto the same path is the likely one. It was regenerated with
   `make_awg_csv.py` and is byte-identical to the committed `b06cf37a`, so
   nothing needed committing.
3. **AWG sent a sine.** Read over the PicoScope MCP
   (`get_signal_generator_state`): waveform Arbitrary, enabled, but
   **frequency 20 MHz**, amplitude 2 V, offset 0 V. In Arbitrary mode the
   frequency is the repeat rate of the whole 1600-sample buffer, which the
   3416E cannot play at 20 MHz, so the filtered output looks like a sine.
   The user set 20 kHz, amplitude 1 V, offset +1 V in the GUI.
4. **Hardware run passed** (see Status). The user read the count and the
   flag, then gave the board revision (Rev A) and the portability decision
   (Yport).
5. **Two commits pushed** to PR #189 (see Status).
6. **MCP tool failure** (open item 1 of the previous note) was traced to its
   root cause and fixed (see "MCP server").

## AWG pitfalls (now in the uart_rx readme)

- **Frequency is 20 kHz, not 20 MHz.** At 20 MHz the output looks like a
  sine.
- **Set the amplitude before the offset.** The allowed offset range shrinks
  as the amplitude grows. At 2 V amplitude it is only +/-500 uV, so +1 V
  cannot be entered until the amplitude is 1 V.
- **Import the CSV from a copy outside the repository**, so that a PicoScope
  save cannot overwrite the committed file. To restore it, run
  `git checkout -- test/helloworld_4mbaud_awg.csv` or
  `python3 make_awg_csv.py`.
- **Danger with the wrong settings:** offset 0 V with amplitude 2 V swings
  below 0 V, which is outside the AM243 input rating. Check the settings
  before you connect BP.31.
- The PicoScope MCP can read the generator state but cannot set it while the
  waveform is Arbitrary: `configure_signal_generator` handles only the preset
  waveforms. Its server was on port 18123 again
  (`ss -ltnp | grep PicoScope`).

## MCP server: root cause and fix

Claude Code (user config `~/.claude.json`) starts the server as
`cd ~/GitHub/pru_simulator && exec ~/AI_code/pru-simulator-venv/bin/python mcp_server/server.py`.
It is a stdio server, spawned **once at session start**, from whatever
branch is checked out at that moment.

| Day | Code the server ran | venv `mcp` | Symptom |
|---|---|---|---|
| 11-02 | `feat/pif-eth-100` @ `43ca5f5` (the session started before the 09:12 switch to `main`) | 1.30.0 | Started. That `server.py` built schemas by hand and mapped every type except int/float/bool to `"string"`, so `payload: list[int]` was advertised as a string and failed with `>>` on str. |
| 11-03 | `main` (mcp 2.x `MCPServer` API since `fd85c00` / `8493abe`) | 1.30.0 | `ImportError: cannot import name 'MCPServer'` at start, so the process exits and Claude Code shows CONNECTION_CLOSED. |

The string-schema bug was already fixed on `main`, where the SDK derives the
schema from the signature. The venv was the only thing out of date: it was
installed on 2026-10-14 with mcp 1.30.0, but `requirements.txt` asks for
`mcp>=2.0`.

**Fix:** `~/AI_code/pru-simulator-venv/bin/python -m pip install -r requirements.txt`
installed mcp 2.3.0, plus a few packages from `requirements.txt` that were
missing (fastapi, websockets, pytest and others).

**Verified:**
- The exact configured command answers `initialize`.
- In a stdio session from the repo, `pru_uart_inject` advertises `payload` as
  `{"type": "array", "items": {"type": "integer"}}`.
- With `source/uart_rx_11frame.asm` and `HelloWorld!` it returns status
  success, 1 frame, `HelloWorld!`, error flag 0. A string payload is now
  rejected with a validation error.
- On a clean `git archive HEAD` copy: 137 passed, 2 xfailed
  (`tests/test_mcp_*.py`, `test_uart_rx_mcp.py`, `test_uart_rx.py`).

**On other PCs:** the venv is per PC. If the pru-simulator MCP server does
not connect there, run the configured command by hand and check
`pip show mcp`. It must be 2.0 or newer.

mcp 2.x client types are snake_case (`input_schema`, `is_error`), which
matters only for ad-hoc client scripts.

## Not bugs, so don't chase them

- **`pip check` lists dozens of ROS packages missing setuptools.** They come
  from `/opt/ros/lyrical` on `PYTHONPATH`, set by the shell profile, not
  from the venv. They were there before the upgrade.
- **pytest crashes during collection with
  `No module named 'osrf_pycommon'`.** A ROS pytest plugin is loaded through
  the same `PYTHONPATH`. Run tests with
  `env -u PYTHONPATH .../pru-simulator-venv/bin/python -m pytest ...`.
- **`test_mcp_stdio_device_sdk.py::test_valid_ssi_frames_over_sdk_stdio`
  fails in the working copy** (`raw_frame_lo` 0, expected 0xABC). Its server
  subprocess reads `memory.cfg` from the repo folder, and the local file is
  at PRU0 200 / PRU1 300 MHz (UI speed selector). `tests/conftest.py` cannot
  pin a subprocess. With the committed `memory.cfg` (250 / 250 MHz) it
  passes. This is a test gap, not a regression (open item 6).
- **open-pru commits fail with "Author identity unknown".** open-pru has no
  `user.name` / `user.email` in its repo config (pru_simulator has one), and
  there is no global identity. Commit with
  `git -c "user.name=Thomas Leyrer" -c user.email=t-leyrer@ti.com commit ...`,
  or set it once in open-pru with `git config`.

## Branches (end of session)

- **pru_simulator:** `main` (`46efc4a`), plus `docs/handoff-2026-11-02`
  (PR #102) with the two notes from 2026-11-02 and this one.
- **open-pru:** `dev_tley_electronica2026_i2c_io` (PR #188, `4a8cad5c`),
  `dev_tley_electronica2026_uart_rx` (PR #189, `d9250549`) and `main`. The
  checkout is on the **uart_rx** branch. Untracked in that checkout:
  - uart_rx build outputs (`pru0_load_bin.h`, `uart_rx.release.lnkxml`);
  - an `examples/electronica2026/i2c_io_expander/` folder of leftover i2c_io
    build output from the branch switch.

## Open items

Carried over from the previous note, with today's status:

1. ~~**pru_uart_inject MCP tool fails.**~~ Fixed (see "MCP server"). Other
   PCs may need the same venv upgrade.
2. ~~**Hardware run of uart_rx.**~~ Passed on LP-AM243 Rev A, recorded in
   `e6b8982b`.
3. **Review and merge** open-pru PR #189 (and PR #188, and pru-simulator
   PR #102).
4. **Merge conflict:** `examples/makefile` and `examples/readme.md` get an
   `electronica2026/...` entry on both open-pru branches. Today's Yport row
   edits the same `examples/readme.md` table. Whichever PR merges second
   needs a small resolution.
5. **Fast senders fail** at 4.05 Mbaud (+1.25 %). The `qbeq store_bN` chain
   pushes the stop sample of byte 11 to cycle 43 of 50. A jump table would
   centre the samples.
6. **No frame sync.** Start the PRU before the sender: DC +2 V first, then
   the R5F, then Arbitrary.
7. **uart_rx housekeeping:** SysConfig GUI re-save, and the untracked build
   outputs (not in any `.gitignore`). The board revision and portability
   FIXMEs are done.
8. **Optional:** let the R5F print new frames on the UART console by polling
   the count at 0x0FFC.

New:

9. **Test gap:** make `test_valid_ssi_frames_over_sdk_stdio` (and any other
   stdio-subprocess test) independent of the local `memory.cfg`, for example
   by running the server against a pinned config. Offered as a small PR, not
   started.
10. **i2c_io open items** from
    [2026-11-02-electronica2026-i2c-io-demo.md](2026-11-02-electronica2026-i2c-io-demo.md)
    are unchanged: board revision, portability, SysConfig, untracked outputs
    and the visualizer minors. The uart_rx answers (Rev A, Yport) probably
    apply to i2c_io as well, but the user has not decided that.
