import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const read = name => readFileSync(new URL(`../ui/static/${name}`, import.meta.url), 'utf8');
const context = vm.createContext({ atob: s => Buffer.from(s, 'base64').toString('binary') });
vm.runInContext(read('framebuffer.js'), context);
const FB = context.PruFramebuffer;
const plain = v => JSON.parse(JSON.stringify(v));

function header(fields = {}) {
  const h = { width: 32, height: 32, format: 1, stride: 0, dataOffset: 0, paletteOffset: 0,
    frame: 0, colors: 0, ...fields };
  const b = new Uint8Array(32);
  const v = new DataView(b.buffer);
  v.setUint32(0, 0x46554246, true);
  v.setUint32(4, (h.height << 16) | h.width, true);
  v.setUint32(8, h.format, true);
  v.setUint32(12, h.stride, true);
  v.setUint32(16, h.dataOffset, true);
  v.setUint32(20, h.paletteOffset, true);
  v.setUint32(24, h.frame, true);
  v.setUint32(28, h.colors, true);
  return b;
}
const px = (rgba, i) => Array.from(rgba.slice(i * 4, i * 4 + 4));

test('header parse', () => {
  const h = FB.parseHeader(header({ width: 64, height: 48, format: 16, stride: 128, dataOffset: 0x40,
    paletteOffset: 0x100, frame: 12, colors: 0x0861_572F }));
  assert.deepEqual(plain(h), { width: 64, height: 48, format: 16, stride: 128, dataOffset: 0x40,
    paletteOffset: 0x100, frame: 12, fg: 0x572F, bg: 0x0861 });
  assert.equal(FB.parseHeader(new Uint8Array(32)), null, 'no magic');
  assert.equal(FB.parseHeader(new Uint8Array(8)), null, 'too short');
  assert.equal(FB.statusText(h), 'FBUF 64×48 RGB565 · frame 12');
});

test('default stride rounds rows up to 4 bytes', () => {
  assert.equal(FB.defaultStride(16, 1), 4);
  assert.equal(FB.defaultStride(32, 1), 4);
  assert.equal(FB.defaultStride(33, 1), 8);
  assert.equal(FB.defaultStride(128, 1), 16);
  assert.equal(FB.defaultStride(3, 8), 4);
  assert.equal(FB.defaultStride(64, 8), 64);
  assert.equal(FB.defaultStride(3, 16), 8);
  assert.equal(FB.defaultStride(64, 16), 128);
});

test('layout defaults and total bytes', () => {
  const l = FB.layout(FB.configFromHeader(FB.parseHeader(header({ width: 32, height: 32 }))));
  assert.deepEqual([l.stride, l.dataOffset, l.total], [4, 0x20, 0x20 + 4 * 32]);
  assert.equal(FB.layout({ width: 16, height: 16, format: 1, dataOffset: 0 }).total, 64, 'manual: pixels at base');
  const indexed = FB.layout({ width: 64, height: 64, format: 8, paletteOffset: 0x2000 });
  assert.equal(indexed.total, 0x2000 + 512, 'palette after pixels extends the read');
});

test('MONO1 bit 31 of each word is the leftmost pixel', () => {
  const bytes = new Uint8Array(0x20 + 4 * 2);
  new DataView(bytes.buffer).setUint32(0x20, 0x80000001, true);      // row 0: x=0 and x=31
  new DataView(bytes.buffer).setUint32(0x24, 0x40000000, true);      // row 1: x=1
  const cfg = FB.layout({ width: 32, height: 2, format: 1, fg: 0xF800, bg: 0x0000 });
  const out = FB.decode(bytes, cfg);
  assert.deepEqual(px(out, 0), [255, 0, 0, 255]);
  assert.deepEqual(px(out, 1), [0, 0, 0, 255]);
  assert.deepEqual(px(out, 31), [255, 0, 0, 255]);
  assert.deepEqual(px(out, 32), [0, 0, 0, 255]);
  assert.deepEqual(px(out, 33), [255, 0, 0, 255]);
});

test('MONO1 wider than 32 uses consecutive words and default colors', () => {
  const bytes = new Uint8Array(0 + 8);
  new DataView(bytes.buffer).setUint32(4, 0x80000000, true);          // second word, leftmost = x=32
  const cfg = FB.layout({ width: 40, height: 1, format: 1, dataOffset: 0 });
  const out = FB.decode(bytes, cfg);
  assert.deepEqual(px(out, 32), [...FB.rgb565ToRgb(FB.DEFAULT_FG), 255]);
  assert.deepEqual(px(out, 0), [...FB.rgb565ToRgb(FB.DEFAULT_BG), 255]);
  assert.deepEqual(px(out, 33), px(out, 0));
});

test('RGB565 conversion replicates bits', () => {
  assert.deepEqual(plain(FB.rgb565ToRgb(0xFFFF)), [255, 255, 255]);
  assert.deepEqual(plain(FB.rgb565ToRgb(0x0000)), [0, 0, 0]);
  assert.deepEqual(plain(FB.rgb565ToRgb(0xF800)), [255, 0, 0]);
  assert.deepEqual(plain(FB.rgb565ToRgb(0x07E0)), [0, 255, 0]);
  assert.deepEqual(plain(FB.rgb565ToRgb(0x001F)), [0, 0, 255]);
  assert.deepEqual(plain(FB.rgb565ToRgb(0x8410)), [132, 130, 132]);
  assert.equal(FB.parseHexColor565('#ff0000'), 0xF800);
  assert.equal(FB.hexColor565(0x07E0), '#00ff00');
});

test('RGB565 decode is little-endian, left pixel at the lower address', () => {
  const bytes = new Uint8Array([0x00, 0xF8, 0xE0, 0x07, 0x1F, 0x00, 0xFF, 0xFF]);   // red green blue white
  const out = FB.decode(bytes, FB.layout({ width: 4, height: 1, format: 16, dataOffset: 0 }));
  assert.deepEqual([0, 1, 2, 3].map(i => px(out, i)),
    [[255, 0, 0, 255], [0, 255, 0, 255], [0, 0, 255, 255], [255, 255, 255, 255]]);
});

test('RGB565 honors stride padding', () => {
  const bytes = new Uint8Array(16);
  bytes.set([0xFF, 0xFF], 8);                                // row 1 starts at stride 8
  const out = FB.decode(bytes, FB.layout({ width: 2, height: 2, format: 16, stride: 8, dataOffset: 0 }));
  assert.deepEqual(px(out, 2), [255, 255, 255, 255]);
  assert.deepEqual(px(out, 0), [0, 0, 0, 255]);
});

test('INDEX8 looks colors up in the palette; no palette means gray', () => {
  const bytes = new Uint8Array(0x20 + 4 + 512);
  bytes.set([0, 1, 2, 255], 0x20);
  const pal = 0x24;
  new DataView(bytes.buffer).setUint16(pal + 2, 0xF800, true);
  new DataView(bytes.buffer).setUint16(pal + 4, 0x07E0, true);
  new DataView(bytes.buffer).setUint16(pal + 510, 0x001F, true);
  const cfg = FB.layout({ width: 4, height: 1, format: 8, paletteOffset: pal });
  const out = FB.decode(bytes, cfg);
  assert.deepEqual([0, 1, 2, 3].map(i => px(out, i)),
    [[0, 0, 0, 255], [255, 0, 0, 255], [0, 255, 0, 255], [0, 0, 255, 255]]);
  const gray = FB.decode(new Uint8Array([0, 255, 0, 0]),
    FB.layout({ width: 2, height: 1, format: 8, dataOffset: 0 }));
  assert.deepEqual(px(gray, 1), [255, 255, 255, 255]);
});

test('invalid sizes are rejected with a clear message', () => {
  assert.match(FB.layout({ width: 513, height: 8, format: 1 }).error, /too large/);
  assert.match(FB.layout({ width: 8, height: 600, format: 1 }).error, /too large/);
  assert.match(FB.layout({ width: 0, height: 8, format: 1 }).error, /Invalid size/);
  assert.match(FB.layout({ width: 8, height: 8, format: 3 }).error, /Unknown format/);
  assert.match(FB.layout({ width: 512, height: 512, format: 16, dataOffset: 0 }).error, /64 KB/);
  assert.match(FB.layout({ width: 64, height: 64, format: 16, stride: 8 }).error, /Stride/);
  assert.throws(() => FB.decode(new Uint8Array(4), FB.layout({ width: 32, height: 32, format: 1 })), /Need/);
});

test('base64 and scale helpers', () => {
  assert.deepEqual(Array.from(FB.base64ToBytes('RkJVRg==')), [0x46, 0x42, 0x55, 0x46]);
  assert.equal(FB.fitScale(32, 32, 330, 200), 6);
  assert.equal(FB.fitScale(512, 512, 100, 100), 1, 'never below 1');
});

test('markup contract: panel, toggle, big view and message routing', () => {
  const html = read('index.html');
  const panel = html.slice(html.indexOf('id="framebuffer-panel"'), html.indexOf('<!-- /#panel-pool -->'));
  for (const id of ['fb-auto', 'fb-base', 'fb-grid', 'fb-bigview', 'fb-manual', 'fb-preset', 'fb-width',
    'fb-height', 'fb-format', 'fb-stride', 'fb-fg', 'fb-bg', 'fb-status', 'fb-canvas'])
    assert.ok(panel.includes(`id="${id}"`), `panel has ${id}`);
  assert.match(panel, /data-panel-id="framebuffer"/);
  assert.match(panel, /id="fb-auto" checked/, 'Auto defaults on');
  assert.match(panel, /PRU1 sees DRAM1.*0x2000/, 'tooltip explains local vs global addresses');
  assert.ok(html.includes('id="fb-big"') && html.includes('id="fb-big-close"') && html.includes('id="fb-big-canvas"'));
  assert.ok(html.indexOf('/static/framebuffer.js') < html.indexOf('/static/framebuffer-panel.js'));
  assert.ok(html.indexOf('/static/app.js') < html.indexOf('/static/framebuffer-panel.js'));
  assert.match(read('layout.js'), /framebuffer:\s+document\.getElementById\('framebuffer-panel'\)/);
  assert.match(read('layout.js'), /DEFAULT_HIDDEN_PANELS = \['framebuffer'\]/);
  assert.match(read('workspace.js'), /framebuffer: \['.*', 'Framebuffer'\]/);
  const app = read('app.js');
  assert.match(app, /"fb-hdr" \|\| msg\.tag === "fb-px"\) window\.PruFbPanel\?\.onMemory/);
  assert.match(app, /window\.PruFbPanel\?\.onError/);
  assert.match(app, /msg\.tag === "mem2"\) renderMemory2/, 'mem2 routing intact');
  assert.match(app, /startsWith\("graph-"\)\) \{ graphHandleMemory/, 'graph routing intact');
  const js = read('framebuffer-panel.js');
  assert.match(js, /encoding: 'base64'/);
  assert.match(js, /imageSmoothingEnabled = false/);
  assert.match(js, /e\.key === 'Escape'/);
});
