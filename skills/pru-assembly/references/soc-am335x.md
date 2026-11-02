# AM335x PRU-ICSS reference (legacy path)

AM335x (BeagleBone) is not supported by OpenPRU or modelled by pru-simulator. Build with the PRU Software Support Package and `scripts/build.sh`,. Loading the `.out` through remoteproc is left to the user. Register-and-pin logic can still be checked in the simulator with `--target am263x`, which is also a V3 core, but none of the addresses below apply there.

Values come from the PRU-ICSS chapter of the AM335x Technical Reference Manual (SPRUH73). Check them against your TRM revision before you rely on an address. Addresses are as seen from the PRU (local addresses), unless marked as global.

## Contents

1. Cores and clock
2. Local memory map
3. Constants table (common entries)
4. Enabling access outside the PRU subsystem
5. Events and host interrupts through R31
6. Control registers and the cycle counter
7. Broadside device IDs
8. remoteproc names

## 1. Cores and clock

- Two cores, PRU0 and PRU1, each with 8 KB of instruction RAM and 8 KB of data RAM, plus 12 KB of shared RAM.
- Clock is 200 MHz, so one cycle is 5 ns.
- PRU core version 3 (`-v3` for clpru). Hardware MAC is available (`--hardware_mac=on`).

## 2. Local memory map

| Local address | Region | Size |
| --- | --- | --- |
| 0x0000_0000 | This core's data RAM | 8 KB |
| 0x0000_2000 | The other core's data RAM | 8 KB |
| 0x0001_0000 | Shared RAM | 12 KB |
| 0x0002_0000 | INTC (interrupt controller) | |
| 0x0002_2000 | PRU0 control registers | |
| 0x0002_4000 | PRU1 control registers | |
| 0x0002_6000 | CFG (subsystem configuration) | |
| 0x0002_8000 | PRU-ICSS UART0 | |
| 0x0002_E000 | IEP (industrial Ethernet peripheral / timer) | |
| 0x0003_0000 | eCAP0 | |

Instruction RAM is a separate address space and not reachable with `LBBO`/`SBBO`.

## 3. Constants table (common entries)

| Entry | Points to | Notes |
| --- | --- | --- |
| C0 | INTC, 0x0002_0000 | Fixed |
| C4 | CFG, 0x0002_6000 | Fixed |
| C24 | This core's data RAM, 0x0000_0n00 | n set by CTBIR0 bits 7:0 (default 0) |
| C25 | Other core's data RAM, 0x0000_2n00 | n set by CTBIR0 bits 23:16 |
| C26 | IEP, 0x0002_En00 | n set by CTBIR1 |
| C28 | 0x00nn_nn00 | nnnn set by CTPPR0 bits 15:0. Point it at shared RAM by writing 0x0100. |
| C30 | L3 OCMC, 0x40nn_nn00 | Programmable via CTPPR1 |
| C31 | DDR (EMIF0), 0x80nn_nn00 | Programmable via CTPPR1 |

The full table (C0–C31, including SoC peripherals) is in the TRM. Look up any other entry there rather than guessing.

Pointing C28 at shared RAM from PRU0:

```
    LDI   r0, 0x0100            ; c28_pointer = 0x0100 -> 0x0001_0000
    LDI32 r1, 0x22028           ; PRU0 CTPPR0 (use 0x24028 for PRU1)
    SBBO  &r0, r1, 0, 4         ; also writes c29_pointer (bits 31:16) = 0
```

## 4. Enabling access outside the PRU subsystem

The PRU's master port starts in standby. Accesses to DDR, L3 or SoC peripherals stall or fail until STANDBY_INIT (bit 4 of SYSCFG, CFG offset 0x4) is cleared. Do this once at startup:

```
    LBCO  &r0, C4, 4, 4         ; read SYSCFG
    CLR   r0, r0, 4             ; STANDBY_INIT = 0
    SBCO  &r0, C4, 4, 4         ; write back
```

## 5. Events and host interrupts through R31

**Raising an event.** Write R31 with bit 5 set (the strobe) and bits 3:0 holding the event number minus 16. This raises one of system events 16–31.

```
EVT .set 19                     ; system event to raise (symbol in column 1)
    LDI   r31, 0x20 | (EVT - 16)
```

**Receiving an interrupt.** Host interrupt 0 appears as R31 bit 30 and host interrupt 1 as bit 31. Wait with `WBS r31, 30` or poll with `QBBS`.

Which host interrupt a system event reaches depends on the INTC channel and host mapping. remoteproc (via the resource table) or your startup code sets that up, and the event must be cleared in the INTC after it is handled.

## 6. Control registers and the cycle counter

PRU0 control registers start at 0x0002_2000 and PRU1's at 0x0002_4000.

| Offset | Register | Use |
| --- | --- | --- |
| 0x00 | CONTROL | Bit 3 COUNTER_ENABLE turns the cycle counter on |
| 0x04 | STATUS | Current program counter |
| 0x0C | CYCLE | Cycle count while COUNTER_ENABLE is set |
| 0x10 | STALL | Cycles the core spent stalled |
| 0x20 | CTBIR0 | C24 / C25 block index |
| 0x24 | CTBIR1 | C26 / C27 block index |
| 0x28 | CTPPR0 | C28 / C29 pointers |
| 0x2C | CTPPR1 | C30 / C31 pointers |

`references/timing.md` shows how to time a block of code with CYCLE.

## 7. Broadside device IDs

| ID | Device |
| --- | --- |
| 0 | MAC (multiply-accumulate) |
| 10, 11, 12 | Scratchpad banks 0, 1, 2 |
| 14 | Direct connect to the other PRU |

Example: `XOUT 10, &r4, 16` stores r4–r7 into scratchpad bank 0.

## 8. remoteproc names

| Core | remoteproc `name` | Default firmware file |
| --- | --- | --- |
| PRU0 | `4a334000.pru` | `/lib/firmware/am335x-pru0-fw` |
| PRU1 | `4a338000.pru` | `/lib/firmware/am335x-pru1-fw` |

The `remoteprocN` numbers vary between kernels, so find the core by its `name` file under `/sys/class/remoteproc/`.
