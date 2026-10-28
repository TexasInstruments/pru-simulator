# clpru 2.3.3 reference fixtures

These sources were assembled using TI PRU CGT **2.3.3**, default silicon version
3, and inspected with the matching `dispru`. Tests assert constants, register
fields, word addresses, and resulting execution; they do not require a local TI
installation. Disassembler addresses below are byte addresses; simulator PCs
are word addresses (divide by four).

Reproduce from the repository root, placing generated files outside the source
directories:

```text
clpru --compile_only --obj_directory=<output> tests/fixtures/clpru_syntax/set.asm
dispru <output>/set.obj
python -m tools.headless_runner --assembly tests/fixtures/clpru_syntax/set.asm --max-cycles 100 --json
```

Repeat for each `.asm`. `call.asm`, `struct.asm`, and `unknown.asm` are
intentional compiler failures, preserving the issue's exact reproductions.

| Source | clpru / dispru reference |
| --- | --- |
| `call.asm` | E0003 Invalid instruction for CALL and RET; E0300 undefined CALL/RET symbols. These are not built-in pseudo-ops in this toolchain. |
| `call_reference.asm` | Explicit JAL/JMP reference for the simulator shorthand, shown below. |
| `set.asm` | `0000: 240020e1 LDI R1,32`; `0004: 240020e2 LDI R2,32`; `0008: 240008e3 LDI R3,8`; `000c: 2a000000 HALT`. |
| `struct.asm` | E0800 for `count .u32`; E0300 empty structure; E0100 Label missing for `.sassign`; E0300 member count not found. |
| `struct_reference.asm` | Accepted TI spellings: `0000: 240007e5 LDI R5,7`; `0004: 240000e1 LDI R1,0`; `0008: 00e5e1e1 ADD R1,R1,R5`; `000c: 24000886 LDI R6.w0,8`; `0010: 24000946 LDI R6.b2,9`; `0014: 24000a66 LDI R6.b3,10`; `0018: 24000ce7 LDI R7,12`; `001c: 2a000000 HALT`. |
| `directives.asm` | Section `.text:main` is exactly 8 bytes: label `start` at 0, `0000: 240001e1 LDI R1,1`; `0004: 2a000000 HALT`. All six directives emit zero bytes. |
| `metadata_symbols.asm` | `.text`, `.global` list, `.def`, `.ref`, and TI's label-field `.asmfunc` produce the same two words `240001e1`, `2a000000`; `start` is 0 and `exported` is 4. |
| `unknown.asm` | E0003 Invalid instruction; E0300 undefined `bogus`. |
| `expressions.asm` | LDI R1–R5 at 0,4,8,12,16 with values **3,7,9,15,28**, then HALT at 20; encodings `240003e1`, `240007e2`, `240009e3`, `24000fe4`, `24001ce5`, `2a000000`. Confirms integer division, negative remainder, arithmetic and bitwise expressions. |
| `macro.asm` | `0000: 240007e4 LDI R4,7`; `0004: 80e03884 SBCO &R4.b0,C24,R0,4`; `0008: 0104e0e0 ADD R0,R0,4`; `000c: 2a000000 HALT`. |
| `unsigned_set.asm` | `.set` of `0xFFFFFFFF`, `0x80000000`, `0xFFFF0000 >> 16`, `1 << 31`, `0xFFFFFFFF & 0xFF` used by `ldi32`/`ldi`: `24ffffc1`, `24ffff81`, `248000c2`, `24000082`, `24ffffe3`, `248000c4`, `24000084`, `2400ffe5`, `2a000000` (clpru emits the `.w2` half of an `ldi32` first). |
| `reg_alias.asm` | `X .set r5`, `Y .equ r6`: `240001e5 LDI R5,1`; `0103e5e5 ADD R5,R5,3`; `240002e6 LDI R6,2`; `2a000000 HALT`. |
| `call_macro.asm` | User macro `CALL` invoked as lower-case `call f` expands (macro names are case-insensitive): `2300009c JAL R28.w0, f`, `209c0000 JMP R28.w0`. |
| `xfr_count.asm` | `2e853e01 XIN 10,&R1.b0,b0`; `2f053e81 XOUT ... b1`; `2effbe01 ZERO &R1.b0,b0`; `2eff3f01 FILL &R1.b0,b2`; `2f853f81 XCHG ... b3` (dispru prints the count codes as 125–128). Upper-case `B0` and `bN` in other operand slots are rejected by clpru. |
| `burst.asm` | Encodings `ff00c201`, `ef00c281`, `9f00f801`, `8f00f881` at 0,4,8,12. dispru prints counts 125–128 for the four reserved register-count encodings; SPRUIJ2 specifies these as R0.b0–R0.b3, rather than literal byte counts. |

For `call_reference.asm`, link the object to resolve JAL's relocation. Use a
command file containing:

```text
MEMORY { CODE: origin = 0x0, length = 0x1000 }
SECTIONS { .text > CODE }
```

```text
clpru --run_linker <output>/call_reference.obj <command-file> --unused_section_elimination=off --output_file=<output>/call_reference.out
dispru <output>/call_reference.out
```

The linker warns that no entry symbol was supplied and selects zero. The linked
stream is:

```text
00000000  240001ee  LDI R14, 1
00000004  230003c3  JAL R3.w2, fn
00000008  2a000000  HALT
0000000c  240063ee  fn: LDI R14, 99
00000010  20c30000  JMP R3.w2
```

Thus the simulator's expected `fn` PC is 3, saved return PC is 2 in R3.w2,
and the five executed instructions leave R14 = 99.

Additional compiler probes confirmed that `.set` redefinition (`A .set 7`
followed by `A .set 9`) is E0300, while a packed `.ubyte` followed by `.ushort`
maps the latter to R5.w1 when assigned at R5. The loader allows that word field
but rejects members crossing register boundaries.

The existing reference sources were also audited: `isa_execution_test.asm`
previously ignored `STORE_RESULT .macro`, its invocations, `.endm`, `.text`, and
`.global`; `pru_encoding_test.asm` ignored `.text`/`.global` and left `b0`
unresolved in memory burst counts. These are now explicitly supported and
covered by regression tests. All 36 pre-existing tracked assembly/include
sources parse successfully; no unknown-token compatibility exception is used.
clpru/dispru report `.text` sizes of `0x8f8` (574 instructions) and `0x240`
(144 instructions) respectively, with `main` at zero; tests assert these sizes
and addresses for the existing source files too.
