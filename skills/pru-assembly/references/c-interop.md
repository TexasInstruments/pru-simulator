# Mixing C and PRU assembly (clpru)

Most PRU firmware with a host interface has a small C `main` that calls assembly routines for the timing-critical parts. The register roles below were checked against TI's *PRU Optimizing C/C++ Compiler User's Guide* v2.3, shipped in OpenPRU as `docs_ai/reference/pru_optimizing_c_compiler_users_guide_v2_3/`. Grep its "Register Conventions" and "Function Structure and Calling Conventions" sections for edge cases.

## Contents

1. Register roles
2. Calling an assembly routine from C
3. Calling C from assembly
4. Loading the firmware: remoteproc or MCU+
5. Project layout

## 1. Register roles

| Registers | Role | Who preserves them |
| --- | --- | --- |
| r0, r1 | Scratch | Caller (free to use in your routine) |
| r2 | Stack pointer | Never change it without restoring it |
| r3.w2 | Return address | Callee: save it before any nested call, return with `JMP r3.w2` |
| r4–r13 | Callee-saved (r4 may also serve as the argument pointer when arguments spill to the stack) | Your routine must restore them before returning |
| r14–r29 | Arguments and return value; otherwise scratch | Caller |
| r30, r31 | Pin I/O and events | Not part of the ABI |

- The compiler guide calls r3.w2–r13 "save on entry" and r0–r1, r14–r29 "save on call".
- Arguments go in r14 upward, in order. Arguments that don't fit in r14–r29 go on the stack.
- Narrow arguments are packed into register fields. The guide's example puts a 32-bit first argument in r14, a 16-bit second argument in r15.w0, and a later 16-bit argument in r15.w2 to fill the gap. Pass `uint32_t` when you want the mapping obvious.
- Return values start in r14; 64-bit values use r14–r15.
- If your routine moves r2, restore it before returning.

## 2. Calling an assembly routine from C

```c
/* main.c */
#include <stdint.h>
#include "resource_table_empty.h"

extern uint32_t pru_toggle(uint32_t count);   /* count -> r14, result <- r14 */

void main(void)
{
    pru_toggle(1000);
    __halt();
}
```

```
; toggle.asm
; ---- Register map ----
; r14   in: toggle count / out: 0
; r1    loop counter (caller-saved, free to use)
    .global pru_toggle
    .text
pru_toggle:
    MOV   r1, r14               ; 1
    QBEQ  done, r1, 0           ; 1  guard zero count
toggle_loop:
    XOR   r30, r30, 32          ; 1  toggle output bit 5
    SUB   r1, r1, 1             ; 1
    QBNE  toggle_loop, r1, 0    ; 1  loop while r1 != 0
done:
    LDI   r14, 0                ; 1  return value
    JMP   r3.w2                 ; 1  return to C
```

C function names have no leading underscore under clpru's EABI, so the label matches the C name exactly.

## 3. Calling C from assembly

- Put arguments in r14 upward and use `CALL c_function`. This overwrites r3.w2, so save the routine's own return address first (in a callee-saved register or on the stack).
- Assume r0, r1 and r14–r29 are clobbered after the call.

## 4. Loading the firmware: remoteproc or MCU+

| Host | Devices | How firmware gets onto the PRU |
| --- | --- | --- |
| Linux remoteproc | AM335x (PSSP), AM62x and AM64x (OpenPRU `linux/` projects) | The kernel loads an ELF `.out`. It expects a resource table, and OpenPRU's C linker files add the interrupt map the driver reads. |
| MCU+ R5F | AM243x, AM64x, AM26x (OpenPRU `mcuplus/` projects) | The build turns the firmware into a header such as `pru0_load_bin.h`, which the R5F application loads through the PRUICSS driver. No resource table. |
| None | Any | Loaded from CCS for development. |

- **AM335x:** copy `resource_table_empty.h` from a PRU Software Support Package example (for example `PRU_Halt`) and include it from `main.c`, as above.
- **OpenPRU:** start from `examples/empty_c` (C or mixed) and keep its loading setup. For Linux, see `examples/rpmsg_echo_linux` and `source/linker_cmd/c_code/README.md`.

## 5. Project layout

For OpenPRU devices, copy an existing project (`examples/empty_c`) as `references/openpru.md` describes. For AM335x with the support package:

```
firmware/
├── main.c                  C entry, resource table include
├── toggle.asm              timing-critical routines
├── resource_table_empty.h  copied from the support package
└── AM335x_PRU.cmd          linker command file from the support package
```

Build with `scripts/build.sh firmware/out/pru0.out firmware/AM335x_PRU.cmd firmware/main.c firmware/toggle.asm`.
