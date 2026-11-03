# PDM audio demo: verification report (2026-11-03)

Branch `feat/pdm-audio-demo`, base `main`. Design:
`docs/superpowers/specs/2026-11-03-pdm-audio-demo-design.md`. Overview and
booth steps: `source/pdm/README.md`.

## What the demo does

PRU1 (`source/pdm/pdm_gen.asm`) synthesises two PDM microphones from 16 kHz
clips: a 3.072 MHz clock on R30 bit 0 and one data line on R30 bit 1 (left
bit valid at the rising edge, right bit at the falling edge). PRU0
(`source/pdm/pdm_rx.asm`) reads them with the ICSS sigma-delta hardware (SINC3,
OSR 96), then comb, FIR /2, DC blocker and sat24, and writes 16 kHz PCM rings in
shared RAM. The dashboard's Audio view draws both channels as live spectrograms.

Simulator additions: a pin-driven SD input (`PdmLink`), an exact idle skip for
run modes, and run-ahead scheduling for one-way producer/consumer pairs.

## Results

### Correctness (all pass)

| Check | Result |
|---|---|
| Generator edges and data bits equal the Python references (`tests/test_pdm_gen.py`, incl. negative samples) | pass |
| Setup margin 26 cycles (130 ns), hold 6-7 cycles | measured over 586 periods |
| Receiver PCM equals `pcm_chain_for_channel` sample for sample (`tests/test_pdm_rx.py`) | pass |
| Worst PRU0 window at N_CH = 2 | 1009 cycles of 6250 |
| End-to-end tones (0.25 s clips) | ch0 1000.008 Hz, ch1 3000.000 Hz, crosstalk -100.6 / -94.3 dB (limit -60) |
| Chirp ridge (`chirp_lin_100_7000.wav`) | pipeline delay 14 samples, worst ridge error 0.48 bin (limit 2) |
| Idle skip equals plain stepping over the full pipeline (merge blocker) | pass, `test_idle_skip_equivalence_full_pipeline` |
| Run-ahead equals lockstep over the full pipeline (merge blocker) | pass, `test_run_ahead_equivalence_full_pipeline` |
| Ring overrun is reported, never a silent gap | `test_overrun_is_reported`, `test_ring_overrun_is_reported_not_fatal` |
| History rebuild after reconnect | `test_history_rebuild_after_reconnect` (server), real browser below |

### Speed

`env -u PYTHONPATH .venv/bin/python source/pdm/tools/bench.py --audio-ms 20`
(Task 7) and a re-run with `--audio-ms 5` at the end of the project:

| mode | skip | 20 ms run: ms audio / s | s audio / min | 5 ms run: ms audio / s | s audio / min |
|---|---|---:|---:|---:|---:|
| lockstep | off | 0.551 | 0.0331 | 0.549 | 0.0329 |
| lockstep | on | 0.577 | 0.0346 | 0.576 | 0.0345 |
| run-ahead | off | 0.623 | 0.0374 | 0.661 | 0.0397 |
| run-ahead | on | **1.494** | **0.0896** | **1.514** | **0.0908** |

The target of 0.1 s of audio per minute is **missed by about 10 %** (spec
estimate was 0.15). The user decided not to optimise further. The pre-project
baseline was 0.61 ms/s, so run-ahead + skip is about 2.4x faster. In the browser
the Audio view showed 1.4-1.5 ms audio/s, i.e. about 5 minutes for the 450 ms
speech clip.

Profile (cProfile, 5 ms, run-ahead + skip): `step_run` 14.2 s cumulative, of
which `PeripheralInterface.advance_cycles` 7.6 s (`perif_channel.advance`,
`set_busy`, `rx_sample_edge`, `_read_u32`). The PERIF model advances its three
channels on every PRU step while the PERIF mux is disabled. Skipping or batching
that would roughly double the speed (about 0.18 s/min). Future work, in
core/PERIF.

### clpru (TI CGT 2.3.3) assembly check

Both firmwares assemble with 0 errors and 0 warnings. clpru prints nothing on
success. Objects were written to the session scratchpad, not the repo.

```
CL=~/ti/ti-cgt-pru_2.3.3/bin/clpru; INC=~/ti/ti-cgt-pru_2.3.3/include
$CL -v4 -c -I source/pdm -I $INC --output_file=$S/pdm_gen.obj source/pdm/pdm_gen.asm   # rc=0
$CL -v4 -c -I source/pdm -I $INC --output_file=$S/pdm_rx.obj  source/pdm/pdm_rx.asm    # rc=0
```

Build-option variants of `pdm_rx.asm`, made on temp copies (`N_CH .set 2` changed
by `sed`, with the `.inc` files copied next to it):

```
N_CH 8:  clpru -v4 -c -I <tmp> -I $INC pdm_rx.asm        # rc=0
N_CH 9:  "pdm_rx.asm", ERROR!   at line 177:
          [ ***** USER ERROR ***** - ]
                 N_CH must be 1..8
                    .emsg "N_CH must be 1..8"
         Errors in Source - Assembler Aborted
         1 Assembly Error, No Assembly Warnings        # rc=1, the guard works
```

(The `--silicon_version=4` spelling gave the same result as `-v4`.) clpru
rejected no syntax the simulator accepts, so no firmware change was needed.

### Real-browser check (headless Chromium 156, light theme)

Headless Chromium (`/snap/bin/chromium --headless=new --remote-debugging-port`)
was driven through the DevTools protocol with a short stdlib/`websockets` Python
script (no new dependency). This is a real browser: the page's own `audio.js`,
websocket and canvas ran, and the script only clicked buttons and read the DOM.
The server ran from a **copy of the working tree** in the session scratchpad with
its own `memory.cfg` (the server resolves `memory.cfg` from the project root, not
the working directory), on port 18765. The repo's `memory.cfg` stayed
byte-identical (md5 `e10f2ca6...` before and after) and the server and browser
were stopped afterwards.

Observed:

1. **Audio tab opens** with the clip lists filled (5 clips), progress "0 ms of
   450 ms audio", mode `run-ahead`. Screenshot `01-audio-idle-light.png`.
2. **Pick `speech_librivox.wav` / `chirp_lin_100_7000.wav` and Run**: the
   progress went 6, 14, 22, ... 60 ms over 40 s; "ms audio / s" read **1.5**;
   load PRU0 16 %, PRU1 45 %; no error, notice or overrun. Spectrogram columns
   appeared on both channels. Right channel shows the chirp ridge rising to
   about 2 kHz in 60 ms. Screenshot `03-audio-running-later-light.png`. The first
   few columns of the speech channel are empty because the clip starts with silence.
3. **Booth in/out** works (class `audio-booth`, button text `Exit booth`, then
   back to `Booth`); the run continued while in booth. Screenshot
   `07-audio-booth-1920.png` (1920x1080, after the fix below).
4. **Reload mid-run**: the run pauses (the owner tab is gone), progress was 72 ms,
   both spectrograms were rebuilt from the server history (screenshot
   `05-audio-after-reload.png`), the Run button is enabled again. Pressing **Run**
   continued at 78, 84, 92, 100 ms, with no reset to zero. **Stop** released the run
   (102 ms).
5. The browser log shows one warning, `requestFullscreen ... user gesture`,
   because the script clicks via JavaScript; the code catches it. No other console
   errors.

Defect found and fixed: in booth mode the canvas text scaled with the height
only, so on 1440x900 and 1920x1080 the time-axis labels ("100 ms", "200 ms",
...) overprinted each other and the "dBFS" label. Fix in `audio.js`: the font is
also capped at 3.5 % of the canvas width (`ui/static/audio.js`, one line;
`tests/ui_audio.test.js` passes). Left over, cosmetic: at 1920x1080 the booth
stage uses about 1555 px of the width, a blank band remains at the right.

Websocket flow (Python `websockets` client, same server), `ws_flow` result:

```
setup: pdm_status mode run-ahead, clips speech_librivox.wav / chirp_lin_100_7000.wav
chunk 0..3: audio_ms 2,4,6,8; ~1.3 s wall per 2 ms chunk; speed 1.54-1.56 ms/s; counts [30,29] ... [126,125]
DISCONNECT
pdm_history on a new connection: {0: 126, 1: 125}, audio_ms 8.0, running False
  rebuilt history == what the first connection received: OK
chunk 4..6 (new connection): counts [158,157] [190,189] [222,221], no gap, no duplicate
third connection: history == live concatenation, [222, 221]: OK
pdm_reset: audio_ms 0.0
```

What was **not** checked in a real browser: fullscreen (needs a real user
gesture; the code path was only exercised without a gesture), a second tab as
observer, touch and hi-dpi screens, the dark and contrast themes, and a run to
the end of the clip (about 5 minutes; the end of a run is covered by
`test_run_ends_at_done_and_releases_execution`).

### Full verification

`env -u PYTHONPATH .venv/bin/python -m pytest tests -q`: **3 failed, 3455
passed, 3 skipped, 2 xfailed** (373 s). `node --test tests/ui_*.test.js`: 97
pass, 9 fail.

Compared with `baseline-failures.txt` (25 pytest, 9 node failures, taken with
memory.cfg at 200/300 MHz):

- **New failures: none**, in pytest or node.
- Still failing, as in the baseline (clock and AM263x config):
  `test_clock_speed_endpoint.py::test_clock_readout_reports_actual_groups_and_presets[AM263x ...]`,
  `test_clock_speed_endpoint.py::test_group_one_edit_does_not_materialize_default_group_zero`,
  `test_server.py::test_am263x_state_advertises_only_supported_cores`.
- 22 baseline failures now pass, because the local `memory.cfg` is 200/200 MHz
  (the user chose to keep it): the perif loopback and drift tests, two
  `test_server.py` multicore catch-up tests, `test_multicore_ws`,
  `test_dashboard_tabs` (2), `test_ssi_runtime` (2), `test_mcp_stdio_device_sdk`,
  `test_perif_server` (2) and `test_pru1_core`. They depend on that file, not on
  this branch.
- Node: the same 9 baseline failures (`ui_chrome.test.js` and the shared-execution
  membership tests), unchanged.
- `memory.cfg` was not staged.

## Concerns and follow-ups

- Speed misses the 0.1 s/min target by about 10 %. The fix is in the PERIF model
  (see profile), not in the PDM code.
- Real-hardware behaviour is untested; see the porting notes in the README:
  GPCFG SD mux, pinmux, IM73D122 edge assignment, CTBIR, SD `ch_sel` latency,
  shared-RAM `LBBO` arbitration on PRU1's clock.
- N_CH 3..8 is extrapolated (4012 / pessimistic 4334 of 6250 cycles) and only
  assembled; the simulator wires SD channels 0..2.
- The odd-channel PCM is 1/32 kHz later than the even one (FIR stagger); relevant
  for a microphone array, not for the spectrogram.
- Default CI runs the two merge-blocker equivalence tests and the short tone
  run (about 3 minutes in total for `test_pdm_e2e.py`); the chirp-law check and
  the full-length tones are opt-in (`PDM_SLOW=1`, about 3.3 minutes each).
- Booth mode: leftover blank band at the right of the stage on wide screens;
  Esc closes booth while a `<select>` is open.
- A `pdm` top-level package name (under `source/`) could clash with the PyPI
  package `pdm` if both are on the path.
- `docs/electronica2026-demo-howto.md` is on branch `docs/electronica2026-howto`
  only; the "Demo D" section lives in `source/pdm/README.md` until that branch
  lands.

## Screenshots

In `docs/reports/2026-11-03-pdm-audio-demo/`: `01-audio-idle-light.png`,
`03-audio-running-later-light.png`, `05-audio-after-reload.png` (1440x900, light
theme) and `07-audio-booth-1920.png` (booth, 1920x1080).
