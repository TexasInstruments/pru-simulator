# SSI Dual-PRU Hardware Test Design

## Goal

Create a Code Composer Studio workspace for AM243x LaunchPad hardware testing
with a PRU0 SSI reader, a PRU1 SSI encoder emulator, and an R5F host that
reports captured 12-bit samples over the board UART. Preserve the existing
standalone bit-banged UART validation project in a separate grouped folder.

## Workspace layout

```text
Project_Tests/
  uart_validation/
    uart_test/
    pru0_uart_tx/
  ssi_test/
    ssi_test/
    pru0_ssi_reader/
    pru1_ssi_emulator/
```

The PRU projects are standalone assembly projects. Each generates a firmware
header consumed by the R5F project, so the PRU projects must be built before
the host project.

## Real-time firmware

PRU0 runs at the shared ICSSG0 core clock of 300 MHz and generates a 4 MHz SSI
clock on PRU0 GPO0. It samples PRU0 GPI8 MSB-first for twelve bits and writes:

| PRU0 DRAM0 offset | Contents |
| ---: | --- |
| 16 | Latest 12-bit position, zero-extended to 32 bits |
| 20 | Monotonically increasing frame counter |

PRU1 receives the clock on GPI16 and drives encoder data on GPO0. It cycles
through `0xABC`, `0xAAA`, `0xBCA`, `0x12A`, and `0xCC2`. Each value is held for
enough SSI frames to be observable through the low-rate host UART report. PRU1
uses its own DRAM1 offsets 8 and 20 for the current emitted value and its frame
counter.

The firmware configures only the PRU pad routes needed by the SSI loopback and
uses the existing OpenPRU ICSSG clock/pinmux include files copied into each
standalone project. PRU0 performs the one-time 300 MHz ICSSG0 clock setup;
PRU1 inherits the shared clock and configures its own pad routes.

## Host application

The R5F FreeRTOS host performs normal driver and board initialization, opens
ICSSG0, initializes both PRU data RAMs, and loads PRU0 before PRU1. It polls
PRU0 DRAM0 with `PRUICSS_readMemory()`, using the frame counter to detect a new
sample and a second counter read to avoid reporting a torn value.

The host reports the latest sample at a bounded interval (100 ms by default),
for example:

```text
SSI value=ABC frame=123456
```

It intentionally does not forward every SSI frame: the continuous SSI stream
is much faster than a 115200-baud UART can carry. The R5F UART is the normal
board console UART configured by SysConfig; the SSI project does not use the
old PRU0 GPO3 bit-banged UART pin.

## SysConfig and wiring

The SSI host project copies the known-good MCU+ SysConfig as a starting point.
Pin routing is left for the user to complete in CCS SysConfig, as required by
the OpenPRU project runbook. Set the ICSSG0 core clock to 300 MHz and route:

| Function | PRU signal | LaunchPad point |
| --- | --- | --- |
| SSI clock output | PRU0 GPO0 | BP.33 |
| SSI data input | PRU0 GPI8 | BP.51 |
| Emulator data output | PRU1 GPO0 | BP.11 |
| Emulator clock input | PRU1 GPI16 | BP.57 |

Wire BP.33 to BP.57 and BP.11 to BP.51. Connect the PC USB-UART adapter to
the board UART TX and GND selected by the host SysConfig, not to PRU0 GPO3.

## Verification

Repository checks verify the project names, generated-header paths, pin and
clock constants, DRAM offsets, twelve-bit sequence, and the host polling
contract. If the TI PRU compiler is installed, each PRU project is then built
first and the generated headers are checked before building the R5F project.
