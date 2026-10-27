"use strict";

// Pure decoder for the Framebuffer panel: header parsing, layout maths and
// pixel decoding into RGBA. No DOM here, so tests/ui_framebuffer.test.js can
// run it without a browser. The format is described in docs/framebuffer.md.
(root => {
  const MAGIC = 0x46554246;          // "FBUF" little-endian
  const HEADER_BYTES = 32;
  const MAX_DIM = 512;
  const MAX_BYTES = 0x10000;         // largest Shared RAM (AM243x): 64 KB
  const FORMATS = { 1: 'MONO1', 8: 'INDEX8', 16: 'RGB565' };
  const DEFAULT_FG = 0x572F;         // light green
  const DEFAULT_BG = 0x0861;         // near black

  const PRESETS = {
    tiny:    { label: 'Tiny 16×16 MONO1',     width: 16,  height: 16, format: 1 },
    mono:    { label: 'Mono 32×32 MONO1',     width: 32,  height: 32, format: 1 },
    wide:    { label: 'Wide 128×64 MONO1',    width: 128, height: 64, format: 1 },
    indexed: { label: 'Indexed 64×64 INDEX8', width: 64,  height: 64, format: 8 },
    color:   { label: 'Color 64×64 RGB565',   width: 64,  height: 64, format: 16 },
    large:   { label: 'Large 128×96 RGB565',  width: 128, height: 96, format: 16 },
  };

  const u32 = (b, o) => ((b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0);
  const u16 = (b, o) => b[o] | (b[o + 1] << 8);

  // 5/6/5 bits widen to 8 by repeating the top bits, so 0x1F -> 0xFF exactly.
  function rgb565ToRgb(v) {
    const r5 = (v >> 11) & 0x1F, g6 = (v >> 5) & 0x3F, b5 = v & 0x1F;
    return [(r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2)];
  }

  function hexColor565(v) {
    return '#' + rgb565ToRgb(v & 0xFFFF).map(c => c.toString(16).padStart(2, '0')).join('');
  }
  function parseHexColor565(text) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(text).trim());
    if (!m) return null;
    const n = parseInt(m[1], 16);
    return (((n >> 16 & 0xFF) >> 3) << 11) | (((n >> 8 & 0xFF) >> 2) << 5) | ((n & 0xFF) >> 3);
  }

  // Header from the first 32 bytes, or null when the magic is absent.
  function parseHeader(bytes) {
    if (!bytes || bytes.length < HEADER_BYTES || u32(bytes, 0) !== MAGIC) return null;
    const dims = u32(bytes, 4);
    const colors = u32(bytes, 0x1C);
    return {
      width: dims & 0xFFFF, height: dims >>> 16, format: u32(bytes, 8),
      stride: u32(bytes, 0x0C), dataOffset: u32(bytes, 0x10), paletteOffset: u32(bytes, 0x14),
      frame: u32(bytes, 0x18), fg: colors & 0xFFFF, bg: colors >>> 16,
    };
  }

  function defaultStride(width, format) {
    const bits = format === 1 ? width : format === 8 ? width * 8 : width * 16;
    return Math.ceil(bits / 32) * 4;
  }

  // Fill in defaults and check sizes. `cfg` has width, height, format and
  // optional stride, dataOffset, paletteOffset. Returns the same fields
  // resolved plus `total`, the byte count to read from base, or {error}.
  function layout(cfg) {
    const { width, height, format } = cfg;
    if (!FORMATS[format]) return { error: `Unknown format ${format} (use 1, 8 or 16)` };
    if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1)
      return { error: `Invalid size ${width}×${height}` };
    if (width > MAX_DIM || height > MAX_DIM)
      return { error: `Size ${width}×${height} is too large (max ${MAX_DIM}×${MAX_DIM})` };
    const rowMin = Math.ceil((format === 1 ? width : width * format) / 8);
    const stride = cfg.stride ? cfg.stride : defaultStride(width, format);
    if (stride < rowMin) return { error: `Stride ${stride} is smaller than one row (${rowMin} bytes)` };
    const dataOffset = cfg.dataOffset === undefined ? HEADER_BYTES : cfg.dataOffset;
    const paletteOffset = format === 8 ? (cfg.paletteOffset || 0) : 0;
    let total = dataOffset + stride * height;
    if (paletteOffset) total = Math.max(total, paletteOffset + 512);
    if (total > MAX_BYTES)
      return { error: `Frame needs ${total} bytes, more than the 64 KB Shared RAM limit` };
    return { width, height, format, stride, dataOffset, paletteOffset, total,
      fg: cfg.fg, bg: cfg.bg };
  }

  // Config for a parsed header (colors default when both are zero).
  function configFromHeader(h) {
    const both0 = !h.fg && !h.bg;
    return { width: h.width, height: h.height, format: h.format, stride: h.stride,
      dataOffset: h.dataOffset || HEADER_BYTES, paletteOffset: h.paletteOffset,
      fg: both0 ? DEFAULT_FG : h.fg, bg: both0 ? DEFAULT_BG : h.bg };
  }

  function grayPalette() {
    const p = new Uint16Array(256);
    for (let i = 0; i < 256; i++) p[i] = ((i >> 3) << 11) | ((i >> 2) << 5) | (i >> 3);
    return p;
  }

  // Decode `bytes` (starting at base) to RGBA. `cfg` is a resolved layout()
  // plus fg/bg for MONO1. Throws if bytes is shorter than cfg.total.
  function decode(bytes, cfg) {
    const { width, height, format, stride, dataOffset } = cfg;
    if (bytes.length < cfg.total) throw new Error(`Need ${cfg.total} bytes, got ${bytes.length}`);
    const out = new Uint8ClampedArray(width * height * 4);
    const put = (i, rgb) => { out[i] = rgb[0]; out[i + 1] = rgb[1]; out[i + 2] = rgb[2]; out[i + 3] = 255; };
    let o = 0;
    if (format === 1) {
      const fg = rgb565ToRgb(cfg.fg === undefined ? DEFAULT_FG : cfg.fg);
      const bg = rgb565ToRgb(cfg.bg === undefined ? DEFAULT_BG : cfg.bg);
      for (let y = 0; y < height; y++) {
        const row = dataOffset + y * stride;
        for (let x = 0; x < width; x++, o += 4) {
          const word = u32(bytes, row + (x >> 5) * 4);
          put(o, (word >>> (31 - (x & 31))) & 1 ? fg : bg);
        }
      }
    } else if (format === 8) {
      const palette = new Array(256);
      if (cfg.paletteOffset) {
        for (let i = 0; i < 256; i++) palette[i] = rgb565ToRgb(u16(bytes, cfg.paletteOffset + i * 2));
      } else {
        const gray = grayPalette();
        for (let i = 0; i < 256; i++) palette[i] = rgb565ToRgb(gray[i]);
      }
      for (let y = 0; y < height; y++) {
        const row = dataOffset + y * stride;
        for (let x = 0; x < width; x++, o += 4) put(o, palette[bytes[row + x]]);
      }
    } else {
      for (let y = 0; y < height; y++) {
        const row = dataOffset + y * stride;
        for (let x = 0; x < width; x++, o += 4) put(o, rgb565ToRgb(u16(bytes, row + x * 2)));
      }
    }
    return out;
  }

  function base64ToBytes(b64) {
    const bin = root.atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  // Largest whole-number scale at which the picture fits the box (min 1).
  function fitScale(width, height, boxW, boxH) {
    return Math.max(1, Math.floor(Math.min(boxW / width, boxH / height)));
  }

  function statusText(h) {
    return `FBUF ${h.width}×${h.height} ${FORMATS[h.format] || 'format ' + h.format} · frame ${h.frame}`;
  }

  root.PruFramebuffer = { MAGIC, HEADER_BYTES, MAX_DIM, MAX_BYTES, FORMATS, PRESETS,
    DEFAULT_FG, DEFAULT_BG, parseHeader, defaultStride, layout, configFromHeader,
    decode, rgb565ToRgb, hexColor565, parseHexColor565, base64ToBytes, fitScale, statusText };
})(globalThis);
