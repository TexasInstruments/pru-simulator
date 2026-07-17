; =============================================================
; Peripheral Interface (3-channel SCU) — 125 Mbit duty-cycle sweep
; =============================================================
; PRU0 channel 0 streams nine bytes back-to-back, continuously, at
; a 125 Mbit/s (8 ns/bit) TX sample-clock rate. Each byte's bit
; pattern is a single pulse (a run of 1s, MSB-first, followed by
; 0s), so the transmitted waveform is nine pulses whose width
; sweeps the duty cycle from 0% to 100% in 12.5% steps:
;
;   byte   binary     ones  duty
;   0x00   00000000   0/8   0.0%
;   0x80   10000000   1/8   12.5%
;   0xC0   11000000   2/8   25.0%
;   0xE0   11100000   3/8   37.5%
;   0xF0   11110000   4/8   50.0%
;   0xF8   11111000   5/8   62.5%
;   0xFC   11111100   6/8   75.0%
;   0xFE   11111110   7/8   87.5%
;   0xFF   11111111   8/8   100.0%
;
; This uses the Peripheral I/F's CONTINUOUS TX mode (tx_frame_size
; = 0), per the AM243x TRM (Table 6-424, "tx_data"): the FIFO is
; 4 bytes (32 bits) deep, transmits MSB first, and software must
; keep it fed — the TRM's guidance is to refill once occupancy
; drops to the 2-byte ("half empty") level. This program issues a
; single TX go for byte 0, then feeds the remaining 8 bytes one at
; a time as R31's tx_fifo_sts0 field (bits [4:2]) reports occupancy
; at or below 2, so the whole 9-byte sweep transmits as one
; continuous, gapless waveform.
;
; Required setup (this file configures it itself, in code):
;   - GPCFG0.PRU_GP_MUX_SEL = 1 (Peripheral mode) — written via SBCO
;     below, per ENDAT_INTERFACE_SPEC.md section 3.
;   - Core/OCP clock = 250 MHz so the channel-0 TX divider (N=2)
;     yields exactly 125 MHz (8 ns/bit). Load this program with
;     ../memory_perif_125mbit_demo.cfg (sets [device] pru_clock_mhz
;     = 250) — the default memory.cfg (200 MHz) cannot reach 125
;     Mbit exactly (see ENDAT_INTERFACE_SPEC.md section 4.4).
;
; Observing the output:
;   The pulses appear on the channel's dedicated serial pin
;   (endat0_out / tx_data_pin), not on GPO — the FIFO/status/data
;   pins are separate from the PRU's R30 parallel output. Enable
;   the "loopback ch0" control in the Peripheral panel to route
;   this channel's TX line into core-1's RX channel 0 and watch
;   the bytes arrive in its RX FIFO, or watch perif0_out in the
;   Signal Graph.
;
; R30 layout : [7:0]=TX data (FIFO push, byte-0 strobe),
;              [17:16]=channel select, [20:19]=clock mode
;              (byte-2 strobe latches channel select + clock mode)
; R31 writes : [18]=TX go (one-shot command strobe)
; R31 reads  : byte-0 bits [4:2]=tx_fifo_sts0 (occupancy 0-4),
;              bit 5=busy (per AM243x TRM Table 6-424)
; =============================================================

GPCFG0_OFF      .set 0x08   ; GPCFG0_REG offset from c4 (ICSS CFG base)
TXCFG_OFF       .set 0xE4   ; EDPRU0TXCFGREGISTER offset from c4

MUX_SEL_BIT     .set 26     ; PR1_PRU0_GP_MUX_SEL field starts at bit 26
TX_CLK_SEL_BIT  .set 4      ; 1 = core/OCP clock source
TX_DIV_BIT      .set 16     ; div_factor field starts at bit 16; value=1 -> N=2
TX_GO_BIT       .set 18
TX_BUSY_BIT     .set 5
FIFO_STS_MASK   .set 0x1C   ; bits [4:2] of R31 byte 0 (tx_fifo_sts0 << 2)
FIFO_HALF_LEVEL .set 0x08   ; occupancy-2 threshold, pre-shifted by the mask

; ---- Enable Peripheral mode: GPCFG0.PRU_GP_MUX_SEL = 1 ----
    ldi   r0, 0
    set   r0, r0, MUX_SEL_BIT
    sbco  &r0, c4, GPCFG0_OFF, 4

; ---- Shared TX clock: core/OCP source, div_factor=1 (N=2) -> 125 MHz ----
    ldi   r0, 0
    set   r0, r0, TX_CLK_SEL_BIT
    set   r0, r0, TX_DIV_BIT
    sbco  &r0, c4, TXCFG_OFF, 4

; ---- Channel 0: continuous mode (tx_frame_size left at its reset value
; of 0 -- no CH0CFG0 write needed), select channel 0 ----
    ldi   r30.b2, 0

; ---- Start the stream: push byte 0, issue TX go ----
    ldi   r30.b0, 0x00          ; 0.0%
    ldi   r0, 0
    ldi   r0.w2, 0x0004         ; r0 = bit 18 (TX go)
    mov   r31, r0

; ---- Feed the remaining 8 bytes as the FIFO drops to half-empty ----
WAIT_1:
    and   r1.b0, r31.b0, FIFO_STS_MASK
    qblt  WAIT_1, r1.b0, FIFO_HALF_LEVEL
    ldi   r30.b0, 0x80          ; 12.5%

WAIT_2:
    and   r1.b0, r31.b0, FIFO_STS_MASK
    qblt  WAIT_2, r1.b0, FIFO_HALF_LEVEL
    ldi   r30.b0, 0xC0          ; 25.0%

WAIT_3:
    and   r1.b0, r31.b0, FIFO_STS_MASK
    qblt  WAIT_3, r1.b0, FIFO_HALF_LEVEL
    ldi   r30.b0, 0xE0          ; 37.5%

WAIT_4:
    and   r1.b0, r31.b0, FIFO_STS_MASK
    qblt  WAIT_4, r1.b0, FIFO_HALF_LEVEL
    ldi   r30.b0, 0xF0          ; 50.0%

WAIT_5:
    and   r1.b0, r31.b0, FIFO_STS_MASK
    qblt  WAIT_5, r1.b0, FIFO_HALF_LEVEL
    ldi   r30.b0, 0xF8          ; 62.5%

WAIT_6:
    and   r1.b0, r31.b0, FIFO_STS_MASK
    qblt  WAIT_6, r1.b0, FIFO_HALF_LEVEL
    ldi   r30.b0, 0xFC          ; 75.0%

WAIT_7:
    and   r1.b0, r31.b0, FIFO_STS_MASK
    qblt  WAIT_7, r1.b0, FIFO_HALF_LEVEL
    ldi   r30.b0, 0xFE          ; 87.5%

WAIT_8:
    and   r1.b0, r31.b0, FIFO_STS_MASK
    qblt  WAIT_8, r1.b0, FIFO_HALF_LEVEL
    ldi   r30.b0, 0xFF          ; 100.0%

; ---- All 9 bytes queued/sent — wait for the stream to fully drain ----
WAIT_DRAIN:
    and   r1.b0, r31.b0, FIFO_STS_MASK
    qbne  WAIT_DRAIN, r1.b0, 0
    qbbs  WAIT_DRAIN, r31, TX_BUSY_BIT

    halt
