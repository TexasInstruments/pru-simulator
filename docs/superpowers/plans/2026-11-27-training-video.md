# Training Video Implementation Plan

> **As built (2026-11-27):** the series has four parts, not five: the narration came out terser than estimated, so the chapters were regrouped after part 1 was recorded (see the Content section of the spec). Tasks 4 to 8 below describe the original five-part cut; `video/training/README.md` describes what exists. Other deviations: frames are linked automatically when the page did not change (no 12-frame rule), the smoke part and the spike were removed after use, and the server copy always gets the committed `memory.cfg`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Five narrated screen recordings (about 5 minutes each) of the PRU simulator dashboard on `main`, built by script, that show and explain every control and run six basic examples.

**Architecture:** Kokoro speaks every narration cue into its own WAV first. A Playwright script then drives the real dashboard (served from a temporary copy of the tree) one 1/30 s frame at a time; each cue's actions run while its speech time elapses, and the capture writes down the frame at which each cue really started. The audio track, subtitles and chapter marks are assembled afterwards from those frame numbers, so the picture never hurries and the voice never overlaps.

**Tech Stack:** Node 22 + Playwright (Chromium from `~/.cache/ms-playwright`), Python 3.12 venv with `kokoro-onnx`, `soundfile`, `numpy`, ffmpeg 8. The method (speech first, frame-stepped capture, ffmpeg assembly) comes from an earlier video build by the same author.

**Spec:** `docs/superpowers/specs/2026-11-27-training-video-design.md`

## Global Constraints

- Everything lives in `video/training/`. Nothing under `ui/`, `core/`, `pru_io/`, `source/`, `tests/` is edited.
- The dashboard server is only ever started from a temporary copy of the tree. The repo's `memory.cfg` must be byte-identical before and after any command of this plan.
- The only elements added to the page are the pointer, the click ring and the group outline, all inside one `#__cap_overlay` node with `pointer-events:none`.
- Voice `af_heart`, speed 1.10, language `en-us`, Kokoro 1.0 model files `kokoro-v1.0.onnx` and `voices-v1.0.bin` in `video/training/build/models/` (not committed; download commands in the README).
- Output per part: 1920x1080, 30 fps, H.264 (`libx264 -preset slow -crf 18 -pix_fmt yuv420p`), mono AAC 48 kHz 128k, one English `mov_text` subtitle track, MP4 chapter marks, `-movflags +faststart`. File name `build/pru-simulator-training-<N>-<slug>.mp4`.
- Narration: second person, present tense, one idea per cue, on-screen labels exactly as written. Every statement comes from `getting_started.md`, `docs/simulator_dashboard.md` or the code on this branch.
- A part is between 3:00 and 6:30 long.
- Python for the dashboard server: `$PRU_PYTHON`, default `python3` (a Python that has the dashboard's requirements). Child processes are started without `PYTHONPATH`.
- `build/`, `node_modules/`, `.venv/`, `__pycache__/` are in `video/training/.gitignore`.
- No commits: Thomas drives commits. Each task ends with `git status --short` showing only files under `video/training/` and `docs/superpowers/`.

## Review Focus

1. **A selector stops matching after a UI change.** `Recorder.point()` throws an error that names the selector and the current cue key; it never skips silently. (Task 1 test.)
2. **The simulator is slower or faster on another PC.** Tours wait for results with `r.until()` on page state, never with a fixed number of frames; `until` fails after its limit with the cue key and the condition text. (Task 1 test.)
3. **A capture crashes halfway.** The server process is killed and the temporary copy removed in a `finally` and on `SIGINT`; a second run finds a free port. (Task 1 test.)
4. **Narration edited by hand: a key duplicated, missing or left without a tour entry.** `tts.py` rejects a duplicate key by name; `check.mjs timeline` names the missing or extra key. (Task 2 tests.)
5. **Model files are missing on another PC.** `tts.py` exits with the two download commands instead of a stack trace. (Task 2 test.)

## File Structure

| File | Responsibility |
|---|---|
| `video/training/package.json`, `.gitignore`, `requirements.txt` | Dependencies: `playwright ^1.56`; `kokoro-onnx>=0.4`, `soundfile`, `numpy`, `pytest` |
| `lib/browser.mjs` | Launch headless Chromium, new 1920x1080 page (from the earlier build) |
| `lib/server.mjs` | Temporary copy of the tree, start and stop the dashboard server |
| `lib/recorder.mjs` | `Recorder`: stepped clock, frames, pointer, actions, cues, coverage log |
| `capture.mjs` | CLI: run one part's tour, write frames, `timeline.json`, `coverage.json` |
| `narration/partN.txt` | Wording of part N |
| `tour/partN.mjs` | What the pointer does in part N |
| `tts.py` | Narration file to per-cue WAVs and `cues.json` |
| `mix.py` | `cues.json` + `timeline.json` to `narration.wav`, `subs.srt`, `chapters.ffmeta` |
| `assemble.sh` | ffmpeg: one part's MP4 |
| `check.mjs` | `timeline N`, `coverage`, `output N` |
| `build.sh` | All steps for one part or for all |
| `test/*.test.mjs`, `test_audio.py` | Tests of the pipeline code |
| `README.md` | Rebuild and change instructions |

---

### Task 1: Scaffold, temporary server, recorder, stepped-clock spike

**Files:**
- Create: `video/training/{package.json,.gitignore,lib/browser.mjs,lib/server.mjs,lib/recorder.mjs,spike.mjs,test/server.test.mjs,test/recorder.test.mjs}`

**Interfaces (produced):**
- `startServer({ repoRoot, python = process.env.PRU_PYTHON || 'python3' }) -> Promise<{ url: string, port: number, root: string, stop(): Promise<void> }>`. Copies `repoRoot` without `.git`, `.venv`, `tests`, `docs`, `references`, `video`, `node_modules`, `__pycache__`, `.pytest_cache`, `.pru_runtime`; starts `python ui/server.py --port <free port>` with cwd = the copy; resolves when `GET /cores` answers 200 (limit 30 s).
- `class Recorder(page, { dir, fps = 30, cues = {} })` where `cues` maps key to `{ dur: seconds, chapter: id }`:
  - `open(url, x, y)`: install the page clock, load, add the overlay, run 1 s.
  - `frame()`, `hold(seconds)`: advance 1/30 s and write `dir/%06d.png`. While `this.live` is false, a `hold` screenshots its first 12 frames and hard-links the rest to the last one.
  - `point(target)`, `moveTo(target, frames = 16)`, `click(target)`, `dblclick(target)`, `select(selector, value)`, `type(selector, text)` (one character per 2 frames), `press(key)`, `drag(target, dx, dy)`, `scrollTo(selector, top)`, `outline(selector)`, `clearOutline()`.
  - `until(conditionFn, { max = 20, what })`: steps frames until `page.evaluate(conditionFn)` is truthy; sets `live = true` meanwhile.
  - `expect(selector, regex)`: throws if the element's text does not match.
  - `answerDialog(value = true)`: the next `confirm()` or `prompt()` of the page is accepted (with `value` as the prompt text) and its message is returned by the promise; an unexpected dialog fails the capture with its message.
  - `cue(key, actions = async () => {})`: records `{ key, frame }`, runs `actions`, then holds until `frame + dur*fps + gap`; gap is 0.38 s, or 0.9 s when the next cue starts another chapter. Throws on an unknown or repeated key.
  - `finish()`: holds 1.6 s, writes `timeline.json` `{ fps, frames, cues: [{ key, frame }], chapters: [{ id, frame }] }` and `coverage.json` (Task 3), returns the frame count.
  - Every action that takes a selector adds the element's descriptor to the "used" set (Task 3 defines the descriptor).

- [ ] **Step 1: Scaffold.** `package.json` (type module, `playwright ^1.56`, script `test: node --test test/`), `.gitignore`, copy `browser.mjs`; `npm install`.
- [ ] **Step 2: Write `test/server.test.mjs`:**
  - `startServer` answers `GET /cores` with a JSON list containing `pru0`.
  - After `PUT /config/clock_speed {"mhz": 250}` on the temporary server, the md5 of `<repoRoot>/memory.cfg` equals the md5 taken before `startServer`.
  - After `stop()`, `root` does not exist and a TCP connect to `port` is refused.
- [ ] **Step 3: Run `npm test`.** Expected: FAIL, `startServer` is not defined.
- [ ] **Step 4: Implement `lib/server.mjs`.** `stop()` is also registered for `process.on('exit')` and `SIGINT`.
- [ ] **Step 5: Run `npm test`.** Expected: 3 pass.
- [ ] **Step 6: Write `test/recorder.test.mjs`** against a `data:` page with one button `#b` and a `<span id="n">` that a `setInterval` increments every 100 ms:
  - `point('#missing')` inside `cue('k')` rejects with a message containing both `#missing` and `k`.
  - `until(() => +document.getElementById('n').textContent >= 5)` returns after 15 to 17 frames (the stepped clock drives the interval).
  - `until(() => false, { max: 1, what: 'never' })` rejects with a message containing `never`.
  - `cue('a')` with `dur` 1.0 and no actions advances exactly `30 + round(0.38*30)` frames; a second `cue('a')` rejects with `a`.
  - A 2 s `hold` with `live` false writes 60 files of which at most 12 have distinct inodes.
- [ ] **Step 7: Run, expect FAIL; implement `lib/recorder.mjs`** starting from the earlier build's recorder class; run, expect PASS.
- [ ] **Step 8: Spike `spike.mjs`.** Start the server, open the dashboard, open `source/running_led.asm` through the Assembly Editor's Open list, **Load & Assemble**, **Step** three times, **Run** for 4 s, **Stop**, start **REC** in the Signal Graph, **SIM** for 3 s, stop. Assert from page state: R30 differs between two frames 1 s apart during Run; the cycle counter in Telemetry rises during Run and stands still 1 s after Stop; no `pageerror`; the websocket status never shows "Waiting for server". Write the frames and make `build/spike.mp4` with ffmpeg.
- [ ] **Step 9: Decide.** Look at 8 stills from the spike (start, each Step, mid-Run, after Stop, mid-SIM, end). If Run, SIM and the graph behave as in a normal browser: approach A stands. If not: find which timer breaks, and if it cannot be made to work without touching `ui/`, switch `Recorder.frame()` to real time (`page.waitForTimeout(1000/fps)` instead of `clock.runFor`, no clock install) and note it in the README. Record the outcome in the README's first section either way.
- [ ] **Step 10:** `git status --short`.

### Task 2: Narration to sound, sound to MP4

**Files:**
- Create: `video/training/{requirements.txt,tts.py,mix.py,assemble.sh,check.mjs,capture.mjs,test_audio.py,test/check.test.mjs,narration/part0.txt,tour/part0.mjs}`

**Interfaces:**
- Narration file format:
  ```
  # comment
  @title First steps
  ## first-program | First program
  load-assemble: Click Load & Assemble. || Click load and assemble.
  ```
  Keys match `[a-z0-9-]+` and are unique in the file. `|| spoken` is optional.
- `tts.py N` writes `build/partN/cues/NNN.wav` (24 kHz mono, silence trimmed) and `build/partN/cues.json`: `{ part, title, slug, chapters: [{ id, title }], cues: [{ key, chapter, text, wav, dur }] }`. Pure functions `parse(text) -> dict` and `slugify(title) -> str` import without Kokoro.
- `capture.mjs N [--sheet]` reads `cues.json`, runs `tour/partN.mjs` (`export default async function tour(r, page)`), writes `build/partN/frames/`, `timeline.json`, `coverage.json`. `--sheet` also copies the frame at 85 % of each cue to `build/partN/sheet/<nnn>-<key>.png`.
- `mix.py N` writes `build/partN/narration.wav` (each clip placed at `frame/fps`, total length `frames/fps`, peak 0.89), `subs.srt` (cue shown from its start to start + dur, wrapped at 58 characters), `chapters.ffmeta`.
- `assemble.sh N` writes `build/pru-simulator-training-N-<slug>.mp4`.
- `check.mjs timeline N | output N | coverage`.

- [ ] **Step 1: `uv venv --python 3.12 .venv && uv pip install -r requirements.txt`; link the model files into `build/models/`.**
- [ ] **Step 2: Write `test_audio.py`:**
  - `parse` returns title, two chapters and three cues for a sample; the cue with `||` has `text` = the left side and `spoken` = the right side.
  - A duplicated key raises `ValueError` containing the key.
  - A cue line before any `##` raises `ValueError`.
  - `mix.place(clips, frames, fps, total_frames, sr)` with two 0.5 s clips of constant 0.5 at frames 30 and 90: output length `total_frames/fps*sr` samples; sample at 1.25 s is non-zero, at 2.0 s is zero, at 3.25 s non-zero.
  - `mix.srt(...)` for a cue at frame 45 with dur 2.0 gives `00:00:01,500 --> 00:00:03,500`.
  - `tts.py` run with a models directory that does not exist exits non-zero and its stderr contains `kokoro-v1.0.onnx` and `curl`.
- [ ] **Step 3: Run `.venv/bin/python -m pytest test_audio.py -q`.** Expected: FAIL (modules missing). Implement `tts.py`, `mix.py`. Run again: PASS.
- [ ] **Step 4: Write `test/check.test.mjs`** for `checkTimeline(cues, timeline) -> string[]` (list of problems):
  - a key in `cues.json` that is missing from the timeline is named;
  - a timeline key not in `cues.json` is named;
  - two cues whose speech overlaps (`next.frame < frame + dur*fps`) are named;
  - a total under 180 s or over 390 s is reported.
- [ ] **Step 5: Run, FAIL; implement `check.mjs` (`timeline`, `output`), `capture.mjs`, `assemble.sh`; run, PASS.** `output N` checks the format of Global Constraints, that audio and video lengths differ by less than 0.1 s, one subtitle stream, at least one chapter.
- [ ] **Step 6: Smoke part 0.** `narration/part0.txt`: one chapter, three cues (load `running_led.asm`; Run; Stop). `tour/part0.mjs`: the same actions as the spike. Run `tts.py 0`, `capture.mjs 0 --sheet`, `mix.py 0`, `assemble.sh 0`, `check.mjs output 0` (for part 0 the length limits are skipped). Expected: `output ok`, and the MP4 plays with voice in step with the clicks.
- [ ] **Step 7:** `git status --short`.

### Task 3: Coverage report

**Files:**
- Modify: `video/training/lib/recorder.mjs`, `video/training/check.mjs`
- Create: `video/training/coverage-groups.mjs`, `video/training/test/coverage.test.mjs`

**Interfaces:**
- Descriptor of an element (computed in the page): `#id` when it has an id; otherwise `<nearest ancestor id> > <tag>[<trimmed text, or title, or aria-label, or name, or data-* pairs>]`.
- At the end of every cue the recorder adds the descriptors of all visible `button, select, input, textarea, summary, [role=tab], [role=button]` outside `#__cap_overlay` to "seen". `coverage.json`: `{ seen: string[], used: string[] }`.
- `coverage-groups.mjs` exports `GROUPS: [{ pattern: RegExp, reason: string }]`. A group is covered when at least one member is in "used".
- `uncovered(parts: {seen, used}[], groups) -> string[]`.

- [ ] **Step 1: Write `test/coverage.test.mjs`:** a control seen and used is covered; seen and unused is returned; unused but matching a group with another used member is covered; a group with no used member returns all its seen members; a control used in part 2 and seen in part 1 is covered.
- [ ] **Step 2: Run, FAIL; implement; run, PASS.**
- [ ] **Step 3: Add `check.mjs coverage`:** reads every `build/part[1-5]/coverage.json`, prints the uncovered descriptors grouped by ancestor, exits 1 if any.
- [ ] **Step 4:** `git status --short`.

### Tasks 4 to 8: the five parts

Each part task has the same steps; what differs is in the table below.

**Files (part N):** Create `video/training/narration/partN.txt`, `video/training/tour/partN.mjs`.

- [ ] **Step 1: Read the sources of every statement:** the sections of `getting_started.md` and `docs/simulator_dashboard.md` for the part's chapters, the listed `.asm` files, and the Help dialog text in `ui/static/index.html`.
- [ ] **Step 2: Write `narration/partN.txt`.** First cue: "Part N of 5: <title>." Last cue names the next part (part 5: where to go next). One cue per control or per named group.
- [ ] **Step 3: Write `tour/partN.mjs`** with one `r.cue(key, ...)` per narration key in file order, the `r.expect` / `r.until` assertions of the table, and `r.outline()` on a panel or card before its controls are explained.
- [ ] **Step 4: `tts.py N && node capture.mjs N --sheet && node check.mjs timeline N`.** Expected: `timeline ok`, length inside 3:00 to 6:30. If over 6:30, move the last chapter to the next part and note it in the spec's part table.
- [ ] **Step 5: Look at every still in `build/partN/sheet/`.** For each: the pointer is on the control the cue names, the text is readable, nothing is cut off, the state matches the sentence. Fix and rerun.
- [ ] **Step 6: `mix.py N && ./assemble.sh N && node check.mjs output N`.** Expected: `output ok`.
- [ ] **Step 7:** `git status --short`.

| Task | Part | Chapters (ids) | Must be true on screen (asserted in the tour) |
|---|---|---|---|
| 4 | 1 First steps | `start`, `screen`, `first-program`, `layout-help` | Terminal still shows the three commands. After Load & Assemble of `running_led.asm` the Source panel lists `ldi r30, 1`. After the first Step R30 reads `0x00000001`, after the loop's first pass `0x00000002`. Arrow Left restores the previous R30. During Run the lit GPO box moves; after Stop the cycle counter stands still. After Reset the PC is 0. Hiding a panel with its Panels toggle removes it; Reset Layout brings all eight panels back. The theme button switches to High contrast and back. Help opens and closes. |
| 5 | 2 Registers and memory | `breakpoints`, `registers`, `memory`, `fill-graph` | A double-click on a source line sets a breakpoint and Run stops there; Set with an address adds one, Clear all removes them. A register edited by double-click keeps the typed value. C-Table shows C0 to C31. With `xfr_test.asm` stepped past its `xout`, the B0 column shows the stored values; SHF toggles its pressed state. After `mem_copy.asm` has run, the destination range in Memory equals the source range. Each format button changes the dump; LSB/MSB swaps byte order in 32b. Fill with Waveform Sine draws a sine in the Memory Graph after Add channel. |
| 6 | 3 Pins, cores and clocks | `io-pins`, `signal-graph`, `cores`, `clocks-config` | Clicking an R31 box sets that bit in R31. With `mvi_gpio_loopback.asm` running and the loopback on, R31 follows R30. Drive/Release changes a pin's direction without changing R30. REC records lanes that move while running; Clear empties them; the window selector changes the time span; Export CSV triggers a download. In CORES: Use PRU1 adds it to the run set, Show adds its pane to Source and Registers, Use all and Show all used work, the card name shows only that core. Releasing the frequency slider at 250 shows 250 MHz. Switching the processor and back changes the core list. The IEP selector, a custom rate with Apply rate, and the configured-rate reset change the IEP readout. Config opens `memory.cfg`; Cancel closes it without a reload. |
| 7 | 4 UART and I2C devices | `devices-view`, `uart-decode`, `uart-inject`, `i2c-expander` | With `uart_print.asm`: Record, Run, stop, Decode shows `Hello 2026`; Clear empties the output. With `uart_rx_11frame.asm`: ASCII payload `HelloWorld!`, pin GPI0, 4 Mbaud, 1 frame, Inject shows `Armed`; after Run, Memory at 0 shows `48 65 6C 6C 6F`. Reset inputs restores the defaults. With `i2c_tca9538_running_led.asm`: Attach expander at `0x23`, SCL 0, SDA 1; after Run the card's CONFIG reads `0x00` and the lit pin box moves; the expander is listed under Attached devices; Detach removes it. |
| 8 | 5 SSI encoder, peripherals and events | `ssi`, `peripherals`, `events`, `next` | Preset selection fills frame length, error bits and encoding. Set up reader + encoder model, Run: Received position equals the form's position and Received frames rises. Next position 1234, Set next position: Received position becomes 1234. Set up reader + encoder PRU, Run: the same readouts, with PRU0 and PRU1 used. Show waveforms opens the Signal Graph with clock and data lanes. Advanced opens name and pin fields. Peripherals: each card says "Inactive in this mux mode" until its link sets the mux; then its controls are live (the OSR `prompt()` is answered by the capture). A program that reads an unmapped address shows the Core fault band, the badge on I/O & Devices counts 1, and Events lists the fault; opening Events clears the badge. |

Chapter 9's device list and the SSI row's own setters are split between parts 4 and 5 as in the spec.

### Task 9: Whole build, coverage, README

**Files:**
- Create: `video/training/build.sh`, `video/training/README.md`
- Delete: `video/training/spike.mjs`, `narration/part0.txt`, `tour/part0.mjs` (their job is done; say so in the README's first section)
- Modify: `video/training/coverage-groups.mjs`

- [ ] **Step 1: `build.sh [N]`** runs `tts.py`, `capture.mjs`, `check.mjs timeline`, `mix.py`, `assemble.sh`, `check.mjs output` for one part or for 1 to 5, then `check.mjs coverage` when all five exist. It takes the md5 of the repo's `memory.cfg` before and after and fails if they differ.
- [ ] **Step 2: Run `./build.sh`.** Expected: five times `output ok`.
- [ ] **Step 3: Work through the coverage report.** For every uncovered control decide: add a cue (a control with its own function), or add a group with a one-line reason (repeated equal controls). Rerun the affected parts until `check.mjs coverage` prints `coverage ok`.
- [ ] **Step 4: Write `README.md`** : what the folder is, the stepped-clock finding, files, rebuild commands including the model download, how to change wording, pronunciation, a chapter, and how to add a part for a new view.
- [ ] **Step 5: Final checks.** `npm test`, `.venv/bin/python -m pytest test_audio.py -q`, `./build.sh` from a clean `build/` except `models/`; `env -u PYTHONPATH $PRU_PYTHON -m pytest tests -q -x -k "server or clock_speed"` in the worktree as a smoke run that product code is untouched; `git status --short` shows only `video/training/` and `docs/superpowers/`; `git diff --stat main -- ui core pru_io source tests` is empty.
- [ ] **Step 6: Watch all five videos once at normal speed** (or, without a player, the contact sheets plus a listen to each `narration.wav` length against the picture) and list anything that reads wrong for Thomas.
