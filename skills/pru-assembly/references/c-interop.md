# Mixing C and PRU assembly (clpru)

Don't write a mixed C and assembly project from scratch. Start from the existing example closest to what you need and keep its linker files, resource table (if it has one) and loading setup:

- OpenPRU: `examples/empty_c` for C or mixed firmware, or any example with a similar structure (for example `examples/rpmsg_echo_linux` for RPMsg to Linux, `academy/uart/uart_loopback` for the hardware UART). Linker files for C projects are in `source/linker_cmd/c_code/`.
- PRU Academy (PRU Getting Started Labs): the labs on mixed C and assembly, mixed C and inline assembly, and when a resource table is required. Follow them for loading and host-side setup.
- Compiler guide: OpenPRU ships *PRU Optimizing C/C++ Compiler User's Guide* v2.3 under `docs_ai/reference/pru_optimizing_c_compiler_users_guide_v2_3/`. Grep its "Register Conventions" and "Function Structure and Calling Conventions" sections for edge cases.

The register roles below are the one fact this skill keeps, because the linter checks against them (`lint_pru.py --c-abi`).

## Register roles

| Registers | Role | Who preserves them |
| --- | --- | --- |
| r0, r1 | Scratch | Caller (free to use in your routine) |
| r2 | Stack pointer | Never change it without restoring it |
| r3.w2 | Return address | Callee: save it before any nested call, return with `JMP r3.w2` |
| r4–r13 | Callee-saved (r4 may also serve as the argument pointer when arguments spill to the stack) | Your routine must restore them before returning |
| r14–r29 | Arguments and return value; otherwise scratch | Caller |
| r30, r31 | Pin I/O and events | Not part of the ABI |

- Arguments go in r14 upward, in order; return values start in r14. See the compiler guide for how narrow and 64-bit values are packed.
- The assembly label matches the C function name exactly (no leading underscore under clpru's EABI).

The calling-convention examples in the compiler guide have not been run through this skill's simulator workflow. Treat a mixed project as unverified until it builds and runs.
