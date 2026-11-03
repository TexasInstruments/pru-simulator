# PDM microphone audio demo: PRU1 PDM generator → PRU0 PDM receiver → live spectrogram

Date: 2026-11-03 · Status: for approval · Target: electronica 2026 booth (pru-simulator)

## 1. Intent

At the electronica booth, visitors should watch a real PRU audio pipeline at work in
pru-simulator. One PRU synthesises the signal of two PDM MEMS microphones from an audio
clip. The other PRU captures the two mics with the ICSS sigma-delta (SD) hardware,
decimates and filters the streams to 16 kHz PCM, and stores the samples in ICSS shared
RAM. The simulator UI turns those samples into a spectrogram that builds up on screen,
colour-coded by level. Every bit on the wire and every PRU instruction is simulated cycle-exactly.
That is the message: this is the real firmware, not an animation.

This project is a stepping stone. Later, the same PRU0 receiver goes onto a real
AM243x board and the PCM is streamed to a PC over Gigabit Ethernet. That later work is a
separate project (see Non-goals). The PRU0 receiver must therefore be real firmware that
can be ported. It is not a simulator-only shortcut.

## 2. Goals

1. **`source/pdm/` PRU0 receiver, two channels:** SINC3 in the SD hardware (OSR 96 →
   32 kHz), then a 63-tap FIR that decimates by 2 (→ 16 kHz), a 20 Hz DC blocker, and
   signed 24-bit PCM. It is ported from `~/GitHub/audio_pdm/audio_pipe` (`pdm_rx.asm`).
   It is structured so the channel count is a build option (`N_CH`, 1..8). Only 2 are
   exercised in the simulator.
2. **`source/pdm/` PRU1 generator:** a 3.072 MHz PDM clock plus one PDM data line that
   carries two mics. Left is valid at the rising edge and right at the falling edge, which
   is the standard stereo-PDM wiring and the wiring of the 8-mic board audio_pipe follows.
   PRU1 itself runs a **2nd-order sigma-delta modulator per channel**. Its input is 16 kHz
   int16 PCM read from shared RAM.
3. **Simulator: pin-driven SD input.** An SD channel can take its data and clock from PRU0
   GPI pins that are driven by PRU1 GPO pins through the existing core-to-core GPIO wires.
   `CLK_SEL` and `CLK_INV` are honoured, so two channels can share one data line on
   opposite clock edges.
4. **Simulator: idle skip.** Simulation is faster because cycles a core spends in pure
   wait/delay code are not executed one instruction at a time. The results must be
   **bit- and cycle-identical** to running without the skip.
5. **UI: new "Audio" workspace view** (layout B from the mockups) with clip selection,
   routing display, progress, speed readout, Run/Stop/Reset, two stacked live spectrograms,
   and a **full-screen booth mode**.
6. **Booth clips:** speech and chirps, about 0.25 s each, 16 kHz.

## 3. Non-goals

- Expanding the SD peripheral model from 3 to 9 channels. Two channels are enough. The
  firmware design must not prevent 8, but nothing in the simulator needs to change for it.
- Real hardware bring-up, pinmux, INTC, and the Ethernet/PC visualisation. Those are the
  next project.
- A PRU-side FFT. The spectrogram is computed in the browser.
- Arbitrary audio formats or file upload in the UI. Clips are prepared files in the repo.
- A general simulator speed-up beyond the idle skip, for example a faster instruction
  dispatch. That is a possible later project.
- Audio playback through the PC speakers. That would be nice to have, but it is out of scope.

## 4. Anti-goals (would make it a failure even if it "works")

- **Faking the pipeline.** Examples: the UI computing PCM from the clip directly, PRU1
  modelled in Python instead of firmware, or the SD fed from the old internal modulator.
  Every visible sample must have passed through PRU1 firmware → pins → SD → PRU0 firmware.
- **An idle skip that changes results.** A skip that drifts by even one cycle or one
  sample silently undermines every other test in the repo.
- **Stripes or gaps in the spectrogram caused by the pipeline,** such as periodic reinit
  drops (this is why pdm_fir_48k was rejected), ring overruns, or lost blocks.
- **New Python dependencies** (numpy, scipy). The repo runs on the stdlib plus fastapi,
  uvicorn and websockets.
- **Breaking existing SD users,** for example `source/sdfm_sinc3_demo` and the SD tests
  that use the internal pattern generator. The pin-driven input is an additional mode.

## 5. Constraints and facts the design rests on

- PRU clock 200 MHz on both cores, as in audio_pipe and the AM243x LP target.
- Simulator speed, measured 2026-11-03, headless: PRU0 alone running `pdm_rx` reaches
  250 k cycles/s. PRU0 + PRU1 paced (`step_paced`) reach 122 k cycles/s per core. Without
  the idle skip, 1 min of wall time is about 37 ms of audio.
- Estimated useful work per second of audio after the idle skip: PRU1 ≈ 22 busy
  instructions per 65-cycle PDM period, about 68 M instructions. PRU0 ≈ 2 ch × (FIR ~900
  per 16 kHz sample plus comb ~35 per 32 kHz sample), about 31 M. Total ≈ 100 M
  instructions. At about 245 k instructions/s this is **≈ 0.15 s of audio per minute** (one
  spectrogram column every ~3 s). This is an estimate. The real figure is measured and
  reported (§11).
- The simulator SD model has 3 channels and is clocked by its internal modulators
  through an async accumulator that advances once per PRU0 instruction
  (`pru_io/sd_filter.py: tick`). It has no pin input and no `CLK_SEL`/`CLK_INV`
  behaviour (registers exist; behaviour is not modelled).
- Core-to-core pin wires exist: `Simulator.add_gpio_wire(core_a, pin_a, core_b, pin_b)`
  → `DeviceBus` (`pru_io/device_model.py`).
- Memory (AM243x cfg): DRAM0 0x0000 (8 KB), DRAM1 0x2000 (8 KB), shared RAM 0x10000
  (64 KB), all with 2-cycle read latency.
- The audio_pipe sweep tooling (ring recorder, sweep response) lives on the unmerged
  branch `origin/feat/sd-sweep`. Its ring recorder (overrun detection, time stamps) is a
  candidate for reuse. Do not depend on that branch unmerged. Cherry-pick or re-implement.
- Simulator assembler quirk carried over from audio_pipe: a `.set` that refers to another
  `.set` symbol is not expanded. Keep build options as plain literals guarded by `.emsg`
  checks, the way audio_pipe does.
- Repo conventions: tests live in `tests/test_*.py`. Run pytest with `env -u PYTHONPATH`
  (ROS on this PC). Never stage `memory.cfg`; pin clocks in tests via `tests/conftest.py`.
  Work goes in a short-lived branch with a PR into `main`. Show the version in the UI tooltip.

## 6. System overview

```
 clip.wav (16 kHz int16, L/R)                                            browser
        │  loader (stdlib wave)                                     ┌──────────────┐
        ▼                                                           │ Audio view   │
 shared RAM  CLIP area (interleaved L/R int16)                      │ 256-pt FFT   │
        │ LBCO, one 32-bit word per 192 PDM periods                 │ hop 128      │
        ▼                                                           │ 2 spectro-   │
 PRU1  pdm_gen.asm  2× 2nd-order ΣΔ, 3.072 MHz                      │ grams        │
   GPO  PDM_CLK ───────────────wire──────────► PRU0 GPI SD0_CLK      └──────▲───────┘
   GPO  PDM_DAT ───────────────wire──────┬───► PRU0 GPI SD0_D               │ websocket
                                         └───► PRU0 GPI SD1_D               │
 PRU0  SD ch0: own clock, CLK_INV=0 (left, rising edge)                     │
       SD ch1: ch0's clock, CLK_INV=1 (right, falling edge)                 │
       SINC3 OSR 96 (HW acc + FW comb) → 63-tap FIR ↓2 → DC block → sat24   │
        ▼                                                                   │
 shared RAM  PCM rings (per channel, 2×128-sample ping-pong) ── server drain ┘
```

Exact GPI/GPO pin numbers: the SDn_CLK and SDn_D positions in PRU0 GPI SD mode come from
the TRM tables already in `references/` (`SD_CLK_SEL_REG_*.png`, `GPCFG0_REG_*.png`).
The builder picks the PRU1 GPO pins (default GPO0 = clock, GPO1 = data) and documents
them in the README and the UI routing panel.

## 7. PRU1: `source/pdm/pdm_gen.asm`

- **Clock:** 3.072 MHz from 200 MHz with the integer fractional adder of audio_pipe
  `pdm_clk.asm`: 65 + 5/48 cycles per period, 43 short and 5 long periods per 48, zero
  drift, ≤ 1 cycle (5 ns) jitter. Duty cycle within 40–60 %.
- **Data:** one line carries both channels. Each half period, PRU1 runs one channel's
  modulator step and drives that channel's bit. The bit must be stable at the edge where the
  receiving SD channel samples it: left at the rising edge, right at the falling edge. Choose
  a setup margin of at least a few cycles and state it in the README. For the later hardware
  port, the edge assignment must be checked against the IM73D122 datasheet. In the
  simulator the convention only has to be consistent with the SD model.
- **Modulator:** a standard 2nd-order ΣΔ (error feedback), add/sub/compare only, no
  multiply. The state per channel is two 32-bit integrators held in registers. The input is
  the int16 sample shifted to the integrator scale. The output 1 corresponds to +FS and 0
  to −FS. The loader normalises clips to a peak of about 0.5 FS, which keeps a 2nd-order
  modulator well inside its stable range.
- **Input pacing:** 3.072 MHz / 16 kHz = 192 PDM periods per PCM sample, so the PCM is
  upsampled by zero-order hold. One 32-bit LBCO (interleaved L/R int16) is fetched every 192
  periods and has to fit inside one half-period slot. ZOH images sit at 16 kHz·k ± f. The
  PRU0 chain rejects them: the SINC3 nulls lie at multiples of 32 kHz, and the FIR has
  < −63 dB stopband from 9 kHz on. Linear interpolation is the builder's fallback if a test
  shows images above the −80 dB colour floor.
- **Clip end:** after the last sample, the modulator input is held at 0 (silence) and PRU1
  sets a `GEN_DONE` flag in the shared-RAM header. The clock keeps running so PRU0 flushes
  its FIR.
- **Timing structure:** every path through a half period must have a fixed cycle count, so
  the clock stays exact independent of bit values. Branch-balancing is the builder's job.
  The test is that the clock edge times equal the fractional-adder schedule exactly.

## 8. PRU0: `source/pdm/pdm_rx.asm`

Port audio_pipe `pdm_rx.asm` and keep its verified DSP: the comb mod 2^28 (no reinit
gaps), the Q27 FIR table generated by `tools/gen_fir_coefs.py` from `decimation_fir.h`,
the multiply-only MAC with signed correction, the Q32 DC blocker and the 24-bit
saturation. Changes:

- **N channels:** `N_CH` is a build option. Range 1..8 is checked by `.emsg`; simulator
  tests use 2. The SD R31 interface shows one channel at a time (`ch_sel` in R30[29:26]),
  so the main loop round-robins: select channel k, wait for valid, read the shadow, clear
  it, run the comb, store, move to the next channel. All channels share the same clock and
  OSR, so their windows complete at nearly the same time. The design must show that the
  round-robin never misses a window: a shadow is overwritten after 96 SD clocks (6250 PRU
  cycles), and the worst case is that every channel runs its FIR in the same window.
- **FIR scheduling:** each channel runs its FIR every 2nd SINC3 sample. The builder
  decides whether all channels run their FIR in the same window or are staggered across the
  two phases. Staggering halves the peak load. The decision and the worst-case cycle count
  go in the README. Budget: 8 channels must fit in 6250 cycles per window at 200 MHz
  (estimated ~65 % load when staggered). For 8 channels this is verified by a cycle count
  of the code path, not by simulation, because the simulator has only 3 SD channels.
- **State:** per-channel comb state, FIR delay line (128 words, mirrored), DC estimate and
  counters live in PRU0 DRAM0, as in audio_pipe, laid out as `N_CH` fixed-stride blocks.
  The coefficient table is shared.
- **SD configuration:** ch0 `CLK_SEL` = own SD0_CLK and `CLK_INV` = 0. ch1 `CLK_SEL` = the
  shared ch0 clock and `CLK_INV` = 1. Both use ACC3 and OSR 96. On hardware the GPCFG
  SD-mode mux and pinmux stay host preconditions, as in audio_pipe. In the simulator the
  test/UI harness sets them.
- **Output:** see the memory map. Sample first, then the per-channel `PCM_COUNT` (publish
  order as in audio_pipe). An R31 event at each block end is kept, and harmless if nothing
  consumes it.

## 9. Shared RAM map (0x10000, 64 KB)

| Offset | Size | Contents | Writer |
|---|---|---|---|
| 0x0000 | 256 B | Header: magic/version, `N_CH`, PCM rate, block size, `PCM_COUNT[8]`, `CLIP_LEN`, `GEN_POS`, `GEN_DONE` | PRU0 / PRU1 / host |
| 0x0100 | 8 × 1 KB | PCM rings: per channel 256 × int32 (24-bit), 2 × 128-sample ping-pong | PRU0 |
| 0x2100 | ≤ 32 KB | Clip: interleaved L/R int16 at 16 kHz, ≤ 8192 frames (0.51 s) | host loader |
| … | rest | free | |

Rings are sized for 8 channels even when only 2 are used, so the layout does not move
when the channel count grows. Exact offsets are the builder's, but they live in one shared
`.inc` and are mirrored by one Python module. There are no magic numbers in the UI.

## 10. Simulator changes

### 10.1 Pin-driven SD input

- A new input source per SD channel, next to the internal pattern generator: **pin
  mode**. Data comes from the channel's SDn_D GPI pin. The clock comes from SDn_CLK, or
  from the shared ch0/3/6 clock when `CLK_SEL` selects it. The channel samples the data
  pin at the clock edge chosen by `CLK_INV`.
- The edges come from the wired PRU1 GPO pins through `DeviceBus`. Requirements: no edge
  is missed, ordering is causal, and the time skew between PRU1's write and PRU0's sample
  is at most one PRU1 instruction. That is well inside the ≥ 30-cycle half period. The
  builder chooses the mechanism. One option is event-driven: the SD channel subscribes to
  net changes with the writer's cycle stamp. The alternative is for PRU0 to sample pins per
  instruction. Event-driven is preferred because PRU0 samples nothing while idle-skipping.
- The internal modulator mode keeps working unchanged and stays the default.
- Pin mode is selected per channel. It is reachable from Python (`Simulator`), from MCP
  (a tool parameter on the existing SD tool, if there is a natural place for it) and from
  the Audio view, which sets it up automatically.

### 10.2 Idle skip

- **Delay-loop collapse:** a `LOOP n` whose body is only `NOP`s executes as one step that
  advances the cycle count (and instruction counters) by the exact amount, ticking every
  per-cycle subsystem (IEP observer, PERIF, SD async clock, device bus) as if it had been
  stepped. This covers PRU1's delay loops.
- **Poll fast-forward:** when a core sits in a single-instruction spin on an R31 bit
  (`QBBC/QBBS self, r31, n`), it may advance in one jump to the earliest of the pacing
  target and the next time anything that drives that R31 bit could change, such as an SD
  valid rise, a pin edge on a wired net, an IEP event or a PERIF event. The exit cycle must
  be exactly the one the stepped run would produce. This covers PRU0's `PDM_WAIT`.
- **Scope (designer's call):** the skip only applies to *run* modes: continuous Run in
  the UI, `run_multicore` chunks, and `run_until`. It never applies to single-step, and never
  to a loop whose body holds a breakpoint. `Simulator.step(core, count)` and the MCP step
  tool keep their meaning (count = instructions). A collapse happens only when the whole
  loop fits inside the remaining count, and it adds n to the instruction counter. Signal-graph
  capture over a skipped span records the span's (unchanged) pin state once, with correct
  timestamps. It is on by default in run modes. A switch turns it off (Python and UI/server
  setting) for comparison and debugging.
- **Correctness gate:** a test runs the full PDM pipeline (and at least one existing
  SD test and one PERIF test) with the skip on and off, and asserts identical PCM, register
  files, cycle counters and pin-edge histories. This test is a merge blocker.
- Any spin pattern the skip does not recognise simply runs stepped. It is always safe to
  not skip.

### 10.3 Run-ahead scheduling for one-way producer cores (decided by the user)

Under today's lockstep pacing (`step_paced_many`, follower catches up to the lead after
every lead step), PRU0's poll fast-forward can only jump as far as PRU1's latest
instruction. PRU0 then pays about one simulator step per PRU1 step, and the estimate drops
to ~0.08 s of audio per minute. The Audio run therefore uses run-ahead:

- PRU1 is the producer. It runs a time slice ahead (for example a few thousand cycles)
  and records its wired pin edges with cycle timestamps. PRU0 then runs through the same
  span and consumes the edges in its own time through the pin-driven SD (§10.1). Its poll
  fast-forward jumps straight to the next valid rise, which is now known.
- **Precondition, checked at run start and refused otherwise:** data flows one way only.
  There is no wire from PRU0 to PRU1, PRU1 reads no memory that PRU0 writes during the
  run (the clip area is host-written, PCM rings are PRU1-unread), there is no shared XFR
  or scratchpad traffic, and there is no IEP/INTC dependency of PRU1 on PRU0. A violated
  precondition falls back to lockstep with a visible notice. It never runs wrong.
- Used only by the Audio run (and its tests/API). Existing multicore runs keep lockstep.
- **Equivalence gate (merge blocker):** the same PDM run under run-ahead and under
  lockstep gives identical PCM, register files, cycle counters and pin-edge histories.
- Expected speed with run-ahead plus idle skip: ≈ 0.15 s of audio per minute (§5).

### 10.4 Clip loader

- `source/pdm/clips/*.wav`: mono or stereo, 16-bit PCM, **exactly 16 kHz** (stdlib
  `wave`, no resampling at run time). The loader normalises to a peak of 0.5 FS, writes
  interleaved L/R into the clip area and sets `CLIP_LEN`. Generator L and generator R can
  each pick any clip. The loader takes one channel from each.
- `source/pdm/tools/make_clips.py`: generates the chirp clips (for example a linear chirp
  100 Hz → 7 kHz and a log chirp) with the stdlib. It also converts the speech recording
  to 16 kHz mono with `ffmpeg` if present. ffmpeg is an offline tool only, not a run-time
  dependency.
- **Speech clip (decided by the user):** a short phrase (~0.25–0.5 s) cut from a
  **public-domain LibriVox recording**. LibriVox dedicates its recordings to the public
  domain. Record the source (book, chapter, reader, URL, time offset) in
  `source/pdm/clips/SOURCES.md`, together with the conversion command, so the cut can be
  reproduced. Prefer a clear voice with strong formants and little room noise.

## 11. UI: "Audio" workspace view

A new tab next to Simulator, I/O & Devices and Events (layout B):

- **Sources panel:** one clip dropdown each for Gen L and Gen R (files in
  `source/pdm/clips/`). It also shows the clip length.
- **Routing panel:** shows Gen L → PRU0 SD ch0 (rising edge) and Gen R → SD ch1 (falling
  edge) on the one shared PDM line, with the pin names. It is read-only, plus an L↔R swap
  toggle that swaps which clip goes to which generator.
- **Progress panel:** a bar of simulated audio against clip length, a **speed readout in
  ms of audio per wall second**, and PRU0/PRU1 load (busy vs. skipped cycles) as a small
  extra that tells the PRU story.
- **Run / Stop / Reset:** Run loads both firmwares, sets up the wires, the SD pin mode and
  the GPCFG SD mux, loads the clips, and runs both cores paced with the idle skip. The
  existing run machinery should be reused, not duplicated. Run stops by itself once the
  PCM covers the clip plus the filter delay.
- **Spectrograms:** ch0 and ch1 stacked; frequency 0–8 kHz on the vertical axis, time on
  the horizontal axis. 256-point FFT with a Hann window and a hop of 128 samples (one
  column every 8 ms of audio), computed in the browser in JS. Colour is the level from
  −90 dBFS to 0 dBFS through a perceptual colormap, with a colour bar and a cursor at
  the build-up edge.
- **Data path:** the server drains each channel's finished ring blocks after every run
  chunk, or on the block event. It keeps the full PCM history of the run, detects ring
  overruns (an overrun is an error shown in the UI, never silent) and pushes new blocks over
  the existing websocket. **Reload (designer's call):** the existing run machinery stays.
  A reload pauses the run, because the owning connection drops. The new page rebuilds
  both spectrograms from the server-held history, and pressing Run continues from where it
  stopped. There is no unattended server-side run loop. An operator presses Run per clip.
- **Clocks (designer's call):** both firmwares assume 200 MHz. If the configured PRU0 or
  PRU1 clock differs, Run switches both cores to 200 MHz through the same path the UI
  speed selector uses. That writes the local `memory.cfg`, which is fine because it is
  local state. Run shows a one-line notice ("PRU clocks set to 200 MHz for the PDM demo")
  and does not restore the old clocks afterwards. Headless tests pin 200 MHz via
  `tests/conftest.py`.
- **Full-screen booth mode:** a button switches to the two spectrograms filling the
  screen, each labelled "Left mic" and "Right mic". It has large axis labels, a single-line
  control strip (two clip pickers, Run, speed counter) and a short caption such as "PRU1
  firmware synthesises 2 PDM mics · PRU0 firmware decodes them · every cycle simulated".
  The final caption text is the builder's choice; the user signs it off at review. Esc or a
  button leaves booth mode.
- It follows existing UI conventions: themes (light/dark), the version tooltip and the
  panel styles.

## 12. Testing and acceptance

Bit-exact references come first, as in audio_pipe.

1. **PRU1 generator:** clock edge times equal the fractional-adder schedule exactly. The
   data bits equal a Python reference of the same integer ΣΔ fed with the same clip. Setup
   margin before each sampling edge ≥ the documented value.
2. **Pin-driven SD:** with a known bit pattern on wired pins, the ACC3 shadows equal a
   Python CIC3 of that pattern, for ch0 on the rising edge and ch1 on the falling edge.
   Existing SD tests stay green.
3. **PRU0 receiver:** PCM for both channels equals the integer CIC3 → FIR → DC-block
   reference computed from the recorded bits, sample for sample. The worst-case window
   cycle count fits the 6250-cycle budget for `N_CH` = 2 measured, and for `N_CH` = 8 by
   static path count.
4. **End to end:** a 1 kHz tone clip on L and a 3 kHz tone clip on R produce PCM tones
   within ±1 % of frequency, with crosstalk between channels below −60 dB. The chirp clip
   produces a spectrogram ridge that follows the chirp law.
5. **Idle skip equivalence:** §10.2 (merge blocker).
6. **Run-ahead equivalence:** §10.3 (merge blocker).
7. **Speed:** measured and reported, both headless and in the UI: ms of audio per wall
   second, with the skip off and on. Target ≥ 0.1 s of audio per minute in the UI. If it
   is missed, report it with the profile. Do not ship silently.
8. **UI (designer's call):** the automated tests follow the repo's existing style, with no
   new dev dependencies. Server endpoints and ring drain/history/overrun use FastAPI
   TestClient. The JS FFT, colormap and column layout use node `vm` with stubbed DOM. A
   real-browser check runs once before the PR, in a headless Chrome driven by the agent the
   way the audio_pipe sweep report did, and is recorded in the report: open the Audio view,
   pick clips, Run, columns appear, booth mode in/out, reload mid-run rebuilds and Run
   continues. It is not a CI test.
10. **Portability check (designer's call):** `pdm_rx.asm` and `pdm_gen.asm` assemble with
   clpru (open-pru Linux build, CCS 21 toolchain) without errors. This shows the
   simulator-accepted syntax is real clpru syntax. Assembly only; no hardware run in this
   project.
9. A how-to section for the booth goes in `source/pdm/README.md`, plus a pointer from
   `docs/electronica2026-demo-howto.md` (Demo D).

## 13. Files (expected)

```
source/pdm/README.md               how it works, pins, memory map, budgets, booth steps
source/pdm/pdm_rx.asm              PRU0 receiver (N_CH build option)
source/pdm/pdm_gen.asm             PRU1 clock + 2× ΣΔ modulator
source/pdm/pdm_shared.inc          shared-RAM map (single source for both firmwares)
source/pdm/fir_coefs.inc           generated
source/pdm/decimation_fir.h        coefficient source (from audio_pipe)
source/pdm/tools/gen_fir_coefs.py  from audio_pipe
source/pdm/tools/make_clips.py     chirps + speech conversion
source/pdm/clips/*.wav             booth clips
source/pdm/pdm_layout.py           Python mirror of the memory map + clip loader + ring drain
pru_io/sd_*.py, pru_io/device_model.py   pin-driven SD
core/pru_core.py, simulator.py     idle skip, run-ahead
source/pdm/clips/SOURCES.md        clip provenance (LibriVox source, conversion command)
ui/server.py, ui/static/*          Audio view, booth mode
tests/test_pdm_*.py, tests/test_idle_skip.py, tests/test_sd_pin_input.py
```

## 14. Decided by the user

- Base the work on audio_pipe, not pdm_fir_48k. The comparison is in §15.
- Two channels. Do not expand the SD model to 9 channels. The firmware is designed to
  scale to 8.
- 16 kHz PCM output. No lowering of rates for simulation speed.
- Do the idle skip.
- PRU1 does the PDM conversion itself, from PCM.
- Short clips are fine. The content is speech and chirps.
- UI layout B (dedicated Audio view) plus a full-screen booth-mode button.
- 256-point FFT. Columns of the order of 10 ms (8 ms with hop 128).
- PCM samples are stored in ICSS shared RAM.
- Ethernet and PC visualisation are a separate, later project.
- Run-ahead scheduling for the Audio run is in scope (§10.3).
- The speech clip comes from a public-domain LibriVox recording (§10.4).

## 15. Calls made by the designer (check these)

- Stereo on one data line, left on the rising edge and right on the falling edge, using
  `CLK_SEL` shared clock and `CLK_INV`. This needs these two register fields modelled in
  the simulator.
- 2nd-order modulator, 16 kHz clip input with zero-order hold, clip peak normalised to
  0.5 FS.
- Rings: 2 × 128-sample ping-pong per channel, sized for 8 channels. The server keeps the
  history. The clip lives in shared RAM (≤ 0.51 s).
- FFT in the browser (no numpy), Hann window, −90..0 dBFS colour range.
- Clips must already be 16 kHz WAV files. `make_clips.py` generates the chirps.
- Idle skip on by default, with an off switch. The equivalence test is a merge blocker.
- PRU0 per-channel state stays in DRAM0, and only the PCM goes to shared RAM.
- Idle skip only in run modes. Single-step and step counts are unchanged (§10.2).
- A reload pauses the run, the picture rebuilds from server history, and an operator
  presses Run (§11).
- Run forces 200/200 MHz with a notice and does not restore (§11).
- UI tests: TestClient + node-vm in CI, one recorded headless-Chrome check (§12.8).
- clpru assembly check of both firmwares (§12.10).

## 16. Left to the builder

The exact pin numbers, the modulator scaling, the branch-balancing in `pdm_gen.asm`, FIR
staggering, the event-driven vs. sampled pin mechanism, the idle-skip internals, the
websocket message shapes, the colormap choice, and the exact shared-RAM offsets. Each must
be documented in the README or code where it is decided.

## Appendix: why audio_pipe over pdm_fir_48k

| | pdm_fir_48k | audio_pipe |
|---|---|---|
| Chain | SINC2 OSR 64 → 48 kHz, 16-tap LP, no decimation | SINC3 OSR 96 → 32 kHz, 63-tap FIR ↓2 → 16 kHz 24-bit, DC block |
| Cost/channel | 266 cyc @ 250 MHz (~6 % @ 200 MHz) | ≤ 901 cyc / 6250 @ 200 MHz (~8 % per 16 kHz output) |
| Quality | Reinit every 128 windows drops 3 frames (2 % gap every 2.7 ms), weak alias rejection | Sweep-verified: +0.06 dB at 1 kHz, < −63 dB at 9–12 kHz, no gaps |
| Clarity | One commented file; register-resident state, sign-magnitude trick, hand-copied coefficients | Build options with `.emsg` guards, generated coefficients, documented host interface, 41 tests and reports |
| Scaling | History fills r2–r17. A second channel means a rewrite | State in memory; channel is a build option; block/ping-pong output with an event fits STFT |

pdm_fir_48k's argument for SINC2 (ACC3 wraps) does not hold. A comb computed mod 2^28 is
exact across wraps, as audio_pipe's bit-exact tests show.
