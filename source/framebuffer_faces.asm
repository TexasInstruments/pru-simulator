; framebuffer_faces.asm -- animated colored faces in an emulated-memory framebuffer.
;
; Runs on PRU0 (AM243x or AM261x). Draws a 32x32 RGB565 picture at 0x11000 in
; Shared RAM (0x10000+ on both targets, so every core sees the same address) and
; cycles through four moods: happy (yellow), sad (blue), surprised (orange) and
; angry (red). Format: docs/framebuffer.md. The first 32 bytes at FB_BASE are the
; "FBUF" header; the frame counter at +0x18 is incremented AFTER a complete
; frame is written, so a viewer that repaints when the counter changes never
; shows a half-drawn face.
;
; How a face is drawn
;   Two 16x16 1-bit masks describe the picture: a shared "disc" (the head) and
;   a per-mood "features" mask (eyes, mouth, brows). One mask row is a 32-bit
;   word, pixel 0 = bit 31 (the 16 columns use bits 31..16). Every mask pixel
;   becomes a 2x2 block of screen pixels (16x16 masks scaled 2x), which keeps
;   the mask data at 16 rows instead of 32. Per pixel:
;       feature bit set -> FEATURE_COLOR, else disc bit set -> mood color,
;       else BG_COLOR.
;   The masks are built in DRAM0 once at start (LDI32 into R16..R23, then 32-byte
;   SBBOs, two per mask); the draw loop reads them back with LBBO.
;
; To customize: change a *_COLOR line below (RGB565) or a mask row (the ASCII
; art beside each row shows the bits). Change DELAY_ITERS for the speed.
;
; Cost: about 4.0k core cycles to draw a frame, plus 2 * DELAY_ITERS (100k) to hold it.

FB_BASE       .set 0x11000     ; framebuffer (header + 2 KB of pixels)
DELAY_ITERS   .set 50000       ; hold time per face: 2 cycles per iteration

BG_COLOR      .set 0x1082      ; dark gray
FEATURE_COLOR .set 0x0000      ; black eyes / mouth
HAPPY_COLOR   .set 0xFFE0      ; yellow
SAD_COLOR     .set 0x345F      ; blue
SURPRISED_COLOR .set 0xFC60    ; orange
ANGRY_COLOR   .set 0xE145      ; red

DISC_TABLE    .set 0x0100      ; DRAM0: 16 disc rows (64 B)
MOOD_TABLE    .set 0x0200      ; DRAM0: 4 records of 0x80 B: rows @+0, color @+0x40
NUM_MOODS     .set 4

; Registers: r8 FB_BASE, r9 mood index, r10 mood record ptr, r11 disc ptr,
; r12 frame counter, r13 rows left, r14 columns left, r15 pixel ptr,
; r2 disc row, r3 feature row, r4 pixel pair, r5 scratch,
; r20 BG, r21 mood color, r22 feature color, r23 delay, r24 MOOD_TABLE,
; r16..r23 hold mask rows while the masks are built (start only);
; r30/r31 are the PRU I/O registers, so they are not used for data.

start:
    ldi32 r8,  FB_BASE
    ldi   r11, DISC_TABLE

    ; ---- header: magic, size, format, stride, offsets, counter, mono colors
    ldi32 r0, 0x46554246            ; "FBUF"
    ldi32 r1, 0x00200020            ; height 32 << 16 | width 32
    ldi32 r2, 16                    ; format 16 = RGB565
    ldi   r3, 0                     ; stride 0 = default (64 B)
    ldi   r4, 0                     ; pixel data offset 0 = 0x20
    ldi   r5, 0                     ; palette offset (unused)
    ldi   r6, 0                     ; frame counter
    ldi   r7, 0                     ; mono colors (unused)
    sbbo  &r0, r8, 0, 32
    ldi   r12, 0

    ; ---- disc mask (shared by all moods) ----
    ldi32 r16, 0x07E00000    ; .....######.....  row  0
    ldi32 r17, 0x1FF80000    ; ...##########...  row  1
    ldi32 r18, 0x3FFC0000    ; ..############..  row  2
    ldi32 r19, 0x7FFE0000    ; .##############.  row  3
    ldi32 r20, 0x7FFE0000    ; .##############.  row  4
    ldi32 r21, 0xFFFF0000    ; ################  row  5
    ldi32 r22, 0xFFFF0000    ; ################  row  6
    ldi32 r23, 0xFFFF0000    ; ################  row  7
    sbbo  &r16, r11, 0, 32
    ldi32 r16, 0xFFFF0000    ; ################  row  8
    ldi32 r17, 0xFFFF0000    ; ################  row  9
    ldi32 r18, 0xFFFF0000    ; ################  row 10
    ldi32 r19, 0x7FFE0000    ; .##############.  row 11
    ldi32 r20, 0x7FFE0000    ; .##############.  row 12
    ldi32 r21, 0x3FFC0000    ; ..############..  row 13
    ldi32 r22, 0x1FF80000    ; ...##########...  row 14
    ldi32 r23, 0x07E00000    ; .....######.....  row 15
    sbbo  &r16, r11, 32, 32

    ldi   r10, MOOD_TABLE
    ; ---- mood 0: happy features ----
    ldi32 r16, 0x00000000    ; ................  row  0
    ldi32 r17, 0x00000000    ; ................  row  1
    ldi32 r18, 0x00000000    ; ................  row  2
    ldi32 r19, 0x00000000    ; ................  row  3
    ldi32 r20, 0x0C300000    ; ....##....##....  row  4
    ldi32 r21, 0x0C300000    ; ....##....##....  row  5
    ldi32 r22, 0x00000000    ; ................  row  6
    ldi32 r23, 0x00000000    ; ................  row  7
    sbbo  &r16, r10, 0, 32
    ldi32 r16, 0x00000000    ; ................  row  8
    ldi32 r17, 0x10080000    ; ...#........#...  row  9
    ldi32 r18, 0x08100000    ; ....#......#....  row 10
    ldi32 r19, 0x06600000    ; .....##..##.....  row 11
    ldi32 r20, 0x01800000    ; .......##.......  row 12
    ldi32 r21, 0x00000000    ; ................  row 13
    ldi32 r22, 0x00000000    ; ................  row 14
    ldi32 r23, 0x00000000    ; ................  row 15
    sbbo  &r16, r10, 32, 32
    ldi   r0, HAPPY_COLOR
    sbbo  &r0, r10, 0x40, 4

    add   r10, r10, 0x80            ; next record
    ; ---- mood 1: sad features ----
    ldi32 r16, 0x00000000    ; ................  row  0
    ldi32 r17, 0x00000000    ; ................  row  1
    ldi32 r18, 0x00000000    ; ................  row  2
    ldi32 r19, 0x00000000    ; ................  row  3
    ldi32 r20, 0x0C300000    ; ....##....##....  row  4
    ldi32 r21, 0x0C300000    ; ....##....##....  row  5
    ldi32 r22, 0x08000000    ; ....#...........  row  6
    ldi32 r23, 0x08000000    ; ....#...........  row  7
    sbbo  &r16, r10, 0, 32
    ldi32 r16, 0x08000000    ; ....#...........  row  8
    ldi32 r17, 0x00000000    ; ................  row  9
    ldi32 r18, 0x01800000    ; .......##.......  row 10
    ldi32 r19, 0x06600000    ; .....##..##.....  row 11
    ldi32 r20, 0x08100000    ; ....#......#....  row 12
    ldi32 r21, 0x00000000    ; ................  row 13
    ldi32 r22, 0x00000000    ; ................  row 14
    ldi32 r23, 0x00000000    ; ................  row 15
    sbbo  &r16, r10, 32, 32
    ldi   r0, SAD_COLOR
    sbbo  &r0, r10, 0x40, 4

    add   r10, r10, 0x80            ; next record
    ; ---- mood 2: surprised features ----
    ldi32 r16, 0x00000000    ; ................  row  0
    ldi32 r17, 0x00000000    ; ................  row  1
    ldi32 r18, 0x00000000    ; ................  row  2
    ldi32 r19, 0x0C300000    ; ....##....##....  row  3
    ldi32 r20, 0x0C300000    ; ....##....##....  row  4
    ldi32 r21, 0x0C300000    ; ....##....##....  row  5
    ldi32 r22, 0x0C300000    ; ....##....##....  row  6
    ldi32 r23, 0x00000000    ; ................  row  7
    sbbo  &r16, r10, 0, 32
    ldi32 r16, 0x00000000    ; ................  row  8
    ldi32 r17, 0x03C00000    ; ......####......  row  9
    ldi32 r18, 0x06600000    ; .....##..##.....  row 10
    ldi32 r19, 0x06600000    ; .....##..##.....  row 11
    ldi32 r20, 0x03C00000    ; ......####......  row 12
    ldi32 r21, 0x00000000    ; ................  row 13
    ldi32 r22, 0x00000000    ; ................  row 14
    ldi32 r23, 0x00000000    ; ................  row 15
    sbbo  &r16, r10, 32, 32
    ldi   r0, SURPRISED_COLOR
    sbbo  &r0, r10, 0x40, 4

    add   r10, r10, 0x80            ; next record
    ; ---- mood 3: angry features ----
    ldi32 r16, 0x00000000    ; ................  row  0
    ldi32 r17, 0x00000000    ; ................  row  1
    ldi32 r18, 0x00000000    ; ................  row  2
    ldi32 r19, 0x18180000    ; ...##......##...  row  3
    ldi32 r20, 0x0C300000    ; ....##....##....  row  4
    ldi32 r21, 0x06600000    ; .....##..##.....  row  5
    ldi32 r22, 0x00000000    ; ................  row  6
    ldi32 r23, 0x06600000    ; .....##..##.....  row  7
    sbbo  &r16, r10, 0, 32
    ldi32 r16, 0x06600000    ; .....##..##.....  row  8
    ldi32 r17, 0x00000000    ; ................  row  9
    ldi32 r18, 0x00000000    ; ................  row 10
    ldi32 r19, 0x07E00000    ; .....######.....  row 11
    ldi32 r20, 0x07E00000    ; .....######.....  row 12
    ldi32 r21, 0x00000000    ; ................  row 13
    ldi32 r22, 0x00000000    ; ................  row 14
    ldi32 r23, 0x00000000    ; ................  row 15
    sbbo  &r16, r10, 32, 32
    ldi   r0, ANGRY_COLOR
    sbbo  &r0, r10, 0x40, 4

    ; ---- animation: draw mood, publish frame, hold, next mood ----
    ldi32 r24, MOOD_TABLE           ; (r24 was scratch while building masks)
    ldi   r9, 0
next_mood:
    lsl   r5, r9, 7                 ; record = MOOD_TABLE + mood * 0x80
    add   r10, r24, r5
    lbbo  &r21, r10, 0x40, 4        ; mood color
    ldi   r20, BG_COLOR
    ldi   r22, FEATURE_COLOR
    ldi   r11, DISC_TABLE
    add   r15, r8, 0x20             ; pixel ptr = first pixel
    ldi   r13, 16                   ; 16 mask rows
row_loop:
    lbbo  &r2, r11, 0, 4            ; disc row
    lbbo  &r3, r10, 0, 4            ; feature row
    ldi   r14, 16                   ; 16 mask columns
col_loop:
    mov   r4, r20                   ; default: background
    qbbc  no_disc, r2, 31
    mov   r4, r21                   ; inside head: mood color
no_disc:
    qbbc  no_feat, r3, 31
    mov   r4, r22                   ; eye/mouth pixel: feature color
no_feat:
    lsl   r5, r4, 16
    or    r4, r4, r5                ; two RGB565 pixels side by side
    sbbo  &r4, r15, 0, 4            ; upper screen row of the 2x2 block
    sbbo  &r4, r15, 64, 4           ; lower screen row (stride 64 B)
    add   r15, r15, 4
    lsl   r2, r2, 1
    lsl   r3, r3, 1
    sub   r14, r14, 1
    qbne  col_loop, r14, 0
    add   r15, r15, 64              ; skip the lower row just written
    add   r11, r11, 4
    add   r10, r10, 4
    sub   r13, r13, 1
    qbne  row_loop, r13, 0

    ; frame complete: bump the counter (header +0x18) so the viewer repaints
    add   r12, r12, 1
    sbbo  &r12, r8, 0x18, 4

    ldi32 r23, DELAY_ITERS
hold:
    sub   r23, r23, 1
    qbne  hold, r23, 0

    add   r9, r9, 1
    qbne  next_mood, r9, NUM_MOODS
    ldi   r9, 0
    qba   next_mood
