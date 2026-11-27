import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { uncovered } from '../lib/checks.mjs';
import { launch, newPage } from '../lib/browser.mjs';
import { Recorder } from '../lib/recorder.mjs';

const GROUPS = [{ pattern: /^#pin-gpo-/, reason: 'the GPO boxes are equal' }];

test('a control that was seen and used is covered', () => {
  assert.deepEqual(uncovered([{ seen: ['#btn-run'], used: ['#btn-run'] }], GROUPS), []);
});

test('a control that was seen but never used is reported', () => {
  assert.deepEqual(uncovered([{ seen: ['#btn-run', '#btn-sim'], used: ['#btn-run'] }], GROUPS), ['#btn-sim']);
});

test('a group is covered when one member was used', () => {
  assert.deepEqual(uncovered([{ seen: ['#pin-gpo-pru0-0', '#pin-gpo-pru0-1'], used: ['#pin-gpo-pru0-1'] }], GROUPS), []);
});

test('a group nobody used returns all its seen members', () => {
  assert.deepEqual(uncovered([{ seen: ['#pin-gpo-pru0-0', '#pin-gpo-pru0-1'], used: [] }], GROUPS), ['#pin-gpo-pru0-0', '#pin-gpo-pru0-1']);
});

test('a control seen in one part and used in another is covered', () => {
  assert.deepEqual(uncovered([{ seen: ['#btn-help'], used: [] }, { seen: [], used: ['#btn-help'] }], GROUPS), []);
});

const PAGE = 'data:text/html,' + encodeURIComponent(`<!doctype html><body>
<div id="bar"><button id="go"><span class="t">Go</span></button><button>Plain</button><select id="sel"><option>a</option></select></div>
<div id="list"><div style="cursor:pointer">item one</div></div>
<div id="form"><input placeholder="addr" value="0x10"></div>
<button id="hidden" style="display:none">Hidden</button>`);

test('the recorder writes what was on screen and what the tour used', async () => {
  const browser = await launch();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cov-test-'));
  try {
    const page = await newPage(browser, 640, 360);
    const r = new Recorder(page, { dir, out: dir, cues: { a: { dur: 0.2, chapter: 'c' } } });
    await r.open(PAGE, 300, 300);
    await r.cue('a', async () => { await r.click('#go .t'); await r.click('#list div'); await r.type('#form input', '99'); });
    await r.finish();
    const cov = JSON.parse(fs.readFileSync(path.join(dir, 'coverage.json'), 'utf8'));
    // The field is named by its placeholder, not by its value, which changed while it was typed into.
    assert.deepEqual(cov.seen.sort(), ['#bar > button[Plain]', '#form > input[addr]', '#go', '#list > div[item one]', '#sel']);
    assert.deepEqual(cov.used.sort(), ['#form > input[addr]', '#go', '#list > div[item one]']);     // a click on a child counts for its control
    await page.context().close();
  } finally { await browser.close(); fs.rmSync(dir, { recursive: true, force: true }); }
});
