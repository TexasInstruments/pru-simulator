# SPI test sequence: step list and bit-by-bit Play (design)

Date: 2026-11-05. Branch: `feat/spi-pru-link` (adds to the SPI link work, before its PR).
Mockup (approved layout A): `docs/superpowers/specs/2026-11-05-spi-sequence-play-mockup.html`.
It is a self-animating sketch with made-up drawing code. It shows the layout and the pacing, not the real scope rendering.

## Intent

The SPI view is a demo at the electronica 2026 booth, and Thomas also uses it to check the link. Today **Run test sequence** runs about 21 frames in one go on the server and shows only one line: "Test sequence: passed, 0 byte errors over N steps". None of those frames can be seen. Thomas expected to watch the walking 1 and walking 0 frames go across the scope bit by bit.

Booth visitors are the main audience. A visitor stays about 30 seconds and should see a few frames drawn bit by bit, after which the loop starts again. Thomas's morning setup is: click **Set up SPI demo**, click **Play**, then walk away. Visitors may touch the laptop while he is away.

## Goals

1. **Recorded steps.** The test sequence keeps the result of every step: its frame (the pin edges), the memory panel contents after the step, and its pass/fail result.
2. **Step chips.** A row of chips sits above the frame strip, one chip per step, each with ✓ or ✗. This is layout A in the mockup. Clicking a chip shows that step's frame in the existing scope, frame strip and memory panels.
3. **Play/Pause.** Play animates the frame bit by bit:
   - the trace draws left to right with a cursor,
   - the current byte is outlined in the frame strip,
   - the PRU1 RX and PRU0 RX panels fill byte by byte,
   - the frame ends on its result, e.g. "16/16 bytes match both ways".
4. **Booth loop.** Play cycles through 4 read/write frames until it is paused: `rw:walking1`, `rw:walking0`, `rw:a5_5a` and `rw:toggle`. Thomas approved this set. Each frame takes about 7 s, about 6 s of drawing plus about 1 s showing the finished frame, so one loop takes about 30 s.
5. **Play stands alone.** Play right after **Set up SPI demo** works. If no recorded sequence exists, Play runs the sequence first (it takes about a second) and then starts the loop.

## Non-goals

- No animation for **Send frame**. It keeps showing its frame immediately, as it does today (my call).
- No separate full-screen or kiosk booth view. The scope stays the size it is today (my call).
- No change to the firmware, the sweep, the timing tables or the test sequence's checks.
- No animation of steps outside the loop. Clicking one shows its completed frame. If Play is pressed while it is selected, the loop continues from the next loop frame (builder's call on the details).

## Anti-goals (would be a failure even if it works)

- **The loop stops, freezes or stutters at the booth.** Thomas will not be at the laptop to fix it.
- **An old trace under new labels.** The view must never show a recorded frame under a mode, rate or MISO delay setting that it was not recorded with. Each shown step names its own command, address and pattern in the step title line, so the Send frame inputs are never read as describing it.
- **A hidden failure.** If a loop step failed, its chip shows ✗ in red, and the loop keeps playing that step as recorded. Play must never skip or hide a failing step.
- **Animated data that isn't the simulator's.** What is drawn must be the step's real recorded pin trace, not data synthesised in the browser. The mockup does synthesise its data, but only to illustrate.
- **A slower dashboard.** Showing the view must not slow the dashboard (see the payload constraint below).

## Decided behaviour

- **Layout A.** The chip row goes above the frame strip, full width. Play/Pause and a speed control go next to **Run test sequence**. The sequence summary line stays.
- **Chips.** Each chip shows the step name (`write:walking1`, …, `unknown_cmd`, `bad_addr`, `abort`, `back_to_back:1`, `back_to_back:2`, `back_to_back`), ✓ or ✗, and the step's notes as a tooltip when it failed. The 4 loop chips are marked as such: green border in the mockup. The current chip is outlined.
- **Steps without a frame.** The `back_to_back` summary step has no frame of its own (it checks the /CS-high gap between `:1` and `:2`). Clicking it shows its result and the measured gap, and the scope keeps or clears its trace (builder's call). It is never part of the loop.
- **Aborted frames.** The `abort` step's frame is aborted on purpose. It shows exactly the edges that were recorded.
- **Replay only.** Play replays the recorded steps in the browser and never runs the simulator again during the loop. The simulator runs once, when the sequence is recorded. This is my call; it is what makes the booth loop unable to stall.
- **Speed control.** The settings are *booth* (default, about 7 s per frame), *slow* and *fast*. The exact factors are the builder's call.
- **Controls are locked during Play (Thomas's decision).** Visitors may touch the laptop, and a stray click must not end the loop. While Play is running, every control in the SPI view is disabled except **Pause**:
  - Mode, SPI CLK, MISO sample delay, Command, Address and Pattern,
  - **Send frame**, **Run test sequence** and **Set up SPI demo**,
  - the step chips and the scope click-to-move-cursor (my call).

  Pressing **Pause** unlocks them. The dashboard's global toolbar (RUN, STEP, RESET, HW RESET, CONFIG …) is outside the SPI view and is not locked (my call).
- **Step title line.** A line above the scope names the shown step: step number, name, command, address and pattern. The mockup has it. The Command, Address and Pattern inputs stay as the user's **Send frame** inputs and do not follow the shown step (my call).
- **What clears the recorded steps (and stops Play in every tab):**
  - changing mode, rate (SPI CLK) or MISO sample delay,
  - **Send frame**,
  - **Set up SPI demo** again,
  - Reset and HW Reset, which rebuild the SPI runtime at the same settings (`_reapply_spi_scenario`, Ruling R21) (my call),
  - leaving the SPI scenario.

  The recorded steps live on the server, so clearing them from any tab or client stops Play in every tab. Pressing Play again records a fresh sequence at the current settings.
- **Run test sequence.** It records the steps and shows the chips without starting Play. The view then shows the last step, which is the same as today's "last frame" behaviour. Whether it pre-selects the first loop step is the builder's call.
- **Timing table.** The table follows the shown step: its "Sim" values are measured from that step's recorded edges (my call). It never shows values from a different frame than the scope. The /CS-high gap (tCSH) needs two frames in a row, so it shows "—" for a single step. The recorder does not keep the previous frame's edges for this. tCSH gets a real value only on the back-to-back steps, where the `back_to_back` step already reports the measured gap. Measuring can happen on the server at record time or on request (builder's call).

## Constraints (from the existing code)

- **Monitor keeps only recent edges.** `SpiBusMonitor` keeps only the most recent frames' edges (`KEEP_FRAMES = 8`, `pru_io/spi_link.py:197`). A step's frame must therefore be captured when that step runs, inside `run_test_sequence` / `frame()` (`pru_io/spi_link.py:912`). It cannot be read back from the monitor afterwards. Today `run_test_sequence` drops `fr` from every step at the end (`s.pop("frame", None)`). Its returned `ok` / `byte_errors` / `steps` contract is used by `tools/spi_sweep.py` and the tests, so any change must keep that contract. One option is an opt-in flag that records the steps, so the sweep stays lean.
- **Per-step snapshot.** Per step, capture what `show()` in `ui/static/spi.js` needs:
  - `frame` in the same shape `_spi_view` builds: `start`, `end`, `bits`, `aborted`, `mosi`, `miso` and `edges`, including the one idle edge before the frame,
  - `memory` (`c_tx`, `c_rx`, `p_rx`, `p_tx` at the step's address),
  - `last` (`cmd`, `addr`, `pattern`),
  - `expect`, `match`, and the step's `ok`, `byte_errors` and `notes`.

  A step whose address is above 0x7F has `p_rx` / `p_tx` = None, as today. Reusing these shapes lets `show()` render a step by substituting it for the live fields.
- **Payload.** `spi_demo` rides inside every core state push (`ui/server.py` around line 1492). The recorded steps, about 21 frames of several hundred edges each, must not be added to every push. Send them once, when the recording changes, for example as their own message or on request, and let the browser cache them by sequence id. A small `sequence` summary in the state (as today, plus an id) is fine.
- **Live state while playing.** While a recorded step is shown, live `spi_demo` state pushes must not overwrite the scope, frame strip or memory panels. Controls, notes and errors still update.
- **Multiple tabs.** Shared selection across tabs exists (`tests/ui_shared_selection.test.js`). Playback and the selected step are local to each browser tab (my call). The recorded steps are shared, because they live on the server.
- **Clocks.** The tests pin their clock as the existing SPI tests do and never read the repo `memory.cfg`.
- **Existing hooks to reuse.** The existing scope cursor (`scopeSvg(state, cursorBit)`), the byte outline in the frame strip (`frameStripHtml(state, cursorByte)`) and the memory "now" index (`memoryHtml(..., nowIndex)`) already take a bit or byte position. The animation should drive these hooks, adding a "draw only up to the cursor" mode for the trace and the memory fill. It should not be a second renderer.

## Tests and docs

- **pytest:**
  - the recorded sequence has one entry per step, and each entry's frame and memory match that step's checked bytes,
  - the loop set is exactly the 4 named steps,
  - recording does not change `run_test_sequence`'s existing result for the sweep,
  - every action in the "What stops Play" list clears the recorded steps,
  - the server sends the recorded steps once rather than on every push.
- **node** (`tests/ui_spi_view.test.js` style):
  - chips render with ✓ / ✗ and the loop marks,
  - clicking a chip shows that step's bytes,
  - Play advances the cursor and moves to the next loop step after the frame ends,
  - Play with no recording sends the sequence action first,
  - during Play every SPI control except Pause is disabled, chips included, and Pause re-enables them,
  - a "recorded steps cleared" update from the server (for example after another tab changed the rate, or after Reset) stops Play,
  - a live state push during Play does not replace the shown step.
- **Docs:**
  - `docs/spi_link.md`: describe the chips, Play, the loop set and the booth setup (**Set up SPI demo** → **Play**),
  - add the booth steps to the electronica 2026 demo how-to (`docs/electronica2026-howto` branch),
  - re-take the SPI view screenshots if the controls bar changes.

## Left to the builder

- The message names and the exact payload format for the recorded steps.
- The speed factors, the hold time at the end of a frame, and the frame rate of the animation (use `requestAnimationFrame` or a timer; it must keep going in a tab that is visible but idle).
- How the trace is clipped at the cursor (clip-path, or path truncation).
- Whether the measurement arrows at the cursor (tCLK, setup, hold) are shown during Play. Hiding them while playing and showing them when paused is suggested.
- Chip styling within the existing SPI view CSS.

## My calls (not decided by Thomas, flag if wrong)

- **Send frame** keeps showing its frame immediately, without animation.
- The animation replays recorded traces; the simulator does not run during the loop.
- Changing a setting while paused clears the recorded steps; nothing re-records until Play is pressed.
- The step chips and the scope cursor are locked during Play too; the global toolbar is not.
- The Command, Address and Pattern inputs do not follow the shown step; the step title line names it instead.
- The timing table follows the shown step; tCSH shows "—" for a single step.
- Reset and HW Reset clear the recorded steps.
- There is no full-screen booth view.
- Playback is local to each tab, but clearing the recorded steps stops Play in every tab.
- Speed defaults to *booth*.
