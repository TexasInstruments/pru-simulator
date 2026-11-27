# Training video for the PRU simulator dashboard: design

Date: 2026-11-27. Branch `docs/training-video` (off `main` 7e1c2f5, dashboard v0.4.0).

## Intent

Customer engineers get the simulator from GitHub and open the dashboard for the
first time. The video shows them what every control does and walks through a few
basic examples, so they can load their own firmware and debug it without reading
all of `getting_started.md` and `docs/simulator_dashboard.md` first.

Stated by Thomas: a training video that explains the functions, runs basic
examples, goes through all clickable functions with a brief explanation, spoken
by a Kokoro v1 female voice; the audience is customer engineers who get the
simulator from GitHub; split into portions of about 5 minutes.

Everything else in this document is a proposal (marked "call") unless it says
otherwise.

## Goals

1. A series of narrated screen recordings of the real dashboard, as it is on
   `main`, each about 5 minutes and watchable on its own (four parts, see Content).
2. Every control a user can click, type into or drag is shown and explained in
   one or two sentences. A build step lists controls the video does not cover.
3. Six basic examples are run on screen, each from loading the source to seeing
   the result.
4. The video is rebuilt by script when the dashboard or the wording changes.

## Non-goals

- The SPI and Audio views. They are not on `main` (PRs #108 and #106). When they
  merge, they become new chapters.
- The MCP server, the headless runner and real hardware. The last chapter names
  them and points to the docs.
- Teaching the PRU instruction set. The viewer is an embedded engineer; the
  narration explains the simulator, not `LBBO`.
- German narration. Kokoro 1.0 has no German voice.

## Anti-goals

- The narration says something the dashboard does not do. Every statement is
  taken from `getting_started.md`, `docs/simulator_dashboard.md` or the code on
  `main`, and what is shown on screen is the real page, never a mock-up.
- The build changes the dashboard to make recording easier. Nothing under `ui/`,
  `core/`, `pru_io/` or `source/` is edited. The only thing added to the page at
  record time is a pointer overlay.
- The build writes `memory.cfg`. The dashboard server rewrites that file on a
  clock change, so the recording runs a server from a temporary copy of the
  tree (the way `source/pdm/tools/ui_check.py` does on the PDM branch).
- A video that is tiring to watch: a pointer that jumps, text too small to read,
  a voice that runs ahead of the picture.

## Constraints

- Voice: Kokoro 1.0 through `kokoro-onnx`, voice `af_heart`, speed 1.10 (call:
  the same voice and speed as an earlier video built with this method). Model files
  `kokoro-v1.0.onnx` and `voices-v1.0.bin` (about 350 MB) are not committed.
  Kokoro-82M is Apache-2.0 and `kokoro-onnx` is MIT, so the audio can be
  published.
- Format (call, same as that earlier video): 1920x1080, 30 fps, H.264, mono AAC
  48 kHz, English `mov_text` subtitle track, plus MP4 chapter marks.
- Text must be readable at 1080p. The dashboard is recorded at a 1920x1080
  viewport; if the default font sizes are too small on a laptop screen, the
  builder may record at device scale factor 1 with browser zoom, but must not
  change the page's CSS.
- Tools on this PC: ffmpeg 8, Node 22, Playwright Chromium in
  `~/.cache/ms-playwright`, `uv` with Python 3.11 and 3.12, the project `.venv`
  (server only). Kokoro goes into its own venv under the video folder, never
  into the project `.venv`.
- Run pytest with `env -u PYTHONPATH` on this PC (ROS plugin).

## Approaches

**A. Frame-stepped scripted capture (chosen, call).** A Playwright script drives
the real dashboard and writes one screenshot per 1/30 s, with the page clock
advanced by the script (the method of an earlier video's tool clips). Narration is spoken first; its measured cue
lengths set how long each step stays on screen. Sharp picture, exact sync, same
result on every build. Risk: the dashboard talks to a live server over a
websocket, which a stepped page clock does not control. The first task of the
plan is a spike that proves Run, SIM and the signal graph behave under a
stepped clock.

**B. Real-time recording.** The same script, but the browser runs in real time
and the picture comes from Playwright's video recording or a DevTools
screencast. No clock question, but the picture is softer (VP8 at a fixed low
bitrate, or frames at uneven intervals), timing depends on machine load, and
two builds differ. This is the fallback for a scene where A does not work.

**C. Stills with highlights.** Screenshots with a highlight box and the voice
over them. Sharpest and simplest, but it cannot show a program running, which
is half of the request. Dropped.

**D. Hand recording.** Thomas records the screen while following a script and
the voice is added afterwards. Natural mouse movement, but hours of his time
for every revision. Dropped.

## What is built

A folder `video/training/` (call: the shape of that earlier video's build folder):

| File | What it does |
|---|---|
| `narration/part1.txt` to `part4.txt` | One file per part. One cue per line, grouped by chapter. `subtitle text \|\| spoken text` gives the voice a different spelling, for example for "R30", "GPO" or "0x23" |
| `tts.py` | Kokoro: for each part writes one clip per cue and `build/partN/cues.json` with their lengths |
| `mix.py` | Places the clips at the frames the capture recorded: `narration.wav`, `subs.srt`, chapter marks |
| `tour/part1.mjs` to `part4.mjs` | The tour of each part: for every cue, what the pointer does while it is spoken (move to a control, click, type, drag, wait for the simulator) |
| `capture.mjs` | Starts the dashboard server from a temporary copy of the tree, opens the page, runs one part's tour and writes frames and `timeline.json` (the frame at which each cue started). Every part starts from a freshly started server, so parts can be rendered alone and in any order |
| `assemble.sh` | ffmpeg: frames, narration, subtitles and chapter marks into one MP4 per part |
| `check.mjs` | Timeline checks, the control coverage report, and checks on the finished file |
| `README.md` | How to rebuild and how to change wording, pronunciation or a chapter |

`build/`, `node_modules/` and `.venv/` are not committed.

### How sound and picture stay together

1. `tts.py` speaks each cue into its own clip and records its length.
2. The capture runs the actions of a cue while the cue's speech time elapses,
   and writes down the frame at which each cue started. An action that needs
   longer than its sentence (a program that must run until a UART frame is
   complete) simply takes the frames it needs; the next cue starts after it.
3. `mix.py` places each clip at the frame its cue started and writes the
   subtitles and chapter marks from the same frame numbers. So the picture
   never has to hurry and the voice never talks over an unfinished action.
4. The pointer starts moving to a control when the cue that names it begins.

### Pointer

An arrow drawn over the page with a ring on each click. It moves along eased paths at a calm speed. Before a
group of controls is explained, the group gets a thin outline for the length of
the cue, so the viewer knows where to look. These are overlay elements; the
dashboard's own DOM and CSS stay as they are.

### Coverage report

`check.mjs` collects every `button`, `select`, `input`, `textarea`, `summary`
and `[role=tab]` in the page after the tour has opened all dialogs and views
(this includes the ones built by script: the CORES deck, the panel toggles, the
I/O pin grids, the SD and Peripheral Interface cards). It compares them with
the controls the tour pointed at or used, and prints the rest. Groups of equal
controls count as covered when one member is shown and the narration names the
group (the 20 GPO boxes, the 20 Drive/Release buttons, the format buttons of
the second Memory panel). The allowed group list is in `check.mjs` with one
line of reason each. The build fails if an ungrouped control is not covered.

## Content

Four parts, 18:54 together (split into portions of about 5 minutes decided by
Thomas; the cut points are a call). This section was first written for five
parts of about 25 minutes. The narration turned out terser than estimated
(about 160 words per minute, one sentence per control), so the chapters were
regrouped into four parts after part 1 was recorded.

| Part | Title | Length | Chapters |
|---|---|---|---|
| 1 | First steps | 4:51 | Start, The screen, First program, Breakpoints, Registers, Layout, theme and help |
| 2 | Memory, pins, cores and clocks | 5:51 | Memory, Fill and Memory Graph, I/O pins, Signal Graph, Cores, Processor, clocks and config |
| 3 | UART and I2C devices | 3:41 | The Devices view, UART decoder, UART RX inject, I2C expander |
| 4 | SSI encoder, peripherals and events | 4:31 | SSI encoder, Peripherals, Events, Where to go next |

Each part stands alone: it opens with one line that names the part ("PRU
simulator training, part 2: memory, pins, cores and clocks"), starts from a
freshly started dashboard, loads what it needs itself, and ends with one line
that names the next part. Inside a part, each chapter has an MP4 chapter mark;
no title cards, the dashboard stays on screen.

The table below is the content plan the parts were written from. Its chapter
numbers and times are the original estimates; `video/training/narration/` is
what is spoken.

| # | Chapter | About | Controls | Example |
|---|---|---|---|---|
| 1 | Start | 0:30 | (none) Clone, `pip install -r requirements.txt`, `python ui/server.py`, open `localhost:8080`; shown as a terminal still | |
| 2 | The screen | 1:15 | The header groups, the two views (Simulator, I/O & Devices), the eight panels, Telemetry | |
| 3 | First program | 3:15 | Assembly Editor: Load into, Open, Project, Save, Load & Assemble, the editor. Execute: Step, Run/Stop, SIM and its interval, Reset, HW Reset. Arrow keys for step and step back | `running_led.asm` |
| 4 | Breakpoints and registers | 2:15 | Double-click a source line, Space, the address field with Set and Clear all. Registers: edit a value, C-Table, SPAD B0, B1, B2, IPC, SHF | `xfr_test.asm`, stepped only |
| 5 | Memory | 3:15 | Address, length, Refresh, auto refresh, Bin/Byte/16b/24b/32b, LSB/MSB, edit a cell, the second Memory panel. Fill dialog: unit, Pattern/Sequence/Waveform, wave type, amplitude, cycles, offset, signed, Fill, Cancel. Memory Graph: refresh, taller, shorter, Add channel | `mem_copy.asm` |
| 6 | Pins and signals | 3:00 | I/O Pins per core: GP mux selector, R30 grid, R31 grid (click an input), loopback strip, Drive/Release. Signal Graph: window, REC, Clear, taller, shorter, Export CSV | `mvi_gpio_loopback.asm` |
| 7 | Cores and clocks | 2:45 | CORES deck: open, pin, Use, Show, Use all, Show all used, card name, frequency slider. Telemetry focus core. Processor selector with its confirmation. IEP clock: presets, custom rate, Apply rate, configured rate. Config dialog: Save & Reload, Cancel | two cores stepped together |
| 8 | Layout, theme, help | 1:00 | Panels toggles, drag a header, drag a divider, minimize, Reset Layout, theme button, Help and its close button | |
| 9a | Devices: UART and I2C | 3:15 | Device core selector. UART: Record, Decode, Clear; RX Inject: Hex/ASCII, payload, pin, baud, frames, Inject, Reset inputs. I2C expander: name, address, SCL, SDA, Attach, the pin boxes and registers, Detach. Attached devices list with Detach | `uart_print.asm`, `uart_rx_11frame.asm`, `i2c_tca9538_running_led.asm` |
| 9b | Devices: SSI encoder | 2:15 | SSI encoder: preset, position, frame length, error bits, error value, encoding, both setup buttons, Next position, Show waveforms, Advanced with name, pins and Attach device only; the SSI row in the Attached devices list | SSI reader + encoder model |
| 10 | Peripherals and events | 2:00 | Peripherals tab: SD filter card and Peripheral Interface card with each of their controls, the link that sets the mux. Events tab, the fault badge, the Core fault band (shown with a deliberate access to an unmapped address) | |
| 11 | Where to go next | 0:20 | `getting_started.md` (examples 4, 8 and 9: MAC, SDFM, Peripheral Interface patterns), the MCP server, the headless runner | |

Calls in this table: which six examples run (the shortest path through every
view; the MAC, SDFM and Peripheral Interface examples are named but not run),
the chapter order (Simulator view left to right, then I/O & Devices), and that
the processor selector is shown by switching to another processor and back.

### Controls the browser draws itself

A page screenshot does not contain what the browser or the operating system
draws: the `confirm()` of the processor switch, the `prompt()` for the OSR of
an SD channel chip, the file picker behind "Browse file system...", and the
open list of a `<select>`. Call: these are narrated, not drawn. The capture
accepts the `confirm()` and answers the `prompt()`; the voice says that the
dashboard asks and what it asks, and the subtitle carries the dialog's own
text. A `<select>` changes its value in place while the voice names the
choices (as in the earlier video's tool clips). "Browse file system..." is
pointed at and explained but not opened; files are opened from the server's
`source/` list. No stand-in dialog is drawn, in line with the anti-goal.

Narration style (call): second person, present tense, short sentences, one
idea per cue, the control's on-screen label used exactly as written. No
marketing. Numbers and names are spelled for the voice after `||`.

## Where the result goes

- `video/training/` sources: committed on `docs/training-video`, PR into `main`.
- The MP4s: written to `video/training/build/pru-simulator-training-1-first-steps.mp4`
  and so on, and committed there (decided by Thomas on 2026-11-27; the first
  draft kept them out of git). The four parts are 66 MB together, each under
  GitHub's 50 MB warning, and every rebuilt video adds its size to the
  history. Still open: a link from the main readme, and whether customers
  should also get them as release assets or on a TI video page.

## Checks

- `check.mjs timeline`: every narration cue appears once in the capture, in
  order; no two cues' speech overlaps; a part is 3:00 to 6:30 long.
- `check.mjs coverage`: the report above, over all parts together; fails on an
  uncovered control.
- `check.mjs output`: for every part, 1920x1080, 30 fps, audio and video lengths within 0.1 s,
  subtitle and chapter tracks present.
- A contact sheet (one still per cue, `--sheet`) for reviewing a change without
  rendering everything.
- The repo's `memory.cfg` is byte-identical before and after a build.
- Existing tests are untouched (no product code changes), so only a smoke run
  of the suite is needed before the PR.

## Left to the builder

- Whether frames go to disk as PNG or straight into ffmpeg, and whether frames
  are repeated while nothing on screen changes (25 minutes is about 45 000
  frames).
- How the stepped clock is combined with the websocket (the spike decides);
  falling back to approach B for single scenes is allowed and must be noted in
  the README.
- How the terminal still of chapter 1 is drawn.
- Where the model files come from on the build PC (an existing copy may be
  linked into `build/models/`), with the download commands in the README for
  other PCs.
- The exact wording of `narration.txt`. Thomas reviews it on the first full
  render; wording changes are a rerun, not a redesign.
