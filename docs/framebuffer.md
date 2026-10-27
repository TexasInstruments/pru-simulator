# Framebuffer format

Firmware can draw a picture into emulated memory; a viewer decodes the memory and paints it. Nothing about the picture is stored outside memory: the viewer is given a `base` address and reads a self-describing header there. All values are little-endian.

## Header (32 bytes at `base`)

| Offset | Field | Meaning |
|---|---|---|
| +0x00 | magic | `0x46554246` (bytes `"FBUF"`) |
| +0x04 | size | width in bits 15:0, height in bits 31:16 |
| +0x08 | format | `1` MONO1, `8` INDEX8 (256-entry RGB565 palette), `16` RGB565 |
| +0x0C | stride | bytes per row; `0` = default, `ceil(width*bpp/8)` rounded up to a multiple of 4 |
| +0x10 | data offset | pixel data offset from `base`; `0` = `0x20` |
| +0x14 | palette offset | from `base`, INDEX8 only: 256 x u16 RGB565 |
| +0x18 | frame counter | firmware increments it **after** each completed frame |
| +0x1C | mono colors | MONO1 only: foreground RGB565 in bits 15:0, background in bits 31:16 |

The viewer repaints when the frame counter changes, so firmware must finish writing a frame before it bumps the counter; the viewer then never shows a half-drawn frame.

## Pixel packing

- **MONO1**: each row is made of 32-bit words; bit 31 is the leftmost pixel of the word. A row of up to 32 pixels is one word.
- **INDEX8**: one byte per pixel, leftmost pixel at the lowest address; the byte indexes the palette.
- **RGB565**: one u16 per pixel (`rrrrrggggggbbbbb`), leftmost pixel at the lowest address.

MONO1 example, one row of a 16-pixel-wide picture:

```
ldi32 r0, 0x07E00000     ; 0000 0111 1110 0000 0000 ...
sbbo  &r0, r1, 0, 4      ; r1 = address of the row
```

Bit 31 is pixel 0, so bits 26..21 are set: pixels 5..10 are lit (`.....######.....`).

RGB565 example: yellow is `0xFFE0` (r=31, g=63, b=0). Stored with `SBBO` as the 16-bit value, the bytes in memory are `E0 FF`. Two adjacent pixels can be written as one word: `0xFFE0FFE0`.

## Where to put the buffer

Core data RAM is 8 KB per core (DRAM0/DRAM1); Shared RAM is 32 KB on AM261x (`0x10000`-`0x17FFF`) and 64 KB on AM243x. The SSI demo uses `0x10000`-`0x1005F`, so the default `base` is `0x11000`.

| Preset | Size | Format | Bytes | Place in |
|---|---|---|---|---|
| Tiny | 16x16 | MONO1 | 64 | DRAM |
| Mono | 32x32 | MONO1 | 128 | DRAM |
| Wide | 128x64 | MONO1 | 1 KB | DRAM |
| Indexed | 64x64 | INDEX8 | 4 KB + 512 B palette | DRAM (8 KB core RAM) |
| Color | 64x64 | RGB565 | 8 KB | Shared RAM |
| Large | 128x96 | RGB565 | 24 KB | Shared RAM |

The header (32 bytes) is extra. Use Shared RAM for anything that does not fit in the 8 KB of DRAM next to the program's own data.

Notes:

- **PRU1 sees DRAM1 at local address 0** (DRAM swap), so a buffer in DRAM is at a different address for PRU0 and PRU1. A buffer in Shared RAM has the same address for every core, which is why the demo uses it.
- **Use direct addresses** (`ldi32 r8, 0x11000`) instead of `C28`: C28 reads as 0 until firmware programs `CTPPR0` (see `mem/constant_table.py`).

## Faces demo

`source/framebuffer_faces.asm` runs on PRU0 on AM243x (`memory.cfg`) and AM261x (`config/memory_am261x.cfg`). It draws a 32x32 RGB565 picture at `0x11000` (format 16, stride 0 = 64 B rows, data at `+0x20`, 2 KB of pixels) and cycles through four moods: happy (yellow), sad (blue), surprised (orange) and angry (red) on a dark background.

Run it: load the file on PRU0 and press Run; point the viewer at base `0x11000`, 32x32, RGB565. A face is drawn in about 4k cycles, the frame counter at `0x11018` is incremented, and the program waits `2 * DELAY_ITERS` cycles (100k) before the next mood. It never halts.

How it works: two 16x16 one-bit masks are held as 32-bit rows (column 0 = bit 31): a shared head "disc" and a per-mood "features" mask (eyes, mouth, brows). The masks are built in DRAM0 at start with `LDI32` and `SBBO`. For each mask pixel, `QBBC` tests choose the color (feature, else disc, else background) and the pixel is written as a 2x2 block of screen pixels, so 16x16 masks fill the 32x32 picture with a fifth of the data.

Edit it (all at the top of the file or beside the mask rows):

- `HAPPY_COLOR`, `SAD_COLOR`, `SURPRISED_COLOR`, `ANGRY_COLOR`, `FEATURE_COLOR`, `BG_COLOR`: RGB565 values.
- Mask rows: each `ldi32 rN, 0x....0000` has the picture as ASCII art beside it. Change a hex value and the face changes (bit 31 = leftmost; the 16 columns are bits 31..16).
- `DELAY_ITERS`: hold time per face (2 cycles per iteration).

`tests/test_framebuffer_faces.py` rebuilds the expected image from the same mask rows and colors, so edits to the masks and colors keep passing.
