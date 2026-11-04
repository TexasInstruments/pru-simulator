# SPI link: PRU0 controller ↔ PRU1 peripheral (design)

Date: 2026-11-04 · Branch: `feat/spi-pru-link` (from `main` @ 46efc4a)
Status: design for approval

## 1. Intent

The theme is **"vibe coding real-time interfaces"**. The demo shows that a fully
specified SPI interface can be coded quickly with the agent workflow, and that it is
easy to show running on pru-simulator. The audience watches one PRU clock SPI frames into
another PRU and sees the waveform, the decoded bytes and the memory being read and
written, all in one simulator view.

Porting to **open-pru and testing on the AM243 LaunchPad is a later, separate step**. This
project still chooses real LP-AM243 pins and quotes real AM243 timing limits, so that the
later port does not have to change the design.

The development flow is part of the deliverable. The project ends with a session report
in the style of `docs/reports/2026-08-12-i2c-tca9538-session-report.html`.

### Goals
- PRU0 as SPI controller and PRU1 as SPI peripheral, both bit-banged in **direct GPO/GPI
  mode**, connected pin to pin with minimum delay.
- SPI **mode 0 and mode 1** (CPOL = 0). The user's base case is "set on rising, capture on
  falling" (mode 1). Mode 0 is "set on falling / before the first rising, capture on rising".
- Selectable SPI CLK from **10 to 50 MHz**, using several PRU core clocks to reach more rates.
- A 19-byte frame protocol with READ, WRITE and READ/WRITE commands that addresses 16-byte
  blocks in PRU1 data memory, with separate TX and RX buffers on both sides so data
  consistency can be checked.
- A new **SPI view in I/O & Devices** with a scope-style timing diagram, PicoScope-style
  byte decode, memory panels that light up as the transfer runs, and a timing table that
  includes AM243 datasheet limits.
- Test patterns that are easy to follow on the scope.
- A session report covering the prompt, clarifications, models, tokens and time.

### Non-goals
- open-pru port, CCS/clpru build for silicon, and testing on LP-AM243 hardware (step 2).
- CPOL = 1 (modes 2 and 3).
- Use of the ICSSG shift, parallel-capture or peripheral-interface modes. The brief asks for direct GPIO.
- Multiple peripherals or more than one /CS.

### Anti-goals (would be a failure even if it "works")
- **PRU1 must be a real edge-polling SPI peripheral.** It must not be clock-locked to PRU0
  after /CS, even though both PRUs share a clock. It must work with any controller that
  meets the timing. If that means 50 MHz is not reached, the result is reported, not hidden.
- No timing number may be presented as a datasheet limit when the datasheet does not give
  one (see §6).
- No cycle-tight claim may rest on hand-counted cycles. Every rate shown as supported must
  pass a zero-error simulator run, confirmed by stepping in the simulator.

## 2. Clarifications (from the brainstorming conversation, for the report)

| # | Question / issue | User's answer | Effect |
|---|---|---|---|
| 1 | Who sees this and what should they take away? | Theme "vibe coding realtime interfaces": show the SPI can be coded and is easy to demonstrate in the simulator. open-pru + AM243 LP test is step 2. | Scope = simulator. Real pins and real timing are kept for step 2. |
| 2 | Prompt had PRU1 with MISO as input and MOSI as output | "yes PRU1 reads MOSI" | PRU1: CLK, MOSI and /CS in; MISO out. |
| 3 | "Set rising / capture falling" is mode 1, not mode 0 | Support both modes 0 and 1 | Mode selector. |
| 4 | 50 MHz at 200 MHz is only 4 cycles/bit | Not needed at 200 MHz; use a higher core clock | 50 MHz is only offered on a faster core clock. |
| 5 | "16 byte address" vs one address byte | Address byte points to 16-byte blocks: 1 → 16, 2 → 32, … | Byte offset = addr × 16. |
| 6 | Scope view | Should also decode byte by byte like PicoScope | Byte boxes over MOSI and MISO. |
| 7 | Layout for the SPI view (3 mockups) | Picked A (scope on top, memory below), "but miss timing" | Timing arrows on the trace and a timing table were added. |
| 8 | Timing table | Add min/max for the AM243 pins | AM243 datasheet and LP guide were fetched (§6, §7). |
| 9 | Pins and table as proposed | "yes pins and table work" | §7 is fixed. |
| 10 | Edge-polling vs clock-locked peripheral for top rates | Must be a real edge-polling peripheral. Hint: sample clock and data in one masked R31 compare, and sample based on the previous edge. | Anti-goal above. Firmware techniques in §5.2. |
| 11 | Playback of protocol, rates, patterns, report and models | "all good" | §3, §4, §8 and §9 as written. |
| 12 | Builder check: simulator has 0-cycle GPI/GPO latency; what delay should "proven" assume? | Silicon measurement: at 333 MHz, `wbs input_pin` + `set output_pin` shows 18 ns from async input edge to output edge on an external scope | Calibrated pin-to-pin hop delay of 4 core cycles in the simulator (§5.4). The controller gets a MISO sample delay (§5.1). |

## 3. Protocol

One frame = /CS low, 19 bytes = 152 SPI clocks, /CS high. MSB first.

| Byte | MOSI (PRU0 → PRU1) | MISO (PRU1 → PRU0) |
|---|---|---|
| 0 | command | 0x00 |
| 1 | address (block index) | 0x00 |
| 2 | idle 0x00 | 0x00. During this byte PRU1 fetches the read data. |
| 3–18 | 16 data bytes (WRITE, R/W) or 0x00 (READ) | 16 data bytes (READ, R/W) or 0x00 (WRITE) |

- Commands *(my call)*: `0x01` WRITE, `0x02` READ, `0x03` READ/WRITE (bit 0 = W, bit 1 = R).
  For any other command, PRU1 ignores the data, drives MISO 0x00, writes nothing and
  increments an error counter.
- Address: byte offset = `addr × 16`. Valid range is **0x00–0x7F** *(my call)*, which gives 2 KB
  per buffer. An out-of-range address is treated like an unknown command.
- READ/WRITE is full duplex. The 16 bytes on MOSI go to PRU1 RX[addr], and PRU1 TX[addr]
  goes out on MISO in the same 16 bytes.
- The idle byte exists so PRU1 can load 16 bytes from data memory before the first data
  bit is due. The fetch must fit inside one byte time at the fastest rate.
- /CS high between frames for at least 2 t<sub>CLK</sub>. PRU1 resynchronises on every /CS falling
  edge. /CS going high mid-frame aborts the frame: PRU1 discards partial data (RX is not
  committed) and counts an abort.

### Memory layout *(my call; the builder may move offsets but must keep TX/RX separate)*
- PRU0 data memory (DMEM0): control block (command, address, pattern id, go/done flag,
  frame count), TX buffer 16 B, RX buffer 16 B, plus a result log for test sequences.
- PRU1 data memory (DMEM1): TX region 2 KB (read source), RX region 2 KB (write target), and
  a status block (frames, errors, aborts, last cmd/addr).
- Consistency check: PRU0 TX ↔ PRU1 RX[addr] (MOSI path) and PRU1 TX[addr] ↔ PRU0 RX
  (MISO path), byte by byte.

## 4. Rates, core clocks, modes

Symmetric 50 % duty: SPI CLK = f<sub>core</sub> / (2·k) with k core cycles per half period. The
core clocks are the existing selector values {200, 225, 250, 300, 333} MHz. Both PRUs always
run on the same clock, as on silicon.

**Test matrix:** every (core, k, mode) combination with a resulting rate of 10–50 MHz,
excluding 50 MHz at 200 MHz (user, clarification 4). The builder generates it from the k
range rather than hard-coding it.

**UI rate list** *(my call, from builder check)*: **one entry per rate**. Selecting a rate
picks the **lowest core clock that passes** for the selected mode. If none passes, the rate
is greyed out with its limit. The timing doc lists every (rate, core) pair with its
pass/fail result. Examples of exact rates: 200 MHz gives 33.3 (k=3), 25 (4), 20 (5),
16.7 (6), 12.5 (8), 10 (10). 225 MHz gives 37.5 (3). 250 MHz gives 41.7 (3). 300 MHz gives
50 (3) and 37.5 (4). 333 MHz gives 33.3 (5) and 41.7 (4).

**Core clock changes from the SPI view are for this session only** *(my call, from builder
check)*. They must not write `memory.cfg`; the existing `PUT /config/clock_speed` does
write it. The builder adds a non-persisting rebuild path, like the IEP selector's
"this session only" behaviour, and re-applies the SPI scenario after the rebuild.

**Supported means proven:** a (rate, core, mode) combination is enabled in the UI only if
the automated test suite runs every command with every pattern at that combination, **with
the calibrated 4-cycle hop delay (§5.4)**, and gets zero byte errors. Combinations that fail are shown greyed out, with the measured limit
(PRU1 cycles needed per bit). Early estimate, which must not be trusted: an edge-polling
PRU1 needs about 8 cycles/bit, so the ceiling is around 37 MHz at 300 MHz. Whether 41.7 MHz
or 50 MHz pass is an open result, and the report states the outcome either way.

## 5. Firmware

clpru-compatible PRU assembly under `source/spi/` (new folder), following the existing
style (see `source/i2c_tca9538_running_led.asm` and the SSI firmware). Use the
`pru-assembly` skill.

### 5.1 PRU0 controller (`source/spi/spi_controller.asm`)
- Fully deterministic. There are no polling loops inside a frame, and timing comes from
  counted instructions. Rate and mode come from the control block (or from assembly-time
  constants per rate; builder's choice, but the UI must be able to switch rates without
  hand-editing source).
- Mode 1: drive MOSI on the rising edge, sample MISO on the falling edge. Mode 0: MOSI valid
  before the first rising edge and changed on each falling edge, MISO sampled on rising.
- Samples MISO as late in the half period as possible, to give PRU1's reaction time the
  most room. This is the tightest budget (§6).
- t<sub>LEAD</sub> = t<sub>LAG</sub> = 1 t<sub>CLK</sub> minimum. Inter-frame /CS high ≥ 2 t<sub>CLK</sub>.
- **MISO sample delay** *(my call, after clarification 12)*: a configurable number of core
  cycles by which PRU0 samples MISO after its nominal capture edge. It may exceed one bit
  time, in which case PRU0 samples bit n during bit n+1 (pipelined), and it keeps sampling
  after the last CLK edge, with no extra clocks. It compensates the round trip CLK →
  PRU1 → MISO → PRU0 (about 2 hops × 4 cycles plus PRU1 reaction time). This is standard on
  real controllers, like the read-data-capture delay on TI's OSPI. It does not change PRU1,
  which stays a real peripheral. The value per rate comes from the test sweep (the
  middle of the passing window), is shown in the timing table, and is selectable in the UI.

### 5.2 PRU1 peripheral (`source/spi/spi_peripheral.asm`)
A real edge-polling peripheral. Required techniques, from the user and the brainstorm:
- **One R31 read for clock and data.** Poll with a masked compare, for example
  `and r1, r31, MASK(CLK|MOSI|CS)` then branch on the CLK state. When the loop exits, the
  same snapshot holds MOSI and /CS, so there is no second R31 read and no `qbbs` per signal.
- **Sample from the previous edge.** Where the data is already stable (from the launch edge),
  take the sample a fixed number of cycles after the edge already seen, instead of polling
  the capture edge as well. The goal is about one poll per bit.
- **One-cycle MISO drive** *(brainstorm idea)*: keep the TX byte pre-shifted so the next bit
  sits at the MISO bit position (GPO11), then write R30 directly. On silicon, only PRU1's
  MISO GPO is pinmuxed for this design, so the other GPO bits being written is harmless.
  This needs checking against the simulator's GPO drive-mask semantics.
- /CS checked often enough to resync and abort. At minimum, /CS falling starts a frame and
  /CS high is checked at byte boundaries.
- Unrolling is allowed and expected. Mode 0 and mode 1 may be separate code paths.
- Each rate claim is confirmed by single-stepping in the simulator (project rule), not by
  hand-counted cycles.

### 5.3 Simulator wiring (see also 5.4)
- Uses `sim.add_gpio_wire` (zero-delay nets, "minimum delay between the interfaces"),
  the same way as the SSI multi-core scenario (`pru_io/scenarios.py::_ssi_multicore`):
  PRU0 GPO16 → PRU1 GPI1 (CLK), PRU0 GPO13 → PRU1 GPI2 (MOSI), PRU0 GPO17 → PRU1 GPI6 (/CS),
  PRU1 GPO11 → PRU0 GPI14 (MISO). Drive masks are set so that each net has exactly one driver.
- New scenario "SPI multi-core (controller + peripheral)" in `pru_io/scenarios.py`, with lead
  core PRU0. `tests/test_dashboard_scope.py` asserts the scenario list, so it must be updated.

### 5.4 Calibrated pin-to-pin delay (simulator change, in scope)
- Silicon reference (user, clarification 12): at 333 MHz, `wbs` on an async input followed
  by `set` on an output gives **18 ns = 6 core cycles** from input edge to output edge. The
  simulator with zero delay gives about 1–2 cycles for the same code, so silicon adds about
  **4 cycles (≈12 ns)** for input synchroniser, pads and output path combined.
- Add a configurable per-core **GPI input delay in core cycles** to the simulator, with a
  default of 0 so all existing tests and demos are unchanged. Applying the whole hop delay on
  the input side is equivalent for this design, because every SPI net is one output → one
  input.
- The SPI scenario and the acceptance suite use **4 cycles at every core clock** (cautious
  at lower clocks, where 4 cycles > 12 ns). Rates also pass at 0, which is reported, but
  "supported" means passing at 4.
- Calibration test: `wbs` + `set` at 333 MHz with delay 4 measures 18 ns ± 1 cycle, edge to
  edge, from the pin trace.
- The UI shows the delay in effect, so the waveform's visible skew is explained.
- The report and the timing doc state that the split between input and output is unknown
  and that the 4-cycle figure comes from one scope measurement at 333 MHz.

## 6. Timing parameters

Shown in the UI for the selected (mode, rate, core) and listed in full in
`docs/spi_link_timing.md`, one table per supported combination. Columns: parameter, symbol,
design value, PRU cycles, measured in sim, AM243 min, AM243 max, source, worst-case margin.

Rows: t<sub>CLK</sub>; t<sub>H</sub>/t<sub>L</sub>; t<sub>sk(GPO-GPO)</sub>; t<sub>LEAD</sub>; t<sub>LAG</sub>; t<sub>CSH</sub>; MOSI setup/hold to PRU1's
capture edge; PRU1 launch edge → MISO valid (t<sub>V,MISO</sub>); MISO setup/hold to PRU0's capture
edge; PRU0 MISO sample delay in effect; modelled hop delay (4 cycles); input slew;
output load; idle-byte budget against the measured PRU1 fetch time.

**AM243 data, quoted from SPRSP65J (AM243x datasheet, rev. June 2026, §6.11.5.16.1):**
- Table 6-112 (PRU timing conditions): input slew rate SR<sub>I</sub> 1–3 V/ns; output load C<sub>L</sub> 2–30 pF.
- Table 6-113 (direct output mode), PRDO1: t<sub>sk(GPO-GPO)</sub> skew GPO to GPO, max 2 ns.
- **There is no direct-input-mode timing table.** Input setup and hold are therefore design
  budgets and are labelled as such: half period − 2 ns output skew − one core cycle of
  polling uncertainty (5 ns at 200 MHz, 3.33 ns at 300 MHz, 3 ns at 333 MHz).
- For reference only, not applicable to direct mode: Table 6-114 parallel capture,
  t<sub>su</sub> ≥ 4 ns and t<sub>h</sub> ≥ 0 ns.

"Measured in sim" comes from the captured pin trace of the last frame, with the
calibrated 4-cycle hop delay (§5.4). Board trace and jumper-wire delay for step 2 are not
modelled; at a few cm of wire this is well under a nanosecond.

## 7. Pins (LP-AM243, SPRUJ12F rev. Jan 2024, Tables 4-11 to 4-14 and B-2)

All on PRG0 = ICSSG0.

| Signal | Core | PRU pin (R30/R31 bit) | Ball | Header |
|---|---|---|---|---|
| CLK out | PRU0 | PRG0_PRU0_GPO16 | N3 | J1.7 |
| MOSI out | PRU0 | PRG0_PRU0_GPO13 | N1 | J2.15 |
| MISO in | PRU0 | PRG0_PRU0_GPI14 | N2 | J2.14 |
| /CS out | PRU0 | PRG0_PRU0_GPO17 | E1 | J2.18 |
| CLK in | PRU1 | PRG0_PRU1_GPI1 | J2 | J7.67 |
| MOSI in | PRU1 | PRG0_PRU1_GPI2 | M2 | J7.68 |
| /CS in | PRU1 | PRG0_PRU1_GPI6 | F5 | J7.69 |
| MISO out | PRU1 | PRG0_PRU1_GPO11 | P1 | J7.70 |

- PRU0 uses site 1's standard BoosterPack SPI positions (board nets SPI3_*). PRU1 uses four
  adjacent J7 pins, so the hardware link in step 2 is four jumper wires.
- J2.15 needs the Servo-BoosterPack switch (SW6, Rev A boards) in its default position.
- None of the balls are among the outputs gated on E1/E2 boards (R21, T19, U18, U20, V20).

## 8. UI: I/O & Devices → SPI

The approved mockup is layout A plus timing (`.superpowers/brainstorm/…/spi-view-layout-a-v2.html`
and `-v3.html`; recreate the content if the files are gone).
- **Controls bar:** mode (0/1), SPI CLK (only rates proven for that mode; core clock chosen
  automatically and shown), command, address (with "= byte N"), pattern, Send frame, and
  Run test sequence. Choosing a rate that needs a different core clock rebuilds the
  simulator the same way the existing speed selector does, and says so.
- **Frame strip:** 19 coloured cells (CMD, ADR, IDLE, D0–D15) with the current byte outlined.
- **Scope:** /CS, CLK, MOSI and MISO from the real simulator pin trace (not synthesised), with
  byte boxes over MOSI and MISO showing hex values and field names, capture-edge markers
  for the selected mode, and t<sub>CLK</sub>/t<sub>LEAD</sub>/setup/hold arrows at a cursor bit.
- **Memory:** four 16-byte panels (PRU0 TX, PRU1 RX@addr, PRU1 TX@addr, PRU0 RX). They read
  real data memory and highlight bytes as they are transferred. A compare badge shows
  "16/16 match" both ways and marks mismatching bytes in red.
- **Timing table:** as in §6, with AM243 columns visually marked as datasheet data and the
  worst-margin row flagged.
- Set-up follows the SSI demo: one button replaces programs, devices and wiring with the
  SPI scenario, then Run.
- Where it sits in the I/O view (new subtab vs device card) is the builder's call, but it
  must be reachable in one click from I/O & Devices.

## 9. Test patterns

Chosen so the two directions are easy to tell apart on the scope. Selectable per frame,
and all of them are used in the test sequence:

- 0xA5 / 0x5A alternating
- walking 1 (01 02 04 … 80, twice)
- walking 0 (FE FD … 7F, twice)
- count up 00–0F on one line against F0–FF on the other
- 00 / FF toggling

PRU1 TX memory is preloaded with a pattern that differs from what PRU0 sends, so a
loopback-style mistake (MISO echoing MOSI) is visible at once.

**Test sequence** (also the zero-error acceptance run for each rate): WRITE a block, READ it
back from a preloaded TX block, R/W full duplex, an unknown command, an out-of-range
address, a mid-frame /CS abort, and back-to-back frames at the minimum /CS-high gap.

## 10. Verification
- pytest: protocol and memory checks per (mode, rate, core), the drive-mask and wiring
  setup, the scenario, and the timing-table generation. Firmware tests pin the core clock
  explicitly (a fixture like the I2C `nominal_config`), never the dashboard's live setting.
- Node UI tests (`tests/ui_*.test.js` style) for the SPI view: controls, rate gating, and
  the compare badge.
- A browser check of the view with a real run.
- Cycle-tight rates are confirmed by single-stepping in the simulator.

## 11. Development flow and report
- Flow: brainstorm (done) → this design → `superpowers:writing-plans` → subagent-driven
  build (implementer and reviewer per task) → whole-branch review → PR.
- Model per task *(my call)*: **Opus** for design, PRU1/PRU0 firmware, cycle validation and
  reviews; **Sonnet** for UI view, Python scenario and wiring, and tests; **Haiku** for
  mechanical steps (doc edits, generated tables). Record the model actually used per
  dispatch.
- Report `docs/reports/2026-11-04-spi-pru-link-session-report.html`, laid out like the I2C
  report, with these sections: the verbatim prompt (typos kept, with interpretations),
  clarifications (§2), process, design choices plus bugs found in review, sources
  (SPRSP65J, SPRUJ12F), a results table (rate × mode pass/fail and limiting cycles/bit),
  tokens per dispatch with the model column, the main-session token totals (from the session
  transcript JSONL `usage` fields), and a git-derived timeline. Wall-clock numbers come from
  git and transcripts, never from estimates.

## 12. Left to the builder
Exact memory offsets; per-rate code layout (generated vs parameterised); the subtab vs card
placement; trace capture stride needed to resolve 50 MHz edges; how the timing doc is
generated.
