; source/spi/spi_peripheral.asm
; ICSSG0 PRU1 -- bit-banged SPI peripheral (CPOL = 0, modes 0 and 1) for the
; 19-byte frames of the PRU0 controller. Spec: docs/superpowers/specs/
; 2026-11-04-spi-pru0-pru1-design.md, sections 3 and 5.2.
;
; Configuration: the ">>> spi config" block below holds the scenario default
; (SPI_MODE 1), so this file assembles and runs as it is.
; pru_io.spi_link.SpiLinkRuntime.load_peripheral replaces that block with
; render_header(cfg, role="peripheral"). That header sets only:
;   SPI_MODE   0 or 1 (mode 1: MISO set on CLK rising, MOSI captured on
;              falling; mode 0: captured on rising, set on falling, bit 0
;              valid before the first rising edge)
; There is no rate, half period or delay constant. PRU1 is a real
; edge-polling peripheral: every bit waits for its CLK edges, and nothing is
; timed from /CS. The same code works with any controller that meets the
; timing (test_peripheral_is_edge_polling runs one assembly at two rates).
;
; Pins (direct GPIO mode): R31 bit 1 CLK, bit 2 MOSI, bit 6 /CS; R30 bit 11
; MISO. The pru1 GPO drive mask holds only bit 11, so the other R30 bits that
; the MISO write sets (see below) never reach a pin. On silicon only GPO11 is
; pinmuxed to the PRU for this design.
;
; PRU1 DMEM (local addresses, c24 = 0; global = local + 0x2000):
;   0x0000 P_STATUS: +0 frames u32 (completed frames, valid or not),
;          +4 errors u32 (completed frames with an unknown command or an
;          address > 0x7F), +8 aborts u32 (/CS high before bit 152),
;          +12 last_cmd u8, +13 last_addr u8 (last completed frame)
;   0x07F0 ZERO_BLK: 16 zero bytes, MISO source for frames that do not read
;   0x0800 P_TX: 128 blocks of 16 B, read source (READ, R/W)
;   0x1000 P_RX: 128 blocks of 16 B, write target (WRITE, R/W); written
;          only after /CS goes high at the end of a complete frame
;   0x1800 RX_DUMMY: 16 B, where the commit store goes for frames that
;          must not write (READ, unknown command, bad address)
;
; ---- How a bit is handled -------------------------------------------------
; Every bit has one launch-edge poll (MISO) and one capture-edge poll (MOSI),
; fully unrolled (tools/gen_spi_peripheral.py generates the frame bodies).
; - One R31 read gives clock and data: the capture poll is
;     and  SNAP, r31.b0, 0x46      ; CLK | MOSI | /CS in one masked read
;     qbbs/qbbc ... SNAP, 1        ; branch on the CLK state of that read
;   and the read that shows the edge also holds MOSI (stable since the
;   launch edge, half a period earlier) and /CS. SNAP is a byte field of one
;   of four snapshot registers, so a bit costs no extra instruction to keep
;   MOSI. The eight snapshots of a byte are packed into the byte later
;   (7 instructions, "gather"), in the gaps of the next byte.
; - MISO is one R30 write: `lsl r30.w0, TXbyte, 4 + i` puts bit 7-i of the
;   TX byte on bit 11 (GPO11). Bits 0..10 and 12..15 of R30 get other TX
;   bits; the pru1 drive mask (1 << 11) keeps them off the pins.
; - /CS is checked at every bit: the mode 1 launch poll and the mode 0
;   capture poll exit to frame_abort when /CS is high. Those are the polls
;   that wait for CLK to rise. The polls that wait for CLK to fall (mode 1
;   capture, mode 0 launch) do not look at /CS: a controller that raises /CS
;   with CLK high is outside CPOL = 0, and PRU1 then sees the abort at the
;   next rising-edge poll after CLK has gone low. If CLK stayed high for good,
;   PRU1 would stay in that poll.
; - Deviation from spec 5.2 "sample from the previous edge": PRU1 does not
;   time its MOSI sample from the edge before; it samples at the capture
;   edge itself, in the snapshot that shows that edge. That costs two polls
;   per bit (launch and capture) instead of about one. Timing the sample
;   from the launch edge would still need a poll of the other CLK level to
;   re-arm for the next edge without counting cycles, so it saves no poll.
;   It would also take MOSI a fixed number of cycles after an edge whose
;   MOSI change may be skewed on silicon (up to 2 ns GPO-GPO, PRDO1), and
;   that fixed number would have to fit the shortest half period of any
;   controller. The capture-edge snapshot has neither problem: MOSI has been
;   stable for half a period when the capture edge is seen.
; - The idle byte (byte 2) does not keep MOSI. Its polls are shorter and its
;   gaps hold the address gather, the TX pointer and the TX fetch (4 x lbbo
;   4 B, each needed before its first MISO bit).
;
; Poll forms (exit = cycles from the R31 read that shows the edge to the next
; instruction; period = loop length while waiting):
;   mode 1 launch  and/qbeq/qbeq/jmp, waits CLK=1 or /CS=1   exit 2, period 3
;   mode 1 capture and/qbbs (snapshot)                       exit 2, period 2
;   mode 1 capture, idle byte  qbbs r31                      exit 1, period 1
;   mode 0 capture and/qbbs/qbbc/jmp (snapshot), /CS=1 abort exit 2, period 3
;   mode 0 capture, idle byte  qbbs/qbbc/jmp on r31          exit 1, period 2
;   mode 0 launch  qbbs r31                                  exit 1, period 1
; Per bit, without gap work (cycles, confirmed by single-stepping in
; tests/test_spi_peripheral.py::test_per_bit_cycles):
;   mode 1: data 5 (2 + MISO 1 + 2), cmd/addr 4, idle 3
;   mode 0: data 4 (2 + 1 + MISO 1), cmd/addr 3, idle 2 (3 for bit 23,
;           which drives data bit 24)
; With gap work, a data byte costs 47 cycles in mode 1 and 40 in mode 0
; (mode 0 adds one xor per byte: CLK=1 in its snapshot inverts the gather),
; and no byte of either mode costs more than 48 (8 bits at k=3). The idle-byte TX fetch is 4 x lbbo 4 B = 12 cycles
; (3 each). The generator places the gap work so that a CLK half period of
; 3 core cycles still leaves every level seen (see its docstring);
; test_fastest_rate_all_patterns passes at 50 MHz (300 MHz core, k=3).
;
; ---- Register map ---------------------------------------------------------
; Reg      Use                                         Live in
; r1.b0    mode 1 launch poll (CLK | /CS)              frame
; r2,r3    MOSI snapshots, even bytes (a, b)           frame
; r4,r5    MOSI snapshots, odd bytes (a, b)            frame
; r6,r7    gather scratch                              frame
; r10-r13  TX block (16 B), MISO source                frame
; r14-r17  RX block (16 B), gathered MOSI data bytes   frame, commit
; r18      b0 cmd, b1 addr                             frame, commit
; r19      TX pointer                                  frame
; r20-r23  status image: frames, errors, aborts,       everywhere
;          b0 last_cmd / b1 last_addr
; r24      read mask (0xFFFFFFFF if cmd reads, else 0) frame
; r25      decode scratch                              frame
; r26      0x0800 (P_TX)                               everywhere
; r27      ZERO_BLK (0x07F0)                           everywhere
; r0       RX_DUMMY (0x1800)                           everywhere
; r28      RX commit pointer (P_RX block or RX_DUMMY)  frame, commit
; r29      error flag (1: unknown cmd or bad address)  frame, commit
; r30      GPO (MISO = bit 11)                         hardware
; r31      GPI                                         hardware

; ---- Configuration (render_header replaces the lines between the markers) --
; >>> spi config
SPI_MODE   .set 1
; <<< spi config

; ---- Constants -------------------------------------------------------------
P_TX_LOC   .set 0x0800
ZERO_BLK   .set 0x07F0
RX_DUMMY   .set 0x1800
CS_BIT     .set 6

start:
    ldi  r30, 0                        ; MISO low
    ldi  r20, 0
    ldi  r21, 0
    ldi  r22, 0
    ldi  r23, 0
    sbco &r20, c24, 0, 16              ; clear P_STATUS
    ldi  r10, 0
    ldi  r11, 0
    ldi  r12, 0
    ldi  r13, 0
    ldi  r27, ZERO_BLK
    sbco &r10, c24, r27, 16            ; ZERO_BLK = 16 x 0x00
    ldi  r26, P_TX_LOC
    ldi  r0, RX_DUMMY
    jmp  frame_wait

; ---- End of a complete frame (152 bits seen) -------------------------------
; Waits for /CS high, then commits the RX block (to RX_DUMMY when the frame
; must not write) and the counters. r28 (RX pointer) and r29 (error flag)
; were computed during the frame. Single-stepped (test_fetch_and_commit_cycles):
; 10 cycles from the R31 read that sees /CS high to the first /CS check in
; frame_idle, in both modes.
frame_end:
    qbbc frame_end, r31, CS_BIT        ; wait /CS high
    ldi  r30, 0                        ; MISO idle low
    add  r20, r20, 1                   ; frames
    add  r21, r21, r29                 ; errors
    mov  r23.w0, r18.w0                ; last_cmd, last_addr
    sbco &r14, c24, r28, 16            ; RX block (or RX_DUMMY)
; Status store, one word at a time while /CS stays high. If /CS falls
; first, the next frame starts at once (at most 3 cycles late) and repeats
; the four stores in its first gaps (generated ops st0..st3), so no frame is
; lost and the status still lands. r20-r23 only change here and in
; frame_abort, so the repeat is idempotent.
frame_idle:
    qbbc frame_go, r31, CS_BIT
    sbco &r20, c24, 0, 4               ; frames
    qbbc frame_go, r31, CS_BIT
    sbco &r21, c24, 4, 4               ; errors
    qbbc frame_go, r31, CS_BIT
    sbco &r22, c24, 8, 4               ; aborts
    qbbc frame_go, r31, CS_BIT
    sbco &r23, c24, 12, 2              ; last_cmd, last_addr
    qba  frame_arm

; ---- /CS high before the end of the frame ----------------------------------
; Partial data is dropped (RX not committed), aborts + 1.
frame_abort:
    ldi  r30, 0                        ; MISO idle low
    add  r22, r22, 1
    qba  frame_idle

; Wait for /CS idle high first (start-up only), so a /CS that is low at
; start-up is not taken as a frame. After a frame or an abort /CS has been
; seen high already, so those paths go to frame_arm (or straight to
; frame_go when /CS fell during frame_idle).
frame_wait:
    qbbc frame_wait, r31, CS_BIT
frame_arm:
    qbbs frame_arm, r31, CS_BIT        ; /CS falling edge: frame start
frame_go:

.if SPI_MODE == 1
; >>> generated: frame mode 1
m1_l0:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g0, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l0, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g0:
m1_c0:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c0, r3.b3, 1       ; CLK high: wait (capture = falling)
    sbco &r20, c24, 0, 4            ; status of the previous frame (1/4)
m1_l1:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g1, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l1, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g1:
m1_c1:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c1, r2.b3, 1       ; CLK high: wait (capture = falling)
    sbco &r21, c24, 4, 4            ; status of the previous frame (2/4)
m1_l2:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g2, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l2, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g2:
m1_c2:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c2, r3.b1, 1       ; CLK high: wait (capture = falling)
    sbco &r22, c24, 8, 4            ; status of the previous frame (3/4)
m1_l3:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g3, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l3, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g3:
m1_c3:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c3, r2.b1, 1       ; CLK high: wait (capture = falling)
    sbco &r23, c24, 12, 2           ; status of the previous frame (4/4)
m1_l4:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g4, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l4, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g4:
m1_c4:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c4, r3.b2, 1       ; CLK high: wait (capture = falling)
m1_l5:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g5, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l5, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g5:
m1_c5:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c5, r2.b2, 1       ; CLK high: wait (capture = falling)
m1_l6:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g6, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l6, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g6:
m1_c6:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c6, r3.b0, 1       ; CLK high: wait (capture = falling)
m1_l7:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g7, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l7, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g7:
m1_c7:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c7, r2.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r3, 1                    ; gather byte 0 (1/7)
    xor  r6, r6, r2
m1_l8:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g8, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l8, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g8:
    lsr  r7, r6, 14
m1_c8:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c8, r5.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l9:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g9, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l9, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g9:
    lsr  r7, r6, 4
m1_c9:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c9, r4.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l10:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g10, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l10, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g10:
    lsr  r18.b0, r6, 2           ; byte 0 done
m1_c10:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c10, r5.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r25, r18.b0, 1                ; cmd >> 1: 1 for READ, R/W
m1_l11:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g11, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l11, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g11:
    xor  r25, r25, 1                   ; 0 iff the command reads
m1_c11:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c11, r4.b1, 1       ; CLK high: wait (capture = falling)
    min  r25, r25, 1
m1_l12:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g12, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l12, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g12:
    sub  r24, r25, 1                   ; M = 0xFFFFFFFF (read) or 0
m1_c12:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c12, r5.b2, 1       ; CLK high: wait (capture = falling)
    sub  r29.b0, r18.b0, 1             ; cmd - 1 (8 bit): 0..2 for cmd 1..3
m1_l13:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g13, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l13, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g13:
    add  r29, r29.b0, 1
m1_c13:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c13, r4.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r29, r29, 2                   ; 0 iff cmd is 1..3
m1_l14:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g14, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l14, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g14:
m1_c14:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c14, r5.b0, 1       ; CLK high: wait (capture = falling)
m1_l15:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g15, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l15, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g15:
m1_c15:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c15, r4.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r5, 1                    ; gather byte 1 (1/7)
    xor  r6, r6, r4
m1_l16:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g16, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l16, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g16:
    lsr  r7, r6, 14
m1_c16:
    qbbs m1_c16, r31, 1              ; idle byte: wait CLK low
    xor  r6, r6, r7
    lsr  r7, r6, 4
m1_l17:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g17, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l17, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g17:
    xor  r6, r6, r7
m1_c17:
    qbbs m1_c17, r31, 1              ; idle byte: wait CLK low
    lsr  r18.b1, r6, 2           ; byte 1 done
    lsl  r19, r18.b1, 4                ; addr * 16
m1_l18:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g18, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l18, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g18:
    xor  r19, r19, r26                 ; ^0x800: addr<0x80 -> P_TX block, else <0x800
m1_c18:
    qbbs m1_c18, r31, 1              ; idle byte: wait CLK low
    and  r19, r19, r24                 ; no read command -> 0
    max  r19, r19, r27                 ; below ZERO_BLK -> ZERO_BLK
m1_l19:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g19, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l19, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g19:
m1_c19:
    qbbs m1_c19, r31, 1              ; idle byte: wait CLK low
    lbbo &r10, r19, 0, 4             ; TX bytes 0..3 (fetch 1/4)
m1_l20:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g20, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l20, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g20:
m1_c20:
    qbbs m1_c20, r31, 1              ; idle byte: wait CLK low
    lbbo &r11, r19, 4, 4             ; TX bytes 4..7 (fetch 2/4)
m1_l21:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g21, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l21, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g21:
m1_c21:
    qbbs m1_c21, r31, 1              ; idle byte: wait CLK low
    lbbo &r12, r19, 8, 4             ; TX bytes 8..11 (fetch 3/4)
m1_l22:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g22, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l22, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g22:
m1_c22:
    qbbs m1_c22, r31, 1              ; idle byte: wait CLK low
    lbbo &r13, r19, 12, 4            ; TX bytes 12..15 (fetch 4/4)
m1_l23:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g23, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l23, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g23:
    lsr  r25, r18.b1, 7                ; 1 iff addr > 0x7F
m1_c23:
    qbbs m1_c23, r31, 1              ; idle byte: wait CLK low
    or   r29, r29, r25
    min  r29, r29, 1                   ; e = 1: unknown cmd or bad addr
m1_l24:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g24, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l24, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g24:
    lsl  r30.w0, r10.b0, 4         ; MISO bit 24: data bit 7 -> GPO11 (one R30 write)
m1_c24:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c24, r5.b3, 1       ; CLK high: wait (capture = falling)
    xor  r25, r29, 1
m1_l25:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g25, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l25, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g25:
    lsl  r30.w0, r10.b0, 5         ; MISO bit 25: data bit 6 -> GPO11 (one R30 write)
m1_c25:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c25, r4.b3, 1       ; CLK high: wait (capture = falling)
    and  r25, r25, r18.b0              ; 1 iff valid and cmd has W (bit 0)
m1_l26:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g26, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l26, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g26:
    lsl  r30.w0, r10.b0, 6         ; MISO bit 26: data bit 5 -> GPO11 (one R30 write)
m1_c26:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c26, r5.b1, 1       ; CLK high: wait (capture = falling)
    sub  r25, r25, 1                   ; 0 (write) or 0xFFFFFFFF
m1_l27:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g27, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l27, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g27:
    lsl  r30.w0, r10.b0, 7         ; MISO bit 27: data bit 4 -> GPO11 (one R30 write)
m1_c27:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c27, r4.b1, 1       ; CLK high: wait (capture = falling)
    lsl  r28, r18.b1, 4
m1_l28:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g28, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l28, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g28:
    lsl  r30.w0, r10.b0, 8         ; MISO bit 28: data bit 3 -> GPO11 (one R30 write)
m1_c28:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c28, r5.b2, 1       ; CLK high: wait (capture = falling)
    set  r28, r28, 12                  ; P_RX (0x1000) + addr * 16
m1_l29:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g29, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l29, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g29:
    lsl  r30.w0, r10.b0, 9         ; MISO bit 29: data bit 2 -> GPO11 (one R30 write)
m1_c29:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c29, r4.b2, 1       ; CLK high: wait (capture = falling)
    or   r28, r28, r25
m1_l30:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g30, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l30, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g30:
    lsl  r30.w0, r10.b0, 10        ; MISO bit 30: data bit 1 -> GPO11 (one R30 write)
m1_c30:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c30, r5.b0, 1       ; CLK high: wait (capture = falling)
    min  r28, r28, r0                  ; no write -> RX_DUMMY (0x1800)
m1_l31:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g31, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l31, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g31:
    lsl  r30.w0, r10.b0, 11        ; MISO bit 31: data bit 0 -> GPO11 (one R30 write)
m1_c31:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c31, r4.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r5, 1                    ; gather byte 3 (1/7)
m1_l32:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g32, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l32, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g32:
    lsl  r30.w0, r10.b1, 4         ; MISO bit 32: data bit 7 -> GPO11 (one R30 write)
m1_c32:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c32, r3.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r4
m1_l33:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g33, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l33, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g33:
    lsl  r30.w0, r10.b1, 5         ; MISO bit 33: data bit 6 -> GPO11 (one R30 write)
m1_c33:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c33, r2.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l34:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g34, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l34, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g34:
    lsl  r30.w0, r10.b1, 6         ; MISO bit 34: data bit 5 -> GPO11 (one R30 write)
m1_c34:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c34, r3.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l35:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g35, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l35, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g35:
    lsl  r30.w0, r10.b1, 7         ; MISO bit 35: data bit 4 -> GPO11 (one R30 write)
m1_c35:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c35, r2.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l36:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g36, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l36, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g36:
    lsl  r30.w0, r10.b1, 8         ; MISO bit 36: data bit 3 -> GPO11 (one R30 write)
m1_c36:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c36, r3.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l37:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g37, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l37, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g37:
    lsl  r30.w0, r10.b1, 9         ; MISO bit 37: data bit 2 -> GPO11 (one R30 write)
m1_c37:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c37, r2.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r14.b0, r6, 2           ; byte 3 done
m1_l38:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g38, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l38, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g38:
    lsl  r30.w0, r10.b1, 10        ; MISO bit 38: data bit 1 -> GPO11 (one R30 write)
m1_c38:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c38, r3.b0, 1       ; CLK high: wait (capture = falling)
m1_l39:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g39, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l39, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g39:
    lsl  r30.w0, r10.b1, 11        ; MISO bit 39: data bit 0 -> GPO11 (one R30 write)
m1_c39:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c39, r2.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r3, 1                    ; gather byte 4 (1/7)
m1_l40:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g40, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l40, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g40:
    lsl  r30.w0, r10.b2, 4         ; MISO bit 40: data bit 7 -> GPO11 (one R30 write)
m1_c40:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c40, r5.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r2
m1_l41:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g41, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l41, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g41:
    lsl  r30.w0, r10.b2, 5         ; MISO bit 41: data bit 6 -> GPO11 (one R30 write)
m1_c41:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c41, r4.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l42:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g42, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l42, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g42:
    lsl  r30.w0, r10.b2, 6         ; MISO bit 42: data bit 5 -> GPO11 (one R30 write)
m1_c42:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c42, r5.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l43:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g43, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l43, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g43:
    lsl  r30.w0, r10.b2, 7         ; MISO bit 43: data bit 4 -> GPO11 (one R30 write)
m1_c43:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c43, r4.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l44:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g44, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l44, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g44:
    lsl  r30.w0, r10.b2, 8         ; MISO bit 44: data bit 3 -> GPO11 (one R30 write)
m1_c44:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c44, r5.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l45:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g45, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l45, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g45:
    lsl  r30.w0, r10.b2, 9         ; MISO bit 45: data bit 2 -> GPO11 (one R30 write)
m1_c45:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c45, r4.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r14.b1, r6, 2           ; byte 4 done
m1_l46:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g46, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l46, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g46:
    lsl  r30.w0, r10.b2, 10        ; MISO bit 46: data bit 1 -> GPO11 (one R30 write)
m1_c46:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c46, r5.b0, 1       ; CLK high: wait (capture = falling)
m1_l47:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g47, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l47, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g47:
    lsl  r30.w0, r10.b2, 11        ; MISO bit 47: data bit 0 -> GPO11 (one R30 write)
m1_c47:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c47, r4.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r5, 1                    ; gather byte 5 (1/7)
m1_l48:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g48, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l48, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g48:
    lsl  r30.w0, r10.b3, 4         ; MISO bit 48: data bit 7 -> GPO11 (one R30 write)
m1_c48:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c48, r3.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r4
m1_l49:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g49, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l49, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g49:
    lsl  r30.w0, r10.b3, 5         ; MISO bit 49: data bit 6 -> GPO11 (one R30 write)
m1_c49:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c49, r2.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l50:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g50, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l50, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g50:
    lsl  r30.w0, r10.b3, 6         ; MISO bit 50: data bit 5 -> GPO11 (one R30 write)
m1_c50:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c50, r3.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l51:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g51, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l51, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g51:
    lsl  r30.w0, r10.b3, 7         ; MISO bit 51: data bit 4 -> GPO11 (one R30 write)
m1_c51:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c51, r2.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l52:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g52, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l52, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g52:
    lsl  r30.w0, r10.b3, 8         ; MISO bit 52: data bit 3 -> GPO11 (one R30 write)
m1_c52:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c52, r3.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l53:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g53, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l53, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g53:
    lsl  r30.w0, r10.b3, 9         ; MISO bit 53: data bit 2 -> GPO11 (one R30 write)
m1_c53:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c53, r2.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r14.b2, r6, 2           ; byte 5 done
m1_l54:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g54, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l54, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g54:
    lsl  r30.w0, r10.b3, 10        ; MISO bit 54: data bit 1 -> GPO11 (one R30 write)
m1_c54:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c54, r3.b0, 1       ; CLK high: wait (capture = falling)
m1_l55:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g55, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l55, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g55:
    lsl  r30.w0, r10.b3, 11        ; MISO bit 55: data bit 0 -> GPO11 (one R30 write)
m1_c55:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c55, r2.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r3, 1                    ; gather byte 6 (1/7)
m1_l56:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g56, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l56, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g56:
    lsl  r30.w0, r11.b0, 4         ; MISO bit 56: data bit 7 -> GPO11 (one R30 write)
m1_c56:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c56, r5.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r2
m1_l57:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g57, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l57, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g57:
    lsl  r30.w0, r11.b0, 5         ; MISO bit 57: data bit 6 -> GPO11 (one R30 write)
m1_c57:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c57, r4.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l58:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g58, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l58, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g58:
    lsl  r30.w0, r11.b0, 6         ; MISO bit 58: data bit 5 -> GPO11 (one R30 write)
m1_c58:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c58, r5.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l59:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g59, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l59, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g59:
    lsl  r30.w0, r11.b0, 7         ; MISO bit 59: data bit 4 -> GPO11 (one R30 write)
m1_c59:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c59, r4.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l60:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g60, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l60, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g60:
    lsl  r30.w0, r11.b0, 8         ; MISO bit 60: data bit 3 -> GPO11 (one R30 write)
m1_c60:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c60, r5.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l61:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g61, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l61, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g61:
    lsl  r30.w0, r11.b0, 9         ; MISO bit 61: data bit 2 -> GPO11 (one R30 write)
m1_c61:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c61, r4.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r14.b3, r6, 2           ; byte 6 done
m1_l62:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g62, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l62, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g62:
    lsl  r30.w0, r11.b0, 10        ; MISO bit 62: data bit 1 -> GPO11 (one R30 write)
m1_c62:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c62, r5.b0, 1       ; CLK high: wait (capture = falling)
m1_l63:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g63, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l63, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g63:
    lsl  r30.w0, r11.b0, 11        ; MISO bit 63: data bit 0 -> GPO11 (one R30 write)
m1_c63:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c63, r4.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r5, 1                    ; gather byte 7 (1/7)
m1_l64:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g64, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l64, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g64:
    lsl  r30.w0, r11.b1, 4         ; MISO bit 64: data bit 7 -> GPO11 (one R30 write)
m1_c64:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c64, r3.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r4
m1_l65:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g65, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l65, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g65:
    lsl  r30.w0, r11.b1, 5         ; MISO bit 65: data bit 6 -> GPO11 (one R30 write)
m1_c65:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c65, r2.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l66:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g66, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l66, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g66:
    lsl  r30.w0, r11.b1, 6         ; MISO bit 66: data bit 5 -> GPO11 (one R30 write)
m1_c66:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c66, r3.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l67:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g67, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l67, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g67:
    lsl  r30.w0, r11.b1, 7         ; MISO bit 67: data bit 4 -> GPO11 (one R30 write)
m1_c67:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c67, r2.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l68:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g68, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l68, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g68:
    lsl  r30.w0, r11.b1, 8         ; MISO bit 68: data bit 3 -> GPO11 (one R30 write)
m1_c68:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c68, r3.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l69:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g69, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l69, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g69:
    lsl  r30.w0, r11.b1, 9         ; MISO bit 69: data bit 2 -> GPO11 (one R30 write)
m1_c69:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c69, r2.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r15.b0, r6, 2           ; byte 7 done
m1_l70:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g70, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l70, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g70:
    lsl  r30.w0, r11.b1, 10        ; MISO bit 70: data bit 1 -> GPO11 (one R30 write)
m1_c70:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c70, r3.b0, 1       ; CLK high: wait (capture = falling)
m1_l71:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g71, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l71, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g71:
    lsl  r30.w0, r11.b1, 11        ; MISO bit 71: data bit 0 -> GPO11 (one R30 write)
m1_c71:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c71, r2.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r3, 1                    ; gather byte 8 (1/7)
m1_l72:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g72, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l72, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g72:
    lsl  r30.w0, r11.b2, 4         ; MISO bit 72: data bit 7 -> GPO11 (one R30 write)
m1_c72:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c72, r5.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r2
m1_l73:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g73, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l73, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g73:
    lsl  r30.w0, r11.b2, 5         ; MISO bit 73: data bit 6 -> GPO11 (one R30 write)
m1_c73:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c73, r4.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l74:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g74, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l74, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g74:
    lsl  r30.w0, r11.b2, 6         ; MISO bit 74: data bit 5 -> GPO11 (one R30 write)
m1_c74:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c74, r5.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l75:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g75, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l75, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g75:
    lsl  r30.w0, r11.b2, 7         ; MISO bit 75: data bit 4 -> GPO11 (one R30 write)
m1_c75:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c75, r4.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l76:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g76, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l76, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g76:
    lsl  r30.w0, r11.b2, 8         ; MISO bit 76: data bit 3 -> GPO11 (one R30 write)
m1_c76:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c76, r5.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l77:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g77, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l77, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g77:
    lsl  r30.w0, r11.b2, 9         ; MISO bit 77: data bit 2 -> GPO11 (one R30 write)
m1_c77:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c77, r4.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r15.b1, r6, 2           ; byte 8 done
m1_l78:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g78, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l78, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g78:
    lsl  r30.w0, r11.b2, 10        ; MISO bit 78: data bit 1 -> GPO11 (one R30 write)
m1_c78:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c78, r5.b0, 1       ; CLK high: wait (capture = falling)
m1_l79:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g79, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l79, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g79:
    lsl  r30.w0, r11.b2, 11        ; MISO bit 79: data bit 0 -> GPO11 (one R30 write)
m1_c79:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c79, r4.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r5, 1                    ; gather byte 9 (1/7)
m1_l80:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g80, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l80, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g80:
    lsl  r30.w0, r11.b3, 4         ; MISO bit 80: data bit 7 -> GPO11 (one R30 write)
m1_c80:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c80, r3.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r4
m1_l81:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g81, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l81, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g81:
    lsl  r30.w0, r11.b3, 5         ; MISO bit 81: data bit 6 -> GPO11 (one R30 write)
m1_c81:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c81, r2.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l82:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g82, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l82, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g82:
    lsl  r30.w0, r11.b3, 6         ; MISO bit 82: data bit 5 -> GPO11 (one R30 write)
m1_c82:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c82, r3.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l83:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g83, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l83, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g83:
    lsl  r30.w0, r11.b3, 7         ; MISO bit 83: data bit 4 -> GPO11 (one R30 write)
m1_c83:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c83, r2.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l84:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g84, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l84, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g84:
    lsl  r30.w0, r11.b3, 8         ; MISO bit 84: data bit 3 -> GPO11 (one R30 write)
m1_c84:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c84, r3.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l85:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g85, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l85, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g85:
    lsl  r30.w0, r11.b3, 9         ; MISO bit 85: data bit 2 -> GPO11 (one R30 write)
m1_c85:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c85, r2.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r15.b2, r6, 2           ; byte 9 done
m1_l86:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g86, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l86, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g86:
    lsl  r30.w0, r11.b3, 10        ; MISO bit 86: data bit 1 -> GPO11 (one R30 write)
m1_c86:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c86, r3.b0, 1       ; CLK high: wait (capture = falling)
m1_l87:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g87, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l87, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g87:
    lsl  r30.w0, r11.b3, 11        ; MISO bit 87: data bit 0 -> GPO11 (one R30 write)
m1_c87:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c87, r2.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r3, 1                    ; gather byte 10 (1/7)
m1_l88:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g88, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l88, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g88:
    lsl  r30.w0, r12.b0, 4         ; MISO bit 88: data bit 7 -> GPO11 (one R30 write)
m1_c88:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c88, r5.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r2
m1_l89:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g89, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l89, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g89:
    lsl  r30.w0, r12.b0, 5         ; MISO bit 89: data bit 6 -> GPO11 (one R30 write)
m1_c89:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c89, r4.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l90:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g90, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l90, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g90:
    lsl  r30.w0, r12.b0, 6         ; MISO bit 90: data bit 5 -> GPO11 (one R30 write)
m1_c90:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c90, r5.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l91:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g91, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l91, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g91:
    lsl  r30.w0, r12.b0, 7         ; MISO bit 91: data bit 4 -> GPO11 (one R30 write)
m1_c91:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c91, r4.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l92:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g92, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l92, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g92:
    lsl  r30.w0, r12.b0, 8         ; MISO bit 92: data bit 3 -> GPO11 (one R30 write)
m1_c92:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c92, r5.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l93:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g93, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l93, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g93:
    lsl  r30.w0, r12.b0, 9         ; MISO bit 93: data bit 2 -> GPO11 (one R30 write)
m1_c93:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c93, r4.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r15.b3, r6, 2           ; byte 10 done
m1_l94:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g94, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l94, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g94:
    lsl  r30.w0, r12.b0, 10        ; MISO bit 94: data bit 1 -> GPO11 (one R30 write)
m1_c94:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c94, r5.b0, 1       ; CLK high: wait (capture = falling)
m1_l95:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g95, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l95, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g95:
    lsl  r30.w0, r12.b0, 11        ; MISO bit 95: data bit 0 -> GPO11 (one R30 write)
m1_c95:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c95, r4.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r5, 1                    ; gather byte 11 (1/7)
m1_l96:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g96, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l96, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g96:
    lsl  r30.w0, r12.b1, 4         ; MISO bit 96: data bit 7 -> GPO11 (one R30 write)
m1_c96:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c96, r3.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r4
m1_l97:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g97, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l97, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g97:
    lsl  r30.w0, r12.b1, 5         ; MISO bit 97: data bit 6 -> GPO11 (one R30 write)
m1_c97:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c97, r2.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l98:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g98, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l98, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g98:
    lsl  r30.w0, r12.b1, 6         ; MISO bit 98: data bit 5 -> GPO11 (one R30 write)
m1_c98:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c98, r3.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l99:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g99, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l99, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g99:
    lsl  r30.w0, r12.b1, 7         ; MISO bit 99: data bit 4 -> GPO11 (one R30 write)
m1_c99:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c99, r2.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l100:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g100, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l100, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g100:
    lsl  r30.w0, r12.b1, 8         ; MISO bit 100: data bit 3 -> GPO11 (one R30 write)
m1_c100:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c100, r3.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l101:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g101, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l101, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g101:
    lsl  r30.w0, r12.b1, 9         ; MISO bit 101: data bit 2 -> GPO11 (one R30 write)
m1_c101:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c101, r2.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r16.b0, r6, 2           ; byte 11 done
m1_l102:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g102, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l102, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g102:
    lsl  r30.w0, r12.b1, 10        ; MISO bit 102: data bit 1 -> GPO11 (one R30 write)
m1_c102:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c102, r3.b0, 1       ; CLK high: wait (capture = falling)
m1_l103:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g103, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l103, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g103:
    lsl  r30.w0, r12.b1, 11        ; MISO bit 103: data bit 0 -> GPO11 (one R30 write)
m1_c103:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c103, r2.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r3, 1                    ; gather byte 12 (1/7)
m1_l104:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g104, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l104, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g104:
    lsl  r30.w0, r12.b2, 4         ; MISO bit 104: data bit 7 -> GPO11 (one R30 write)
m1_c104:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c104, r5.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r2
m1_l105:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g105, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l105, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g105:
    lsl  r30.w0, r12.b2, 5         ; MISO bit 105: data bit 6 -> GPO11 (one R30 write)
m1_c105:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c105, r4.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l106:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g106, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l106, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g106:
    lsl  r30.w0, r12.b2, 6         ; MISO bit 106: data bit 5 -> GPO11 (one R30 write)
m1_c106:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c106, r5.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l107:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g107, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l107, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g107:
    lsl  r30.w0, r12.b2, 7         ; MISO bit 107: data bit 4 -> GPO11 (one R30 write)
m1_c107:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c107, r4.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l108:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g108, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l108, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g108:
    lsl  r30.w0, r12.b2, 8         ; MISO bit 108: data bit 3 -> GPO11 (one R30 write)
m1_c108:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c108, r5.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l109:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g109, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l109, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g109:
    lsl  r30.w0, r12.b2, 9         ; MISO bit 109: data bit 2 -> GPO11 (one R30 write)
m1_c109:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c109, r4.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r16.b1, r6, 2           ; byte 12 done
m1_l110:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g110, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l110, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g110:
    lsl  r30.w0, r12.b2, 10        ; MISO bit 110: data bit 1 -> GPO11 (one R30 write)
m1_c110:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c110, r5.b0, 1       ; CLK high: wait (capture = falling)
m1_l111:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g111, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l111, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g111:
    lsl  r30.w0, r12.b2, 11        ; MISO bit 111: data bit 0 -> GPO11 (one R30 write)
m1_c111:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c111, r4.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r5, 1                    ; gather byte 13 (1/7)
m1_l112:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g112, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l112, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g112:
    lsl  r30.w0, r12.b3, 4         ; MISO bit 112: data bit 7 -> GPO11 (one R30 write)
m1_c112:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c112, r3.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r4
m1_l113:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g113, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l113, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g113:
    lsl  r30.w0, r12.b3, 5         ; MISO bit 113: data bit 6 -> GPO11 (one R30 write)
m1_c113:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c113, r2.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l114:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g114, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l114, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g114:
    lsl  r30.w0, r12.b3, 6         ; MISO bit 114: data bit 5 -> GPO11 (one R30 write)
m1_c114:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c114, r3.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l115:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g115, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l115, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g115:
    lsl  r30.w0, r12.b3, 7         ; MISO bit 115: data bit 4 -> GPO11 (one R30 write)
m1_c115:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c115, r2.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l116:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g116, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l116, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g116:
    lsl  r30.w0, r12.b3, 8         ; MISO bit 116: data bit 3 -> GPO11 (one R30 write)
m1_c116:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c116, r3.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l117:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g117, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l117, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g117:
    lsl  r30.w0, r12.b3, 9         ; MISO bit 117: data bit 2 -> GPO11 (one R30 write)
m1_c117:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c117, r2.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r16.b2, r6, 2           ; byte 13 done
m1_l118:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g118, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l118, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g118:
    lsl  r30.w0, r12.b3, 10        ; MISO bit 118: data bit 1 -> GPO11 (one R30 write)
m1_c118:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c118, r3.b0, 1       ; CLK high: wait (capture = falling)
m1_l119:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g119, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l119, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g119:
    lsl  r30.w0, r12.b3, 11        ; MISO bit 119: data bit 0 -> GPO11 (one R30 write)
m1_c119:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c119, r2.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r3, 1                    ; gather byte 14 (1/7)
m1_l120:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g120, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l120, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g120:
    lsl  r30.w0, r13.b0, 4         ; MISO bit 120: data bit 7 -> GPO11 (one R30 write)
m1_c120:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c120, r5.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r2
m1_l121:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g121, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l121, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g121:
    lsl  r30.w0, r13.b0, 5         ; MISO bit 121: data bit 6 -> GPO11 (one R30 write)
m1_c121:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c121, r4.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l122:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g122, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l122, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g122:
    lsl  r30.w0, r13.b0, 6         ; MISO bit 122: data bit 5 -> GPO11 (one R30 write)
m1_c122:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c122, r5.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l123:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g123, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l123, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g123:
    lsl  r30.w0, r13.b0, 7         ; MISO bit 123: data bit 4 -> GPO11 (one R30 write)
m1_c123:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c123, r4.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l124:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g124, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l124, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g124:
    lsl  r30.w0, r13.b0, 8         ; MISO bit 124: data bit 3 -> GPO11 (one R30 write)
m1_c124:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c124, r5.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l125:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g125, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l125, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g125:
    lsl  r30.w0, r13.b0, 9         ; MISO bit 125: data bit 2 -> GPO11 (one R30 write)
m1_c125:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c125, r4.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r16.b3, r6, 2           ; byte 14 done
m1_l126:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g126, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l126, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g126:
    lsl  r30.w0, r13.b0, 10        ; MISO bit 126: data bit 1 -> GPO11 (one R30 write)
m1_c126:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c126, r5.b0, 1       ; CLK high: wait (capture = falling)
m1_l127:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g127, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l127, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g127:
    lsl  r30.w0, r13.b0, 11        ; MISO bit 127: data bit 0 -> GPO11 (one R30 write)
m1_c127:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c127, r4.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r5, 1                    ; gather byte 15 (1/7)
m1_l128:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g128, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l128, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g128:
    lsl  r30.w0, r13.b1, 4         ; MISO bit 128: data bit 7 -> GPO11 (one R30 write)
m1_c128:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c128, r3.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r4
m1_l129:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g129, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l129, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g129:
    lsl  r30.w0, r13.b1, 5         ; MISO bit 129: data bit 6 -> GPO11 (one R30 write)
m1_c129:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c129, r2.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l130:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g130, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l130, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g130:
    lsl  r30.w0, r13.b1, 6         ; MISO bit 130: data bit 5 -> GPO11 (one R30 write)
m1_c130:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c130, r3.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l131:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g131, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l131, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g131:
    lsl  r30.w0, r13.b1, 7         ; MISO bit 131: data bit 4 -> GPO11 (one R30 write)
m1_c131:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c131, r2.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l132:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g132, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l132, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g132:
    lsl  r30.w0, r13.b1, 8         ; MISO bit 132: data bit 3 -> GPO11 (one R30 write)
m1_c132:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c132, r3.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l133:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g133, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l133, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g133:
    lsl  r30.w0, r13.b1, 9         ; MISO bit 133: data bit 2 -> GPO11 (one R30 write)
m1_c133:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c133, r2.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r17.b0, r6, 2           ; byte 15 done
m1_l134:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g134, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l134, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g134:
    lsl  r30.w0, r13.b1, 10        ; MISO bit 134: data bit 1 -> GPO11 (one R30 write)
m1_c134:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c134, r3.b0, 1       ; CLK high: wait (capture = falling)
m1_l135:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g135, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l135, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g135:
    lsl  r30.w0, r13.b1, 11        ; MISO bit 135: data bit 0 -> GPO11 (one R30 write)
m1_c135:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c135, r2.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r3, 1                    ; gather byte 16 (1/7)
m1_l136:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g136, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l136, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g136:
    lsl  r30.w0, r13.b2, 4         ; MISO bit 136: data bit 7 -> GPO11 (one R30 write)
m1_c136:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c136, r5.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r2
m1_l137:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g137, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l137, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g137:
    lsl  r30.w0, r13.b2, 5         ; MISO bit 137: data bit 6 -> GPO11 (one R30 write)
m1_c137:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c137, r4.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l138:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g138, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l138, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g138:
    lsl  r30.w0, r13.b2, 6         ; MISO bit 138: data bit 5 -> GPO11 (one R30 write)
m1_c138:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c138, r5.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l139:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g139, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l139, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g139:
    lsl  r30.w0, r13.b2, 7         ; MISO bit 139: data bit 4 -> GPO11 (one R30 write)
m1_c139:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c139, r4.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l140:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g140, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l140, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g140:
    lsl  r30.w0, r13.b2, 8         ; MISO bit 140: data bit 3 -> GPO11 (one R30 write)
m1_c140:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c140, r5.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l141:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g141, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l141, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g141:
    lsl  r30.w0, r13.b2, 9         ; MISO bit 141: data bit 2 -> GPO11 (one R30 write)
m1_c141:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c141, r4.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r17.b1, r6, 2           ; byte 16 done
m1_l142:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g142, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l142, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g142:
    lsl  r30.w0, r13.b2, 10        ; MISO bit 142: data bit 1 -> GPO11 (one R30 write)
m1_c142:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c142, r5.b0, 1       ; CLK high: wait (capture = falling)
m1_l143:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g143, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l143, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g143:
    lsl  r30.w0, r13.b2, 11        ; MISO bit 143: data bit 0 -> GPO11 (one R30 write)
m1_c143:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c143, r4.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r5, 1                    ; gather byte 17 (1/7)
m1_l144:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g144, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l144, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g144:
    lsl  r30.w0, r13.b3, 4         ; MISO bit 144: data bit 7 -> GPO11 (one R30 write)
m1_c144:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c144, r3.b3, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r4
m1_l145:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g145, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l145, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g145:
    lsl  r30.w0, r13.b3, 5         ; MISO bit 145: data bit 6 -> GPO11 (one R30 write)
m1_c145:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c145, r2.b3, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 14
m1_l146:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g146, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l146, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g146:
    lsl  r30.w0, r13.b3, 6         ; MISO bit 146: data bit 5 -> GPO11 (one R30 write)
m1_c146:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c146, r3.b1, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l147:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g147, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l147, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g147:
    lsl  r30.w0, r13.b3, 7         ; MISO bit 147: data bit 4 -> GPO11 (one R30 write)
m1_c147:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c147, r2.b1, 1       ; CLK high: wait (capture = falling)
    lsr  r7, r6, 4
m1_l148:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g148, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l148, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g148:
    lsl  r30.w0, r13.b3, 8         ; MISO bit 148: data bit 3 -> GPO11 (one R30 write)
m1_c148:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c148, r3.b2, 1       ; CLK high: wait (capture = falling)
    xor  r6, r6, r7
m1_l149:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g149, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l149, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g149:
    lsl  r30.w0, r13.b3, 9         ; MISO bit 149: data bit 2 -> GPO11 (one R30 write)
m1_c149:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c149, r2.b2, 1       ; CLK high: wait (capture = falling)
    lsr  r17.b2, r6, 2           ; byte 17 done
m1_l150:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g150, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l150, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g150:
    lsl  r30.w0, r13.b3, 10        ; MISO bit 150: data bit 1 -> GPO11 (one R30 write)
m1_c150:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c150, r3.b0, 1       ; CLK high: wait (capture = falling)
m1_l151:
    and  r1.b0, r31.b0, 0x42          ; CLK|/CS
    qbeq m1_g151, r1.b0, 2            ; CLK high, /CS low: launch edge
    qbeq m1_l151, r1.b0, 0            ; CLK low, /CS low: wait
    jmp  frame_abort                   ; /CS high
m1_g151:
    lsl  r30.w0, r13.b3, 11        ; MISO bit 151: data bit 0 -> GPO11 (one R30 write)
m1_c151:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m1_c151, r2.b0, 1       ; CLK high: wait (capture = falling)
    lsl  r6, r3, 1                    ; gather byte 18 (1/7)
; after the last capture edge: remaining gathers
    xor  r6, r6, r2
    lsr  r7, r6, 14
    xor  r6, r6, r7
    lsr  r7, r6, 4
    xor  r6, r6, r7
    lsr  r17.b3, r6, 2           ; byte 18 done
    jmp  frame_end
; <<< generated: frame mode 1
.endif

.if SPI_MODE == 0
; >>> generated: frame mode 0
m0_c0:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g0, r3.b3, 1       ; CLK high: capture edge
    qbbc m0_c0, r3.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g0:
m0_l0:
    qbbs m0_l0, r31, 1              ; wait CLK low (launch = falling)
    sbco &r20, c24, 0, 4            ; status of the previous frame (1/4)
m0_c1:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g1, r2.b3, 1       ; CLK high: capture edge
    qbbc m0_c1, r2.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g1:
m0_l1:
    qbbs m0_l1, r31, 1              ; wait CLK low (launch = falling)
    sbco &r21, c24, 4, 4            ; status of the previous frame (2/4)
m0_c2:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g2, r3.b1, 1       ; CLK high: capture edge
    qbbc m0_c2, r3.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g2:
m0_l2:
    qbbs m0_l2, r31, 1              ; wait CLK low (launch = falling)
    sbco &r22, c24, 8, 4            ; status of the previous frame (3/4)
m0_c3:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g3, r2.b1, 1       ; CLK high: capture edge
    qbbc m0_c3, r2.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g3:
m0_l3:
    qbbs m0_l3, r31, 1              ; wait CLK low (launch = falling)
    sbco &r23, c24, 12, 2           ; status of the previous frame (4/4)
m0_c4:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g4, r3.b2, 1       ; CLK high: capture edge
    qbbc m0_c4, r3.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g4:
m0_l4:
    qbbs m0_l4, r31, 1              ; wait CLK low (launch = falling)
m0_c5:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g5, r2.b2, 1       ; CLK high: capture edge
    qbbc m0_c5, r2.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g5:
m0_l5:
    qbbs m0_l5, r31, 1              ; wait CLK low (launch = falling)
m0_c6:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g6, r3.b0, 1       ; CLK high: capture edge
    qbbc m0_c6, r3.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g6:
m0_l6:
    qbbs m0_l6, r31, 1              ; wait CLK low (launch = falling)
m0_c7:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g7, r2.b0, 1       ; CLK high: capture edge
    qbbc m0_c7, r2.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g7:
    lsl  r6, r3, 1                    ; gather byte 0 (1/7)
m0_l7:
    qbbs m0_l7, r31, 1              ; wait CLK low (launch = falling)
    xor  r6, r6, r2
    lsr  r7, r6, 14
m0_c8:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g8, r5.b3, 1       ; CLK high: capture edge
    qbbc m0_c8, r5.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g8:
    xor  r6, r6, r7
m0_l8:
    qbbs m0_l8, r31, 1              ; wait CLK low (launch = falling)
    lsr  r7, r6, 4
    xor  r6, r6, r7
m0_c9:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g9, r4.b3, 1       ; CLK high: capture edge
    qbbc m0_c9, r4.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g9:
    lsr  r18.b0, r6, 2           ; byte 0 done
m0_l9:
    qbbs m0_l9, r31, 1              ; wait CLK low (launch = falling)
    xor  r18.b0, r18.b0, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
    lsr  r25, r18.b0, 1                ; cmd >> 1: 1 for READ, R/W
m0_c10:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g10, r5.b1, 1       ; CLK high: capture edge
    qbbc m0_c10, r5.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g10:
    xor  r25, r25, 1                   ; 0 iff the command reads
m0_l10:
    qbbs m0_l10, r31, 1              ; wait CLK low (launch = falling)
    min  r25, r25, 1
    sub  r24, r25, 1                   ; M = 0xFFFFFFFF (read) or 0
m0_c11:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g11, r4.b1, 1       ; CLK high: capture edge
    qbbc m0_c11, r4.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g11:
    sub  r29.b0, r18.b0, 1             ; cmd - 1 (8 bit): 0..2 for cmd 1..3
m0_l11:
    qbbs m0_l11, r31, 1              ; wait CLK low (launch = falling)
    add  r29, r29.b0, 1
    lsr  r29, r29, 2                   ; 0 iff cmd is 1..3
m0_c12:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g12, r5.b2, 1       ; CLK high: capture edge
    qbbc m0_c12, r5.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g12:
m0_l12:
    qbbs m0_l12, r31, 1              ; wait CLK low (launch = falling)
m0_c13:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g13, r4.b2, 1       ; CLK high: capture edge
    qbbc m0_c13, r4.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g13:
m0_l13:
    qbbs m0_l13, r31, 1              ; wait CLK low (launch = falling)
m0_c14:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g14, r5.b0, 1       ; CLK high: capture edge
    qbbc m0_c14, r5.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g14:
m0_l14:
    qbbs m0_l14, r31, 1              ; wait CLK low (launch = falling)
m0_c15:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g15, r4.b0, 1       ; CLK high: capture edge
    qbbc m0_c15, r4.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g15:
    lsl  r6, r5, 1                    ; gather byte 1 (1/7)
m0_l15:
    qbbs m0_l15, r31, 1              ; wait CLK low (launch = falling)
    xor  r6, r6, r4
    lsr  r7, r6, 14
m0_c16:
    qbbs m0_g16, r31, 1              ; idle byte: CLK high = capture edge
    qbbc m0_c16, r31, 6              ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g16:
    xor  r6, r6, r7
    lsr  r7, r6, 4
m0_l16:
    qbbs m0_l16, r31, 1              ; wait CLK low (launch = falling)
    xor  r6, r6, r7
    lsr  r18.b1, r6, 2           ; byte 1 done
m0_c17:
    qbbs m0_g17, r31, 1              ; idle byte: CLK high = capture edge
    qbbc m0_c17, r31, 6              ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g17:
    xor  r18.b1, r18.b1, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
    lsl  r19, r18.b1, 4                ; addr * 16
m0_l17:
    qbbs m0_l17, r31, 1              ; wait CLK low (launch = falling)
    xor  r19, r19, r26                 ; ^0x800: addr<0x80 -> P_TX block, else <0x800
    and  r19, r19, r24                 ; no read command -> 0
m0_c18:
    qbbs m0_g18, r31, 1              ; idle byte: CLK high = capture edge
    qbbc m0_c18, r31, 6              ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g18:
    max  r19, r19, r27                 ; below ZERO_BLK -> ZERO_BLK
m0_l18:
    qbbs m0_l18, r31, 1              ; wait CLK low (launch = falling)
    lbbo &r10, r19, 0, 4             ; TX bytes 0..3 (fetch 1/4)
m0_c19:
    qbbs m0_g19, r31, 1              ; idle byte: CLK high = capture edge
    qbbc m0_c19, r31, 6              ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g19:
m0_l19:
    qbbs m0_l19, r31, 1              ; wait CLK low (launch = falling)
    lbbo &r11, r19, 4, 4             ; TX bytes 4..7 (fetch 2/4)
m0_c20:
    qbbs m0_g20, r31, 1              ; idle byte: CLK high = capture edge
    qbbc m0_c20, r31, 6              ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g20:
    lbbo &r12, r19, 8, 4             ; TX bytes 8..11 (fetch 3/4)
m0_l20:
    qbbs m0_l20, r31, 1              ; wait CLK low (launch = falling)
m0_c21:
    qbbs m0_g21, r31, 1              ; idle byte: CLK high = capture edge
    qbbc m0_c21, r31, 6              ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g21:
    lbbo &r13, r19, 12, 4            ; TX bytes 12..15 (fetch 4/4)
m0_l21:
    qbbs m0_l21, r31, 1              ; wait CLK low (launch = falling)
    lsr  r25, r18.b1, 7                ; 1 iff addr > 0x7F
    or   r29, r29, r25
m0_c22:
    qbbs m0_g22, r31, 1              ; idle byte: CLK high = capture edge
    qbbc m0_c22, r31, 6              ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g22:
    min  r29, r29, 1                   ; e = 1: unknown cmd or bad addr
    xor  r25, r29, 1
m0_l22:
    qbbs m0_l22, r31, 1              ; wait CLK low (launch = falling)
    and  r25, r25, r18.b0              ; 1 iff valid and cmd has W (bit 0)
    sub  r25, r25, 1                   ; 0 (write) or 0xFFFFFFFF
m0_c23:
    qbbs m0_g23, r31, 1              ; idle byte: CLK high = capture edge
    qbbc m0_c23, r31, 6              ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g23:
    lsl  r28, r18.b1, 4
    set  r28, r28, 12                  ; P_RX (0x1000) + addr * 16
m0_l23:
    qbbs m0_l23, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b0, 4         ; MISO bit 24: data bit 7 -> GPO11 (one R30 write)
    or   r28, r28, r25
m0_c24:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g24, r5.b3, 1       ; CLK high: capture edge
    qbbc m0_c24, r5.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g24:
    min  r28, r28, r0                  ; no write -> RX_DUMMY (0x1800)
m0_l24:
    qbbs m0_l24, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b0, 5         ; MISO bit 25: data bit 6 -> GPO11 (one R30 write)
m0_c25:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g25, r4.b3, 1       ; CLK high: capture edge
    qbbc m0_c25, r4.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g25:
m0_l25:
    qbbs m0_l25, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b0, 6         ; MISO bit 26: data bit 5 -> GPO11 (one R30 write)
m0_c26:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g26, r5.b1, 1       ; CLK high: capture edge
    qbbc m0_c26, r5.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g26:
m0_l26:
    qbbs m0_l26, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b0, 7         ; MISO bit 27: data bit 4 -> GPO11 (one R30 write)
m0_c27:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g27, r4.b1, 1       ; CLK high: capture edge
    qbbc m0_c27, r4.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g27:
m0_l27:
    qbbs m0_l27, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b0, 8         ; MISO bit 28: data bit 3 -> GPO11 (one R30 write)
m0_c28:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g28, r5.b2, 1       ; CLK high: capture edge
    qbbc m0_c28, r5.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g28:
m0_l28:
    qbbs m0_l28, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b0, 9         ; MISO bit 29: data bit 2 -> GPO11 (one R30 write)
m0_c29:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g29, r4.b2, 1       ; CLK high: capture edge
    qbbc m0_c29, r4.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g29:
m0_l29:
    qbbs m0_l29, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b0, 10        ; MISO bit 30: data bit 1 -> GPO11 (one R30 write)
m0_c30:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g30, r5.b0, 1       ; CLK high: capture edge
    qbbc m0_c30, r5.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g30:
m0_l30:
    qbbs m0_l30, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b0, 11        ; MISO bit 31: data bit 0 -> GPO11 (one R30 write)
m0_c31:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g31, r4.b0, 1       ; CLK high: capture edge
    qbbc m0_c31, r4.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g31:
    lsl  r6, r5, 1                    ; gather byte 3 (1/7)
m0_l31:
    qbbs m0_l31, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b1, 4         ; MISO bit 32: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r4
m0_c32:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g32, r3.b3, 1       ; CLK high: capture edge
    qbbc m0_c32, r3.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g32:
    lsr  r7, r6, 14
m0_l32:
    qbbs m0_l32, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b1, 5         ; MISO bit 33: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c33:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g33, r2.b3, 1       ; CLK high: capture edge
    qbbc m0_c33, r2.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g33:
    lsr  r7, r6, 4
m0_l33:
    qbbs m0_l33, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b1, 6         ; MISO bit 34: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c34:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g34, r3.b1, 1       ; CLK high: capture edge
    qbbc m0_c34, r3.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g34:
    lsr  r14.b0, r6, 2           ; byte 3 done
m0_l34:
    qbbs m0_l34, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b1, 7         ; MISO bit 35: data bit 4 -> GPO11 (one R30 write)
    xor  r14.b0, r14.b0, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c35:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g35, r2.b1, 1       ; CLK high: capture edge
    qbbc m0_c35, r2.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g35:
m0_l35:
    qbbs m0_l35, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b1, 8         ; MISO bit 36: data bit 3 -> GPO11 (one R30 write)
m0_c36:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g36, r3.b2, 1       ; CLK high: capture edge
    qbbc m0_c36, r3.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g36:
m0_l36:
    qbbs m0_l36, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b1, 9         ; MISO bit 37: data bit 2 -> GPO11 (one R30 write)
m0_c37:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g37, r2.b2, 1       ; CLK high: capture edge
    qbbc m0_c37, r2.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g37:
m0_l37:
    qbbs m0_l37, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b1, 10        ; MISO bit 38: data bit 1 -> GPO11 (one R30 write)
m0_c38:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g38, r3.b0, 1       ; CLK high: capture edge
    qbbc m0_c38, r3.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g38:
m0_l38:
    qbbs m0_l38, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b1, 11        ; MISO bit 39: data bit 0 -> GPO11 (one R30 write)
m0_c39:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g39, r2.b0, 1       ; CLK high: capture edge
    qbbc m0_c39, r2.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g39:
    lsl  r6, r3, 1                    ; gather byte 4 (1/7)
m0_l39:
    qbbs m0_l39, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b2, 4         ; MISO bit 40: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r2
m0_c40:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g40, r5.b3, 1       ; CLK high: capture edge
    qbbc m0_c40, r5.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g40:
    lsr  r7, r6, 14
m0_l40:
    qbbs m0_l40, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b2, 5         ; MISO bit 41: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c41:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g41, r4.b3, 1       ; CLK high: capture edge
    qbbc m0_c41, r4.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g41:
    lsr  r7, r6, 4
m0_l41:
    qbbs m0_l41, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b2, 6         ; MISO bit 42: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c42:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g42, r5.b1, 1       ; CLK high: capture edge
    qbbc m0_c42, r5.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g42:
    lsr  r14.b1, r6, 2           ; byte 4 done
m0_l42:
    qbbs m0_l42, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b2, 7         ; MISO bit 43: data bit 4 -> GPO11 (one R30 write)
    xor  r14.b1, r14.b1, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c43:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g43, r4.b1, 1       ; CLK high: capture edge
    qbbc m0_c43, r4.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g43:
m0_l43:
    qbbs m0_l43, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b2, 8         ; MISO bit 44: data bit 3 -> GPO11 (one R30 write)
m0_c44:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g44, r5.b2, 1       ; CLK high: capture edge
    qbbc m0_c44, r5.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g44:
m0_l44:
    qbbs m0_l44, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b2, 9         ; MISO bit 45: data bit 2 -> GPO11 (one R30 write)
m0_c45:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g45, r4.b2, 1       ; CLK high: capture edge
    qbbc m0_c45, r4.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g45:
m0_l45:
    qbbs m0_l45, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b2, 10        ; MISO bit 46: data bit 1 -> GPO11 (one R30 write)
m0_c46:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g46, r5.b0, 1       ; CLK high: capture edge
    qbbc m0_c46, r5.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g46:
m0_l46:
    qbbs m0_l46, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b2, 11        ; MISO bit 47: data bit 0 -> GPO11 (one R30 write)
m0_c47:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g47, r4.b0, 1       ; CLK high: capture edge
    qbbc m0_c47, r4.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g47:
    lsl  r6, r5, 1                    ; gather byte 5 (1/7)
m0_l47:
    qbbs m0_l47, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b3, 4         ; MISO bit 48: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r4
m0_c48:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g48, r3.b3, 1       ; CLK high: capture edge
    qbbc m0_c48, r3.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g48:
    lsr  r7, r6, 14
m0_l48:
    qbbs m0_l48, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b3, 5         ; MISO bit 49: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c49:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g49, r2.b3, 1       ; CLK high: capture edge
    qbbc m0_c49, r2.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g49:
    lsr  r7, r6, 4
m0_l49:
    qbbs m0_l49, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b3, 6         ; MISO bit 50: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c50:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g50, r3.b1, 1       ; CLK high: capture edge
    qbbc m0_c50, r3.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g50:
    lsr  r14.b2, r6, 2           ; byte 5 done
m0_l50:
    qbbs m0_l50, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b3, 7         ; MISO bit 51: data bit 4 -> GPO11 (one R30 write)
    xor  r14.b2, r14.b2, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c51:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g51, r2.b1, 1       ; CLK high: capture edge
    qbbc m0_c51, r2.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g51:
m0_l51:
    qbbs m0_l51, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b3, 8         ; MISO bit 52: data bit 3 -> GPO11 (one R30 write)
m0_c52:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g52, r3.b2, 1       ; CLK high: capture edge
    qbbc m0_c52, r3.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g52:
m0_l52:
    qbbs m0_l52, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b3, 9         ; MISO bit 53: data bit 2 -> GPO11 (one R30 write)
m0_c53:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g53, r2.b2, 1       ; CLK high: capture edge
    qbbc m0_c53, r2.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g53:
m0_l53:
    qbbs m0_l53, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b3, 10        ; MISO bit 54: data bit 1 -> GPO11 (one R30 write)
m0_c54:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g54, r3.b0, 1       ; CLK high: capture edge
    qbbc m0_c54, r3.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g54:
m0_l54:
    qbbs m0_l54, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r10.b3, 11        ; MISO bit 55: data bit 0 -> GPO11 (one R30 write)
m0_c55:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g55, r2.b0, 1       ; CLK high: capture edge
    qbbc m0_c55, r2.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g55:
    lsl  r6, r3, 1                    ; gather byte 6 (1/7)
m0_l55:
    qbbs m0_l55, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b0, 4         ; MISO bit 56: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r2
m0_c56:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g56, r5.b3, 1       ; CLK high: capture edge
    qbbc m0_c56, r5.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g56:
    lsr  r7, r6, 14
m0_l56:
    qbbs m0_l56, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b0, 5         ; MISO bit 57: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c57:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g57, r4.b3, 1       ; CLK high: capture edge
    qbbc m0_c57, r4.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g57:
    lsr  r7, r6, 4
m0_l57:
    qbbs m0_l57, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b0, 6         ; MISO bit 58: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c58:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g58, r5.b1, 1       ; CLK high: capture edge
    qbbc m0_c58, r5.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g58:
    lsr  r14.b3, r6, 2           ; byte 6 done
m0_l58:
    qbbs m0_l58, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b0, 7         ; MISO bit 59: data bit 4 -> GPO11 (one R30 write)
    xor  r14.b3, r14.b3, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c59:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g59, r4.b1, 1       ; CLK high: capture edge
    qbbc m0_c59, r4.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g59:
m0_l59:
    qbbs m0_l59, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b0, 8         ; MISO bit 60: data bit 3 -> GPO11 (one R30 write)
m0_c60:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g60, r5.b2, 1       ; CLK high: capture edge
    qbbc m0_c60, r5.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g60:
m0_l60:
    qbbs m0_l60, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b0, 9         ; MISO bit 61: data bit 2 -> GPO11 (one R30 write)
m0_c61:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g61, r4.b2, 1       ; CLK high: capture edge
    qbbc m0_c61, r4.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g61:
m0_l61:
    qbbs m0_l61, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b0, 10        ; MISO bit 62: data bit 1 -> GPO11 (one R30 write)
m0_c62:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g62, r5.b0, 1       ; CLK high: capture edge
    qbbc m0_c62, r5.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g62:
m0_l62:
    qbbs m0_l62, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b0, 11        ; MISO bit 63: data bit 0 -> GPO11 (one R30 write)
m0_c63:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g63, r4.b0, 1       ; CLK high: capture edge
    qbbc m0_c63, r4.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g63:
    lsl  r6, r5, 1                    ; gather byte 7 (1/7)
m0_l63:
    qbbs m0_l63, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b1, 4         ; MISO bit 64: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r4
m0_c64:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g64, r3.b3, 1       ; CLK high: capture edge
    qbbc m0_c64, r3.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g64:
    lsr  r7, r6, 14
m0_l64:
    qbbs m0_l64, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b1, 5         ; MISO bit 65: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c65:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g65, r2.b3, 1       ; CLK high: capture edge
    qbbc m0_c65, r2.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g65:
    lsr  r7, r6, 4
m0_l65:
    qbbs m0_l65, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b1, 6         ; MISO bit 66: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c66:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g66, r3.b1, 1       ; CLK high: capture edge
    qbbc m0_c66, r3.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g66:
    lsr  r15.b0, r6, 2           ; byte 7 done
m0_l66:
    qbbs m0_l66, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b1, 7         ; MISO bit 67: data bit 4 -> GPO11 (one R30 write)
    xor  r15.b0, r15.b0, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c67:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g67, r2.b1, 1       ; CLK high: capture edge
    qbbc m0_c67, r2.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g67:
m0_l67:
    qbbs m0_l67, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b1, 8         ; MISO bit 68: data bit 3 -> GPO11 (one R30 write)
m0_c68:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g68, r3.b2, 1       ; CLK high: capture edge
    qbbc m0_c68, r3.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g68:
m0_l68:
    qbbs m0_l68, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b1, 9         ; MISO bit 69: data bit 2 -> GPO11 (one R30 write)
m0_c69:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g69, r2.b2, 1       ; CLK high: capture edge
    qbbc m0_c69, r2.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g69:
m0_l69:
    qbbs m0_l69, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b1, 10        ; MISO bit 70: data bit 1 -> GPO11 (one R30 write)
m0_c70:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g70, r3.b0, 1       ; CLK high: capture edge
    qbbc m0_c70, r3.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g70:
m0_l70:
    qbbs m0_l70, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b1, 11        ; MISO bit 71: data bit 0 -> GPO11 (one R30 write)
m0_c71:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g71, r2.b0, 1       ; CLK high: capture edge
    qbbc m0_c71, r2.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g71:
    lsl  r6, r3, 1                    ; gather byte 8 (1/7)
m0_l71:
    qbbs m0_l71, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b2, 4         ; MISO bit 72: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r2
m0_c72:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g72, r5.b3, 1       ; CLK high: capture edge
    qbbc m0_c72, r5.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g72:
    lsr  r7, r6, 14
m0_l72:
    qbbs m0_l72, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b2, 5         ; MISO bit 73: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c73:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g73, r4.b3, 1       ; CLK high: capture edge
    qbbc m0_c73, r4.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g73:
    lsr  r7, r6, 4
m0_l73:
    qbbs m0_l73, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b2, 6         ; MISO bit 74: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c74:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g74, r5.b1, 1       ; CLK high: capture edge
    qbbc m0_c74, r5.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g74:
    lsr  r15.b1, r6, 2           ; byte 8 done
m0_l74:
    qbbs m0_l74, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b2, 7         ; MISO bit 75: data bit 4 -> GPO11 (one R30 write)
    xor  r15.b1, r15.b1, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c75:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g75, r4.b1, 1       ; CLK high: capture edge
    qbbc m0_c75, r4.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g75:
m0_l75:
    qbbs m0_l75, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b2, 8         ; MISO bit 76: data bit 3 -> GPO11 (one R30 write)
m0_c76:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g76, r5.b2, 1       ; CLK high: capture edge
    qbbc m0_c76, r5.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g76:
m0_l76:
    qbbs m0_l76, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b2, 9         ; MISO bit 77: data bit 2 -> GPO11 (one R30 write)
m0_c77:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g77, r4.b2, 1       ; CLK high: capture edge
    qbbc m0_c77, r4.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g77:
m0_l77:
    qbbs m0_l77, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b2, 10        ; MISO bit 78: data bit 1 -> GPO11 (one R30 write)
m0_c78:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g78, r5.b0, 1       ; CLK high: capture edge
    qbbc m0_c78, r5.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g78:
m0_l78:
    qbbs m0_l78, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b2, 11        ; MISO bit 79: data bit 0 -> GPO11 (one R30 write)
m0_c79:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g79, r4.b0, 1       ; CLK high: capture edge
    qbbc m0_c79, r4.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g79:
    lsl  r6, r5, 1                    ; gather byte 9 (1/7)
m0_l79:
    qbbs m0_l79, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b3, 4         ; MISO bit 80: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r4
m0_c80:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g80, r3.b3, 1       ; CLK high: capture edge
    qbbc m0_c80, r3.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g80:
    lsr  r7, r6, 14
m0_l80:
    qbbs m0_l80, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b3, 5         ; MISO bit 81: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c81:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g81, r2.b3, 1       ; CLK high: capture edge
    qbbc m0_c81, r2.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g81:
    lsr  r7, r6, 4
m0_l81:
    qbbs m0_l81, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b3, 6         ; MISO bit 82: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c82:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g82, r3.b1, 1       ; CLK high: capture edge
    qbbc m0_c82, r3.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g82:
    lsr  r15.b2, r6, 2           ; byte 9 done
m0_l82:
    qbbs m0_l82, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b3, 7         ; MISO bit 83: data bit 4 -> GPO11 (one R30 write)
    xor  r15.b2, r15.b2, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c83:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g83, r2.b1, 1       ; CLK high: capture edge
    qbbc m0_c83, r2.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g83:
m0_l83:
    qbbs m0_l83, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b3, 8         ; MISO bit 84: data bit 3 -> GPO11 (one R30 write)
m0_c84:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g84, r3.b2, 1       ; CLK high: capture edge
    qbbc m0_c84, r3.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g84:
m0_l84:
    qbbs m0_l84, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b3, 9         ; MISO bit 85: data bit 2 -> GPO11 (one R30 write)
m0_c85:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g85, r2.b2, 1       ; CLK high: capture edge
    qbbc m0_c85, r2.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g85:
m0_l85:
    qbbs m0_l85, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b3, 10        ; MISO bit 86: data bit 1 -> GPO11 (one R30 write)
m0_c86:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g86, r3.b0, 1       ; CLK high: capture edge
    qbbc m0_c86, r3.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g86:
m0_l86:
    qbbs m0_l86, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r11.b3, 11        ; MISO bit 87: data bit 0 -> GPO11 (one R30 write)
m0_c87:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g87, r2.b0, 1       ; CLK high: capture edge
    qbbc m0_c87, r2.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g87:
    lsl  r6, r3, 1                    ; gather byte 10 (1/7)
m0_l87:
    qbbs m0_l87, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b0, 4         ; MISO bit 88: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r2
m0_c88:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g88, r5.b3, 1       ; CLK high: capture edge
    qbbc m0_c88, r5.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g88:
    lsr  r7, r6, 14
m0_l88:
    qbbs m0_l88, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b0, 5         ; MISO bit 89: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c89:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g89, r4.b3, 1       ; CLK high: capture edge
    qbbc m0_c89, r4.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g89:
    lsr  r7, r6, 4
m0_l89:
    qbbs m0_l89, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b0, 6         ; MISO bit 90: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c90:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g90, r5.b1, 1       ; CLK high: capture edge
    qbbc m0_c90, r5.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g90:
    lsr  r15.b3, r6, 2           ; byte 10 done
m0_l90:
    qbbs m0_l90, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b0, 7         ; MISO bit 91: data bit 4 -> GPO11 (one R30 write)
    xor  r15.b3, r15.b3, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c91:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g91, r4.b1, 1       ; CLK high: capture edge
    qbbc m0_c91, r4.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g91:
m0_l91:
    qbbs m0_l91, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b0, 8         ; MISO bit 92: data bit 3 -> GPO11 (one R30 write)
m0_c92:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g92, r5.b2, 1       ; CLK high: capture edge
    qbbc m0_c92, r5.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g92:
m0_l92:
    qbbs m0_l92, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b0, 9         ; MISO bit 93: data bit 2 -> GPO11 (one R30 write)
m0_c93:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g93, r4.b2, 1       ; CLK high: capture edge
    qbbc m0_c93, r4.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g93:
m0_l93:
    qbbs m0_l93, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b0, 10        ; MISO bit 94: data bit 1 -> GPO11 (one R30 write)
m0_c94:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g94, r5.b0, 1       ; CLK high: capture edge
    qbbc m0_c94, r5.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g94:
m0_l94:
    qbbs m0_l94, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b0, 11        ; MISO bit 95: data bit 0 -> GPO11 (one R30 write)
m0_c95:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g95, r4.b0, 1       ; CLK high: capture edge
    qbbc m0_c95, r4.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g95:
    lsl  r6, r5, 1                    ; gather byte 11 (1/7)
m0_l95:
    qbbs m0_l95, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b1, 4         ; MISO bit 96: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r4
m0_c96:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g96, r3.b3, 1       ; CLK high: capture edge
    qbbc m0_c96, r3.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g96:
    lsr  r7, r6, 14
m0_l96:
    qbbs m0_l96, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b1, 5         ; MISO bit 97: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c97:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g97, r2.b3, 1       ; CLK high: capture edge
    qbbc m0_c97, r2.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g97:
    lsr  r7, r6, 4
m0_l97:
    qbbs m0_l97, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b1, 6         ; MISO bit 98: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c98:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g98, r3.b1, 1       ; CLK high: capture edge
    qbbc m0_c98, r3.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g98:
    lsr  r16.b0, r6, 2           ; byte 11 done
m0_l98:
    qbbs m0_l98, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b1, 7         ; MISO bit 99: data bit 4 -> GPO11 (one R30 write)
    xor  r16.b0, r16.b0, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c99:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g99, r2.b1, 1       ; CLK high: capture edge
    qbbc m0_c99, r2.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g99:
m0_l99:
    qbbs m0_l99, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b1, 8         ; MISO bit 100: data bit 3 -> GPO11 (one R30 write)
m0_c100:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g100, r3.b2, 1       ; CLK high: capture edge
    qbbc m0_c100, r3.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g100:
m0_l100:
    qbbs m0_l100, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b1, 9         ; MISO bit 101: data bit 2 -> GPO11 (one R30 write)
m0_c101:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g101, r2.b2, 1       ; CLK high: capture edge
    qbbc m0_c101, r2.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g101:
m0_l101:
    qbbs m0_l101, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b1, 10        ; MISO bit 102: data bit 1 -> GPO11 (one R30 write)
m0_c102:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g102, r3.b0, 1       ; CLK high: capture edge
    qbbc m0_c102, r3.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g102:
m0_l102:
    qbbs m0_l102, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b1, 11        ; MISO bit 103: data bit 0 -> GPO11 (one R30 write)
m0_c103:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g103, r2.b0, 1       ; CLK high: capture edge
    qbbc m0_c103, r2.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g103:
    lsl  r6, r3, 1                    ; gather byte 12 (1/7)
m0_l103:
    qbbs m0_l103, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b2, 4         ; MISO bit 104: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r2
m0_c104:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g104, r5.b3, 1       ; CLK high: capture edge
    qbbc m0_c104, r5.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g104:
    lsr  r7, r6, 14
m0_l104:
    qbbs m0_l104, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b2, 5         ; MISO bit 105: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c105:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g105, r4.b3, 1       ; CLK high: capture edge
    qbbc m0_c105, r4.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g105:
    lsr  r7, r6, 4
m0_l105:
    qbbs m0_l105, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b2, 6         ; MISO bit 106: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c106:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g106, r5.b1, 1       ; CLK high: capture edge
    qbbc m0_c106, r5.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g106:
    lsr  r16.b1, r6, 2           ; byte 12 done
m0_l106:
    qbbs m0_l106, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b2, 7         ; MISO bit 107: data bit 4 -> GPO11 (one R30 write)
    xor  r16.b1, r16.b1, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c107:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g107, r4.b1, 1       ; CLK high: capture edge
    qbbc m0_c107, r4.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g107:
m0_l107:
    qbbs m0_l107, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b2, 8         ; MISO bit 108: data bit 3 -> GPO11 (one R30 write)
m0_c108:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g108, r5.b2, 1       ; CLK high: capture edge
    qbbc m0_c108, r5.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g108:
m0_l108:
    qbbs m0_l108, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b2, 9         ; MISO bit 109: data bit 2 -> GPO11 (one R30 write)
m0_c109:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g109, r4.b2, 1       ; CLK high: capture edge
    qbbc m0_c109, r4.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g109:
m0_l109:
    qbbs m0_l109, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b2, 10        ; MISO bit 110: data bit 1 -> GPO11 (one R30 write)
m0_c110:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g110, r5.b0, 1       ; CLK high: capture edge
    qbbc m0_c110, r5.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g110:
m0_l110:
    qbbs m0_l110, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b2, 11        ; MISO bit 111: data bit 0 -> GPO11 (one R30 write)
m0_c111:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g111, r4.b0, 1       ; CLK high: capture edge
    qbbc m0_c111, r4.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g111:
    lsl  r6, r5, 1                    ; gather byte 13 (1/7)
m0_l111:
    qbbs m0_l111, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b3, 4         ; MISO bit 112: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r4
m0_c112:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g112, r3.b3, 1       ; CLK high: capture edge
    qbbc m0_c112, r3.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g112:
    lsr  r7, r6, 14
m0_l112:
    qbbs m0_l112, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b3, 5         ; MISO bit 113: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c113:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g113, r2.b3, 1       ; CLK high: capture edge
    qbbc m0_c113, r2.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g113:
    lsr  r7, r6, 4
m0_l113:
    qbbs m0_l113, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b3, 6         ; MISO bit 114: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c114:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g114, r3.b1, 1       ; CLK high: capture edge
    qbbc m0_c114, r3.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g114:
    lsr  r16.b2, r6, 2           ; byte 13 done
m0_l114:
    qbbs m0_l114, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b3, 7         ; MISO bit 115: data bit 4 -> GPO11 (one R30 write)
    xor  r16.b2, r16.b2, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c115:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g115, r2.b1, 1       ; CLK high: capture edge
    qbbc m0_c115, r2.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g115:
m0_l115:
    qbbs m0_l115, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b3, 8         ; MISO bit 116: data bit 3 -> GPO11 (one R30 write)
m0_c116:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g116, r3.b2, 1       ; CLK high: capture edge
    qbbc m0_c116, r3.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g116:
m0_l116:
    qbbs m0_l116, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b3, 9         ; MISO bit 117: data bit 2 -> GPO11 (one R30 write)
m0_c117:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g117, r2.b2, 1       ; CLK high: capture edge
    qbbc m0_c117, r2.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g117:
m0_l117:
    qbbs m0_l117, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b3, 10        ; MISO bit 118: data bit 1 -> GPO11 (one R30 write)
m0_c118:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g118, r3.b0, 1       ; CLK high: capture edge
    qbbc m0_c118, r3.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g118:
m0_l118:
    qbbs m0_l118, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r12.b3, 11        ; MISO bit 119: data bit 0 -> GPO11 (one R30 write)
m0_c119:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g119, r2.b0, 1       ; CLK high: capture edge
    qbbc m0_c119, r2.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g119:
    lsl  r6, r3, 1                    ; gather byte 14 (1/7)
m0_l119:
    qbbs m0_l119, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b0, 4         ; MISO bit 120: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r2
m0_c120:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g120, r5.b3, 1       ; CLK high: capture edge
    qbbc m0_c120, r5.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g120:
    lsr  r7, r6, 14
m0_l120:
    qbbs m0_l120, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b0, 5         ; MISO bit 121: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c121:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g121, r4.b3, 1       ; CLK high: capture edge
    qbbc m0_c121, r4.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g121:
    lsr  r7, r6, 4
m0_l121:
    qbbs m0_l121, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b0, 6         ; MISO bit 122: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c122:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g122, r5.b1, 1       ; CLK high: capture edge
    qbbc m0_c122, r5.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g122:
    lsr  r16.b3, r6, 2           ; byte 14 done
m0_l122:
    qbbs m0_l122, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b0, 7         ; MISO bit 123: data bit 4 -> GPO11 (one R30 write)
    xor  r16.b3, r16.b3, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c123:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g123, r4.b1, 1       ; CLK high: capture edge
    qbbc m0_c123, r4.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g123:
m0_l123:
    qbbs m0_l123, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b0, 8         ; MISO bit 124: data bit 3 -> GPO11 (one R30 write)
m0_c124:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g124, r5.b2, 1       ; CLK high: capture edge
    qbbc m0_c124, r5.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g124:
m0_l124:
    qbbs m0_l124, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b0, 9         ; MISO bit 125: data bit 2 -> GPO11 (one R30 write)
m0_c125:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g125, r4.b2, 1       ; CLK high: capture edge
    qbbc m0_c125, r4.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g125:
m0_l125:
    qbbs m0_l125, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b0, 10        ; MISO bit 126: data bit 1 -> GPO11 (one R30 write)
m0_c126:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g126, r5.b0, 1       ; CLK high: capture edge
    qbbc m0_c126, r5.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g126:
m0_l126:
    qbbs m0_l126, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b0, 11        ; MISO bit 127: data bit 0 -> GPO11 (one R30 write)
m0_c127:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g127, r4.b0, 1       ; CLK high: capture edge
    qbbc m0_c127, r4.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g127:
    lsl  r6, r5, 1                    ; gather byte 15 (1/7)
m0_l127:
    qbbs m0_l127, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b1, 4         ; MISO bit 128: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r4
m0_c128:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g128, r3.b3, 1       ; CLK high: capture edge
    qbbc m0_c128, r3.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g128:
    lsr  r7, r6, 14
m0_l128:
    qbbs m0_l128, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b1, 5         ; MISO bit 129: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c129:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g129, r2.b3, 1       ; CLK high: capture edge
    qbbc m0_c129, r2.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g129:
    lsr  r7, r6, 4
m0_l129:
    qbbs m0_l129, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b1, 6         ; MISO bit 130: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c130:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g130, r3.b1, 1       ; CLK high: capture edge
    qbbc m0_c130, r3.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g130:
    lsr  r17.b0, r6, 2           ; byte 15 done
m0_l130:
    qbbs m0_l130, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b1, 7         ; MISO bit 131: data bit 4 -> GPO11 (one R30 write)
    xor  r17.b0, r17.b0, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c131:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g131, r2.b1, 1       ; CLK high: capture edge
    qbbc m0_c131, r2.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g131:
m0_l131:
    qbbs m0_l131, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b1, 8         ; MISO bit 132: data bit 3 -> GPO11 (one R30 write)
m0_c132:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g132, r3.b2, 1       ; CLK high: capture edge
    qbbc m0_c132, r3.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g132:
m0_l132:
    qbbs m0_l132, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b1, 9         ; MISO bit 133: data bit 2 -> GPO11 (one R30 write)
m0_c133:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g133, r2.b2, 1       ; CLK high: capture edge
    qbbc m0_c133, r2.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g133:
m0_l133:
    qbbs m0_l133, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b1, 10        ; MISO bit 134: data bit 1 -> GPO11 (one R30 write)
m0_c134:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g134, r3.b0, 1       ; CLK high: capture edge
    qbbc m0_c134, r3.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g134:
m0_l134:
    qbbs m0_l134, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b1, 11        ; MISO bit 135: data bit 0 -> GPO11 (one R30 write)
m0_c135:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g135, r2.b0, 1       ; CLK high: capture edge
    qbbc m0_c135, r2.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g135:
    lsl  r6, r3, 1                    ; gather byte 16 (1/7)
m0_l135:
    qbbs m0_l135, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b2, 4         ; MISO bit 136: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r2
m0_c136:
    and  r5.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g136, r5.b3, 1       ; CLK high: capture edge
    qbbc m0_c136, r5.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g136:
    lsr  r7, r6, 14
m0_l136:
    qbbs m0_l136, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b2, 5         ; MISO bit 137: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c137:
    and  r4.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g137, r4.b3, 1       ; CLK high: capture edge
    qbbc m0_c137, r4.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g137:
    lsr  r7, r6, 4
m0_l137:
    qbbs m0_l137, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b2, 6         ; MISO bit 138: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c138:
    and  r5.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g138, r5.b1, 1       ; CLK high: capture edge
    qbbc m0_c138, r5.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g138:
    lsr  r17.b1, r6, 2           ; byte 16 done
m0_l138:
    qbbs m0_l138, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b2, 7         ; MISO bit 139: data bit 4 -> GPO11 (one R30 write)
    xor  r17.b1, r17.b1, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c139:
    and  r4.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g139, r4.b1, 1       ; CLK high: capture edge
    qbbc m0_c139, r4.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g139:
m0_l139:
    qbbs m0_l139, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b2, 8         ; MISO bit 140: data bit 3 -> GPO11 (one R30 write)
m0_c140:
    and  r5.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g140, r5.b2, 1       ; CLK high: capture edge
    qbbc m0_c140, r5.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g140:
m0_l140:
    qbbs m0_l140, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b2, 9         ; MISO bit 141: data bit 2 -> GPO11 (one R30 write)
m0_c141:
    and  r4.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g141, r4.b2, 1       ; CLK high: capture edge
    qbbc m0_c141, r4.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g141:
m0_l141:
    qbbs m0_l141, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b2, 10        ; MISO bit 142: data bit 1 -> GPO11 (one R30 write)
m0_c142:
    and  r5.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g142, r5.b0, 1       ; CLK high: capture edge
    qbbc m0_c142, r5.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g142:
m0_l142:
    qbbs m0_l142, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b2, 11        ; MISO bit 143: data bit 0 -> GPO11 (one R30 write)
m0_c143:
    and  r4.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g143, r4.b0, 1       ; CLK high: capture edge
    qbbc m0_c143, r4.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g143:
    lsl  r6, r5, 1                    ; gather byte 17 (1/7)
m0_l143:
    qbbs m0_l143, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b3, 4         ; MISO bit 144: data bit 7 -> GPO11 (one R30 write)
    xor  r6, r6, r4
m0_c144:
    and  r3.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g144, r3.b3, 1       ; CLK high: capture edge
    qbbc m0_c144, r3.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g144:
    lsr  r7, r6, 14
m0_l144:
    qbbs m0_l144, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b3, 5         ; MISO bit 145: data bit 6 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c145:
    and  r2.b3, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g145, r2.b3, 1       ; CLK high: capture edge
    qbbc m0_c145, r2.b3, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g145:
    lsr  r7, r6, 4
m0_l145:
    qbbs m0_l145, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b3, 6         ; MISO bit 146: data bit 5 -> GPO11 (one R30 write)
    xor  r6, r6, r7
m0_c146:
    and  r3.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g146, r3.b1, 1       ; CLK high: capture edge
    qbbc m0_c146, r3.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g146:
    lsr  r17.b2, r6, 2           ; byte 17 done
m0_l146:
    qbbs m0_l146, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b3, 7         ; MISO bit 147: data bit 4 -> GPO11 (one R30 write)
    xor  r17.b2, r17.b2, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
m0_c147:
    and  r2.b1, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g147, r2.b1, 1       ; CLK high: capture edge
    qbbc m0_c147, r2.b1, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g147:
m0_l147:
    qbbs m0_l147, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b3, 8         ; MISO bit 148: data bit 3 -> GPO11 (one R30 write)
m0_c148:
    and  r3.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g148, r3.b2, 1       ; CLK high: capture edge
    qbbc m0_c148, r3.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g148:
m0_l148:
    qbbs m0_l148, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b3, 9         ; MISO bit 149: data bit 2 -> GPO11 (one R30 write)
m0_c149:
    and  r2.b2, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g149, r2.b2, 1       ; CLK high: capture edge
    qbbc m0_c149, r2.b2, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g149:
m0_l149:
    qbbs m0_l149, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b3, 10        ; MISO bit 150: data bit 1 -> GPO11 (one R30 write)
m0_c150:
    and  r3.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g150, r3.b0, 1       ; CLK high: capture edge
    qbbc m0_c150, r3.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g150:
m0_l150:
    qbbs m0_l150, r31, 1              ; wait CLK low (launch = falling)
    lsl  r30.w0, r13.b3, 11        ; MISO bit 151: data bit 0 -> GPO11 (one R30 write)
m0_c151:
    and  r2.b0, r31.b0, 0x46      ; snapshot CLK|MOSI|/CS
    qbbs m0_g151, r2.b0, 1       ; CLK high: capture edge
    qbbc m0_c151, r2.b0, 6       ; /CS low: wait
    jmp  frame_abort                   ; /CS high
m0_g151:
    lsl  r6, r3, 1                    ; gather byte 18 (1/7)
; after the last capture edge: remaining gathers
    xor  r6, r6, r2
    lsr  r7, r6, 14
    xor  r6, r6, r7
    lsr  r7, r6, 4
    xor  r6, r6, r7
    lsr  r17.b3, r6, 2           ; byte 18 done
    xor  r17.b3, r17.b3, 0xFF   ; mode 0: CLK=1 in the snapshot inverts the byte
    jmp  frame_end
; <<< generated: frame mode 0
.endif

