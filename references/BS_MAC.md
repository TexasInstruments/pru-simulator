## Page 1

1} TEXAS
INSTRUMENTS

Processors and Accelerators www.ti.com

6.4.6 PRU_ICSSG Broadside Accelerators

6.4.6.1 PRU_ICSSG Broadside Accelerators Overview

The PRU_ICSSG supports a broadside interface, which uses the XFR (XIN, XOUT, or XCHG) instruction to
transfer the contents of PRUn, RTU_PRUn or TX_PRUn (where n = 0 or 1) registers to or from accelerators.
This interface enables up to 31 registers (RO-R30, or 124 bytes) to be transferred in a single instruction.

This section details the various accelerators that are available to the PRUn and/or RTU_PRUn and TX_PRUn
through the broadside interface.

Each of those functions have a unique XIN ID to determine which operation will occur. For more information see
Table 6-403.

6.4.6.2 PRU_ICSSG Data Processing Accelerators Functional
6.4,6.2.1 PRU Multiplier with Accumulation (MPY/MAC)

This section describes the MAC (multiplier with accumulation) module integrated to PRUO/PRU‘1 cores,
RTU_PRUO/RTU_PRU1 auxiliary cores and TX_PRUO/TX_PRU1 cores of PRU_ICSSGn (or CFG1, PRU1).

Each of the six PRU cores (PRU0/PRU1, RTU_PRUO/RTU_PRU1 auxiliary cores and TX_PRUO/TX_PRU1
transmit cores ) has a designated unsigned multiplier with accumulation (MPY/MAC). The MAC supports two
modes of operation: Multiply Only and Multiply and Accumulate.

The MAC is directly connected with the PRU internal registers R25-R29 and uses the broadside load/store PRU
interface and XFR instructions to both control and mode of the MAC and import the multiplication results into the
PRU.

The PRU MPY/MAC features are:

* Configurable Multiply Only and Multiply and Accumulate functionality via PRU register R25

* 32-bit operands with direct connection to PRU registers R28 and R29

+ 64-bit result (with carry flag) with direct connection to PRU registers R26 and R27

+ One clock cycle per operation

+ PRU broadside interface and XFR instructions (XIN, XOUT) allow for importing multiplication results and
initiating accumulate function

+ Firmware can SEED the accumulator

6.4.6.2.1.1 PRU MAC Operations
6.4.6.2.1.1.1 PRU versus MAC Interface

The MAC directly connects with the PRU internal registers R25-R29 through use of the PRU broadside interface
and XFR instructions. Figure 6-201 shows the functionality of each register.

3324 AM64x /AM243x Processors Silicon Revision 2.0 SPRUIM2H — MAY 2020 — REVISED OCTOBER 2023
Texas Instruments Families of Products Submit Document Feedback

Copyright © 2023 Texas Instruments Incorporated


---

## Page 2

1} TEXAS
INSTRUMENTS

www.ti.com Processors and Accelerators

Function Function
PRU

Bit Bit
[0] loads current state of MAC_MODE R25 [0] Loads MAC_MODE, if set to “1”, the
[1] loads the current state of MAC mode /status MAC will perform one multiply and

ACC_CARRY accumulate function.
= [1] write “1” clears ACC_CARRY
Lower 32 bit product
Upper 32 bit product

R26
Lower product

R27
Upper product

R26 Auto-sampled_,| 32 operands:

Operand Sampled every clock.
In MAC mode, the product
R29 Auto-sampled of R28*R29 will be added

Operand to the accumulator on
i every XOUT of R25.

MAC.

XFR device ID for
MPY/MAC = 0

icss-022

Figure 6-201. Integration of the PRU and MPY/MAC

The XFR instructions (XIN and XOUT) are used to load/store register contents between the PRU core and the
MAC. These instructions define the start, size, direction of the operation, and device ID. The device ID number
corresponding to the MPY/MAC is shown in Table 6-427.

Table 6-427. MPY/MAC XFR ID
Device ID Function
0 Selects MPY/MAC

The PRU register R25 is mapped to the MAC_CTRL_STATUS register (Table 6-428). The MAC’s current status
(MAC_MODE and ACC_CARRY states) is loaded into R25 using the XIN command on R25. The PRU sets the
MAC’s mode and clears the ACC_CARRY using the XOUT command on R25.

Table 6-428. MAC_CTRL_STATUS Register (R25) Field Descriptions

Bit Field Description
7-2 RESERVED Reserved
Write 1 to clear.
It is sticky.
1 ACC_CARRY It is set 0 cycles after the event.

Oh: 64-bit accumulator carry has not occurred
1h: 64-bit accumulator carry occurred

Oh: Accumulation mode disabled and accumulator is cleared

9 MAC_MODE 1h: Accumulation mode enabled

The two 32-bit operands for the multiplication are loaded into R28 and R29. These registers have a direction
connection with the MAC. Therefore, XOUT is not required to load the MAC. In multiply mode, the MAC samples
these registers every clock cycle. In multiply and accumulate mode, the MAC samples these registers every
XOUT R25[7-0] transaction when MAC_MODE = 1.

The product from the MAC is linked to R26 (lower 32 bits) and R27 (upper 32 bits). The product is loaded into
register R26 and R27 using XIN.

SPRUIM2H — MAY 2020 — REVISED OCTOBER 2023 AM64x /AM243x Processors Silicon Revision 2.0 3325
Submit Document Feedback Texas Instruments Families of Products
Copyright © 2023 Texas Instruments Incorporated


---

## Page 3

i} Texas
INSTRUMENTS

Processors and Accelerators www.ti.com

6.4.6.2.1.1.2 Multiply only mode(default state), MAC_MODE =0

The Figure 6-202 summarizes the MAC operation in "Multiply-only"mode, in which the MAC multiplies the
contents of R28 and R29 on every clock cycle.

R28 R29 R27 R26
32-bit operand 32-bit operand Upper 32-bit product Lower 32-bit operand
Multiply mode

sampled every clock cycle XIN
~sresiema

icss-023

Figure 6-202, MAC Multiply-only Mode- Functional Diagram

6.4.6.2.1.1.2.1 Programming PRU MAC in "Multiply-ONLY”" mode

The following steps are performed by the PRU firmware for multiply-only mode:
1. 1. Enable multiply only MAC_MODE.

a. (a) Clear R25[0] for multiply only mode.
b. (b) Store MAC_MODE to MAC using XOUT instruction with the following parameters:

* Device ID =0

« Base register = R25

+ Size=1
2. 2. Load operands into R28 and R29.
3. 3. Delay at least 1 PRU cycle before executing XIN in step 4.
4. 4. Load product into PRU using XIN instruction on R26, R27.

Repeat steps 2 and 4 for each new operand.
6.4.6.2.1.1.3 Multiply and Accumulate Mode, MAC_MODE = 1

The Figure 6-203 summarizes the MAC operation in "Multiply and Accumulate" mode. On every XOUT
R25_REG[7-0] transaction, the MAC multiplies the contents of R28 and R29, adds the product to its
accumulated result, and sets ACC_CARRY if an accumulation overflow occurs.

R28 R29 R27 R26

32-bit operand 32-bit operand Upper 32-bit product Lower 32-bit operand

‘ Multiply and Accumulate mode
| sampled every XOUT of R25

icss-024
Figure 6-203. MAC Multiply and Accumulate Mode Functional Diagram
6.4.6.2.1.1.3.1 Programming PRU MAC in Multiply and Accumulate Mode
The following steps are performed by the PRU firmware for multiply and accumulate mode:
1. Enable multiply and accumulate MAC_MODE.
Texas Instruments Families of Products Submit Document Feedback

Copyright © 2023 Texas Instruments Incorporated


---

## Page 4

1} TEXAS
INSTRUMENTS

www.ti.com Processors and Accelerators

(a) Set R25[1-0] = 1 for accumulate mode.

(b) Store MAC_MODE to MAC using XOUT instruction with the following parameters:
- Device ID = 0

- Base register = R25

- Size = 1

2. Clear accumulator and carry flag.

(a) Set R25[1-0] = 2 to clear carry flag (R25[1]=1) and clear accumulator (R25[0]=0).
(b) Store accumulator to MAC using XOUT instruction on R25.

3. Load operands into R28 and R29.

4. Multiply and accumulate, XOUT R25[1-0] = 1

Repeat step 4 for each multiply and accumulate using same operands.

Repeat step 3 and 4 for each multiply and accumulate for new operands.

5. Load the accumulated product into R26, R27, and the ACC_CARRY status into R25 using the XIN instruction.
1.

Firmware can load/seed the accumulator by doing XOUT of R27:R26. This will also clear the carry flag.

Note

Steps one and two are required to set the accumulator mode and clear the accumulator and carry flag.

SPRUIM2H — MAY 2020 — REVISED OCTOBER 2023 AM64x /AM243x Processors Silicon Revision 2.0 3327
Submit Document Feedback Texas Instruments Families of Products
Copyright © 2023 Texas Instruments Incorporated


---

## Page 5

1} TEXAS
INSTRUMENTS

Processors and Accelerators www.ti.com

6.4.6.2.2 PRU CRC16/32 Module

Each of the PRUO/PRU1 cores, RTU_PRU0O/RTU_PRU‘1 auxiliary cores and TX_PRUO/TX_PRU‘1 transmit cores
have a designated CRC16/32 module.

In general, CRC adds error detection capability to communication systems. The CRC encoder appends
redundant bits (or CRC bits) to the systematic data message. During reception of the data message, the
received data is also encoded with the same CRC encoder. The 2 sets of CRC bits are compared together. If
they match, there were no transmission errors; and if they don’t match, a transmission error has been detected.

CRC16/32 supports the following features:
+ Supports CRC32:
324.264.234.224. 16 4.124.114.1048 4x74 54 424K Y
* Supports CRC16:
x64 154244
+ Supports CRC16 - CCITT:
x64 124544
+ PRU broadside interface and XFR instructions (XIN, XOUT) allow for importing CRC results and executing
accumulate function

6.4.6.2.2.1 PRU and CRC16/32 Interface

The CRC16/32 module directly connects with the PRU internal registers R25-R29 through use of the PRU
broadside interface and XFR instructions. Table 6-429 shows the functionality of each register.

The XFR instructions (XIN/KOUT/XCHG) are used to load/store register contents between the PRU core and the
CRC16/32 module. These instructions define the start, size, direction of the operation, and device ID. The XFR
device ID number corresponding to the CRC16/32 module is 1.

Table 6-429. CRC Register to PRU Port Mapping
CRC Register RIW Description PRU Mapping

CRC_CFG Ww Always write all 4 bytes. R25
bit [0] CRC32_ENABLE:
0: CRC16 mode is selected. Hardware will auto-set init state of
CRC_SEED to 0000_0000h. However, for CRC16-CCITT software will
need to write the init state of FFFF_FFFFh to CRC_SEED. Note: The
CRC16 result value is only 16-bits.
1: CRC32 mode is selected. Hardware will auto-set init state of
CRC_SEED will be FFFF_FFFFh.
bit [1] CRC_32B_NOT_EMPTY:
0: CRC 32Byte buffer is empty
1: CRC 32Byte buffer is not empty
bit [2] CRC16_MOD_ENABLE:
0: CRC16 (x1®+x15+x2+1 )
4: CRC16-CCITT (x'®+x12+x5+1 ) - Note: CRC32_ENABLE field must =
0.

CRC_DATA_8_BFLIP R 8-bit flip of CRC_DATA. CRC_DATA_8_BFLIP has the same byte order R27
as CRC_DATA|[31-0], but each byte has all bits flipped.
CRC_DATA_32_FLIP[7-0] = CRC_DATA[0-7]
CRC_DATA_32_FLIP[15-8] = CRC_DATA[8-15]
CRC_DATA_32_FLIP[23-16] = CRC_DATA[16-23]
CRC_DATA_32_FLIP[31-24] = CRC_DATA[24-31]
For CRC16, only CRC_DATA_8_BFLIP[15-0] are valid. No auto reset
on CRC_DATA_8_BFLIP read.

CRC_SEED Ww CRC SEED value. R28
Hardware will auto-initialize the CRC_SEED value to 0000_0000h for
CRC16 and FFFF_FFFFh for CRC32. Software only needs to initialize
CRC_SEED if a different default value is required. For CRC16-CCITT,
software needs to update initial CRC_SEED value to FFFF_FFFFh.
Always write 4 bytes.
Note: Reading the CRC_DATA register will reset the CRC value to the
CRC_SEED state.

3328 AM64x /AM243x Processors Silicon Revision 2.0 SPRUIM2H — MAY 2020 — REVISED OCTOBER 2023
Texas Instruments Families of Products Submit Document Feedback
Copyright © 2023 Texas Instruments Incorporated


---

## Page 6

1} TEXAS

INSTRUMENTS
www.ti.com Processors and Accelerators
Table 6-429. CRC Register to PRU Port Mapping (continued)
CRC Register RW Description PRU Mapping
CRC_DATA_32_BFLIP R Full 32-bit flip of CRC_DATA R28

CRC_DATA_32_BFLIP[0] = CRC_DATA[31] ...
CRC_DATA_32_BFLIP[31] = CRC_DATA|O0]

For CRC16, only CRC_DATA_32_BFLIP[31-16] are valid.
No auto reset on CRC_DATA_32_BFLIP read.

CRC_DATA RW For Write, must use a fixed width throughout the session. The CRC R29
module supports lower 8-bit, or lower 16-bit, or full 32-bit data widths.
For Read, LSB or CRC_DATA|O] is first bit on the wire.
For Read, reset the CRC_DATA back to CRC_SEED state.
Note: Firmware must add 1 to 2 NOPs after the last XOUT to the XIN.
For CRC16, only CRC_DATA[15-0] is valid.

6.4.6.2.2.2 CRC Programming Model
The following steps are performed by the PRU firmware to use the CRC module:
Step1: Configuration (optional)

1. Configure CRC type:
For CRC32 operation, set CRC32_ENABLE using XOUT instruction with the following parameters:
+ Device ID = 1
+ Base register = R25
+ Size=1
2. Update CRC_SEED, if required using XOUT with the following parameters:
+ Device ID = 1
+ Base register = R28
+ Size=1to4

Step 2:

1. Load new CRC data into R29
2. Push CRC data to the CRC16/32 module using XOUT with the following parameters:
* Device ID =1
+ Base register = R29
* Size=1to4
3. 10r2NOPS
4. Load the accumulated CRC result into the PRU using the XIN instruction with the following parameters:
* Device ID = 1
+ Base register = R29
+ Size=4

Repeat Step 2, numbers 1 and 2 for each new CRC data.

Note

When a session starts, the PRU firmware must use the same write data width throughout the session.

6.4.6.2.2.3 PRU and CRC16/32 Interface (R9:R2)

The PRU_ICSSG system implements a new wide 32-Bytes data path. The firmware can perform one XOUT of
32-Bytes, the hardware will feed the CRC16/32 4-Bytes at a time. This will take 20 clock cycles for CRC16 and
12 clock cycles for CRC32 for a 32-Bytes XOUT.

Table 6-430. PRU Register to XFR Mapping
PRU Register XFRID Domain/Function Description
R9:R2 Data 1 Data XOUT Only
1-Byte to 32-Bytes in size

SPRUIM2H — MAY 2020 — REVISED OCTOBER 2023 AM64x /AM243x Processors Silicon Revision 2.0 3329
Submit Document Feedback Texas Instruments Families of Products

Copyright © 2023 Texas Instruments Incorporated
