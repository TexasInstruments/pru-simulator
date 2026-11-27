import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { launch, newPage } from '../lib/browser.mjs';
import { Recorder } from '../lib/recorder.mjs';

// One button, a counter that a 100 ms interval increments, and a clock that never changes.
const PAGE = 'data:text/html,' + encodeURIComponent(`<!doctype html><body style="margin:0;background:#222;color:#eee">
<button id="b" style="position:absolute;left:100px;top:100px" onclick="document.getElementById('c').textContent='clicked'">Go</button>
<span id="n">0</span><span id="c"></span><span id="still">still</span>
<script>
  let n = 0, running = true;
  setInterval(() => { if (running) document.getElementById('n').textContent = ++n; }, 100);
  window.stopCounter = () => { running = false; };
</script>`);

let browser;
test.before(async () => { browser = await launch(); });
test.after(async () => { await browser.close(); });

async function recorder(cues = {}) {
  const page = await newPage(browser, 640, 360);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rec-test-'));
  const r = new Recorder(page, { dir, out: dir, cues });
  await r.open(PAGE, 320, 180);
  return { r, page, dir, close: async () => { await page.context().close(); fs.rmSync(dir, { recursive: true, force: true }); } };
}
const inodes = (dir) => new Set(fs.readdirSync(dir).filter((f) => f.endsWith('.png')).map((f) => fs.statSync(path.join(dir, f)).ino));

test('a selector that matches nothing fails with the selector and the cue key', async () => {
  const { r, close } = await recorder({ 'load-thing': { dur: 0.2, chapter: 'c' } });
  try {
    const started = Date.now();
    await assert.rejects(r.cue('load-thing', () => r.click('#missing')), (e) => /#missing/.test(e.message) && /load-thing/.test(e.message));
    assert.ok(Date.now() - started < 5000, `took ${Date.now() - started} ms to notice`);    // not Playwright's 30 s default
  } finally { await close(); }
});

test('until() is driven by the stepped clock', async () => {
  const { r, page, close } = await recorder();
  try {
    const start = await page.evaluate(() => +document.getElementById('n').textContent);
    const n0 = r.n;
    await r.until(`+document.getElementById('n').textContent >= ${start + 5}`);
    assert.ok(r.n - n0 >= 13 && r.n - n0 <= 16, `took ${r.n - n0} frames`);   // 5 ticks of 100 ms = 15 frames
    await new Promise((res) => setTimeout(res, 600));     // real time must not move the page clock
    assert.equal(await page.evaluate(() => +document.getElementById('n').textContent), start + 5);
  } finally { await close(); }
});

test('until() gives up after its limit and says what it waited for', async () => {
  const { r, close } = await recorder();
  try {
    await assert.rejects(r.until('false', { max: 1, what: 'never happens' }), /never happens/);
  } finally { await close(); }
});

test('a cue lasts its speech plus the gap, and a key is used once', async () => {
  const { r, close } = await recorder({ a: { dur: 1.0, chapter: 'c' }, b: { dur: 1.0, chapter: 'c' }, z: { dur: 1.0, chapter: 'd' } });
  try {
    const n0 = r.n;
    await r.cue('a');
    assert.equal(r.n - n0, 30 + Math.round(0.38 * 30));
    await assert.rejects(r.cue('a'), /\ba\b.*(twice|again|already)/);
    await assert.rejects(r.cue('nope'), /nope/);
    const n1 = r.n;
    await r.cue('b');                                   // the next cue starts another chapter
    assert.equal(r.n - n1, 30 + Math.round(0.9 * 30));
  } finally { await close(); }
});

test('an action longer than the speech is not cut short', async () => {
  const { r, close } = await recorder({ a: { dur: 0.2, chapter: 'c' }, b: { dur: 0.2, chapter: 'c' } });
  try {
    const n0 = r.n;
    await r.cue('a', () => r.hold(2));
    assert.ok(r.n - n0 >= 60, `only ${r.n - n0} frames`);
  } finally { await close(); }
});

test('frames of a still page are linked, frames of a changing page are not', async () => {
  const { r, page, dir, close } = await recorder();
  try {
    await page.evaluate(() => window.stopCounter());
    await r.hold(0.5);                                  // let the last change through
    const before = inodes(dir).size, n0 = r.n;
    await r.hold(2);
    assert.equal(r.n - n0, 60);
    assert.ok(inodes(dir).size - before <= 2, `still page: ${inodes(dir).size - before} new images for 60 frames`);
    assert.equal(fs.readdirSync(dir).filter((f) => f.endsWith('.png')).length, r.n);
  } finally { await close(); }
  const second = await recorder();
  try {
    const before = inodes(second.dir).size;
    await second.r.hold(2);                             // counter changes 20 times
    assert.ok(inodes(second.dir).size - before >= 15, `changing page: ${inodes(second.dir).size - before} new images`);
  } finally { await second.close(); }
});

test('click moves the pointer to the control and really clicks it', async () => {
  const { r, page, close } = await recorder();
  try {
    await r.click('#b');
    assert.equal(await page.locator('#c').innerText(), 'clicked');
    const box = await page.locator('#b').boundingBox();
    assert.ok(Math.abs(r.x - (box.x + box.width / 2)) < 1 && Math.abs(r.y - (box.y + box.height / 2)) < 1);
  } finally { await close(); }
});

test('a dialog nobody asked for fails the capture; an expected one is answered', async () => {
  const { r, page, close } = await recorder();
  try {
    const asked = r.answerDialog(true);
    const result = page.evaluate(() => confirm('Really?'));
    assert.equal(await asked, 'Really?');
    assert.equal(await result, true);
    await page.evaluate(() => { setTimeout(() => confirm('Surprise'), 0); });
    await assert.rejects(r.hold(0.5), /Surprise/);
  } finally { await close(); }
});

test('finish() writes the timeline with cue and chapter frames', async () => {
  const { r, dir, close } = await recorder({ a: { dur: 0.3, chapter: 'one' }, b: { dur: 0.3, chapter: 'two' } });
  try {
    await r.hold(0.5);
    await r.cue('a');
    await r.cue('b');
    const frames = await r.finish();
    const tl = JSON.parse(fs.readFileSync(path.join(dir, 'timeline.json'), 'utf8'));
    assert.equal(tl.fps, 30);
    assert.equal(tl.frames, frames);
    assert.deepEqual(tl.cues.map((c) => c.key), ['a', 'b']);
    assert.equal(tl.cues[0].frame, 15);                 // open() writes no frames; 0.5 s hold
    assert.deepEqual(tl.chapters.map((c) => c.id), ['one', 'two']);
    assert.equal(tl.chapters[1].frame, tl.cues[1].frame);
  } finally { await close(); }
});
