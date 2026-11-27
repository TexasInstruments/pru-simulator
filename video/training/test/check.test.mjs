import test from 'node:test';
import assert from 'node:assert/strict';
import { checkTimeline } from '../lib/checks.mjs';

const cues = (list) => ({ cues: list.map(([key, dur]) => ({ key, dur, chapter: 'c' })) });
const timeline = (frames, list) => ({ fps: 30, frames, cues: list.map(([key, frame]) => ({ key, frame })) });
const LONG = 200 * 30;   // a length inside the allowed range

test('a good timeline has no problems', () => {
  assert.deepEqual(checkTimeline(cues([['a', 2], ['b', 2]]), timeline(LONG, [['a', 30], ['b', 120]])), []);
});

test('a narration cue the capture never started is named', () => {
  const problems = checkTimeline(cues([['a', 2], ['load-assemble', 2]]), timeline(LONG, [['a', 30]]));
  assert.equal(problems.length, 1);
  assert.match(problems[0], /load-assemble/);
});

test('a captured cue that is not in the narration is named', () => {
  const problems = checkTimeline(cues([['a', 2]]), timeline(LONG, [['a', 30], ['ghost', 200]]));
  assert.equal(problems.length, 1);
  assert.match(problems[0], /ghost/);
});

test('cues captured in another order than the narration are reported', () => {
  const problems = checkTimeline(cues([['a', 1], ['b', 1]]), timeline(LONG, [['b', 30], ['a', 120]]));
  assert.ok(problems.some((p) => /order/.test(p)), problems.join('; '));
});

test('speech that overlaps the next cue is named', () => {
  const problems = checkTimeline(cues([['a', 4], ['b', 2]]), timeline(LONG, [['a', 30], ['b', 90]]));   // a talks until frame 150
  assert.equal(problems.length, 1);
  assert.match(problems[0], /\ba\b.*\bb\b/);
});

test('the last cue must end before the picture does', () => {
  const problems = checkTimeline(cues([['a', 4]]), timeline(LONG, [['a', LONG - 30]]));
  assert.ok(problems.some((p) => /\ba\b/.test(p) && /end/.test(p)), problems.join('; '));
});

test('a part shorter than 3:00 or longer than 6:30 is reported, unless limits are off', () => {
  assert.match(checkTimeline(cues([['a', 2]]), timeline(100 * 30, [['a', 30]]))[0], /1:40/);
  assert.match(checkTimeline(cues([['a', 2]]), timeline(400 * 30, [['a', 30]]))[0], /6:40/);
  assert.deepEqual(checkTimeline(cues([['a', 2]]), timeline(100 * 30, [['a', 30]]), { limits: false }), []);
});

test('a length just under a full minute is not printed as 60 seconds', () => {
  const problems = checkTimeline(cues([['a', 2]]), timeline(Math.round(419.7 * 30), [['a', 30]]));
  assert.match(problems[0], /7:00/);
  assert.doesNotMatch(problems[0], /6:60/);
});
