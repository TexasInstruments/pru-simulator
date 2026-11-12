# Assembly source syntax

The assembly loader accepts a subset of TI PRU assembler source. Unsupported
mnemonics, directives, and unresolved instruction operands are load errors.
Check the errors returned by `Simulator.load`, or use the headless runner's JSON
`errors` and `success` fields. Operand and structure errors carry source line
numbers, including after constant substitution and pseudo-op expansion (include
locations are relative to the included file). A few preprocessing failures, such
as an unterminated `.macro` or an `.if` problem, report the line of the construct
that opened them or none at all.

- `call target` expands to `jal r3.w2, target`; `ret` expands to `jmp r3.w2`.
  Inline colon labels work. These are simulator conveniences using the R3.w2
  return convention: **clpru 2.3.3 rejects the bare CALL/RET spellings**. Use the
  explicit JAL/JMP instructions when assembling with that toolchain. Macro names
  are case-insensitive, as in clpru, and a macro named `call` or `ret` always
  takes precedence over the shorthand. If such a macro exists but a `call`/`ret`
  line is not expanded as a macro invocation (for example because a label
  precedes it on the same line), that line is a load error rather than being
  silently turned into the shorthand.
- `NAME .set expression` and `.equ` accept integer constants, previously defined
  symbols, parentheses, unary `+ - ~`, and binary `+ - * / % << >> & | ^`.
  Division truncates toward zero; remainder has the dividend's sign. Values and
  intermediate results may range from -2^31 to 2^32-1, so unsigned masks such as
  `0xFFFFFFFF` work; shifts require a nonnegative left operand and a count from
  0 through 31. A value that is exactly a register name (`Counter .set r5`)
  makes `Counter` an alias for that whole register. Overflow, forward references,
  redefinitions, functions, floating point, and other expression forms are load
  errors. This deliberately bounded subset does not implement TI's full
  expression grammar, relocatable expressions, or integer overflow rules.
- Flat structures use TI syntax:

  ```asm
  CtxType .struct
  count   .uint
  low     .ushort
  flag    .ubyte
  spare   .ubyte
  Ctx_len .endstruct
  Ctx     .sassign r5, CtxType
      add r1, r1, Ctx.count  ; R5
      ldi Ctx.low, 8        ; R6.w0
      ldi Ctx.flag, 9       ; R6.b2
  ```

  Members are packed in declaration order. `.endstruct` may name the byte size.
  `.sassign` requires a label, a whole starting register, and a previously
  defined nonempty structure. Each field must fit one supported register field
  within R0–R31. Nested structures, unions, arrays, initial offsets, other member
  directives, and appended register suffixes on mapped members are load errors.
  The older simulator spelling `.struct Name`, `.u8 field` / `.u16 field` /
  `.u32 field`, `.ends` remains supported. The issue's `count .u32` and unlabeled
  `.sassign r5, Ctx` are rejected by both clpru 2.3.3 and this loader.
- `.retain`, `.retainrefs` (optionally with a quoted section name), `.global`,
  `.def`, `.ref` (comma-separated identifiers), and argument-free `.asmfunc` /
  `.endasmfunc` emit no instructions. Both `label: .asmfunc` and TI's
  `label .asmfunc` spelling are accepted. Colon labels on metadata retain the
  next instruction's address.
- `.text` and `.sect ".text[:subsection]"` select one contiguous code section
  without emitting instructions. Switching to a different section after code,
  data sections, and section attributes are load errors. The source loader
  does not perform linking or section placement.
- TI's `NAME .macro [parameters]` and the existing `.macro NAME [parameters]`
  spelling accept flat instruction bodies through `.endm`. Register counts
  `b0`–`b3` (lower case, as clpru requires) read the corresponding byte of R0 in
  the length operand of LBBO, SBBO, LBCO, SBCO, XIN, XOUT, XCHG, ZERO, and FILL.
  The ELF disassembler decodes the same encodings to the same operands.
  Constant-table names C0–C31 remain valid in the table slot.
- `ldi32 reg, value` accepts a decimal or `0x` literal. Negative values and
  symbolic operands that do not reduce to such a literal are load errors even
  though clpru accepts them.

## Known limitations

The loader is stricter than it used to be but is not a full TI assembler. These
inputs are still accepted without a diagnostic, although clpru rejects or
handles them differently:

- `.include` of a file that cannot be found, or with an unquoted name, is
  silently ignored, so everything it would have defined is missing.
- `.if` expressions the loader cannot evaluate (for example `$isdefed("X")`)
  count as false. A nested `.else` under an inactive outer `.if` still assembles
  its body. Stray `.else`/`.endif` lines are ignored.
- Duplicate labels, macro invocations with too many or too few arguments, and
  empty operands (`ldi r1, 5,`) are not reported.
- Register numbers above 31 (`ldi r32, 1`) load and fail only at run time.
- `.if` conditions are evaluated with Python's expression evaluator, which is
  not a sandbox; do not assemble untrusted source.
- Python-only literal spellings such as `1_000` and `0b101` are accepted.

The assembler/disassembler evidence and reproducible tool commands are in
[`tests/fixtures/clpru_syntax/README.md`](../tests/fixtures/clpru_syntax/README.md).
The register-count meaning is also specified in TI's
[PRU Assembly Instruction User Guide, SPRUIJ2](https://www.ti.com/lit/ug/spruij2/spruij2.pdf),
LBBO/SBBO register-count forms. Structure syntax is documented in the
[PRU Assembly Language Tools directives reference](https://downloads.ti.com/docs/esd/SPRUHV6/directives-reference-stdz0723929.html).
