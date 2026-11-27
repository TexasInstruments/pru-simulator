#!/usr/bin/env python3
"""narration/partN.txt -> build/partN/cues/NNN.wav and build/partN/cues.json

    .venv/bin/python tts.py 1            # part 1
    .venv/bin/python tts.py 1 2 3 4      # several parts

Kokoro 1.0 (kokoro-onnx), voice af_heart, speed 1.10. Every cue becomes its own
clip; cues.json lists their lengths. The capture reads the lengths, and mix.py
later places the clips where the capture really started each cue.

A cue whose spoken text did not change is not spoken again.

Narration format:

    # comment
    @title First steps
    ## first-program | First program
    load-assemble: Click Load & Assemble. || Click load and assemble.

"## id | Title" starts a chapter. A cue is "key: subtitle text", optionally
followed by "|| spoken text" when the voice needs another spelling.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
BUILD = HERE / "build"
VOICE, SPEED, LANG, SR = "af_heart", 1.10, "en-us", 24000
MODEL, VOICES = "kokoro-v1.0.onnx", "voices-v1.0.bin"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

CUE = re.compile(r"^([a-z0-9][a-z0-9-]*):\s+(\S.*)$")


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def parse(text: str) -> dict:
    """The narration file as {"title", "chapters": [{id, title}], "cues": [{key, chapter, text, spoken}]}."""
    title, chapters, cues, seen = None, [], [], set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line == "#" or line.startswith("# "):
            continue
        if line.startswith("@title"):
            title = line[len("@title"):].strip()
            continue
        if line.startswith("## "):
            chapter_id, _, chapter_title = line[3:].partition("|")
            chapters.append({"id": chapter_id.strip(), "title": chapter_title.strip() or chapter_id.strip()})
            continue
        match = CUE.match(line)
        if not match:
            raise ValueError(f"line {number} is not 'key: text': {line[:60]!r}")
        key, rest = match.groups()
        if not chapters:
            raise ValueError(f"line {number}: cue {key} comes before the first '## chapter'")
        if key in seen:
            raise ValueError(f"line {number}: cue key {key} is used twice")
        seen.add(key)
        shown, _, spoken = rest.partition("||")
        cues.append({"key": key, "chapter": chapters[-1]["id"], "text": shown.strip(),
                     "spoken": (spoken or shown).strip()})
    if not title:
        raise ValueError("the narration needs an '@title ...' line")
    return {"title": title, "chapters": chapters, "cues": cues}


def trim(samples, sr, thresh=0.01, pad=0.03):
    """Cut the silence Kokoro leaves before and after a sentence."""
    import numpy as np
    idx = np.where(np.abs(samples) > thresh)[0]
    if len(idx) == 0:
        return samples
    return samples[max(0, idx[0] - int(pad * sr)):min(len(samples), idx[-1] + int(pad * sr))]


def speak_part(part: int, kokoro) -> None:
    import numpy as np
    import soundfile as sf
    doc = parse((HERE / "narration" / f"part{part}.txt").read_text(encoding="utf-8"))
    out = BUILD / f"part{part}"
    (out / "cues").mkdir(parents=True, exist_ok=True)
    old = {}
    if (out / "cues.json").exists():
        old = {c["key"]: c for c in json.loads((out / "cues.json").read_text(encoding="utf-8"))["cues"]}
    total, spoken_now = 0.0, 0
    for index, cue in enumerate(doc["cues"]):
        spoken = cue.pop("spoken")
        digest = hashlib.sha1(f"{VOICE}|{SPEED}|{spoken}".encode()).hexdigest()[:16]
        wav = f"cues/{index:03d}-{cue['key']}.wav"
        before = old.get(cue["key"])
        if before and before.get("hash") == digest and (out / before["wav"]).exists():
            if before["wav"] != wav:
                (out / before["wav"]).rename(out / wav)
            cue["dur"] = before["dur"]
        else:
            samples, got_sr = kokoro.create(spoken, voice=VOICE, speed=SPEED, lang=LANG)
            assert got_sr == SR, got_sr
            samples = trim(np.asarray(samples, dtype=np.float32), SR)
            sf.write(out / wav, samples, SR, subtype="PCM_16")
            cue["dur"] = round(len(samples) / SR, 3)
            spoken_now += 1
        cue["wav"], cue["hash"] = wav, digest
        total += cue["dur"]
    keep = {c["wav"] for c in doc["cues"]}
    for stale in (out / "cues").glob("*.wav"):
        if f"cues/{stale.name}" not in keep:
            stale.unlink()
    doc.update(part=part, slug=slugify(doc["title"]), voice=VOICE, speed=SPEED, sr=SR)
    (out / "cues.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    words = sum(len(c["text"].split()) for c in doc["cues"])
    print(f"part {part}: {len(doc['cues'])} cues ({spoken_now} spoken now), {words} words, {total:.1f} s of speech")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("parts", nargs="+", type=int)
    parser.add_argument("--models", type=Path, default=BUILD / "models", help="folder with the Kokoro model files")
    args = parser.parse_args(argv)
    missing = [name for name in (MODEL, VOICES) if not (args.models / name).exists()]
    if missing:
        print(f"Kokoro model files missing in {args.models}: {', '.join(missing)}\nDownload them (about 350 MB):\n"
              f"  mkdir -p {args.models}\n" + "".join(
                  f"  curl -L -o {args.models / name} {RELEASE}/{name}\n" for name in (MODEL, VOICES)), file=sys.stderr)
        return 2
    from kokoro_onnx import Kokoro
    kokoro = Kokoro(str(args.models / MODEL), str(args.models / VOICES))
    for part in args.parts:
        speak_part(part, kokoro)
    return 0


if __name__ == "__main__":
    sys.exit(main())
