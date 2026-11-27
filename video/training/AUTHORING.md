# Writing a part

How to write the narration and the pointer script (the "tour") of one part of the
training video. `narration/part1.txt` and `tour/part1.mjs` are the model: read both
before you start.

## The two files

`narration/partN.txt`: what the voice says.

```
@title Memory, pins, cores and clocks
## memory | Memory
mem-address: Type an address and a length, then click Refresh. || Type an address and a length, then click refresh.
```

- `## id | Title` starts a chapter (an MP4 chapter mark).
- A cue is `key: subtitle text`, with `|| spoken text` when the voice needs another
  spelling. Keys are `[a-z0-9-]+`, unique in the file.
- The first cue is `part-intro: PRU simulator training, part N: <title>.` and the last
  cue names the next part.

`tour/partN.mjs`: what happens on screen. One `await r.cue('key', async () => { ... })`
per narration key, in the same order. Its actions run while the cue is spoken; the next
cue starts when both the speech and the actions are over. Actions outside a cue are
silent.

## Recorder (`lib/recorder.mjs`)

| Call | What it does |
|---|---|
| `r.click(sel)`, `r.dblclick(sel)` | Move the pointer there and really click. Without `sel`: click where the pointer is |
| `r.show(sel, seconds)` | Point at a control without clicking. Counts as "shown" for the coverage check |
| `r.moveTo(sel \| [x, y], frames)` | Move only |
| `r.select(sel, value)` | Change a `<select>` (its list is not drawn headless, so name the choices in the narration) |
| `r.type(sel, text, { clear })` | Click into a field and type |
| `r.press(key)` | Keyboard key. Dashboard shortcuts are ignored while a button or field has the focus: click a neutral element first |
| `r.drag(sel, dx, dy)` | Mouse drag |
| `r.reveal(sel, container)`, `r.scrollTo(container, top)` | Scroll a list or panel |
| `r.outline(cssSel)`, `r.clearOutline()` | Thin frame around a panel or card while it is explained (CSS selector only) |
| `r.until(jsExpression, { max, what })` | Step frames until the expression is true in the page. Every wait for the simulator uses this |
| `r.expect(sel, regex)` | The element's text must match, else the capture fails |
| `r.answerDialog(value)` | Call just before the click that opens a `confirm()` or `prompt()`; returns a promise of the message |
| `r.hold(seconds)` | Let time pass |
| `r.live = true / false` | While true every frame is a real screenshot. Set it while only a canvas changes (Signal Graph, Memory Graph) |
| `r.goto(url)` | Load another page |

Selectors are Playwright selectors (`#id`, `text=`, `:has-text()`, `>> nth=0`).
`lib/dashboard.mjs` has `openFile(r, name)`, `text(page, sel)`, `reg(page, n)`,
`cycles(page)`.

## Rules

1. **Nothing is said that the dashboard does not do.** Take every statement from the
   code under `ui/static/`, the Help dialog text in `ui/static/index.html`,
   `getting_started.md` or `docs/`. Where the docs and the code differ, the code wins
   (the docs name two themes, the code has three).
2. **Assert what the voice claims.** If the cue says "R30 becomes 1", the tour checks
   it with `r.expect` or `r.until` and throws otherwise.
3. **Wait with `r.until`, never with a guessed `r.hold`,** when the simulator has to
   reach a state.
4. **One cue per control, or one cue for a named group** of equal controls (the 20 GPO
   boxes). Point at the control (`show` or `click`) in that cue.
5. **Style:** second person, present tense, short sentences, one idea per cue, labels
   exactly as on screen in the subtitle text. No marketing words.
6. **Spoken form** after `||` for anything the voice would misread: `PRU0` as
   `P R U zero`, `R30` as `R thirty`, `I/O` as `I O`, `IEP` as `I E P`, `GPO` as
   `G P O`, `0x23` as `hex twenty-three`, `0.1` as `zero point one`, `.asm` file names
   spoken without the extension, `HW Reset` as `hardware reset`, `&` as `and`.
7. A part is its two files, `narration/partN.txt` and `tour/partN.mjs`. A helper only
   one part needs stays in its tour file; `lib/dashboard.mjs` is for what several parts
   share.

## Things that are easy to get wrong

- Status badge values are `HALTED`, `RUNNING` and `BREAK`.
- An empty list often still has a child element (the breakpoint row shows "none").
  Count the real items (`.bp-chip`), not `children`.
- A stray click on empty-looking space can hit a control. Check the coordinates on a
  still before using `[x, y]`.
- Load & Assemble does not reset the core: click Reset before running a new program.
- Run executes a chunk of instructions per update and samples the Signal Graph every
  100th instruction in GPIO mode; SIM executes one instruction per interval.
- The page clock only moves in `r.frame()`. Code that waits for real time
  (`page.waitForTimeout`) does not advance the dashboard.

## Loop

```
export PRU_PYTHON=python3                       # a Python with the dashboard's requirements
env -u PYTHONPATH .venv/bin/python tts.py N     # speak (only changed cues are spoken again)
node capture.mjs N --sheet                      # record; on failure see build/partN/failure.png
node check.mjs timeline N                       # 3:00 to 6:30, no overlapping speech
./sheet.sh N                                    # build/partN/contact/NN.jpg, four stills per page
env -u PYTHONPATH .venv/bin/python mix.py N && ./assemble.sh N && node check.mjs output N
node check.mjs coverage N                       # controls that were on screen but never pointed at
```

Look at every contact page. For each still: the pointer is on the control the cue
names, the state matches the sentence, nothing is cut off. Crop a region at full size
with `ffmpeg -v error -y -i build/partN/sheet/<still>.png -vf crop=W:H:X:Y out.png` when
the half-size page is not enough.

To find selectors, write a throwaway script that starts the server with
`startServer` (`lib/server.mjs`), opens the page with `launch`/`newPage`
(`lib/browser.mjs`), and prints `outerHTML` or takes a screenshot. Delete it afterwards.
Never start `ui/server.py` from the repository itself: it rewrites `memory.cfg`.
