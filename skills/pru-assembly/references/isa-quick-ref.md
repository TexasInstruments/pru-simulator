# PRU instruction set quick reference (clpru syntax)

Covers PRU core revisions V3 and V4. Checked against TI's `docs/PRU Assembly Instruction Cheat Sheet.md` in OpenPRU (commit 36ad470) and the *PRU Assembly Instruction User Guide* (SPRUIJ2). Where this file and those disagree, they win.

## Contents

1. Operand notation
2. Register fields and fixed-register users
3. Arithmetic and logic
4. Data movement
5. Memory access
6. Branches and the comparison table
7. Calls, loops and halt
8. Waiting on a bit
9. Core revisions
10. Assembler syntax: clpru vs pasm

## 1. Operand notation

| Notation | Meaning |
| --- | --- |
| `REG` | Register or register field (`r1`, `r1.w0`, `r1.b2`) |
| `OP(255)` | Register, or an immediate from 0 to 255 |
| `OP(31)` | Register, or an immediate from 0 to 31 (bit numbers, shift counts) |
| `OP(256)` | Register, or an immediate up to 256 (`loop` counts; a zero count is never useful, so the linter rejects a literal 0) |
| `IM(65535)` | Immediate only, 0 to 65535 |
| `IM(124)` | Byte count, 1 to 124, or a byte field of r0 (`b0`–`b3`) |
| `IM(253)` | Broadside (XFR) device ID |
| `Cn` | Constants table entry `c0`–`c31` |

**Never let a register-supplied byte count be 0.** A zero in `r0.bN` used as a length can hang the PRU.

Values larger than an operand allows must go into a register first.

## 2. Register fields and fixed-register users

There are 32 registers, `r0`–`r31`. `r30` drives output pins and `r31` reads input pins and raises events. Neither is general-purpose.

| Field | Bits | Example |
| --- | --- | --- |
| `.b0`–`.b3` | 8-bit bytes: 7:0, 15:8, 23:16, 31:24 | `r4.b1` |
| `.w0`, `.w1`, `.w2` | 16-bit words: 15:0, 23:8, 31:16 (`.w1` overlaps both) | `r3.w2` |
| `.t0`–`.t31` | Single bits | `r31.t30` |

Writing a field leaves the other bits of the register unchanged.

Some hardware uses fixed registers, so keep them out of your own register map while it's active:

| User | Registers |
| --- | --- |
| Hardware multiplier (MAC) | r25–r29 |
| Broadside RAM (where present) | r1–r9 |
| `mvib`/`mviw`/`mvid` pointer | A byte field of r1 (`r1.b0`–`r1.b3`) only |
| C ABI (when C is linked) | r2 stack pointer, r3.w2 return address; see `references/c-interop.md` |

## 3. Arithmetic and logic

| Instruction | Form | Effect |
| --- | --- | --- |
| `ADD` / `ADC` | `ADD REG1, REG2, OP(255)` | REG1 = REG2 + OP (ADC adds carry) |
| `SUB` / `SUC` | `SUB REG1, REG2, OP(255)` | REG1 = REG2 − OP (SUC subtracts borrow) |
| `RSB` / `RSC` | `RSB REG1, REG2, OP(255)` | REG1 = OP − REG2 |
| `AND` / `OR` / `XOR` | `AND REG1, REG2, OP(255)` | Bitwise |
| `NOT` | `NOT REG1, REG2` | Bitwise invert |
| `LSL` / `LSR` | `LSL REG1, REG2, OP(31)` | Shift left / right |
| `MIN` / `MAX` | `MIN REG1, REG2, OP(255)` | Unsigned minimum / maximum |
| `CLR` / `SET` | `CLR REG1, REG2, OP(31)` | Clear / set one bit |
| `LMBD` | `LMBD REG1, REG2, OP(255)` | Index of the left-most bit matching OP (0 or 1); 32 if none |

There is no hardware multiply or divide in the core ALU. Use shifts, or the MAC broadside unit (`references/soc-am335x.md`).

## 4. Data movement

| Instruction | Form | Notes |
| --- | --- | --- |
| `MOV` | `MOV REG1, REG2` | Register copy, zero-extended. With an immediate it assembles to `LDI`. |
| `LDI` | `LDI REG, IM(65535)` | 16-bit immediate |
| `LDI32` | `LDI32 REG, IM(0xFFFFFFFF)` | clpru pseudo-op, two instructions (2 cycles) |
| `ZERO` | `ZERO &REG, IM(124)` | Clear a run of bytes starting at REG |
| `FILL` | `FILL &REG, IM(124)` | Set a run of bytes to 0xFF |
| `XIN` / `XOUT` / `XCHG` | `XIN IM(253), &REG, IM(124)` | Broadside transfer to scratchpad, MAC or the other PRU; device IDs are device-specific (use the names in OpenPRU's includes) |
| `MVIB` / `MVIW` / `MVID` | `MVID r2, *r1.b0++` | Register-file indirect move; one side must be a `*r1.bN` pointer; pointer values over 127 wrap to 0 |

## 5. Memory access

| Instruction | Form | Effect |
| --- | --- | --- |
| `LBBO` | `LBBO &REG1, REG2, OP(255), IM(124)` | Load count bytes from address REG2 + offset into REG1 onward |
| `SBBO` | `SBBO &REG1, REG2, OP(255), IM(124)` | Store count bytes from REG1 onward to REG2 + offset |
| `LBCO` | `LBCO &REG1, Cn, OP(255), IM(124)` | Load relative to constants table entry Cn |
| `SBCO` | `SBCO &REG1, Cn, OP(255), IM(124)` | Store relative to constants table entry Cn |

- Multi-byte transfers fill consecutive registers: `LBBO &r4, r1, 0, 12` fills r4, r5, r6.
- Memory accesses take several cycles, more outside the PRU subsystem. See `references/timing.md`.
- Use `LBCO`/`SBCO` for fixed peripherals; they save a register and an `LDI32`.

## 6. Branches and the comparison table

Every quick branch compares **OP against REG**, not REG against OP. Read `QBGT label, r1, 10` as "branch if 10 > r1".

| Instruction | Form | Branches when |
| --- | --- | --- |
| `QBGT` | `QBGT label, REG, OP(255)` | OP > REG |
| `QBGE` | `QBGE label, REG, OP(255)` | OP ≥ REG |
| `QBLT` | `QBLT label, REG, OP(255)` | OP < REG |
| `QBLE` | `QBLE label, REG, OP(255)` | OP ≤ REG |
| `QBEQ` | `QBEQ label, REG, OP(255)` | OP = REG |
| `QBNE` | `QBNE label, REG, OP(255)` | OP ≠ REG |
| `QBBS` | `QBBS label, REG, OP(31)` | Bit OP of REG is set |
| `QBBC` | `QBBC label, REG, OP(31)` | Bit OP of REG is clear |
| `QBA` | `QBA label` | Always |

Pick the instruction by what you want to test:

| To branch if… | Use | Because |
| --- | --- | --- |
| r1 > 10 | `qblt label, r1, 10` | 10 < r1 |
| r1 ≥ 10 | `qble label, r1, 10` | 10 ≤ r1 |
| r1 < 10 | `qbgt label, r1, 10` | 10 > r1 |
| r1 ≤ 10 | `qbge label, r1, 10` | 10 ≥ r1 |
| r1 = 10 | `qbeq label, r1, 10` | 10 = r1 |
| r1 ≠ 10 | `qbne label, r1, 10` | 10 ≠ r1 |

Comparisons are unsigned. Quick branches have a limited range. If the assembler reports a target out of range, branch to a nearby `JMP`.

## 7. Calls, loops and halt

| Instruction | Form | Notes |
| --- | --- | --- |
| `JMP` | `JMP label` or `JMP REG` | Absolute jump |
| `JAL` | `JAL REG, label` | Jump and store the return address in REG |
| `CALL` / `RET` | `CALL label` / `RET` | clpru pseudo-ops for `JAL r3.w2, label` / `JMP r3.w2` |
| `LOOP` | `LOOP end_label, OP(256)` | Hardware loop (V3+). The body runs from the next instruction up to (not including) `end_label`. Setup takes 1 cycle; the loop is not interruptible. |
| `HALT` | `HALT` | Stop the core |
| `SLP` | `SLP 0` / `SLP 1` | Sleep permanently / until a wake event |
| `TSEN` | `TSEN 0` / `TSEN 1` | Disable / enable the task manager (V4 PRU_ICSSG only) |

- `CALL` overwrites r3.w2. Nested calls must save r3.w2 first.
- Guard a `LOOP` whose count comes from a register against zero, and check the ISA guide for zero-count behaviour on your core.
- Don't branch into or out of a `LOOP` body.

## 8. Waiting on a bit

| Instruction | Form | Effect |
| --- | --- | --- |
| `WBS` | `WBS REG, OP(31)` | Wait until the bit is set (pseudo-op for a `QBBC` to itself) |
| `WBC` | `WBC REG, OP(31)` | Wait until the bit is clear |

Typical use is waiting for a host interrupt: `WBS r31, 30`.

## 9. Core revisions

| Feature | V3 | V4 (PRU_ICSSG) |
| --- | --- | --- |
| `mvi*` register-indirect moves | Yes | Yes |
| `loop`, `fill` | Yes | Yes |
| `tsen` task manager | No | Yes |

- V3: AM261x, AM263x, AM263Px, AM62x (and AM335x, built with `-v3`).
- V4: AM243x, AM64x, AM65x.
- Older V1/V2 cores lack `loop` and `fill`.

## 10. Assembler syntax: clpru vs pasm

| Topic | clpru (TI CGT) | pasm (legacy) |
| --- | --- | --- |
| Comments | `;` anywhere; `*` or `;` in column 1; `!` in column 1 inside a macro | `//` and `/* */` |
| Constants | `NAME .set 19` (name in column 1), `.asg r1, count`, `.cdecls` for C headers | `#define` |
| Memory ops | `LBBO &r0, r1, 0, 4` (needs `&`) | `LBBO r0, r1, 0, 4` |
| 32-bit immediates | `LDI32 r0, 0x12345678` | `MOV r0, 0x12345678` |
| Entry point | Linker command file / C `main` | `.origin` and `.entrypoint` |
| Column 1 | Labels and symbol names only | More permissive |
| File extension | `.asm`, headers `.h`/`.inc` | `.p`, headers `.hp` |

GNU binutils (`pru-as`) differs again. Don't mix syntaxes in one file; when porting, convert the whole file and rerun the linter.

Mnemonics are case-insensitive. OpenPRU writes them in lowercase; this file uses uppercase only to make them stand out in tables.
