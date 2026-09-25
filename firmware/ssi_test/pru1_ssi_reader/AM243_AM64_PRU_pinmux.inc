; +--------------------------------------------------------------------------+**
; |                            ****                                          |**
; |                            ****                                          |**
; |                            ******o***                                    |**
; |                      ********_///_****                                   |**
; |                      ***** /_//_/ ****                                   |**
; |                       ** ** (__/ ****                                    |**
; |                           *********                                      |**
; |                            ****                                          |**
; |                            ***                                           |**
; |                                                                          |**
; |         Copyright (c) 1998-2015 Texas Instruments Incorporated           |**
; |                        ALL RIGHTS RESERVED                               |**
; |                                                                          |**
; | Permission is hereby granted to licensees of Texas Instruments           |**
; | Incorporated (TI) products to use this computer program for the sole     |**
; | purpose of implementing a licensee product based on TI products.         |**
; | No other rights to reproduce, use, or disseminate this computer          |**
; | program, whether in part or in whole, are granted.                       |**
; |                                                                          |**
; | TI makes no representation or warranties with respect to the             |**
; | performance of this computer program, and specifically disclaims         |**
; | any responsibility for any damages, special or consequential,            |**
; | connected with the use of this program.                                  |**
; |                                                                          |**
; +--------------------------------------------------------------------------+**
; *****************************************************************************/
;  file:   AM243_AM64_PRU_pinmux.inc
;
;  brief:  Device specific pinmux defines for direct PRU configuration
;          SDK driver on PRU would consume too much memory
;          This is a trade off between readablility and code size.
;
;  author: Thomas Leyrer
;
;   (C) Copyright 2024, Texas Instruments, Inc
;
    .if ! $defined(__AM243_AM64_PRU_pinmux)
    .define "1", __AM243_AM64_PRU_pinmux

; LOCK UNLOCK defines
PAD_PARTITION0_L0       .set  (0x000f1008)
PAD_PARTITION0_L1       .set  (0x000f100C)
PAD_PARTITION1_L0       .set  (0x000f5008)
PAD_PARTITION1_L1       .set  (0x000f500C)

PAD_KEY0                .set  (0x68EF3490)
PAD_KEY1                .set  (0xD172BC5A)

; Input/Output defines
PRU_GPI                 .set  (0x00040001)
PRU_GPO                 .set  (0x00010000)
IEP_SYNC_OUT            .set  (0x00010002)
IEP_LATCH_IN            .set  (0x00040002)

; ICSS_G0 PRU0
PRG0_PRU0_GPO0          .set  (0x000F4160)
PRG0_PRU0_GPO1          .set  (0x000F4164)
PRG0_PRU0_GPO2          .set  (0x000F4168)
PRG0_PRU0_GPO3          .set  (0x000F416C)
PRG0_PRU0_GPO4          .set  (0x000F4170)
PRG0_PRU0_GPO5          .set  (0x000F4174)
PRG0_PRU0_GPO6          .set  (0x000F4178)
PRG0_PRU0_GPO7          .set  (0x000F417C)
PRG0_PRU0_GPO8          .set  (0x000F4180)
PRG0_PRU0_GPO9          .set  (0x000F4184)
PRG0_PRU0_GPO10         .set  (0x000F4188)
PRG0_PRU0_GPO11         .set  (0x000F418C)
PRG0_PRU0_GPO12         .set  (0x000F4190)
PRG0_PRU0_GPO13         .set  (0x000F4194)
PRG0_PRU0_GPO14         .set  (0x000F4198)
PRG0_PRU0_GPO15         .set  (0x000F419C)
PRG0_PRU0_GPO16         .set  (0x000F41A0)
PRG0_PRU0_GPO17         .set  (0x000F41A4)
PRG0_PRU0_GPO18         .set  (0x000F41A8)
PRG0_PRU0_GPO19         .set  (0x000F41AC)
; ICSS_G0 PRU1
PRG0_PRU1_GPO0          .set  (0x000F41B0)
PRG0_PRU1_GPO1          .set  (0x000F41B4)
PRG0_PRU1_GPO2          .set  (0x000F41B8)
PRG0_PRU1_GPO3          .set  (0x000F41BC)
PRG0_PRU1_GPO4          .set  (0x000F41C0)
PRG0_PRU1_GPO5          .set  (0x000F41C4)
PRG0_PRU1_GPO6          .set  (0x000F41C8)
PRG0_PRU1_GPO7          .set  (0x000F41CC)
PRG0_PRU1_GPO8          .set  (0x000F41D0)
PRG0_PRU1_GPO9          .set  (0x000F41D4)
PRG0_PRU1_GPO10         .set  (0x000F41D8)
PRG0_PRU1_GPO11         .set  (0x000F41DC)
PRG0_PRU1_GPO12         .set  (0x000F41E0)
PRG0_PRU1_GPO13         .set  (0x000F41E4)
PRG0_PRU1_GPO14         .set  (0x000F41E8)
PRG0_PRU1_GPO15         .set  (0x000F41EC)
PRG0_PRU1_GPO16         .set  (0x000F41F0)
PRG0_PRU1_GPO17         .set  (0x000F41F4)
PRG0_PRU1_GPO18         .set  (0x000F41F8)
PRG0_PRU1_GPO19         .set  (0x000F41FC)

; ICSS_G1 PRU0
PRG1_PRU0_GPO0          .set  (0x000F40B8)
PRG1_PRU0_GPO1          .set  (0x000F40BC)
PRG1_PRU0_GPO2          .set  (0x000F40C0)
PRG1_PRU0_GPO3          .set  (0x000F40C4)
PRG1_PRU0_GPO4          .set  (0x000F40C8)
PRG1_PRU0_GPO5          .set  (0x000F40CC)
PRG1_PRU0_GPO6          .set  (0x000F40D0)
PRG1_PRU0_GPO7          .set  (0x000F40D4)
PRG1_PRU0_GPO8          .set  (0x000F40D8)
PRG1_PRU0_GPO9          .set  (0x000F40DC)
PRG1_PRU0_GP10          .set  (0x000F40E0)
PRG1_PRU0_GPO11         .set  (0x000F40E4)
PRG1_PRU0_GPO12         .set  (0x000F40E8)
PRG1_PRU0_GPO13         .set  (0x000F40EC)
PRG1_PRU0_GPO14         .set  (0x000F40F0)
PRG1_PRU0_GPO15         .set  (0x000F40F4)
PRG1_PRU0_GPO16         .set  (0x000F40F8)
PRG1_PRU0_GPO17         .set  (0x000F40FC)
PRG1_PRU0_GPO18         .set  (0x000F4100)
PRG1_PRU0_GPO19         .set  (0x000F4104)

; ICSS_G1 PRU1
PRG1_PRU1_GPO0          .set  (0x000F4108)
PRG1_PRU1_GPO1          .set  (0x000F410C)
PRG1_PRU1_GPO2          .set  (0x000F4110)
PRG1_PRU1_GPO3          .set  (0x000F4114)
PRG1_PRU1_GPO4          .set  (0x000F4118)
PRG1_PRU1_GPO5          .set  (0x000F411C)
PRG1_PRU1_GPO6          .set  (0x000F4120)
PRG1_PRU1_GPO7          .set  (0x000F4124)
PRG1_PRU1_GPO8          .set  (0x000F4128)
PRG1_PRU1_GPO9          .set  (0x000F412C)
PRG1_PRU1_GPO10         .set  (0x000F4130)
PRG1_PRU1_GPO11         .set  (0x000F4134)
PRG1_PRU1_GPO12         .set  (0x000F4138)
PRG1_PRU1_GPO13         .set  (0x000F413C)
PRG1_PRU1_GPO14         .set  (0x000F4140)
PRG1_PRU1_GPO15         .set  (0x000F4144)
PRG1_PRU1_GPO16         .set  (0x000F4148)
PRG1_PRU1_GPO17         .set  (0x000F414C)
PRG1_PRU1_GPO18         .set  (0x000F4150)
PRG1_PRU1_GPO19         .set  (0x000F4154)
