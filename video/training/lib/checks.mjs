// Checks that need no browser and no files: they take parsed JSON and return a list of problems.

const clock = (seconds) => { const s = Math.round(seconds); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };

/**
 * cues.json (from tts.py) against timeline.json (from the capture).
 * @returns {string[]} problems, empty when the part is fine
 */
export function checkTimeline(cuesDoc, timeline, { limits = true, minSeconds = 180, maxSeconds = 390 } = {}) {
  const problems = [];
  const { fps, frames } = timeline;
  const wanted = cuesDoc.cues.map((c) => c.key);
  const got = timeline.cues.map((c) => c.key);
  for (const key of wanted) if (!got.includes(key)) problems.push(`cue ${key} is in the narration but the tour never starts it`);
  for (const key of got) if (!wanted.includes(key)) problems.push(`cue ${key} is started by the tour but is not in the narration`);
  if (problems.length) return problems;
  if (got.join() !== wanted.join()) {
    const at = got.findIndex((key, i) => key !== wanted[i]);
    return [`cue order differs from the narration at position ${at + 1}: tour has ${got[at]}, narration has ${wanted[at]}`];
  }
  const start = Object.fromEntries(timeline.cues.map((c) => [c.key, c.frame]));
  cuesDoc.cues.forEach((cue, i) => {
    const speechEnd = start[cue.key] + cue.dur * fps;
    const next = cuesDoc.cues[i + 1];
    if (next && start[next.key] < speechEnd) problems.push(`cue ${cue.key} is still speaking when ${next.key} starts (frame ${start[next.key]} < ${Math.ceil(speechEnd)})`);
    if (!next && speechEnd > frames) problems.push(`cue ${cue.key} speaks past the end of the picture`);
  });
  const seconds = frames / fps;
  if (limits && (seconds < minSeconds || seconds > maxSeconds)) problems.push(`the part is ${clock(seconds)} long, outside ${clock(minSeconds)} to ${clock(maxSeconds)}`);
  return problems;
}

/**
 * Controls that were on screen in some part but that no part pointed at or used.
 * A group (repeated equal controls) is covered when one of its members was used.
 * @param {{seen: string[], used: string[]}[]} parts  coverage.json of every part
 * @param {{pattern: RegExp, reason: string}[]} groups
 * @returns {string[]} uncovered descriptors, sorted
 */
export function uncovered(parts, groups) {
  const seen = new Set(parts.flatMap((p) => p.seen));
  const used = new Set(parts.flatMap((p) => p.used));
  const groupUsed = groups.map((g) => [...used].some((d) => g.pattern.test(d)));
  return [...seen].filter((d) => {
    if (used.has(d)) return false;
    const g = groups.findIndex((group) => group.pattern.test(d));
    return g < 0 || !groupUsed[g];
  }).sort();
}
