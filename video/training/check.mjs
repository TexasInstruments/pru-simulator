// node check.mjs timeline N   cues.json against timeline.json of part N
// node check.mjs output N     the finished MP4 of part N against the target format
// node check.mjs coverage     every control seen in any part is shown in one, or belongs to a named group
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { checkTimeline, uncovered } from './lib/checks.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const [what, arg] = process.argv.slice(2);
const fail = (lines) => { for (const line of [].concat(lines)) console.error('FAIL: ' + line); process.exit(1); };
const json = (...parts) => JSON.parse(fs.readFileSync(path.join(HERE, 'build', ...parts), 'utf8'));
const clock = (seconds) => { const s = Math.round(seconds); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };

if (what === 'timeline') {
  const doc = json(`part${arg}`, 'cues.json'), tl = json(`part${arg}`, 'timeline.json');
  const problems = checkTimeline(doc, tl);
  if (problems.length) fail(problems);
  console.log(`timeline ok: part ${arg}, ${clock(tl.frames / tl.fps)}, ${tl.cues.length} cues, ${tl.chapters.length} chapters`);
} else if (what === 'output') {
  const doc = json(`part${arg}`, 'cues.json'), tl = json(`part${arg}`, 'timeline.json');
  const file = path.join(HERE, 'build', `pru-simulator-training-${arg}-${doc.slug}.mp4`);
  if (!fs.existsSync(file)) fail('missing ' + file);
  const info = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-show_streams', '-show_format', '-show_chapters', '-of', 'json', file], { encoding: 'utf8' }));
  const v = info.streams.find((s) => s.codec_type === 'video');
  const a = info.streams.find((s) => s.codec_type === 'audio');
  const subs = info.streams.filter((s) => s.codec_type === 'subtitle');
  const problems = [];
  if (!v || v.codec_name !== 'h264' || v.width !== 1920 || v.height !== 1080 || v.r_frame_rate !== '30/1' || v.pix_fmt !== 'yuv420p') problems.push('video stream: ' + JSON.stringify(v && [v.codec_name, v.width, v.height, v.r_frame_rate, v.pix_fmt]));
  if (!a || a.codec_name !== 'aac' || a.sample_rate !== '48000' || a.channels !== 1) problems.push('audio stream: ' + JSON.stringify(a && [a.codec_name, a.sample_rate, a.channels]));
  if (subs.length !== 1 || subs[0].codec_name !== 'mov_text') problems.push('subtitle streams: ' + subs.length);
  if (info.chapters.length !== tl.chapters.length) problems.push(`chapters: ${info.chapters.length}, expected ${tl.chapters.length}`);
  const vd = Number(v?.duration), ad = Number(a?.duration), want = tl.frames / tl.fps;
  if (Math.abs(vd - ad) > 0.1) problems.push(`video is ${vd} s, audio is ${ad} s`);
  if (Math.abs(vd - want) > 0.1) problems.push(`video is ${vd} s, the capture has ${want.toFixed(2)} s`);
  if (problems.length) fail(problems);
  console.log(`output ok: ${path.basename(file)}  ${v.width}x${v.height} ${v.r_frame_rate} ${v.codec_name}, ${a.codec_name} ${a.sample_rate} Hz mono, 1 subtitle track, ${info.chapters.length} chapters, ${clock(vd)}, ${(Number(info.format.size) / 1e6).toFixed(1)} MB`);
} else if (what === 'coverage') {
  const { GROUPS } = await import('./coverage-groups.mjs');
  const all = fs.readdirSync(path.join(HERE, 'narration')).map((f) => /^part(\d+)\.txt$/.exec(f)?.[1]).filter(Boolean).sort((a, b) => a - b);
  const parts = (arg ? arg.split(',') : all).map((n) => json(`part${n}`, 'coverage.json'));
  const left = uncovered(parts, GROUPS);
  const seen = new Set(parts.flatMap((p) => p.seen)).size;
  if (left.length) {
    const by = {};
    for (const d of left) (by[d.includes(' > ') ? d.split(' > ')[0] : '(own id)'] ||= []).push(d);
    for (const [where, list] of Object.entries(by)) console.error(`${where}\n` + list.map((d) => '    ' + d).join('\n'));
    fail(`${left.length} of ${seen} controls were on screen but never shown`);
  }
  console.log(`coverage ok: ${seen} controls seen, all shown or in one of ${GROUPS.length} named groups`);
} else fail('usage: node check.mjs timeline N | output N | coverage [N,N,...]');
