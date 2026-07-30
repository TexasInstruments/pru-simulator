; =============================================================
; Peripheral Interface — selectable TX test patterns (ch0)
; =============================================================
; The waveform-inspection sibling of perif_tx_pattern.asm, which streams
; the plain 8-bit counter and is what the drift experiment and its tools
; expect. This one transmits the classic bit patterns first, so the
; Signal Graph shows something you can check by eye.
;
;   PATTERN = 0   sequence, then the counter forever   (default)
;           = 1   0x00 forever          (line held low)
;           = 2   0xFF forever          (line held high)
;           = 3   0xAA forever          (1010... , bit clock / 2)
;           = 4   0x55 forever          (0101... , same, opposite phase)
;           = 5   walking 1 forever     01 02 04 08 10 20 40 80
;           = 6   walking 0 forever     FE FD FB F7 EF DF BF 7F
;           = 7   counter only          00 01 02 ... FF 00 ...
;
; The PATTERN = 0 sequence is one byte of each, twenty bytes total:
;
;   00 FF AA 55 | 01 02 04 08 10 20 40 80 | FE FD FB F7 EF DF BF 7F
;
; then 00 01 02 ... At the div=7 bit clock one byte is 64 graph samples,
; so the whole sequence is 1280 samples — pick the 2048 window to catch
; it in one capture, and note that Run capture in peripheral mode is
; single-shot (it fills the window once, then REC switches itself off).
;
; Edit PATTERN below and click Load & Assemble again to switch.
;
; As in perif_tx_pattern.asm, the RX consumes the first '1' as its start
; bit, so each pushed byte is the payload pre-shifted right by one:
;   push_k = carry | (p_k >> 1), carry_next = (p_k & 1) << 7,
;   carry_0 = 0x80 (the start bit).
; The receiver then captures exactly p_0, p_1, ... byte-aligned. On the
; Signal Graph's perif0_out lane this means every octet boundary sits one
; bit after the first rising edge.
;
; Self-configuring: the prologue writes GPCFG0 mux=1 (perif mode),
; TXCFG (0x260E4) = 0x00070010 (clk_sel=core, div=7 -> 25 MHz bit clock)
; and CH0CFG0 (0x260E8) = 0 (tx_frame_size=0 -> continuous mode).
; Host prerequisite: enable loopback ch0 (harness, not memory-mapped) if
; you want to receive what this sends.
;
; The pattern table lives in this core's own DRAM at 0x1F00..0x1F13, well
; clear of the low DRAM, and is visible in the Memory panel.
;
; Register map: r2=payload byte p, r3=carry bits, r4=byte to push,
;               r5=R31 status scratch, r6=FIFO prime counter,
;               r7=table read pointer, r8=table end pointer,
;               r9=source (0=table, 1=counter), r10=counter value,
;               r0=prologue value scratch / TX-go word, r1=prologue addr
; =============================================================

PATTERN .set 0

; ---- table window for the selected pattern (absolute DRAM addresses) ----
.if PATTERN == 0
PAT_START .set 0x1F00
PAT_END   .set 0x1F14
.endif
.if PATTERN == 1
PAT_START .set 0x1F00
PAT_END   .set 0x1F01
.endif
.if PATTERN == 2
PAT_START .set 0x1F01
PAT_END   .set 0x1F02
.endif
.if PATTERN == 3
PAT_START .set 0x1F02
PAT_END   .set 0x1F03
.endif
.if PATTERN == 4
PAT_START .set 0x1F03
PAT_END   .set 0x1F04
.endif
.if PATTERN == 5
PAT_START .set 0x1F04
PAT_END   .set 0x1F0C
.endif
.if PATTERN == 6
PAT_START .set 0x1F0C
PAT_END   .set 0x1F14
.endif
.if PATTERN == 7
PAT_START .set 0x1F00
PAT_END   .set 0x1F00
.endif

start:
    ldi  r0, 0x0000         ; GPCFG0: mux_sel=1 (bits 29:26)
    ldi  r0.w2, 0x0400
    ldi  r1, 0x6008
    ldi  r1.w2, 0x0002
    sbbo &r0, r1, 0, 4
    ldi  r0, 0x0010         ; TXCFG = 0x00070010
    ldi  r0.w2, 0x0007
    ldi  r1, 0x60E4
    ldi  r1.w2, 0x0002
    sbbo &r0, r1, 0, 4
    ldi  r0, 0              ; CH0CFG0 = 0 (continuous mode)
    ldi  r0.w2, 0
    sbbo &r0, r1, 4, 4      ; 0x260E8, full 32-bit clear

; ---- build the pattern table in DRAM (little-endian words) ----
    ldi  r1, 0x1F00         ; table base
    ldi  r0, 0xFF00         ; 0x1F00: 00 FF AA 55
    ldi  r0.w2, 0x55AA
    sbbo &r0, r1, 0, 4
    ldi  r0, 0x0201         ; 0x1F04: 01 02 04 08  (walking 1, low half)
    ldi  r0.w2, 0x0804
    sbbo &r0, r1, 4, 4
    ldi  r0, 0x2010         ; 0x1F08: 10 20 40 80  (walking 1, high half)
    ldi  r0.w2, 0x8040
    sbbo &r0, r1, 8, 4
    ldi  r0, 0xFDFE         ; 0x1F0C: FE FD FB F7  (walking 0, low half)
    ldi  r0.w2, 0xF7FB
    sbbo &r0, r1, 12, 4
    ldi  r0, 0xDFEF         ; 0x1F10: EF DF BF 7F  (walking 0, high half)
    ldi  r0.w2, 0x7FBF
    sbbo &r0, r1, 16, 4

    ldi  r30.b2, 0x00       ; select ch0 (byte2 strobe; clk_mode 0)
    ldi  r2, 0              ; p = 0
    ldi  r3, 0x80           ; carry = start bit
    ldi  r6, 0              ; bytes pushed, saturates at 4 (FIFO depth)
    ldi  r7, PAT_START      ; table read pointer
    ldi  r8, PAT_END        ; one past the last table byte
    ldi  r10, 0             ; counter value
.if PATTERN == 7
    ldi  r9, 1              ; counter only — never read the table
.else
    ldi  r9, 0              ; table first
.endif

; The FIFO is 4 deep and must be primed before the TX go strobe, so the
; first four passes push without checking for room; from then on every
; pass waits for the FIFO to drop below full. One loop body either way,
; which keeps the pattern generator in exactly one place.
loop:
    qbeq gen_counter, r9, 1
    lbbo &r2, r7, 0, 1      ; p = table[ptr]  (1-byte load: low byte only)
    and  r2, r2, 0xFF
    add  r7, r7, 1
    qbne gen_done, r7, r8   ; still inside the table window
.if PATTERN == 0
    ldi  r9, 1              ; sequence finished -> counter from here on
.else
    ldi  r7, PAT_START      ; repeat this pattern forever
.endif
    jmp  gen_done
gen_counter:
    mov  r2, r10
    add  r10, r10, 1
    and  r10, r10, 0xFF
gen_done:
    lsr  r4, r2, 1          ; frame: push = carry | (p >> 1)
    or   r4, r4, r3
    and  r3, r2, 1          ; carry_next = (p & 1) << 7
    lsl  r3, r3, 7
    qbgt push, r6, 4        ; r6 < 4: still priming, push unconditionally
wait_room:
    and  r5, r31, 0x1C      ; ch0 tx_fifo count, bits [4:2]
    qbeq wait_room, r5, 0x10 ; spin while FIFO full (count == 4)
push:
    mov  r30.b0, r4         ; push (byte0 strobe)
    qbgt prime, r6, 4       ; count the priming pushes, then go
    jmp  loop
prime:
    add  r6, r6, 1
    qbne loop, r6, 4        ; FIFO not primed yet
    ldi  r0, 0              ; TX go: R31 bit 18
    ldi  r0.w2, 0x0004
    mov  r31, r0
    jmp  loop
