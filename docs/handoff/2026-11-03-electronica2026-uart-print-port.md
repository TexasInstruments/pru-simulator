# Handoff: electronica 2026 uart_print port (2026-11-03)

This note follows on from
[2026-11-03-uart-rx-hw-run-and-mcp-venv.md](2026-11-03-uart-rx-hw-run-and-mcp-venv.md).
Agent memory is stored per PC, so this note in the repo is how the state
reaches other machines.

The session ran from this repo. Its commits are in **open-pru**. No
pru_simulator source changed. The only pru_simulator commit is this note.

## Status

**Done and pushed:**
- `source/uart_print.asm` is ported to open-pru as
  `examples/electronica2026/uart_print/uart_print` (AM243x LP, ICSSG0 PRU0,
  r5fss0-0 FreeRTOS host).
- **It passes on hardware:** LP-AM243 **Rev A**, loaded with CCS, decoded
  with a PicoScope UART decoder (user, 2026-11-03).
- open-pru branch `dev_tley_electronica2026_uart_print` at **`fdf7b1a3`**
  (one commit), in
  [open-pru PR #190](https://github.com/TexasInstruments/open-pru/pull/190)
  into `main`.
- The project has a `readme.md` and a `docs/porting_report.md` in the same
  layout as uart_rx: prompt, decisions, model, effort, tokens and time,
  changes, verification, open items.

## What the port does

- **TX pin:** `PRG0_PRU0_GPO0` (R30 bit 0), LaunchPad pin **BP.33** (J4),
  3.3 V push-pull.
- **Line format:** 115200 baud 8N1 at a **200 MHz** PRU clock. `BAUD_COUNT` is
  868, as in the source.
- **Output:** writes `Hello 2026\0` to DRAM0 offset 0 and sends it. It then
  holds the line high for 1 ms and sends it again, forever. The repeat
  period is 1.871 ms.
- **Changing the string:** DRAM0 is read on every pass, so a string written
  from the CCS memory browser (null-terminated, at most 255 bytes) is sent
  from the next pass on.
- **Host:** the R5F clears PRU0 DRAM, sets GP_MUX_SEL to GP mode, and loads
  and starts PRU0 only.
- **Project files:** copied from the uart_rx project tree (on its branch),
  with names swapped. The SysConfig pin lines match i2c_io's GPO0.

## How the session went

1. **Prompt:** "convert the uart_print to open-pru like the uart_rx_11frame
   project".
2. **logic_scope check.** The earlier notes said to test with "logicscope",
   so open-pru `examples/logic_scope` was read first. That example would
   conflict with the port in three ways:
   - it occupies ICSSG0 PRU0 + RTU0;
   - its PRU0 `main.c` turns the PRG0_PRU0_GPO0-7 pads into inputs;
   - it sets the ICSSG0 core clock to 333 MHz (PLL0 HSDIV9 = 2), which would
     change the baud rate of any other core on ICSSG0.
3. **Four questions, one dialog.** The user chose:
   - **PRU0**, because "logic_scope is not used but real picoscope". So
     "logicscope" meant the PicoScope;
   - **200 MHz, 115200 baud**;
   - **repeat with an idle gap**;
   - folder **`uart_print/uart_print`**.
4. **Port, lint, build, simulator runs, docs** (see the porting report,
   sections 5-7).
5. **Hardware test by the user** with CCS, the board and the PicoScope:
   pass, Rev A.
6. **Commit, push, PR #190.**

## Results worth knowing

- **Timing** (simulator, built `.out`): start bit 1739 cycles, data bits
  1742 (bit = 1) / 1743 (bit = 0), stop bit 1745. The ideal is 1736.1 cycles
  per bit, so the port runs **0.37 % slow** (114,780 baud). The source never
  subtracted the per-bit overhead. `BAUD_COUNT = 865` would bring the error
  to about 0.03 %. Offered, not done.
- **Same timing as the source:** the edge timing of the built `.out` is
  identical to the source for the first string (57 intervals).
- **Terminator fix.** The source loads one byte (`lbco &r0, ..., 1`) and
  then tests all of `r0` (`qbeq done, r0, 0`). The simulator starts with
  zeroed registers, but silicon does not promise that. The port clears
  r0-r29 at start and compares `r0.b0`.
  - **Correction made in-session:** the agent first said this could stop the
    string from ending on silicon. That was overstated. Each byte shifts
    `r0` right by 8, so leftover bits are gone after three characters. Only
    strings shorter than 3 characters on the first pass are affected. The
    report has the corrected text.
- **Simulator API for ad-hoc checks:** `Simulator('memory.cfg').load_elf('pru0', bytes)`,
  then `step()` and `memory_read(addr, n)`. The headless runner's JSON has no
  memory dump. The skill's `simulate.py edges <file.out> --pin 0` gives the
  edge cycles. Run it with `env -u PYTHONPATH` on this PC (ROS).

## Not bugs, so don't chase them

- `gh pr create` warns "4 uncommitted changes" in open-pru. They are
  untracked build outputs:
  - `pru0_load_bin.h` and `*.lnkxml` of uart_print and uart_rx;
  - the leftover `examples/electronica2026/i2c_io_expander/` folder.

  None of them belong in a commit.
- The new branch was created with `git switch -c ... origin/main`, so it
  first tracked `origin/main`. The push used `-u`, so it now tracks its own
  remote branch.

## Branches (end of session)

- **pru_simulator:**
  - `main` (`46efc4a`);
  - `docs/handoff-2026-11-02` (PR #102) with the earlier notes and this one;
  - `docs/electronica2026-howto` (`1133380`, booth how-to draft, no PR).
    That branch was checked out at session start. The checkout is now on
    the handoff branch.
- **open-pru:**
  - `dev_tley_electronica2026_i2c_io` (PR #188, `b38990d9`);
  - `dev_tley_electronica2026_uart_rx` (PR #189, `d9250549`);
  - `dev_tley_electronica2026_uart_print` (PR #190, `fdf7b1a3`);
  - `main`.

  The checkout is on the **uart_print** branch.

## Open items

New, for uart_print:

1. **PicoScope settings.** The readme's settings are suggestions:
   - UART decoder 115200 8N1 on channel A;
   - pulse-width trigger on a high pulse longer than 500 us, which only the
     1 ms gap produces;
   - about 100 us/div.

   The settings used in the hardware run were not recorded. Save them as a
   `.psdata` and put the real values in the readme. Where the `.psdata`
   files go is still undecided for all demos.
2. **Portability row** in `examples/readme.md`: `Yport` for the five other
   devices, set like uart_rx, awaiting user confirmation.
3. **SysConfig GUI re-save** of `example.syscfg` (hand-edited, as in #188
   and #189).
4. **Merge conflicts:** #188, #189 and #190 each add an `electronica2026/...`
   entry to `examples/makefile` and `examples/readme.md`. Every PR that
   merges after the first needs a small resolution.
5. **Optional:** `BAUD_COUNT = 865` for 0.03 % baud error.

Carried over (see the previous notes for detail):

6. **Review and merge** open-pru PRs #188, #189, #190 and pru-simulator
   PR #102.
7. **Booth how-to** (`docs/electronica2026-howto`): fold in the PicoScope
   findings from the earlier 2026-11-03 session:
   - the Demo B trigger is a pulse width on A longer than 20 us;
   - AWG amplitude is a peak value;
   - `I2C_IO_Expander.psdata` turns the signal generator on.

   Also add uart_print as a third demo, and open a PR when ready.
8. uart_rx: fast senders fail (4.05 Mbaud), no frame sync, SysConfig
   re-save.
9. i2c_io: the two unticked FIXMEs in its porting report, SysConfig re-save,
   visualizer minors.
10. **Test gap:** `test_valid_ssi_frames_over_sdk_stdio` depends on the
    local `memory.cfg`.
