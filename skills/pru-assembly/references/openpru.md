# Working with OpenPRU

OpenPRU (`github.com/TexasInstruments/open-pru`) is TI's PRU development package for current devices. It contains its own AI-agent instructions. This file tells you where they are and how they fit with this skill. Paths are relative to the repo root and were checked at commit 36ad470 (2026-08-19); confirm they still exist in the user's checkout.

## Contents

1. Supported devices and subsystems
2. The repo's AI-agent entry points
3. Files worth reading, and when
4. Building
5. Host side: MCU+ and Linux
6. Rules from best_practices.md that are easy to miss

## 1. Supported devices and subsystems

| Device | Subsystem | PRU cores | Core revision |
| --- | --- | --- | --- |
| AM243x, AM64x | PRU_ICSSG | pru0, pru1, rtu_pru0, rtu_pru1, tx_pru0, tx_pru1 | V4 |
| AM261x, AM263x, AM263Px | PRU-ICSSM | pru0, pru1 | V3 |
| AM62x | PRUSS | pru0, pru1 | V3 |

Device identifiers in makefiles are `am243x`, `am261x`, `am263px`, `am263x`, `am62x` and `am64x`, with board suffixes such as `-evm`, `-lp`, `-cc` or `-som`.

AM335x, AM437x and AM57x are not supported by OpenPRU; they use the PRU Software Support Package (see `references/soc-am335x.md`).

## 2. The repo's AI-agent entry points

- `AGENTS.md` (also loaded through `CLAUDE.md`): read `docs_ai/README.md` before any OpenPRU task.
- `docs_ai/README.md`: routes to a task runbook and lists reference files with when-to-read triggers.
- `docs_ai/task_create_project.md`: creating a project. It has a decision tree (host type, devices and cores, firmware language), checklists, verification steps and a Do-not-do list.
- `docs_ai/task_port_project.md`: porting to another board or device, with the same structure.

When one of those runbooks applies, follow it step by step. It already uses a checklist with verification. Merge this skill's lint, simulate and timing steps into its firmware and verification sections rather than running two parallel checklists.

Do-not-do items that matter most:

- Never edit `example.syscfg` pin routing, PRUICSS configuration or device settings. Flag them as manual steps for the user.
- Never start a project from scratch; copy `examples/empty` (assembly) or `examples/empty_c` (C or mixed).
- Never hard-code absolute paths. Use `$(OPEN_PRU_PATH)` and `imports.mak`.
- Never guess portability-table entries. Mark unknown entries FIXME and ask.
- Don't retry a failing build indefinitely. Stop and report the output.

## 3. Files worth reading, and when

| File | Read when |
| --- | --- |
| `best_practices.md` | Before writing or reviewing PRU assembly or C (not for makefiles or linker files) |
| `docs/PRU Assembly Instruction Cheat Sheet.md` | Writing or debugging instructions; this is TI's source for `references/isa-quick-ref.md` |
| `docs_ai/reference/pru_subsystem_features_comparison_g/…md` | Checking whether a feature or peripheral exists on a subsystem |
| `docs_ai/reference/pru_subsystem_migration_guide/…md` | Porting between PRU-ICSS and PRU_ICSSG: memory maps, constants tables, I/O, interrupts |
| `docs_ai/reference/pru_assembly_language_tools_users_guide_v2_3/…md` | Assembler directives, sections, macros, linker (12,500 lines: grep, don't read) |
| `docs_ai/reference/pru_optimizing_c_compiler_users_guide_v2_3/…md` | C ABI, pragmas, intrinsics (5,800 lines: grep, don't read) |
| `source/firmware/common/icss_constant_defines.inc` | Constants-table and register names for firmware |
| `source/include/<device>/` | Device register definitions |
| `source/linker_cmd/asm_code/<device>/`, `source/linker_cmd/c_code/<device>/` | Linker command files for assembly-only or C projects |

## 4. Building

- One-time setup: copy `imports.mak.default` to `imports.mak` and edit the paths marked `UPDATE` (toolchain, SDK). The default PRU compiler path is `ti-cgt-pru_2.3.3`.
- From the repo root, run `make help` to see targets. `make -s` builds everything, and `make -s clean` cleans.
- Project-level checks used by the create runbook: from the project directory run `make -s clean && make`, then from the repo root run `make -s clean && make -s`.
- Use `gmake` on Windows (shipped with CCS, for example `C:\ti\ccs2050\ccs\utils\bin`; add it to PATH) and `make` on Linux.
- On a Windows machine with `TOOLS_PATH=C:/ti`, the default `CGT_TI_PRU_PATH=$(TOOLS_PATH)/ti-cgt-pru_2.3.3`, `CCS_PATH` and `MCU_PLUS_SDK_PATH` often don't exist. Check with `ls` and point them at the installed copies in `imports.mak`. If `imports.mak` already exists, edit it in place and never overwrite it: it is git-ignored and can't be recovered.
- MCU+ SDK 11.02 requires SysConfig 1.25.0 or newer; an older one fails at "Generating SysConfig files". Set `SYSCFG_PATH`.
- Known working setup on the user's machine (verified building `examples/empty` for `am243x`, PRU firmware and R5F appimage): `CCS_PATH=C:/ti/ccs2011/ccs` (has `ti-cgt-pru_2.3.3`), `SYSCFG_PATH=C:/ti/sysconfig_1.27.0`, `MCU_PLUS_SDK_PATH=C:/ti/mcu_plus_sdk_am243x_11_02_00_24`, and `C:\ti\ccs2011\ccs\utils\bin` on PATH for `gmake`. These are also set in `C:\ti\GitHub\open-pru\imports.mak`.
- The MCU+ build (`BUILD_MCUPLUS=y`) is the normal end of the workflow: `examples/<project>/mcuplus/<board>/` holds the R5F application that embeds the PRU firmware. Build it with `gmake -s` from the project directory, which builds PRU firmware first and then host code. Flashing or loading onto the board is a separate step, and only on request.
- In CCS, import `example.projectspec` and build the PRU firmware before the R5F project.
- Don't add a new project to the parent makefile until the project-level build succeeds.

## 5. Host side: MCU+ and Linux

- **MCU+ (R5F):** the PRU firmware is built into a header (for example `pru0_load_bin.h`) that the R5F application loads through the PRUICSS driver. The MCU+ project's include path must contain the PRU project directory. See `docs/open_pru_create_new_mcuplus_project.md`.
- **Linux (A53, AM62x and AM64x):** firmware is loaded by the PRU remoteproc driver. C projects use linker files from `source/linker_cmd/c_code/` with an interrupt map the driver reads. See `docs/getting_started_linux.md` and `examples/rpmsg_echo_linux`.

## 6. Rules from best_practices.md that are easy to miss

- Spaces only (no tabs), 4-space indentation, Unix line endings.
- 79-column soft limit, 129-column hard limit.
- Every source file needs a header with the file name and TI copyright.
- `.include` paths use backslashes, for example `.include "source\firmware\common\icss_constant_defines.inc"`.
- Header guards use four underscores: `.if !$isdefed("____my_header_h")`.
- FIXME, never TODO.
- Write "for example", "that is" and "and so on" in comments, not "e.g.", "i.e." and "etc.".
- Known error: the file's "Timing Assumptions" pitfall example says a taken `qbne` costs 2 cycles. It costs 1, like every branch. Don't use that example's numbers; see `references/timing.md`.
