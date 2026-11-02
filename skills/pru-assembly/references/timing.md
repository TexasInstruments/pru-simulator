# PRU timing

Use this file whenever code has a timing requirement. Examples assume a 200 MHz PRU clock (5 ns per cycle). Check your device's PRU clock: cycle counts stay the same, nanoseconds scale.

## Contents

1. The cycle model
2. Instruction costs
3. Budgeting a loop: worked example
4. Measuring: simulator first, then hardware
5. What breaks determinism
6. Known error in OpenPRU's best_practices.md

## 1. The cycle model

The PRU has no cache and no branch prediction. Most instructions, including taken and untaken branches, take exactly one cycle. OpenPRU's `best_practices.md` gets this wrong in one place; see section 6. That makes hand counting reliable for code that stays inside registers and pin I/O. Anything that touches memory needs measuring.

## 2. Instruction costs

| Instructions | Cycles | Confidence |
| --- | --- | --- |
| ALU ops, `MOV`, `LDI`, `SET`, `CLR`, `QB*`, `JMP`, `JAL` | 1 | Count by hand |
| Read `r31`, write `r30` | 1 | Count by hand |
| `LDI32` | 2 (two instructions) | Count by hand |
| `LOOP` | 1 cycle setup, then no per-iteration overhead | Count by hand |
| `LBBO`/`SBBO`/`LBCO`/`SBCO` to PRU-local RAM | Several cycles, growing with byte count | Measure |
| Loads and stores to DDR, L3 or SoC peripherals | Tens of cycles or more, variable | Keep out of timed paths |
| `XIN`/`XOUT` broadside | Short and fixed per device | Measure once |

When you report a budget, mark each figure as counted or measured.

## 3. Budgeting a loop: worked example

Verified in pru-simulator (AM243x and AM263x configs): `scripts/simulate.py edges` reports 100, 100, 100 cycles between pin edges.

Goal: a 1 MHz square wave on output bit 5. The period is 1000 ns, so each half period is 100 ns = 100 cycles.

```
; ---- Cycle budget ----
; Path        Budget    Estimate   Verified
; half period 100 cyc   100 cyc    counted
DELAY .set 48                   ; symbol in column 1
wave:
    XOR   r30, r30, 32          ; 1  toggle bit 5
    LDI   r1, DELAY             ; 1
delay:
    SUB   r1, r1, 1             ; 1  } 2 cycles x DELAY
    QBNE  delay, r1, 0          ; 1  }
    MOV   r0, r0                ; 1  padding to hit 100 exactly
    QBA   wave                  ; 1
```

Total per half period: 1 + 1 + 2 × 48 + 1 + 1 = 100 cycles.

To solve for the delay: fixed overhead = 4 cycles, loop = 2 cycles per iteration, so DELAY = (budget − 4) / 2. If that isn't a whole number, round down and pad with single-cycle instructions that have no side effects, like `MOV r0, r0` on a scratch register.

## 4. Measuring: simulator first, then hardware

**Simulator.** For AM243x and AM263x code, or register-and-pin code on any device (`references/simulator.md`):

```bash
python3 scripts/simulate.py edges fw.asm --pin 5 --edges 4     # cycles between pin changes
python3 scripts/simulate.py run fw.asm --max-cycles 1200        # fails if the run needs more
```

Simulated counts are exact for register and pin code. Memory accesses use configured latencies, so treat those counts as estimates.

**Cycle counter on hardware.** Every PRU has CONTROL (bit 3 enables counting) and CYCLE registers in its control block. The example uses the AM335x PRU0 base 0x22000 (PRU1: 0x24000). On OpenPRU devices, take the control-register base from `source/include/<device>/` instead.

```
    LDI32 r10, 0x22000          ; PRU0 control base
    LBBO  &r11, r10, 0, 4       ; CONTROL
    CLR   r11, r11, 3           ; counter off
    SBBO  &r11, r10, 0, 4
    LDI   r12, 0
    SBBO  &r12, r10, 0xC, 4     ; CYCLE = 0
    SET   r11, r11, 3           ; counter on
    SBBO  &r11, r10, 0, 4
    ; ... code under test ...
    LBBO  &r13, r10, 0xC, 4     ; r13 = elapsed cycles
```

The result includes the cost of the enabling store and the reading load. Time an empty block the same way first and subtract that figure. Store r13 in data RAM so the host can read it.

**Scope.** Toggle a spare R30 pin at the start and end of the path and measure the pulse width. This includes the toggle instructions, which cost 1 cycle each.

## 5. What breaks determinism

- Memory accesses outside the PRU subsystem, which contend with the ARM and DMA for the bus.
- Accesses to shared RAM or the other core's RAM while the other PRU is using it.
- Polling loops (`WBS`, `QBBC` to self), whose duration depends on the external signal. Budget the work after the event, not the wait.
- Code paths with different lengths on each branch. Pad the shorter branch if both must take the same time.

## 6. Known error in OpenPRU's best_practices.md

The "Timing Assumptions" pitfall example in `best_practices.md` says a taken `qbne` costs 2 cycles, making a `sub` + `qbne` delay loop 3 cycles per iteration. That is a bug in the document. `qbne` takes 1 cycle whether or not the branch is taken, so the loop costs 2 cycles per iteration, which matches the file's own `m_delay_cycles` macro and pru-simulator.

Don't copy the 3-cycle figure into budgets. OpenPRU uses `best_practices.md` as context for automated PR review, so if a review comment cites it, point out the error.
