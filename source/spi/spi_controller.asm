; source/spi/spi_controller.asm
; ICSSG0 PRU0 -- bit-banged SPI controller (CPOL = 0, modes 0 and 1), 19-byte
; frames to the PRU1 edge-polling peripheral. Spec: docs/superpowers/specs/
; 2026-11-04-spi-pru0-pru1-design.md, sections 3 and 5.1.
;
; Configuration: the ">>> spi config" block below holds the scenario default
; (mode 1, 25 MHz SPI CLK at a 200 MHz core, MISO_DELAY 17), so this file
; assembles and runs as it is. pru_io.spi_link.SpiLinkRuntime replaces that
; block with render_header(cfg) when it loads another configuration:
;   SPI_MODE   0 or 1 (mode 1: launch on CLK rising, capture on falling;
;              mode 0: capture on rising, launch on falling, bit 0 valid
;              before the first rising edge)
;   HALF_K     core cycles per CLK half period (>= 3)
;   MISO_DELAY core cycles from each MISO launch edge to PRU0's MISO sample.
;              Not realisable when it lands on a CLK write: MISO_DELAY mod
;              2*HALF_K must not be 0 or HALF_K, and MISO_DELAY / (2*HALF_K)
;              must be <= 6 (spi_link.controller_delay_ok checks this).
; For mode 0 the "launch edge" of bit 0 is the virtual one HALF_K before the
; first rising edge.
;
; Pins (direct GPIO mode): R30 bit 16 CLK, bit 13 MOSI, bit 17 /CS; R31 bit 14
; MISO. R30 is only ever written with those three bits set or clear; all other
; R30 bits stay 0.
;
; Control block in PRU0 DMEM (local 0x0000, see spi_link.py):
;   +0 go u8 (host 1, PRU0 clears after /CS is high again), +1 cmd, +2 addr,
;   +3 status (0 idle, 1 busy, 2 done), +4 frame_count u32,
;   +8 abort_after_bits u16 (0 = full frame; 1..151 = raise /CS after that
;   many bits, then complete as normal).
;   0x0100 C_TX 16 B, 0x0110 C_RX 16 B (MISO bytes 3-18),
;   0x0120 C_RX_HDR 3 B (MISO bytes 0-2).
;
; ---- How the frame is timed -----------------------------------------------
; Nothing between /CS low and /CS high polls, loads or stores. The 152 bits
; are fully unrolled as "units", one per bit, each exactly 2*HALF_K cycles.
; A unit holds three fixed-time events, relative to the launch edge L at 0:
;   L  and r30.w1, r1.w0, r2.w0    launch edge + MOSI   (cycle 0)
;   C  xor r30.b2, r30.b2, r1.b2   capture edge         (cycle HALF_K)
;   S  and r3, r31.b1, 0x40        MISO sample          (cycle MISO_DELAY
;                                  mod 2*HALF_K, of the unit MISO_DELAY /
;                                  (2*HALF_K) units later: pipelined)
; and three free instructions: P (MOSI bit of the next unit into r1.b0 bit 5),
; A/B (shift the sample into a chunk accumulator). Samples land in seven
; 24-unit chunks r10..r16; after /CS is high they are realigned by the
; pipelining depth and stored.
;   HALF_K 3..5: the unit is inline (2*HALF_K instructions, nops as filler).
;   HALF_K >= 6: the unit is P, A, B, jal into a shared skeleton that holds
;   L, S, C and the delay loops (the macro assembler has no nested macros).
; L writes r30.w1 = r1.w0 & 0x0320: r1.b0 bit 5 = MOSI (pin 13), r1.b1
; bit 0 = CLK (pin 16), bit 1 = /CS (pin 17). C toggles r30.b2 by r1.b2.
; P only writes r1.b0 during the frame, so r1.b1/r1.b2 are constants
; (SPI_MODE, 1) until the tail units rewrite them to stop the clock and raise
; /CS exactly 2*HALF_K after the last CLK edge.
;
; Cycle counts below are confirmed by single-stepping: tests/
; test_spi_controller.py measures every CLK half period (== HALF_K for
; HALF_K 3..8, 11, 16, both modes, every sample-position class), tLEAD and
; tLAG (== 2*HALF_K), and the MISO sample point against IdealSpiPeripheral.
;
; ---- Register map ---------------------------------------------------------
; Reg      Use                                         Live in
; r0       0 (DMEM base for lbbo/sbbo)                 everywhere
; r1       b0 MOSI (bit 5), b1 L control, b2 C toggle  frame
; r2       0x0320 L mask (MOSI, CLK, /CS in r30.w1)    everywhere
; r3       latest MISO sample (bit 6)                  frame
; r5-r9    TX frame image bytes 0..19 (r9.b3 = 0);     frame
;          RX frame bytes after the frame              post-frame
; r10-r16  MISO sample chunks 0..6 (24 units each)     frame, post-frame
; r17      delay loop counter                          frame, hold
; r18.w0   skeleton return address (HALF_K >= 6)       frame
; r19      scratch                                     pre/post-frame
; r22.w0   /CS-low word (MOSI bit 0, CLK 0)            pre-frame
; r23-r28  BE words (abort shift, RX realign)          pre/post-frame
; r24-r25  tail control words V151/V152/V153          frame
; r28      entry unit E                                pre-frame
; r29      scratch, entry address                      pre/post-frame
; r30      GPO                                         hardware
; r31      GPI                                         hardware
; ---- Cycle budget (verified by the tests named above) --------------------
; Path              Budget            Verified
; unit (one bit)    2*HALF_K          every half period == HALF_K
; /CS low -> 1st CLK  2*HALF_K        tLEAD == 2*HALF_K
; last CLK -> /CS high 2*HALF_K       tLAG == 2*HALF_K
; /CS high hold     >= 4*HALF_K       tCSH >= 2 tCLK (back-to-back test)

; ---- Configuration (render_header replaces the lines between the markers) --
; >>> spi config
SPI_MODE   .set 1
HALF_K     .set 4
MISO_DELAY .set 17
; <<< spi config

; ---- Constants -------------------------------------------------------------
CTRL_GO       .set 0
CTRL_CMD      .set 1
CTRL_STATUS   .set 3
CTRL_FRAMES   .set 4
CTRL_ABORT    .set 8
C_TX_ADDR     .set 0x0100
C_RX_ADDR     .set 0x0110
C_RX_HDR_ADDR .set 0x0120
ST_BUSY       .set 1
ST_DONE       .set 2
CS_BIT        .set 17
FRAME_BITS    .set 152

TWO_K     .set 2 * HALF_K
S_OFF     .set MISO_DELAY % TWO_K           ; sample cycle within a unit
Q         .set MISO_DELAY / TWO_K           ; pipelining depth in units
S_HALF    .set S_OFF / HALF_K               ; 0: before C, 1: after C
BODY_SEL  .set HALF_K * 16 + S_OFF          ; inline unit variant (HALF_K <= 5)
LEAD      .set HALF_K + SPI_MODE * HALF_K   ; /CS low -> L of unit 0
L_CTRL    .set 256 | SPI_MODE               ; r1.w1: b1 = CLK at L, b2 = 1
HOLD_N    .set 2 * HALF_K                   ; hold loop: 1 + 2*HOLD_N cycles

; Unit class: 0 inline (HALF_K <= 5), 1 skeleton with S before C,
; 2 skeleton with S after C.
.if HALF_K >= 6
CLS .set 1 + S_HALF
.else
CLS .set 0
.endif

; Per class: MP = 1 when B merges the previous unit's sample (stream slot =
; sample + 1), PADX = lead filler, SG1..3 = skeleton gaps.
.if CLS == 0
PADX .set LEAD - 2
SG1 .set 0
SG2 .set 0
SG3 .set 0
.endif
.if CLS == 1
MP .set 0
PADX .set LEAD - HALF_K + 4
SG1 .set HALF_K - 6
SG2 .set S_OFF - 1
SG3 .set HALF_K - 1 - S_OFF
.endif
.if CLS == 2
MP .set 1
PADX .set LEAD - 1
SG1 .set HALF_K - 6
SG2 .set S_OFF - HALF_K - 1
SG3 .set 2 * HALF_K - 1 - S_OFF
.endif

; ---- Unit macro: m_unit xop, xdst, xsrc, xsh, acc ---------------------------
; P = "xop xdst, xsrc, xsh"; acc = chunk register of this unit.
; Inline variants (HALF_K 3..5): L at 0, C at HALF_K, S at S_OFF, then P, A, B
; in the first free slots, nops after. PEAK = 2*HALF_K cycles.
; >>> generated: inline units (tools/gen_spi_controller_units.py --write)
.if BODY_SEL == 49               ; k=3, s=1
MP .set 0
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    lsl  acc, acc, 1                  ; A
    or   acc, acc, r3                 ; B  merge latest sample
.endm
.endif
.if BODY_SEL == 50               ; k=3, s=2
MP .set 0
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    lsl  acc, acc, 1                  ; A
    or   acc, acc, r3                 ; B  merge latest sample
.endm
.endif
.if BODY_SEL == 52               ; k=3, s=4
MP .set 0
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    or   acc, acc, r3                 ; B  merge latest sample
.endm
.endif
.if BODY_SEL == 53               ; k=3, s=5
MP .set 1
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    or   acc, acc, r3                 ; B  merge latest sample
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
.endm
.endif
.if BODY_SEL == 65               ; k=4, s=1
MP .set 0
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    or   acc, acc, r3                 ; B  merge latest sample
    nop
    nop
.endm
.endif
.if BODY_SEL == 66               ; k=4, s=2
MP .set 0
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    lsl  acc, acc, 1                  ; A
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    or   acc, acc, r3                 ; B  merge latest sample
    nop
    nop
.endm
.endif
.if BODY_SEL == 67               ; k=4, s=3
MP .set 0
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    or   acc, acc, r3                 ; B  merge latest sample
    nop
    nop
.endm
.endif
.if BODY_SEL == 69               ; k=4, s=5
MP .set 1
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    or   acc, acc, r3                 ; B  merge latest sample
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    nop
    nop
.endm
.endif
.if BODY_SEL == 70               ; k=4, s=6
MP .set 1
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    or   acc, acc, r3                 ; B  merge latest sample
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    nop
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    nop
.endm
.endif
.if BODY_SEL == 71               ; k=4, s=7
MP .set 1
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    or   acc, acc, r3                 ; B  merge latest sample
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    nop
    nop
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
.endm
.endif
.if BODY_SEL == 81               ; k=5, s=1
MP .set 0
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    or   acc, acc, r3                 ; B  merge latest sample
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    nop
    nop
    nop
    nop
.endm
.endif
.if BODY_SEL == 82               ; k=5, s=2
MP .set 0
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    lsl  acc, acc, 1                  ; A
    or   acc, acc, r3                 ; B  merge latest sample
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    nop
    nop
    nop
    nop
.endm
.endif
.if BODY_SEL == 83               ; k=5, s=3
MP .set 0
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    or   acc, acc, r3                 ; B  merge latest sample
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    nop
    nop
    nop
    nop
.endm
.endif
.if BODY_SEL == 84               ; k=5, s=4
MP .set 1
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    or   acc, acc, r3                 ; B  merge latest sample
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    nop
    nop
    nop
    nop
.endm
.endif
.if BODY_SEL == 86               ; k=5, s=6
MP .set 1
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    or   acc, acc, r3                 ; B  merge latest sample
    nop
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    nop
    nop
    nop
.endm
.endif
.if BODY_SEL == 87               ; k=5, s=7
MP .set 1
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    or   acc, acc, r3                 ; B  merge latest sample
    nop
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    nop
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    nop
    nop
.endm
.endif
.if BODY_SEL == 88               ; k=5, s=8
MP .set 1
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    or   acc, acc, r3                 ; B  merge latest sample
    nop
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    nop
    nop
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
    nop
.endm
.endif
.if BODY_SEL == 89               ; k=5, s=9
MP .set 1
m_unit .macro xop, xdst, xsrc, xsh, acc
    and  r30.w1, r1.w0, r2.w0         ; L  launch edge (+MOSI)
    xop  xdst, xsrc, xsh              ; P  MOSI of the next bit -> r1.b0 bit 5
    lsl  acc, acc, 1                  ; A
    or   acc, acc, r3                 ; B  merge latest sample
    nop
    xor  r30.b2, r30.b2, r1.b2        ; C  capture edge
    nop
    nop
    nop
    and  r3, r31.b1, 0x40             ; S  sample MISO (GPI14 -> bit 6)
.endm
.endif
; <<< generated: inline units

; Skeleton variant (HALF_K >= 6): 4 inline instructions + spi_skel.
.if CLS >= 1
m_unit .macro xop, xdst, xsrc, xsh, acc
    xop  xdst, xsrc, xsh              ; P
    lsl  acc, acc, 1                  ; A
    or   acc, acc, r3                 ; B
    jal  r18.w0, spi_skel             ; skeleton returns 2*HALF_K - 4 later
.endm
.endif

; Skeleton events in time order: class 1 L, S, C; class 2 C, S, L.
.if CLS == 2
m_ev1 .macro
    xor  r30.b2, r30.b2, r1.b2        ; C
.endm
m_ev3 .macro
    and  r30.w1, r1.w0, r2.w0         ; L
.endm
.else
m_ev1 .macro
    and  r30.w1, r1.w0, r2.w0         ; L
.endm
m_ev3 .macro
    xor  r30.b2, r30.b2, r1.b2        ; C
.endm
.endif

; Tail control: PB = 1 when P runs before C in its unit (classes 0 and 2).
PB   .set (CLS - 1) * (CLS - 1)
TCX  .set 2 * SPI_MODE                      ; mode 1 raises /CS on a C slot
V151 .set (PB + (1 - PB) * TCX) * 256       ; L152: CLK 0
V152 .set 2 + PB * TCX * 256                ; L153: /CS high
V153 .set 2
RSH   .set Q + MP                           ; realign: frame bit n = slot n + RSH
RSH_C .set 32 - RSH
ACC6_SH .set 16 - Q                         ; chunk 6 holds Q + 10 units

m_bswap .macro dst, src
    mov  dst.b0, src.b3
    mov  dst.b1, src.b2
    mov  dst.b2, src.b1
    mov  dst.b3, src.b0
.endm

; ===========================================================================
start:
    ; /CS high first (the host also presets it before enabling the drive
    ; mask), then CLK and MOSI low: no /CS-low glitch after a (re)load.
    ldi  r30.w2, 0x0002               ; /CS high, CLK low
    ldi  r30.w0, 0                    ; MOSI low
    ldi  r2, 0x0320
    ldi  r0, 0

idle:                                 ; /CS high, CLK low, MOSI keeps the last bit sent
    lbbo &r19.b0, r0, CTRL_GO, 1
    qbeq idle, r19.b0, 0
    ldi  r19.b0, ST_BUSY
    sbbo &r19.b0, r0, CTRL_STATUS, 1

    ; TX frame image: cmd, addr, idle 0x00, 16 data bytes, pad 0x00
    lbbo &r5.b0, r0, CTRL_CMD, 2
    ldi  r5.b2, 0
    ldi  r29, C_TX_ADDR
    lbbo &r5.b3, r29, 0, 16
    ldi  r9.b3, 0
    ldi  r3, 0
    zero &r10, 28                     ; r10..r16

    ; L word of bit 0 (frame bit 0 = cmd bit 7 -> r1.b0 bit 5)
    lsr  r1.b0, r5.b0, 2
    ldi  r1.w1, L_CTRL
    mov  r22.b0, r1.b0
    ldi  r22.b1, 0

    ; Aborted frame: enter at unit E = 152 - N with the TX bits moved right
    ; by E, so the N bits sent are the frame's first N.
    ldi  r28, 0
    lbbo &r19.w0, r0, CTRL_ABORT, 2
    qbeq spi_entry, r19.w0, 0
    qble spi_entry, r19.w0, FRAME_BITS       ; N >= 152: full frame
    ldi  r28, FRAME_BITS
    sub  r28, r28, r19.w0
    mov  r19, r28
    m_bswap r23, r5
    m_bswap r24, r6
    m_bswap r25, r7
    m_bswap r26, r8
    m_bswap r27, r9
spi_abort_shift:
    lsr  r27, r27, 1
    lsl  r29, r26, 31
    or   r27, r27, r29
    lsr  r26, r26, 1
    lsl  r29, r25, 31
    or   r26, r26, r29
    lsr  r25, r25, 1
    lsl  r29, r24, 31
    or   r25, r25, r29
    lsr  r24, r24, 1
    lsl  r29, r23, 31
    or   r24, r24, r29
    lsr  r23, r23, 1
    sub  r19, r19, 1
    qbne spi_abort_shift, r19, 0
    m_bswap r5, r23
    m_bswap r6, r24
    m_bswap r7, r25
    m_bswap r8, r26
    m_bswap r9, r27

spi_entry:
    ; r29 = address of unit E (units are all the same size)
    ldi  r29, spi_unit0
    ldi  r19, spi_unit1
    sub  r19, r19, r29
spi_entry_mul:
    qbeq spi_entry_done, r28, 0
    add  r29, r29, r19
    sub  r28, r28, 1
    qba  spi_entry_mul
spi_entry_done:
    mov  r18.w0, r29.w0               ; class 1: the skeleton returns into unit E
    ldi  r24.w0, V151
    ldi  r24.w2, V152
    ldi  r25.w0, V153

    ; ---- frame: cycle-exact from here to /CS high --------------------------
    and  r30.w1, r22.w0, r2.w0        ; /CS low (mode 0: MOSI = bit 0)
PADX_N .set (PADX - 1) / 2
PADX_R .set (PADX - 1) % 2
.if PADX == 1
    nop
.endif
.if PADX == 2
    nop
    nop
.endif
.if PADX >= 3
    ldi  r17, PADX_N                  ; 1
spi_lead_wait:
    sub  r17, r17, 1                    ; 2 per pass
    qbne spi_lead_wait, r17, 0
.endif
.if PADX * PADX_R >= 3
    nop
.endif
.if CLS == 0
    jmp  r29                          ; unit E starts with L
.endif
.if CLS == 1
    jmp  spi_skel                     ; L, S, C of unit E, return to unit E
.endif
.if CLS == 2
    and  r30.w1, r1.w0, r2.w0         ; L of unit E
    jmp  r29
.endif

; Units 0..151 send bits 0..151; units 152.. stop the clock, raise /CS and
; take the last pipelined samples (units 152 .. 153 + Q).
; Unit m's arguments (mechanical): P prepares frame bit n = m + 1, i.e. byte
; n / 8 held in r(5 + byte / 4).b(byte % 4), bit b = 7 - n % 8, moved to bit 5
; by "lsr ..., b - 5" (b >= 5) or "lsl ..., 5 - b"; units 151, 152, 153+ load
; the tail control words V151, V152, V153 into r1.w1 instead. acc = r(10 + m / 24).
; >>> generated: unit list (tools/gen_spi_controller_units.py --write)
spi_unit0:
    m_unit lsr, r1.b0, r5.b0, 1, r10
spi_unit1:
    m_unit lsr, r1.b0, r5.b0, 0, r10
    m_unit lsl, r1.b0, r5.b0, 1, r10
    m_unit lsl, r1.b0, r5.b0, 2, r10
    m_unit lsl, r1.b0, r5.b0, 3, r10
    m_unit lsl, r1.b0, r5.b0, 4, r10
    m_unit lsl, r1.b0, r5.b0, 5, r10
    m_unit lsr, r1.b0, r5.b1, 2, r10
    m_unit lsr, r1.b0, r5.b1, 1, r10
    m_unit lsr, r1.b0, r5.b1, 0, r10
    m_unit lsl, r1.b0, r5.b1, 1, r10
    m_unit lsl, r1.b0, r5.b1, 2, r10
    m_unit lsl, r1.b0, r5.b1, 3, r10
    m_unit lsl, r1.b0, r5.b1, 4, r10
    m_unit lsl, r1.b0, r5.b1, 5, r10
    m_unit lsr, r1.b0, r5.b2, 2, r10
    m_unit lsr, r1.b0, r5.b2, 1, r10
    m_unit lsr, r1.b0, r5.b2, 0, r10
    m_unit lsl, r1.b0, r5.b2, 1, r10
    m_unit lsl, r1.b0, r5.b2, 2, r10
    m_unit lsl, r1.b0, r5.b2, 3, r10
    m_unit lsl, r1.b0, r5.b2, 4, r10
    m_unit lsl, r1.b0, r5.b2, 5, r10
    m_unit lsr, r1.b0, r5.b3, 2, r10
    m_unit lsr, r1.b0, r5.b3, 1, r11
    m_unit lsr, r1.b0, r5.b3, 0, r11
    m_unit lsl, r1.b0, r5.b3, 1, r11
    m_unit lsl, r1.b0, r5.b3, 2, r11
    m_unit lsl, r1.b0, r5.b3, 3, r11
    m_unit lsl, r1.b0, r5.b3, 4, r11
    m_unit lsl, r1.b0, r5.b3, 5, r11
    m_unit lsr, r1.b0, r6.b0, 2, r11
    m_unit lsr, r1.b0, r6.b0, 1, r11
    m_unit lsr, r1.b0, r6.b0, 0, r11
    m_unit lsl, r1.b0, r6.b0, 1, r11
    m_unit lsl, r1.b0, r6.b0, 2, r11
    m_unit lsl, r1.b0, r6.b0, 3, r11
    m_unit lsl, r1.b0, r6.b0, 4, r11
    m_unit lsl, r1.b0, r6.b0, 5, r11
    m_unit lsr, r1.b0, r6.b1, 2, r11
    m_unit lsr, r1.b0, r6.b1, 1, r11
    m_unit lsr, r1.b0, r6.b1, 0, r11
    m_unit lsl, r1.b0, r6.b1, 1, r11
    m_unit lsl, r1.b0, r6.b1, 2, r11
    m_unit lsl, r1.b0, r6.b1, 3, r11
    m_unit lsl, r1.b0, r6.b1, 4, r11
    m_unit lsl, r1.b0, r6.b1, 5, r11
    m_unit lsr, r1.b0, r6.b2, 2, r11
    m_unit lsr, r1.b0, r6.b2, 1, r12
    m_unit lsr, r1.b0, r6.b2, 0, r12
    m_unit lsl, r1.b0, r6.b2, 1, r12
    m_unit lsl, r1.b0, r6.b2, 2, r12
    m_unit lsl, r1.b0, r6.b2, 3, r12
    m_unit lsl, r1.b0, r6.b2, 4, r12
    m_unit lsl, r1.b0, r6.b2, 5, r12
    m_unit lsr, r1.b0, r6.b3, 2, r12
    m_unit lsr, r1.b0, r6.b3, 1, r12
    m_unit lsr, r1.b0, r6.b3, 0, r12
    m_unit lsl, r1.b0, r6.b3, 1, r12
    m_unit lsl, r1.b0, r6.b3, 2, r12
    m_unit lsl, r1.b0, r6.b3, 3, r12
    m_unit lsl, r1.b0, r6.b3, 4, r12
    m_unit lsl, r1.b0, r6.b3, 5, r12
    m_unit lsr, r1.b0, r7.b0, 2, r12
    m_unit lsr, r1.b0, r7.b0, 1, r12
    m_unit lsr, r1.b0, r7.b0, 0, r12
    m_unit lsl, r1.b0, r7.b0, 1, r12
    m_unit lsl, r1.b0, r7.b0, 2, r12
    m_unit lsl, r1.b0, r7.b0, 3, r12
    m_unit lsl, r1.b0, r7.b0, 4, r12
    m_unit lsl, r1.b0, r7.b0, 5, r12
    m_unit lsr, r1.b0, r7.b1, 2, r12
    m_unit lsr, r1.b0, r7.b1, 1, r13
    m_unit lsr, r1.b0, r7.b1, 0, r13
    m_unit lsl, r1.b0, r7.b1, 1, r13
    m_unit lsl, r1.b0, r7.b1, 2, r13
    m_unit lsl, r1.b0, r7.b1, 3, r13
    m_unit lsl, r1.b0, r7.b1, 4, r13
    m_unit lsl, r1.b0, r7.b1, 5, r13
    m_unit lsr, r1.b0, r7.b2, 2, r13
    m_unit lsr, r1.b0, r7.b2, 1, r13
    m_unit lsr, r1.b0, r7.b2, 0, r13
    m_unit lsl, r1.b0, r7.b2, 1, r13
    m_unit lsl, r1.b0, r7.b2, 2, r13
    m_unit lsl, r1.b0, r7.b2, 3, r13
    m_unit lsl, r1.b0, r7.b2, 4, r13
    m_unit lsl, r1.b0, r7.b2, 5, r13
    m_unit lsr, r1.b0, r7.b3, 2, r13
    m_unit lsr, r1.b0, r7.b3, 1, r13
    m_unit lsr, r1.b0, r7.b3, 0, r13
    m_unit lsl, r1.b0, r7.b3, 1, r13
    m_unit lsl, r1.b0, r7.b3, 2, r13
    m_unit lsl, r1.b0, r7.b3, 3, r13
    m_unit lsl, r1.b0, r7.b3, 4, r13
    m_unit lsl, r1.b0, r7.b3, 5, r13
    m_unit lsr, r1.b0, r8.b0, 2, r13
    m_unit lsr, r1.b0, r8.b0, 1, r14
    m_unit lsr, r1.b0, r8.b0, 0, r14
    m_unit lsl, r1.b0, r8.b0, 1, r14
    m_unit lsl, r1.b0, r8.b0, 2, r14
    m_unit lsl, r1.b0, r8.b0, 3, r14
    m_unit lsl, r1.b0, r8.b0, 4, r14
    m_unit lsl, r1.b0, r8.b0, 5, r14
    m_unit lsr, r1.b0, r8.b1, 2, r14
    m_unit lsr, r1.b0, r8.b1, 1, r14
    m_unit lsr, r1.b0, r8.b1, 0, r14
    m_unit lsl, r1.b0, r8.b1, 1, r14
    m_unit lsl, r1.b0, r8.b1, 2, r14
    m_unit lsl, r1.b0, r8.b1, 3, r14
    m_unit lsl, r1.b0, r8.b1, 4, r14
    m_unit lsl, r1.b0, r8.b1, 5, r14
    m_unit lsr, r1.b0, r8.b2, 2, r14
    m_unit lsr, r1.b0, r8.b2, 1, r14
    m_unit lsr, r1.b0, r8.b2, 0, r14
    m_unit lsl, r1.b0, r8.b2, 1, r14
    m_unit lsl, r1.b0, r8.b2, 2, r14
    m_unit lsl, r1.b0, r8.b2, 3, r14
    m_unit lsl, r1.b0, r8.b2, 4, r14
    m_unit lsl, r1.b0, r8.b2, 5, r14
    m_unit lsr, r1.b0, r8.b3, 2, r14
    m_unit lsr, r1.b0, r8.b3, 1, r15
    m_unit lsr, r1.b0, r8.b3, 0, r15
    m_unit lsl, r1.b0, r8.b3, 1, r15
    m_unit lsl, r1.b0, r8.b3, 2, r15
    m_unit lsl, r1.b0, r8.b3, 3, r15
    m_unit lsl, r1.b0, r8.b3, 4, r15
    m_unit lsl, r1.b0, r8.b3, 5, r15
    m_unit lsr, r1.b0, r9.b0, 2, r15
    m_unit lsr, r1.b0, r9.b0, 1, r15
    m_unit lsr, r1.b0, r9.b0, 0, r15
    m_unit lsl, r1.b0, r9.b0, 1, r15
    m_unit lsl, r1.b0, r9.b0, 2, r15
    m_unit lsl, r1.b0, r9.b0, 3, r15
    m_unit lsl, r1.b0, r9.b0, 4, r15
    m_unit lsl, r1.b0, r9.b0, 5, r15
    m_unit lsr, r1.b0, r9.b1, 2, r15
    m_unit lsr, r1.b0, r9.b1, 1, r15
    m_unit lsr, r1.b0, r9.b1, 0, r15
    m_unit lsl, r1.b0, r9.b1, 1, r15
    m_unit lsl, r1.b0, r9.b1, 2, r15
    m_unit lsl, r1.b0, r9.b1, 3, r15
    m_unit lsl, r1.b0, r9.b1, 4, r15
    m_unit lsl, r1.b0, r9.b1, 5, r15
    m_unit lsr, r1.b0, r9.b2, 2, r15
    m_unit lsr, r1.b0, r9.b2, 1, r16
    m_unit lsr, r1.b0, r9.b2, 0, r16
    m_unit lsl, r1.b0, r9.b2, 1, r16
    m_unit lsl, r1.b0, r9.b2, 2, r16
    m_unit lsl, r1.b0, r9.b2, 3, r16
    m_unit lsl, r1.b0, r9.b2, 4, r16
    m_unit lsl, r1.b0, r9.b2, 5, r16
    m_unit or, r1.w1, r24.w0, 0, r16
    m_unit or, r1.w1, r24.w2, 0, r16
    m_unit or, r1.w1, r25.w0, 0, r16
.if Q >= 1
    m_unit or, r1.w1, r25.w0, 0, r16
.endif
.if Q >= 2
    m_unit or, r1.w1, r25.w0, 0, r16
.endif
.if Q >= 3
    m_unit or, r1.w1, r25.w0, 0, r16
.endif
.if Q >= 4
    m_unit or, r1.w1, r25.w0, 0, r16
.endif
.if Q >= 5
    m_unit or, r1.w1, r25.w0, 0, r16
.endif
.if Q >= 6
    m_unit or, r1.w1, r25.w0, 0, r16
.endif
; <<< generated: unit list

    ; ---- /CS is high: hold it >= 4*HALF_K cycles ---------------------------
    ldi  r17, HOLD_N
spi_hold:
    sub  r17, r17, 1
    qbne spi_hold, r17, 0

    ; ---- realign MISO samples: chunks -> 24-bit groups at bits 31..8 -------
    lsl  r10, r10, 2
    lsl  r11, r11, 2
    lsl  r12, r12, 2
    lsl  r13, r13, 2
    lsl  r14, r14, 2
    lsl  r15, r15, 2
    lsl  r16, r16, ACC6_SH
    ; big-endian 32-bit words W0..W5 = r23..r28
    lsr  r29, r11, 24
    or   r23, r10, r29
    lsl  r24, r11, 8
    lsr  r29, r12, 16
    or   r24, r24, r29
    lsl  r25, r12, 16
    lsr  r29, r13, 8
    or   r25, r25, r29
    lsr  r29, r15, 24
    or   r26, r14, r29
    lsl  r27, r15, 8
    lsr  r29, r16, 16
    or   r27, r27, r29
    lsl  r28, r16, 16
.if RSH > 0
    lsl  r23, r23, RSH
    lsr  r29, r24, RSH_C
    or   r23, r23, r29
    lsl  r24, r24, RSH
    lsr  r29, r25, RSH_C
    or   r24, r24, r29
    lsl  r25, r25, RSH
    lsr  r29, r26, RSH_C
    or   r25, r25, r29
    lsl  r26, r26, RSH
    lsr  r29, r27, RSH_C
    or   r26, r26, r29
    lsl  r27, r27, RSH
    lsr  r29, r28, RSH_C
    or   r27, r27, r29
.endif
    m_bswap r5, r23
    m_bswap r6, r24
    m_bswap r7, r25
    m_bswap r8, r26
    m_bswap r9, r27
    ldi  r29, C_RX_HDR_ADDR
    sbbo &r5.b0, r29, 0, 3
    ldi  r29, C_RX_ADDR
    sbbo &r5.b3, r29, 0, 16

    lbbo &r19, r0, CTRL_FRAMES, 4
    add  r19, r19, 1
    sbbo &r19, r0, CTRL_FRAMES, 4
    ldi  r19.b0, 0
    sbbo &r19.b0, r0, CTRL_GO, 1
    ldi  r19.b0, ST_DONE
    sbbo &r19.b0, r0, CTRL_STATUS, 1
    jmp  idle                         ; beyond the 10-bit qba range

; ---- Skeleton (HALF_K >= 6; assembled but unused below that) ---------------
; Entered by jal (or the lead's jmp) 4 cycles after the previous event run;
; holds the unit's three events with the gaps between them, returns with
; jmp r18.w0. PEAK = 2*HALF_K - 4 cycles.
spi_skel:
SG1_N .set (SG1 - 1) / 2
SG1_R .set (SG1 - 1) % 2
.if SG1 == 1
    nop
.endif
.if SG1 == 2
    nop
    nop
.endif
.if SG1 >= 3
    ldi  r17, SG1_N                  ; 1
spi_sg1_wait:
    sub  r17, r17, 1                    ; 2 per pass
    qbne spi_sg1_wait, r17, 0
.endif
.if SG1 * SG1_R >= 3
    nop
.endif
    m_ev1
SG2_N .set (SG2 - 1) / 2
SG2_R .set (SG2 - 1) % 2
.if SG2 == 1
    nop
.endif
.if SG2 == 2
    nop
    nop
.endif
.if SG2 >= 3
    ldi  r17, SG2_N                  ; 1
spi_sg2_wait:
    sub  r17, r17, 1                    ; 2 per pass
    qbne spi_sg2_wait, r17, 0
.endif
.if SG2 * SG2_R >= 3
    nop
.endif
    and  r3, r31.b1, 0x40             ; S
SG3_N .set (SG3 - 1) / 2
SG3_R .set (SG3 - 1) % 2
.if SG3 == 1
    nop
.endif
.if SG3 == 2
    nop
    nop
.endif
.if SG3 >= 3
    ldi  r17, SG3_N                  ; 1
spi_sg3_wait:
    sub  r17, r17, 1                    ; 2 per pass
    qbne spi_sg3_wait, r17, 0
.endif
.if SG3 * SG3_R >= 3
    nop
.endif
    m_ev3
    jmp  r18.w0
