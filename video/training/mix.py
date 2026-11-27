#!/usr/bin/env python3
"""cues.json + timeline.json -> narration.wav, subs.srt, chapters.ffmeta (all in build/partN/)

    .venv/bin/python mix.py 1

The capture wrote down the frame at which each cue started (timeline.json).
Each cue's clip is placed at that frame, and the subtitles and chapter marks
use the same frame numbers, so sound and picture cannot drift apart.
"""
import json
import sys
import textwrap
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
BUILD = HERE / "build"
PEAK = 0.89


def place(clips, frames, fps, total_frames, sr):
    """One track of total_frames/fps seconds with clip i starting at frames[i]/fps, peak-normalised."""
    out = np.zeros(int(round(total_frames / fps * sr)), dtype=np.float32)
    for index, (clip, frame) in enumerate(zip(clips, frames)):
        start = int(round(frame / fps * sr))
        if start + len(clip) > len(out):
            raise ValueError(f"clip {index} ends after the picture ({(start + len(clip)) / sr:.2f} s > {len(out) / sr:.2f} s)")
        out[start:start + len(clip)] += clip
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    return out * (PEAK / peak) if peak > 0 else out


def stamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def srt(cues, fps) -> str:
    """Subtitles: each cue from its start to the end of its speech, wrapped at 58 characters."""
    blocks = []
    for number, cue in enumerate(cues, 1):
        start = cue["frame"] / fps
        blocks.append("%d\n%s --> %s\n%s\n" % (number, stamp(start), stamp(start + cue["dur"]),
                                               "\n".join(textwrap.wrap(cue["text"], 58))))
    return "\n".join(blocks)


def ffmeta(title, chapters, total_frames, fps) -> str:
    """ffmpeg metadata file with the title and one [CHAPTER] per entry of [{title, frame}]."""
    lines = [";FFMETADATA1", f"title={title}"]
    for index, chapter in enumerate(chapters):
        end = chapters[index + 1]["frame"] if index + 1 < len(chapters) else total_frames
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(chapter['frame'] / fps * 1000)}",
                  f"END={round(end / fps * 1000)}", f"title={chapter['title']}"]
    return "\n".join(lines) + "\n"


def mix_part(part: int) -> None:
    import soundfile as sf
    out = BUILD / f"part{part}"
    doc = json.loads((out / "cues.json").read_text(encoding="utf-8"))
    timeline = json.loads((out / "timeline.json").read_text(encoding="utf-8"))
    fps, total = timeline["fps"], timeline["frames"]
    started = {c["key"]: c["frame"] for c in timeline["cues"]}
    missing = [c["key"] for c in doc["cues"] if c["key"] not in started]
    if missing:
        raise SystemExit(f"part {part}: the capture has no start frame for: {', '.join(missing)}")
    cues = [dict(c, frame=started[c["key"]]) for c in doc["cues"]]
    clips = []
    for cue in cues:
        samples, sr = sf.read(out / cue["wav"], dtype="float32")
        assert sr == doc["sr"], (cue["wav"], sr)
        clips.append(samples)
    track = place(clips, [c["frame"] for c in cues], fps, total, doc["sr"])
    sf.write(out / "narration.wav", track, doc["sr"], subtype="PCM_16")
    (out / "subs.srt").write_text(srt(cues, fps), encoding="utf-8")
    titles = {c["id"]: c["title"] for c in doc["chapters"]}
    chapters = [{"title": titles[c["id"]], "frame": c["frame"]} for c in timeline["chapters"]]
    chapters[0]["frame"] = 0
    name = "PRU simulator training, part %d: %s" % (part, doc["title"])
    (out / "chapters.ffmeta").write_text(ffmeta(name, chapters, total, fps), encoding="utf-8")
    print(f"part {part}: {total / fps:.1f} s, {len(cues)} cues, {len(chapters)} chapters")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        mix_part(int(arg))
