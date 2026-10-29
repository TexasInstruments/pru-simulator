---
name: pru-assembly
description: Write, review, debug, simulate and build assembly for TI PRU cores (PRU-ICSS, PRU_ICSSG, PRUSS) on AM243x, AM64x, AM62x, AM261x, AM263x, AM263Px and legacy AM335x/BeagleBone, using the TI clpru toolchain, the OpenPRU repo and TI's pru-simulator. Use this skill whenever the user mentions PRU, ICSS, ICSSG, PRUSS, clpru, pasm, OpenPRU, open-pru, pru-simulator, remoteproc PRU firmware, R30/R31 pin bit-banging, or cycle-exact timing, or asks to write, port, optimize, simulate or explain PRU .asm code, even if they don't say "assembly".
compatibility: TI PRU Code Generation Tools (clpru) 2.3.x; Python 3.9+ for the linter and simulator; optional TI pru-simulator (MCP server or headless runner); Linux remoteproc or MCU+ SDK on the target.
metadata:
  version: "0.2.1"
  sources: "TexasInstruments/open-pru @ 36ad470 (2026-08-19); TexasInstruments/pru-simulator @ a17fd62 (2026-10-01, v0.2.6)"
  tested-with: "Not yet tested. Run evals/evals.json on every model you use and record them here (e.g. claude-opus-5-5, claude-sonnet-5-5)."
---

# PRU assembly development

PRU cores are small 32-bit RISC cores where almost every instruction takes one cycle (5 ns at 200 MHz). They exist for jobs a CPU running an OS can't time reliably. Mistakes fail silently: a reversed branch comparison loops forever, a zero byte count hangs the core, and a missing `HALT` lets it run into garbage. This skill keeps that risk down with a linter, a simulator run, an explicit cycle budget and fixed build steps.

## Reference files

Read only what the current task needs. Every reference file is linked directly from here, so none is reachable only through another.

| File | Read when |
| --- | --- |
| `references/openpru.md` | The target is AM243x, AM64x, AM62x, AM261x, AM263x or AM263Px, or you are inside an `open-pru` checkout |
| `references/simulator.md` | Running code in TI's pru-simulator (MCP tools or headless runner), or checking timing without hardware |
| `references/isa-quick-ref.md` | Writing or reviewing instructions: operand limits, branch order, register fields, core revisions, clpru vs pasm |
| `references/timing.md` | Any timing requirement: bit-banging, sampling, protocol generation, delay loops |
| `references/c-interop.md` | Calling assembly from C or the reverse, or choosing how firmware is loaded |
| `references/soc-am335x.md` | Legacy AM335x / BeagleBone work (PRU Software Support Package, remoteproc) |

**When sources disagree**, trust them in this order: TI's assembler and compiler guides and instruction cheat sheet (shipped in OpenPRU, see `references/openpru.md`), then a hardware measurement, then a simulator run, then this skill's reference files. Say which source you relied on.

**Inside an `open-pru` checkout**, its `AGENTS.md` comes first: it routes to task runbooks for creating and porting projects, with Do-not-do lists you must keep (for example, never edit `example.syscfg`). Use this skill alongside those runbooks for linting, simulation and timing, not instead of them.

## Workflow

For any task that produces or changes code, copy this checklist into your reply and tick items off as you go.

```
PRU task progress
- [ ] 1. Target confirmed (device, subsystem, core, toolchain, host/loader, timing budget)
- [ ] 2. Register map and cycle budget written
- [ ] 3. Code written
- [ ] 4. Lint passes
- [ ] 5. Build passes
- [ ] 6. Simulation passes (or skipped with a reason)
- [ ] 7. Timing verified against budget
- [ ] 8. MCU+ project builds (flash only if asked and on target hardware)
```

**1. Confirm the target.** You need the device, the subsystem and core (for example ICSSG0 PRU1, ICSS-M0 PRU0), the toolchain, the host side (MCU+ R5F, Linux remoteproc, or none) and the timing requirement in ns or cycles. Infer what you can from the user's files and ask only for what changes the code. The device decides addresses, constants-table entries and available instructions, so ask for it if it can't be inferred. Pure instruction-set questions don't need one.

**2. Plan registers and cycles.** Before writing code, fill in the register map template below and estimate cycles for each timing-critical path. Most PRU bugs are prevented here: routines clobbering each other's registers, and loops that miss their budget.

**3. Write the code.** Follow the conventions below, and OpenPRU's `best_practices.md` for code in that repo. How you structure the algorithm is your call.

**4. Lint.** Run `python3 scripts/lint_pru.py <file.asm>...`, adding `--openpru` for code in the OpenPRU repo. It catches mnemonics in column 1, out-of-range immediates, invalid register fields, undefined labels and missing `HALT`. It also checks register use per function (`PRU-C`: read before write, discarded writes, and with `--c-abi` save-on-entry registers r4-r13), macros and includes (`PRU-M`: undefined, recursive or redefined macros, unresolved `.include`; add folders with `-I`) and, with `--openpru`, function and macro headers (`PRU-D`). `PRU-C001` fires for registers the caller supplies, so name each input in the function header or check it against the register map instead of blindly "fixing" it. Fix errors and rerun until clean; don't tick this step with errors outstanding. Each warning needs either a fix or a one-line reason it is fine. Apply `--openpru` style warnings to lines you wrote, not to legacy code you didn't touch.

**5. Build.** In OpenPRU, use the repo makefiles (`references/openpru.md`). For AM335x and other PSSP targets, run `scripts/build.sh` as documented in its header. Don't change compiler flags or invent workarounds; after a few failed fix attempts, stop and report the build output. If the build fails, return to step 3.

**6. Simulate.** If the target is AM243x or AM263x, or the code is core logic that doesn't depend on device addresses, run it in pru-simulator with `scripts/simulate.py` (`references/simulator.md`). Check the end state: registers, memory and pins match what the code should produce. If the simulator isn't installed or can't model the code (INTC, UART, eCAP, CRC and others aren't simulated), skip this step and say why. If the result is wrong, return to step 3.

**7. Verify timing.** Compare each critical path against its budget using `references/timing.md`. Use simulator cycle counts where available (`simulate.py edges` for pin timing) and hand counts otherwise, and report numbers ("half period 100 cycles = 500 ns, budget 500 ns, simulated"). If any path is over budget, return to step 2. When margin is under about 5%, recommend confirming on hardware.

**8. Build the MCU+ project.** For OpenPRU devices, build the project with its MCU+ SDK host code (`BUILD_MCUPLUS=y` in `imports.mak`, then `gmake -s` in the project directory) so the R5F application embeds the PRU firmware. Follow the MCU+ flow in `references/openpru.md`. Only flash or load onto hardware when the user asks. There is no deploy script: don't hand-write sysfs or loader commands, and for AM335x stop at the built `.out` and tell the user it needs loading.

## How much freedom each step has

| Step | Freedom | Why |
| --- | --- | --- |
| Algorithm, structure, naming | High | Many correct solutions exist |
| Register map, cycle budget, function headers | Medium | Use the templates; add columns or fields if useful |
| Lint, build, simulate, MCU+ project build | Low | Run the scripts and makefiles unchanged. A wrong flag or a half-loaded core costs the user hours |

## Register map and function header templates

Put the register map at the top of every `.asm` file as comments and keep it in sync with the code.

```
; ---- Register map --------------------------------------------
; Reg     Use                       Live in         Owner
; r0      scratch                   everywhere      -
; r1      loop counter              main_loop       main
; r2      C stack pointer           never touch     C ABI (if C linked)
; r3.w2   return address            never touch     C ABI (if C linked)
; r30     GPO: output pins          -               hardware
; r31     GPI: input pins / events  -               hardware
; ---- Cycle budget --------------------------------------------
; Path          Budget     Estimate   Verified
; main_loop     100 cyc    100 cyc    simulated
```

Every function and macro gets a header recording what OpenPRU's guidelines require: pseudo code, PEAK cycles (worst case), parameters and their valid ranges, and registers modified, including those of macros it invokes.

## Conventions

- Indent with spaces. Only labels and symbol names (as in `DELAY .set 48`) start in column 1, because the TI assembler treats anything there as a label.
- Write mnemonics in lowercase, unless the file you are editing consistently uses uppercase.
- Name constants with `.set`, defined before use (clpru has no forward references for `.set`). In OpenPRU code, name registers through `.struct` and `.sassign` as `best_practices.md` requires; elsewhere `.asg r1, count` is fine.
- Prefix macros with `m_` and use `?`-suffixed local labels inside them (`loop?:`). Name functions `FN_<NAME>` and wrap them in `.asmfunc` / `.endasmfunc`. Validate macro parameters with `.if` / `.emsg`.
- Comment each line in a timing-critical path with its cycle cost (`; 1`).
- End every standalone program with `halt`. Every routine called from C returns with `jmp r3.w2`. Assembly-only builds need `.retain` and `.retainrefs`.
- Keep timing-critical loops in registers and subsystem-local memory. Accesses outside the PRU subsystem have variable latency.
- Prefer the hardware `loop` instruction (V3+ cores) for fixed-count loops, then down-counting `sub` + `qbne` loops.
- Mark unfinished work with FIXME, not TODO.

## Pitfalls that cost the most time

1. **Branch comparisons read right to left.** `qbgt label, r1, r2` branches if **r2 > r1**. Check every QB comparison against the table in `references/isa-quick-ref.md`.
   Every branch takes 1 cycle, taken or not. OpenPRU's `best_practices.md` wrongly gives a taken `qbne` 2 cycles in one example.
2. **Most immediates are limited to 0–255.** `add r1, r1, 300` is invalid. Load larger values with `ldi` (16-bit) or `ldi32` (32-bit, 2 cycles) first.
3. **A zero byte count in `r0.bN` hangs the PRU.** Guard any register-supplied length for `lbbo`, `sbbo`, `lbco`, `sbco`, `xin` or `xout`.
4. **Broadside units own fixed registers.** The multiplier uses r25–r29 and broadside RAM uses r1–r9. Keep them out of your register map while those units are in use.
5. **Addresses differ between subsystems.** Memory maps, constants tables and peripherals differ between PRU-ICSS, PRU_ICSSG and PRUSS. Never reuse AM335x values on newer devices; use the device's OpenPRU includes.
6. **The C ABI owns some registers.** When C is linked in, r2 and r3.w2 belong to the compiler and r3.w2–r13 must be preserved (`references/c-interop.md`).

## Before you finish

Review the result once more: lint clean, build clean, simulation matching (or skipped with a reason), every critical path within budget, register map and function headers matching the code. If anything fails, go back to the step that owns it rather than patching around it.

If you hit a problem not covered by the pitfalls above, describe it in a sentence or two at the end of your reply and propose it as a new pitfall for this file. The user decides whether to add it.
