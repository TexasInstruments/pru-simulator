# ICSSG-only blocks on the ICSS-M (AM261x / AM263x)

The AM261x and AM263x carry the two-core ICSS-M. It has no RTU_PRU, TX_PRU,
Task Manager, PA_STATS or IEP1, and its CFG block does not have the ICSSG PWM
trip registers. Firmware written for the AM243x / AM64x ICSSG can still touch
those addresses (SPRUIM2J Table 6-63).

## What silicon does

Measured on an LP-AM261 (ICSS-M0) through the PRU, one address per run: a read
of each address below returns `0x00000000`, with no bus error and no hang.

| Block | Base | Window | Words read on silicon |
|---|---|---|---|
| RTU_PRU0 / RTU_PRU1 IRAM | `0x4000` / `0x6000` | 8 KB each | +0, +4, +0x100 |
| TX_PRU0 / TX_PRU1 IRAM | `0xA000` / `0xC000` | 6 KB each | +0, +4, +0x100 |
| RTU_PRU0, RTU_PRU1 Control and Debug | `0x23000`, `0x23400`, `0x23800`, `0x23C00` | 0x400 each | +0, +4, +0x100 |
| TX_PRU0, TX_PRU1 Control and Debug | `0x25000`, `0x25400`, `0x25800`, `0x25C00` | 0x400 each | +0, +4, +0x100 |
| PA_STATS_QRAM | `0x27000` | 4 KB | +0, +4, +0x100 |
| TM_CFG_PRU0, TM_CFG_RTU0, TM_CFG_TX_PRU0, TM_CFG_TX_PRU1 | `0x2A000`, `0x2A100`, `0x2A400`, `0x2A500` | 0x100 each | +0, +4, +0x100 |
| PA_STATS_CRAM | `0x2C000` | 8 KB | +0, +4, +0x100 |
| IEP1 | `0x2F000` | 4 KB | +0, +4, +0x100 |
| CFG ICSSG PWM registers | `0x26130` .. `0x2616C` | 0x40 | every word |

Only the words in the last column were read. The rest of each window is
inferred from them, and the window sizes come from the AM243x / AM64x address
map, not from the ICSS-M.

Not covered, so still an error: CFG `0x40` and `0x170` (present on the ICSS-M,
see "Writes"), RAT, and anything else that is not listed.

## Reserved ranges behind the C10 and C12-C14 pointers

On the ICSS-M the constant table holds pointers in the reserved entries C10
(`0x2A000` on PRU0, `0x2A200` on PRU1), C12 (`0x27000`), C13 (`0x2C000`) and C14
(`0x24800`), and an LBCO through each returns zeros on an LP-AM261. The
reserved ranges they reach, sized from the AM261x TRM SPRUJB6 local memory map
(Table 7-38), are covered by the same windows:

| Range | Covered by |
|---|---|
| `0x24800` .. `0x24BFF` (up to PROTECT) | `Reserved 0x24800` |
| `0x27000` .. `0x27FFF` (up to UART0) | PA_STATS_QRAM |
| `0x2A000` .. `0x2DFFF` (up to IEP0) | TM_CFG_PRU0, TM_CFG_RTU0, TM_CFG_PRU1, TM_CFG_RTU1, TM_CFG_TX_PRU0, TM_CFG_TX_PRU1, `Reserved 0x2A600`, PA_STATS_CRAM |

TM_CFG_PRU1 and TM_CFG_RTU1 are at `0x2A200` and `0x2A300`, as on the ICSSG. Of
the words added here, only the pointer targets were read on silicon (C10 on
PRU1 at `0x2A200`, C14 at `0x24800`); writes to them were not measured. The two
`Reserved` windows are reserved on the ICSS-M, not ICSSG blocks, so their
messages leave out "ICSSG-only block".

## What the simulator does

By default an ICSS-M target (`target = AM261x` or `AM263x`) models the windows
above: a read returns zero, a write is dropped, and the first access to each
block logs one warning ("ICSSG-only block ... is absent on ICSS-M"). Cycle
costs are the same as for the other ICSS-local registers (2 for a read, 1 for
a write); they were not measured.

To catch firmware that reaches for hardware the device does not have, set

```ini
[device]
target = AM261x
icssg_only_blocks = strict
```

An access then raises, as it did before this option existed, and the message
names the block, for example
`read of ICSSG-only block IEP1 at 0x0002F000: this block is absent on ICSS-M (AM261x)`.
The core halts with that text in `fault["error"]`.

`icssg_only_blocks` accepts `read_zero` (the default) or `strict`. It has no
effect on AM243x / AM64x, where the ICSSG blocks are real.

## Writes

On the same LP-AM261, a 32-bit store of `0xA5A55A5A` to each of those words
was dropped: the word read back `0x00000000`, no register that the ICSS-M does
implement changed (CFG, PRU0/PRU1 control, INTC, IEP0, MII_RT_CFG, and words in
both data RAMs and the shared RAM), and nothing faulted or hung. The simulator
therefore drops writes. Other store widths and values were not tried.

CFG `0x40` (`SA_MX_REG`) and CFG `0x170` (`SPIN_LOCK0`) are ICSS-M registers, not
absent blocks, and are not part of these windows. The simulator does not model
them, so an access to them still raises.
