# SPI test sequence step chips and Play Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The SPI view records every step of the test sequence. It shows the steps as chips above the frame strip, and a Play button replays 4 read/write frames bit by bit in a ~30 s booth loop. All SPI controls are locked while Play runs.

**Architecture:**
- `run_test_sequence` gains an opt-in `record=True` flag that keeps each step's frame edges and memory.
- The scenario layer turns that into UI-shaped steps under a sequence id. The state push carries only the summary.
- A new `spi_steps` websocket request fetches the full steps once per id.
- `spi.js` caches them, renders the chips, and substitutes a step's fields into the live state before rendering. Play is a timer that moves a bit cursor through the existing scope, frame strip and memory hooks.

**Tech Stack:** Python 3.14 (pru_io, FastAPI websocket in `ui/server.py`), vanilla JS (`ui/static/spi.js`, `app.js`), pytest, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-05-spi-sequence-play-design.md` (mockup: `docs/superpowers/specs/2026-11-05-spi-sequence-play-mockup.html`).

## Global Constraints

- **Booth loop:** exactly `rw:walking1`, `rw:walking0`, `rw:a5_5a`, `rw:toggle`, in that order, repeating until paused.
- **Booth speed (default):** about 7 s per frame, of which about 6 s is drawing the 152 bits and about 1 s holds the finished frame. *Slow* and *fast* are the other speeds.
- **Locked during Play:** every SPI-view control except Pause is disabled. That covers Mode, SPI CLK, MISO sample delay, Command, Address, Pattern, Send frame, Run test sequence, Set up SPI demo, the step chips and the scope cursor click. The global toolbar is not locked.
- **Replay only:** Play never runs the simulator during the loop.
- **Play stands alone:** Play with no recorded sequence first sends `spi_sequence`, then starts the loop.
- **What clears the recorded steps (and stops Play in every tab):**
  - changing mode, rate or MISO delay,
  - Send frame,
  - Set up SPI demo,
  - Reset and HW Reset,
  - leaving the SPI scenario.
- **Never show a trace from another frame:** no recorded frame under a mode, rate or MISO delay it was not recorded with, and no "Sim" timing values from a different frame than the scope.
- **Payload:** recorded steps never ride in the per-core state push.
- **Send frame stays unanimated:** it shows its frame immediately. No full-screen booth view.
- **Failing steps stay visible:** a failing step's chip shows ✗ in red, and the loop plays it as recorded; it is never skipped.
- **Clocks:** tests pin their clock (`sim_config` / `fresh_sim` fixtures). The repo `memory.cfg` is never read or staged.
- **Commands:**
  - Python tests: `env -u PYTHONPATH .venv/bin/python -m pytest <path> -q`.
  - UI tests: run one file at a time with `node --test tests/<file>.test.js`.
- **Existing failures:** 3 pytest failures (`test_clock_speed_endpoint` ×2, `test_server` am263x) and 9 node failures (`ui_capture_integrity` 2, `ui_chrome` 1, `ui_shared_selection` 6) already fail on `main`. They are not regressions.

## Review Focus

1. **Play pressed, but the sequence run errors** (e.g. a timeout). Play must stop and unlock the controls, not stay locked with "Running…" forever. Test in Task 4.
2. **A loop step that failed.** Its chip shows ✗ (`spi-chip-bad`), and Play still plays it, never skipping to the next. Test in Task 4.
3. **`spi_steps` reply for a stale id** (the recording was cleared or replaced while the request was in flight). The browser ignores it and does not show those steps. Test in Task 3.
4. **Many state pushes for the same sequence id** (a running dashboard, or a reconnect). The browser asks for `spi_steps` once per id, not once per push. Test in Task 3.
5. **Steps with no frame or no peripheral block** (`back_to_back` has `frame: null`; `bad_addr` has `p_rx`/`p_tx` null). Clicking them renders without throwing. Test in Task 3.

---

### Task 1: Record per-step traces in `run_test_sequence`

**Files:**
- Modify: `pru_io/spi_link.py` (`run_test_sequence` ~889-982; add `frame_edges` near `measure_timing` ~266)
- Modify: `pru_io/scenarios.py` (`_spi_view` ~314: use `frame_edges`)
- Test: `tests/test_spi_sweep.py`

**Interfaces:**
- Produces: `frame_edges(edges: list, frame: dict) -> list[list[int]]`. It returns the last edge before `frame["start"]` (if any), followed by every `[cycle, word]` with `start <= cycle <= end`. This is exactly what `_spi_view` computes today; `_spi_view` now calls it.
- Produces: `run_test_sequence(runtime, patterns=None, record: bool = False) -> dict`. The return value is unchanged, plus:
  - every frame step dict gains `"pattern": str`. It is the pattern whose controller bytes were sent: `names[0]` for `unknown_cmd` / `bad_addr` / `abort` / `back_to_back:1`, and `names[-1]` for `back_to_back:2`.
  - with `record=True`, every frame step also keeps `"trace"`, captured inside `frame()` straight after `rt.transfer`, because the monitor keeps only 8 frames:
    ```
    {"frame": None | {"start", "end", "bits", "aborted",
                      "mosi": bytes, "miso": bytes, "edges": frame_edges(...)},
     "memory": {"c_tx": bytes(16), "c_rx": bytes(16),
                "p_rx": bytes(16) | None, "p_tx": bytes(16) | None}}   # None when addr > ADDR_MAX
    ```
  - the `back_to_back` summary step gets `"trace": None`.
  - with `record=False`, no `trace` key is added, so the sweep stays lean.

- [ ] **Step 1: Write the failing tests** in `tests/test_spi_sweep.py`. Reuse the file's `_runtime(sim_config, mode, core, half_k, miso_delay)` helper with the default row (mode 1, 200 MHz, k 4, the row's `miso_delay` from `load_supported()[1]` at 25 MHz).
  - `test_sequence_record_keeps_one_trace_per_frame_step`. With `res = run_test_sequence(rt, record=True)`, check:
    - `res["ok"] is True`,
    - `len(res["steps"]) == 21`,
    - every step except `back_to_back` has a `trace` whose `frame["edges"]` is non-empty,
    - for `rw:walking1`, `trace["frame"]["mosi"] == bytes([3, 0x40, 0]) + PATTERNS["walking1"][0]`, and `trace["memory"]["c_rx"] == PATTERNS["walking1"][1]` and `trace["memory"]["p_rx"] == PATTERNS["walking1"][0]`,
    - `bad_addr`'s `trace["memory"]["p_rx"] is None`,
    - the `back_to_back` step's `trace is None`,
    - `abort`'s `trace["frame"]["aborted"] is True`,
    - `rw:walking1`'s `pattern == "walking1"` and `back_to_back:2`'s `pattern == "toggle"`.
  - `test_sequence_without_record_has_no_trace`: no step of `run_test_sequence(rt)` has a `trace` key, and `ok` / `byte_errors` / the step names are the same as with `record=True` on a fresh runtime.

- [ ] **Step 2: Run them to verify they fail.**
  Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/test_spi_sweep.py -q -k "record or without_record"`
  Expected: FAIL (`unexpected keyword argument 'record'`).

- [ ] **Step 3: Implement.** Add `frame_edges`, the `record` flag, the `pattern` key and the trace capture. In the final loop, pop `"frame"` as today, and pop `"trace"` only when `record` is False. Switch `_spi_view` to `frame_edges`.

- [ ] **Step 4: Run to verify they pass, along with the existing SPI tests.**
  Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/test_spi_sweep.py tests/test_spi_scenario.py tests/test_spi_link_model.py -q`
  Expected: all PASS. Also run `env -u PYTHONPATH .venv/bin/python tools/spi_sweep.py --check` and expect it to report a match.

- [ ] **Step 5: Commit**
  ```bash
  git add pru_io/spi_link.py pru_io/scenarios.py tests/test_spi_sweep.py
  git commit -m "feat(spi): run_test_sequence can record each step's frame and memory"
  ```

---

### Task 2: Scenario recording, `spi_steps` request, publish to all tabs

**Files:**
- Modify: `pru_io/scenarios.py` (`spi_send`, `spi_sequence`, `spi_demo_state`, new `spi_sequence_steps`, `LOOP_STEPS`)
- Modify: `pru_io/spi_link.py` (`SpiLinkRuntime.__init__`: `self.recording = None`)
- Modify: `ui/server.py` (new `spi_steps` action next to `spi_supported` ~1123; the `spi_send`/`spi_sequence` branch ~1155 also calls `_publish_updates(origin=websocket)`)
- Test: `tests/test_spi_scenario.py`

**Interfaces:**
- Consumes: `run_test_sequence(..., record=True)`, `frame_edges`, `measure_timing` (Task 1).
- Produces:
  - `LOOP_STEPS = ("rw:walking1", "rw:walking0", "rw:a5_5a", "rw:toggle")` in `pru_io/scenarios.py`.
  - `spi_sequence(sim) -> None`. It records the steps. `runtime.sequence` becomes the summary without traces plus `"id": int` and `"loop": list(LOOP_STEPS)`. The id comes from a module-level `itertools.count(1)`, so it is unique across runtimes. `runtime.recording = {"id": id, "steps": [...]}`.
  - `spi_send(...)` clears both `runtime.sequence` and `runtime.recording`. `spi_setup`, `scenario_load`, Reset and HW Reset already build a new runtime, so the recording is gone there with no extra code.
  - `spi_sequence_steps(sim) -> dict`. It returns `{"id": int, "steps": [step...]}` and raises `ValueError("No recorded test sequence")` when there is no recording. Each step is:
    ```
    {"step", "ok", "byte_errors", "notes", "cmd", "addr", "pattern",   # cmd/addr/pattern absent on back_to_back
     "tcsh_cycles" (back_to_back only),
     "frame":  same shape as spi_demo_state()["frame"] (hex strings, edges) or None,
     "memory": same shape as spi_demo_state()["memory"] (hex lists / None),
     "last":   {"cmd", "addr", "pattern"} or None,
     "expect": {"mosi": cmd in (WRITE, RW), "miso": cmd in (READ, RW)} or None,
     "match":  {"mosi": int|None, "miso": int|None}   # computed like spi_demo_state, None when addr > 0x7F
     "timing": measure_timing(trace edges, cfg, hop_delay=..., p1_fetch_cycles=...) or []}
    ```
  - Websocket: `{"action": "spi_steps"}` → `{"type": "spi_steps", "id": ..., "steps": [...]}`, or `{"type": "error", "tag": "spi", ...}`.
  - `spi_demo_state()["sequence"]` keeps today's keys (`ok`, `byte_errors`, `steps` without frames or traces), plus `id` and `loop`.

- [ ] **Step 1: Write the failing tests** in `tests/test_spi_scenario.py`, using `fresh_sim`, `_load_spi`, `_state_after` and `_drain_until`.
  - `test_sequence_state_carries_summary_only`: after `spi_sequence`, the `sequence` in the state has an int `id`, has `loop == ["rw:walking1", "rw:walking0", "rw:a5_5a", "rw:toggle"]`, and no step in it has a `frame`, `trace` or `memory` key.
  - `test_spi_steps_returns_recorded_frames`:
    - `{"action": "spi_steps"}` → the reply's `id` equals the state's `sequence.id`, and it has 21 steps,
    - the `rw:walking1` step has `frame["mosi"][:3] == ["03", "40", "00"]` and `match == {"mosi": 16, "miso": 16}`, its timing has a row whose symbol contains `SU,MISO`, and its `tCSH` row has `sim_ns is None` (a single step shows "—"),
    - the `back_to_back` step has `frame is None` and an int `tcsh_cycles`,
    - the `bad_addr` step has `memory["p_rx"] is None`.
  - `test_spi_steps_without_recording_is_spi_error`: `spi_steps` straight after load → an error with tag `spi`.
  - `test_recording_cleared_by` (parametrized):
    - over `("spi_send", {"cmd": 3, "addr": 2, "pattern": "count"})`, `("spi_setup", {"mode": 1, "spi_mhz": 25.0})`, `("reset", {})`, `("hard_reset", {})` and `("scenario_load", {"name": SPI})`,
    - after each one, the state's `sequence is None` and `spi_steps` → a `spi` error.
  - `test_sequence_and_clear_reach_other_tabs`:
    - open two websockets on the same client and load SPI in A,
    - A sends `spi_sequence` → B receives a pru0 state whose `sequence.id` is set,
    - A sends `spi_send` → B receives a pru0 state with `sequence is None`.
  - `test_second_sequence_gets_new_id`: two `spi_sequence` runs give different ids.

- [ ] **Step 2: Run to verify they fail.**
  Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/test_spi_scenario.py -q -k "summary_only or spi_steps or cleared_by or other_tabs or new_id"`
  Expected: FAIL.

- [ ] **Step 3: Implement** the interfaces above. Build the UI-shaped `frame`, `memory` and `match` with the same helpers `spi_demo_state` / `_spi_view` use (`_hex`, the match counting), so the two cannot drift. Factor them into small functions if needed.

- [ ] **Step 4: Run the SPI suites to verify they pass.**
  Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/test_spi_scenario.py tests/test_spi_sweep.py -q`
  Expected: all PASS.

- [ ] **Step 5: Commit**
  ```bash
  git add pru_io/scenarios.py pru_io/spi_link.py ui/server.py tests/test_spi_scenario.py
  git commit -m "feat(spi): recorded test-sequence steps via spi_steps, sequence id in state"
  ```

---

### Task 3: Step chips, step title, show a step

**Files:**
- Modify: `ui/static/index.html` (SPI pane ~2385-2390). Add these inside `#spi-result`, above the `spi-sec` frame heading:
  - `<div class="spi-steps" id="spi-steps"></div>`
  - `<p class="spi-step-title" id="spi-step-title"></p>`
- Modify: `ui/static/spi.js`
- Modify: `ui/static/spi.css` (`.spi-steps`, `.spi-chip`, `.spi-chip-ok`, `.spi-chip-bad`, `.spi-chip-loop`, `.spi-chip-cur`, `.spi-step-title`, in the existing palette: ok `#1f8a5e`, bad `#d64545`, outline `#e8a500` like the frame-strip cursor)
- Modify: `ui/static/app.js` (`else if (msg.type === "spi_steps") window.SpiView?.setSteps(msg);` next to the `spi_supported` branch ~461)
- Test: `tests/ui_spi_view.test.js`

**Interfaces:**
- Consumes: the `sequence.id` / `sequence.loop` from the state and the `spi_steps` message (Task 2).
- Produces (exported on `window.SpiView`):
  - `setSteps(msg: {id, steps}) -> void`: caches the steps only if `msg.id === view.last?.sequence?.id`, then re-renders.
  - `stepState(state, step) -> state`: a pure function returning a shallow copy of `state` with `frame`, `memory`, `last`, `expect`, `match` and `timing` taken from `step`. Everything else (config, hop_delay, status, faults, sequence) stays live.
  - `chipsHtml(steps, loop, current, locked) -> string`: one `<button class="spi-chip …" data-step="i">` per step. `spi-chip-ok` with `✓` or `spi-chip-bad` with `✗`, `spi-chip-loop` when the name is in `loop`, `spi-chip-cur` when `i === current`, the notes joined into `title` when the step failed, and `disabled` when `locked`.
  - `view.shown` (an index, or null) holds the selected step.
  - The step title text is `Step ${i+1}/${n} · ${step} · ${CMD_NAMES[cmd]} @ 0x${hex2(addr)} · pattern ${pattern}`, where `CMD_NAMES = {1: 'WRITE (01)', 2: 'READ (02)', 3: 'READ/WRITE (03)', 4: 'Unknown (04)'}`. For `back_to_back` it reads `Step 21/21 · back_to_back · /CS-high gap ${tcsh_cycles} cycles`.
- Behaviour in `show(state)`:
  - when `state.sequence?.id` differs from the cached id, drop the cache and set `view.shown = null`; send `{action: 'spi_steps'}` once per new id (remember the id requested).
  - when `state.sequence` is null, clear the cache, `view.shown` and the chips.
  - when `view.shown != null`, render the scope, frame strip, memory, badge and timing from `stepState(state, steps[view.shown])`. A step with `frame: null` renders the existing empty-scope message.
  - a chip click (delegated on `#spi-steps`, reading `data-step`) sets `view.shown` and re-renders. It is ignored while locked.

- [ ] **Step 1: Write the failing tests** in `tests/ui_spi_view.test.js`. Build steps from the fixture:
  ```
  mk = (name, extra) => ({ step: name, ok: true, byte_errors: 0, notes: [], cmd: 3, addr: 2, pattern: 'count',
       frame: fx.state.frame, memory: fx.state.memory, last: fx.state.last, expect: fx.state.expect,
       match: fx.state.match, timing: fx.state.timing, ...extra })
  ```
  The state is `{...fx.state, sequence: {id: 7, ok: true, byte_errors: 0, steps: [], loop: ['rw:walking1']}}`.
  - `chips render ok, bad and loop marks`:
    - `chipsHtml([mk('write:count'), mk('rw:walking1'), mk('abort', {ok: false, notes: ['x']})], ['rw:walking1'], 1, false)` contains 3 `spi-chip` buttons,
    - the second has `spi-chip-loop` and `spi-chip-cur`,
    - the third has `spi-chip-bad`, `✗` and `title="x"`,
    - with `locked = true`, every button is `disabled`.
  - `steps are requested once per sequence id and stale replies are ignored` (Review Focus 3 and 4):
    - rendering the id-7 state twice → exactly one `{action: 'spi_steps'}` sent,
    - `setSteps({id: 6, steps: [...]})` → `#spi-steps` stays empty,
    - `setSteps({id: 7, steps: [...]})` → the chips appear.
  - `clicking a chip shows that step`:
    - after `setSteps` with a step whose `frame.mosi[0]` is changed to `'01'`, click its chip (`get('spi-steps').listeners.click({ target: { dataset: { step: '0' } } })`),
    - `#spi-frame-strip` now shows `01` in the CMD cell, and `#spi-step-title` reads `Step 1/…`,
    - re-rendering the live `fx.state` (a state push) keeps the step shown.
  - `frameless and out-of-range steps render` (Review Focus 5):
    - showing `mk('back_to_back', {frame: null, memory: {...fx.state.memory, p_rx: null, p_tx: null}, tcsh_cycles: 16})` does not throw,
    - the scope has the empty-trace message, and the title contains `/CS-high gap 16 cycles`.
  - `a cleared sequence clears chips and selection`: rendering the state with `sequence: null` empties `#spi-steps` and `#spi-step-title`.

- [ ] **Step 2: Run to verify they fail.**
  Run: `node --test tests/ui_spi_view.test.js`
  Expected: the new tests FAIL (`chipsHtml is not a function`, …).

- [ ] **Step 3: Implement** the interfaces above in `spi.js`, `index.html`, `spi.css` and `app.js`. The existing index-ids test enforces that every `byId('…')` id exists in `index.html`.

- [ ] **Step 4: Run to verify everything passes.**
  Run: `node --test tests/ui_spi_view.test.js`
  Expected: all PASS.

- [ ] **Step 5: Commit**
  ```bash
  git add ui/static/spi.js ui/static/spi.css ui/static/index.html ui/static/app.js tests/ui_spi_view.test.js
  git commit -m "feat(spi-ui): test sequence step chips; click a step to show its frame"
  ```

---

### Task 4: Play / Pause, bit-by-bit animation, controls locked

**Files:**
- Modify: `ui/static/index.html`. In `.spi-bar`, directly after `#spi-sequence`, add:
  - `<button … id="spi-play">` with the text `▶ Play`, in the same `runtime-device-button btn-17` markup as its neighbours,
  - `<label class="spi-field" for="spi-speed">Speed <select id="spi-speed"><option value="1" selected>booth</option><option value="0.5">slow</option><option value="3">fast</option></select></label>`.
- Modify: `ui/static/spi.js`, `ui/static/spi.css`
- Test: `tests/ui_spi_view.test.js`

**Interfaces:**
- Consumes: `view.steps`, `view.shown`, `stepState`, `chipsHtml`, `setSteps` (Task 3). `sequence.loop` (Task 2).
- Produces:
  - `view.play = { running: bool, pending: bool, bit: number, hold: number }`.
  - `BOOTH_BITS_PER_S = 152 / 6` and `HOLD_S = 1`, both at speed 1. The value of `#spi-speed` multiplies them.
  - `tick(ms: number) -> void` (exported):
    - while drawing, advance `play.bit` by `BOOTH_BITS_PER_S * speed * ms / 1000`, up to 152,
    - when the frame is complete, accumulate `hold` until `HOLD_S / speed` has passed,
    - then set `view.shown` to the next loop step in `sequence.loop` order (wrapping), and reset `bit` and `hold`.
    - A timer calls it every 40 ms through `globalThis.setInterval`, if present, while `running`. The node vm context has no timers, so tests call `SpiView.tick` directly.
  - `setLocked(locked: bool)`. `setBusy` computes `el.disabled = busy || view.locked || …` for its ids, and the lock also covers `spi-cmd`, `spi-addr`, `spi-pattern` and `spi-setup-button`. Chips re-render with `locked`, and the scope click handler returns early while locked. `spi-play` and `spi-speed` are never locked.
  - The Play button text is `❚❚ Pause` while running and `▶ Play` otherwise.
- Behaviour:
  - **Play with cached steps:** start at the first loop step, or continue from the current loop step when one is shown, with `bit = 0`, `running = true`, and lock the controls.
  - **Play with no recording:** set `pending`, send `{action: 'spi_sequence'}` and lock the controls. When `setSteps` delivers steps for the new id, start as above.
  - **Pause:** stop the timer and unlock the controls. The shown step stays, with its full frame drawn (`bit = 152`).
  - **The sequence id changes or becomes null while running or pending** (another tab, or Reset): stop and unlock.
  - **`showError` while pending or running:** stop and unlock (Review Focus 1).
  - **While running:** the scope draws only the part before the cursor bit's capture cycle, and the cursor is `Math.floor(play.bit)`. Clip at that cycle; clip-path or path truncation is the builder's call. The measurement arrows are hidden while running.
    - The frame strip outlines byte `Math.floor(bit / 8)`.
    - The PRU1 RX and PRU0 RX panels show only the bytes up to the current data byte. Add a `filled` parameter to `memoryHtml` (default 16); unfilled cells show `··`.
    - The compare badge reads `Transferring…` until `bit` reaches 152, then shows the step's real badge.

- [ ] **Step 1: Write the failing tests** in `tests/ui_spi_view.test.js`, reusing Task 3's `mk`. The loop is `['rw:walking1', 'rw:walking0']`, with steps `[mk('write:count'), mk('rw:walking1'), mk('rw:walking0', {ok: false, notes: ['bad']})]`.
  - `Play locks every SPI control except Pause and Speed`:
    - after `setSteps` and clicking `spi-play`, `disabled` is true for `spi-mode`, `spi-rate`, `spi-miso-delay`, `spi-cmd`, `spi-addr`, `spi-pattern`, `spi-send`, `spi-sequence` and `spi-setup-button`,
    - `spi-play` and `spi-speed` are enabled, every chip is `disabled`, and `spi-play` reads `❚❚ Pause`,
    - a state push (re-render) keeps everything locked,
    - clicking `spi-play` again unlocks everything.
  - `Play draws bit by bit and advances through the loop, including a failing step` (Review Focus 2):
    - after Play, the title shows `rw:walking1`,
    - `tick(3000)` → `#spi-compare` reads `Transferring…` and the frame strip's `spi-c-cur` cell is a data byte (index 3 or more),
    - `tick(3100)` then `tick(1100)` → the title shows `rw:walking0` and its chip has `spi-chip-bad`,
    - another full frame (`tick(6100)`, `tick(1100)`) → back to `rw:walking1`.
  - `fast speed finishes a frame sooner`: with `spi-speed` set to `'3'`, `tick(2100)` then `tick(400)` → the title has moved on to `rw:walking0`.
  - `Play with no recording runs the sequence first`:
    - on a state whose `sequence` is null, clicking Play sends `{action: 'spi_sequence'}` and the controls are locked,
    - rendering a state with `sequence.id: 9` sends `spi_steps`,
    - `setSteps({id: 9, …})` starts playing (the title shows the first loop step).
  - `sequence error while pending unlocks` (Review Focus 1): Play with no recording, then `showError(['timeout'])` → `spi-send` is enabled and `spi-play` reads `▶ Play`.
  - `a cleared or replaced sequence stops Play`: while running, rendering a state with `sequence: null` → `spi-play` reads `▶ Play` and the controls are enabled.

- [ ] **Step 2: Run to verify they fail.**
  Run: `node --test tests/ui_spi_view.test.js`
  Expected: the new tests FAIL.

- [ ] **Step 3: Implement** the interfaces and behaviour above.

- [ ] **Step 4: Run to verify everything passes.**
  Run: `node --test tests/ui_spi_view.test.js` (expect all PASS), then each `tests/ui_*.test.js` file one at a time. Expect only the 9 failures that already fail on `main` (see Global Constraints).

- [ ] **Step 5: Check in the real dashboard.**
  - Start it with `env -u PYTHONPATH .venv/bin/python -m ui.server`, then go to I/O & Devices → SPI → Set up SPI demo → Play.
  - Watch one full loop (about 30 s) and confirm:
    - the trace grows left to right,
    - the 4 loop frames cycle,
    - the controls are greyed out,
    - Pause unlocks them,
    - changing the rate after Pause clears the chips.

- [ ] **Step 6: Commit**
  ```bash
  git add ui/static/spi.js ui/static/spi.css ui/static/index.html tests/ui_spi_view.test.js
  git commit -m "feat(spi-ui): Play replays the booth loop bit by bit with the SPI controls locked"
  ```

---

### Task 5: Docs, screenshots, handoff

**Files:**
- Modify: `docs/spi_link.md`. In "Run the demo in the dashboard", add a step after step 4 covering:
  - the step chips (✓ / ✗, click to show a step),
  - **Play**: the booth loop of the 4 frames, about 30 s, repeating, with the speed choices,
  - that Play runs the sequence itself when needed,
  - the lock (only Pause works while playing),
  - what clears the recorded steps.

  Add one line on the booth setup: **Set up SPI demo** → **Play**. In "Run from Python", document `run_test_sequence(..., record=True)` and its `trace` / `pattern` keys.
- Modify: `docs/reports/img/spi-view-*.png`. Re-take all four in the browser the same way `docs/handoff/2026-11-04-spi-pru-link-sdd/task-7-report.md` describes. The controls bar now has Play and Speed, and the chip row is visible after a sequence run.
- Create: `docs/handoff/2026-11-05-spi-sequence-play.md`. It covers:
  - the state of the work,
  - the commits,
  - the test results against the existing failures,
  - a note that the electronica 2026 booth how-to (branch `docs/electronica2026-howto`, not touched here) still needs the **Set up SPI demo** → **Play** steps.

- [ ] **Step 1:** Write the doc changes.
- [ ] **Step 2:** Re-take the screenshots and check that each one shows the new controls.
- [ ] **Step 3:** Run the full Python suite: `env -u PYTHONPATH .venv/bin/python -m pytest tests -q`. Expect only the 3 failures that already fail on `main`. Also run `env -u PYTHONPATH .venv/bin/python tools/spi_sweep.py --check` and expect it to report a match.
- [ ] **Step 4: Commit**
  ```bash
  git add docs/spi_link.md docs/reports/img/spi-view-*.png docs/handoff/2026-11-05-spi-sequence-play.md
  git commit -m "docs(spi): step chips and booth Play loop; screenshots; handoff note"
  ```
