// Records one part: runs tour/partN.mjs against the live dashboard and writes
// build/partN/frames/%06d.png, timeline.json and coverage.json.
//
//   node capture.mjs 1            record part 1
//   node capture.mjs 1 --sheet    also copy one still per cue to build/part1/sheet/
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { launch, newPage } from './lib/browser.mjs';
import { startServer } from './lib/server.mjs';
import { Recorder } from './lib/recorder.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const W = 1920, H = 1080, LEAD = 0.9;
const part = Number(process.argv[2]);
if (!Number.isInteger(part)) { console.error('usage: node capture.mjs <part> [--sheet]'); process.exit(2); }
const out = path.join(HERE, 'build', `part${part}`);
const doc = JSON.parse(fs.readFileSync(path.join(out, 'cues.json'), 'utf8'));
const cues = Object.fromEntries(doc.cues.map((c) => [c.key, { dur: c.dur, chapter: c.chapter }]));
// A tour may export `startPage` (a file beside capture.mjs) to begin somewhere else than the dashboard.
const { default: tour, startPage } = await import(pathToFileURL(path.join(HERE, 'tour', `part${part}.mjs`)).href);

const server = await startServer({ repoRoot: path.resolve(HERE, '../..') });
const browser = await launch();
const started = Date.now();
let page, r;
try {
  page = await newPage(browser, W, H, 1, 'light');
  r = new Recorder(page, { dir: path.join(out, 'frames'), out, cues });
  const cue = r.cue.bind(r);
  r.cue = async (key, actions) => {            // progress line per cue
    console.log(`  ${(r.n / r.fps).toFixed(1).padStart(6)} s  ${key}`);
    return cue(key, actions);
  };
  await r.open(startPage ? pathToFileURL(path.join(HERE, startPage)).href : server.url, W / 2, H / 2);
  await r.hold(LEAD);
  await tour(r, page, { url: server.url });
  const missing = doc.cues.map((c) => c.key).filter((key) => !r.done.some((d) => d.key === key));
  if (missing.length) throw new Error('the tour never started: ' + missing.join(', '));
  const frames = await r.finish();
  console.log(`part ${part}: ${frames} frames (${(frames / r.fps).toFixed(1)} s) in ${((Date.now() - started) / 1000).toFixed(0)} s`);
  if (process.argv.includes('--sheet')) {
    const sheet = path.join(out, 'sheet');
    fs.rmSync(sheet, { recursive: true, force: true });
    fs.mkdirSync(sheet, { recursive: true });
    r.done.forEach((c, i) => {
      const next = r.done[i + 1] ? r.done[i + 1].frame : frames;
      const n = Math.min(frames - 1, Math.floor(c.frame + (next - c.frame) * 0.85));
      fs.copyFileSync(path.join(out, 'frames', String(n).padStart(6, '0') + '.png'),
        path.join(sheet, `${String(i).padStart(3, '0')}-${c.key}.png`));
    });
    console.log(`${r.done.length} stills in ${path.relative(HERE, sheet)}`);
  }
} catch (e) {
  console.error('FAIL: ' + e.message);
  if (page) await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
  await server.stop();
}
