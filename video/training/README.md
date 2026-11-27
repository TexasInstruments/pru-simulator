# Training videos

Sources of the PRU simulator training series: four narrated screen recordings of the
dashboard that show every control and run the basic examples. Design and plan are in
[`docs/superpowers`](../../docs/superpowers) (`2026-11-27-training-video`).

| Part | Title | Chapters |
|---|---|---|
| 1 | First steps | Start, The screen, First program, Breakpoints, Registers, Layout, theme and help |
| 2 | Memory, pins, cores and clocks | Memory, Fill and Memory Graph, I/O pins, Signal Graph, Cores, Processor, clocks and config |
| 3 | UART and I2C devices | The Devices view, UART decoder, UART RX inject, I2C expander |
| 4 | SSI encoder, peripherals and events | SSI encoder, Peripherals, Events, Where to go next |

The videos are not committed. `./build.sh` writes them to
`build/pru-simulator-training-<N>-<title>.mp4`: 1920x1080, 30 fps, H.264, mono AAC,
an English subtitle track and chapter marks.

## How it works

The picture is the real dashboard, driven by a script. Nothing in the dashboard is
changed for recording; the only thing added to the page is the pointer, the click ring
and the outline around a panel.

1. `tts.py` speaks every cue of `narration/partN.txt` into its own clip (Kokoro 1.0,
   voice `af_heart`) and writes the clip lengths to `build/partN/cues.json`.
2. `capture.mjs` starts the dashboard server from a temporary copy of the tree, opens
   it in headless Chromium and runs `tour/partN.mjs`. Each cue's actions run while its
   speech time passes. The page clock is paused and advanced by exactly 1/30 s per
   frame, so the page's own timers do not depend on how long a screenshot takes.
   (How far a program gets during Run still depends on how fast the server answers;
   the tours therefore wait for results, not for a time.) The capture writes
   the frames and the frame at which each cue started (`timeline.json`).
3. `mix.py` places each clip at the frame its cue started and writes the subtitles and
   chapter marks from the same frame numbers. Sound and picture cannot drift apart.
4. `assemble.sh` makes the MP4.

The server runs from a temporary copy because `ui/server.py` rewrites `memory.cfg`
(a clock change saves it). The copy gets the committed `memory.cfg`, so a locally
changed core clock does not change the recording. The build checks that the
repository's `memory.cfg` is byte-identical afterwards.

Where the narration names a result ("R30 becomes 1", "Decode shows Hello 2026"), the
tour asserts it against the live page. If the dashboard
changes so that a sentence is no longer true, the capture fails at that cue and names
it.

## Files

| File | What it does |
|---|---|
| `narration/partN.txt` | The wording: one cue per line, grouped in chapters. `subtitle text \|\| spoken text` gives the voice another spelling |
| `tour/partN.mjs` | What the pointer does during each cue, and the assertions |
| `tts.py` | Narration to clips and `cues.json`. Only changed cues are spoken again |
| `capture.mjs` | Records one part: frames, `timeline.json`, `coverage.json` |
| `mix.py` | `narration.wav`, `subs.srt`, `chapters.ffmeta` |
| `assemble.sh` | ffmpeg: one MP4 per part |
| `check.mjs` | `timeline N`, `output N`, `coverage` |
| `coverage-groups.mjs` | Repeated equal controls that count as shown when one of them is |
| `sheet.sh` | Contact pages of the stills, four per page, for reviewing a change |
| `build.sh` | All steps for all parts, or for the parts named |
| `lib/recorder.mjs` | The frame recorder: stepped clock, pointer, actions, cues, coverage log |
| `lib/server.mjs` | The dashboard server on a temporary copy of the tree |
| `lib/dashboard.mjs`, `lib/browser.mjs`, `lib/checks.mjs` | Shared tour helpers, the browser launch, the check functions |
| `terminal.html` | The drawn terminal of part 1's first chapter (the only picture that is not a recording) |
| `AUTHORING.md` | How to write or change a part |
| `test/`, `test_audio.py` | Tests of the pipeline code |

`build/`, `node_modules/` and `.venv/` are not committed.

## Rebuild

Written and run on Linux. Needs Node 20 or newer, `uv` (or any way to make a Python
3.10 to 3.12 venv) for Kokoro, ffmpeg, and a Python that can run the dashboard
(`pip install -r ../../requirements.txt`).

```
cd video/training
npm install
npx playwright install chromium
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt

# Kokoro model files (about 350 MB)
mkdir -p build/models
curl -L -o build/models/kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o build/models/voices-v1.0.bin  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

export PRU_PYTHON=python3        # the Python that runs ui/server.py
./build.sh                       # all parts, about 10 minutes
./build.sh 2                     # one part
```

Tests of the pipeline:

```
PRU_PYTHON=python3 npm test
.venv/bin/python -m pytest test_audio.py -q
```

## Changing it

- **Wording:** edit `narration/partN.txt` and run `./build.sh N`. The timing follows
  the new clip lengths by itself.
- **Pronunciation:** change the part after `||`. The subtitle keeps the written form.
- **A control moved or was renamed:** the capture fails with the cue key and the
  selector. Fix the selector in `tour/partN.mjs`.
- **A new control:** `node check.mjs coverage` lists it once it is on screen in any
  part. Add a cue that points at it.
- **A new view** (for example SPI or Audio, when they are on `main`): add
  `narration/part5.txt` and `tour/part5.mjs`. `build.sh` and the coverage check pick
  up every part that has a narration file. `AUTHORING.md` describes the recorder calls
  and the rules.

## Coverage

At the end of every cue the recorder notes each control that is on screen, and it notes
each control the tour points at. `node check.mjs coverage` fails if a control was on
screen in some part but was never pointed at in any part. Repeated equal controls (the
20 boxes of a pin grid) are listed in `coverage-groups.mjs` with a reason and count as
shown when one of them is.
