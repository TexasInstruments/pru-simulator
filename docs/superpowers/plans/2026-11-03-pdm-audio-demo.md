# PDM Audio Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PRU1 firmware synthesises two PDM microphones from audio clips, PRU0 firmware decodes them
with the SD hardware to 16 kHz PCM in ICSS shared RAM, and a new "Audio" view in the simulator UI
draws the live spectrograms, with a booth full-screen mode.

**Architecture:** Three simulator additions make it possible and fast enough: a pin-driven SD
input (`PdmLink`, time-stamped PRU1 pin edges feeding PRU0 SD channels with `CLK_SEL`/`CLK_INV`),
an exact idle skip in `PRUCore` (collapse NOP delay loops, fast-forward R31 poll spins), and a
run-ahead scheduler for one-way producer/consumer core pairs. On top sit two firmwares in
`source/pdm/` (ported from `~/GitHub/audio_pdm/audio_pipe`), a Python driver `PdmDemo` shared by
tests and the UI server, and a browser-side 256-point FFT spectrogram.

**Tech Stack:** Python 3 stdlib (`wave`, `fractions`, `struct`), pytest, FastAPI/websockets (existing),
vanilla JS (existing UI, node `vm` tests), PRU assembly (simulator assembler + clpru 2.3.3 check),
ffmpeg (offline clip preparation only).

**Spec:** `docs/superpowers/specs/2026-11-03-pdm-audio-demo-design.md` (read it first; section numbers
below, e.g. §10.2, refer to it).

## Global Constraints

- PRU clock 200 MHz on PRU0 and PRU1. PDM clock 3.072 MHz = 65 + 5/48 PRU cycles. OSR 96 → 32 kHz SINC3, FIR ↓2 → 16 kHz PCM, signed 24-bit (±(2^23−1)).
- No new Python dependencies (no numpy/scipy). No new JS/dev dependencies (no Playwright).
- Run pytest as `env -u PYTHONPATH python3 -m pytest ...` (ROS is on PYTHONPATH on this PC). JS tests: `node --test --test-isolation=none tests/ui_*.test.js`.
- Never stage `memory.cfg`. Cycle-timed tests pin clocks with the `nominal_config` fixture from `tests/conftest.py` (`Simulator(config_path=nominal_config)`).
- Simulator assembler: a `.set` whose value refers to another `.set` is not expanded — keep build options as plain literals and guard derived literals with `.if … .emsg`. The parser does not accept clpru `NAME .macro` (later found accepted — see pdm_rx.asm).
- Existing behaviour is unchanged unless a task says otherwise: SD internal-modulator mode stays the default; `Simulator.step()`, `PRUCore.step()`, MCP step tools, single-step and step-multicore never skip.
- Idle skip and run-ahead must be bit- and cycle-identical to plain stepping / lockstep. Equivalence tests are merge blockers.
- Shared RAM is `0x00010000`, 64 KB, in both PRU0 and PRU1 local maps. PRU0 DRAM0 is `0x0000` (8 KB).
- Work on branch `feat/pdm-audio-demo` (exists, holds the spec). PR into `main`. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Consumer ahead of producer** (e.g. the generic Run with PRU0 as lead while the PdmLink is wired): expected a clear error ("PDM link: consumer pru0 is ahead of producer pru1"), never silently wrong SD samples. Test in Task 1 (`test_consumer_ahead_of_producer_raises`).
2. **Breakpoint inside a NOP delay loop or on a poll spin during Run:** expected the run stops at the breakpoint exactly as without skip. Test in Task 2 (`test_breakpoint_inside_delay_loop_still_hits`).
3. **Clip that is not 16 kHz / not 16-bit / too long / silent:** expected a readable error naming the file and the rule (silent clip: allowed, no normalisation divide-by-zero). Test in Task 4 (`test_loader_rejects_bad_clips`, `test_loader_accepts_silence`).
4. **Ring overrun when a chunk is too long or the server falls behind:** expected an overrun error in status and the UI, never a silently gapped history. Test in Task 7 (`test_overrun_is_reported`).
5. **Reload or second browser tab during a PDM run:** expected the run pauses (owner dropped), the new page rebuilds both spectrograms from server history, and Run continues without duplicated or missing samples. Test in Task 8 (`test_history_rebuild_after_reconnect`).

---

## File Structure

| File | Responsibility |
|---|---|
| `pru_io/pdm_link.py` (new) | `PdmLink`: taps producer GPO writes, time-stamps clock/data edges, applies them to consumer SD channels in time order; `next_valid_time()` for the poll fast-forward |
| `pru_io/sd_filter.py` (mod) | per-channel source mode (`modulator`/`pin`/`off`), `tick_n()`, `clock_edge()` honouring `CLK_SEL`/`CLK_INV`, `pin_link` attribute, snapshot/restore of the new state |
| `pru_io/io_port.py` (mod) | `gpo_listeners` called on GPO change in `write_r30` |
| `core/pru_core.py` (mod) | consumer pre-tick `pin_link.advance_consumer()`, `step_run(max_cycles)`, `_advance_bulk()`, delay-loop collapse, poll fast-forward |
| `simulator.py` (mod) | `attach_pdm_link()`, `idle_skip` switch, `step_paced_many(..., idle_skip=)`, `one_way_violations()`, `run_ahead()` |
| `source/pdm/pdm_shared.inc` (new) | shared-RAM map, single source for both firmwares |
| `source/pdm/pdm_layout.py` (new) | Python mirror of the map, clip loader, header/ring access |
| `source/pdm/reference.py` (new) | integer references: ΣΔ modulator, CIC3, FIR, DC blocker |
| `source/pdm/pdm_gen.asm` (new) | PRU1: 3.072 MHz clock + 2× 2nd-order ΣΔ on one data line |
| `source/pdm/pdm_rx.asm` (new) | PRU0: N_CH-channel SINC3 + FIR ↓2 + DC block → shared-RAM rings |
| `source/pdm/fir_coefs.inc`, `decimation_fir.h`, `tools/gen_fir_coefs.py` (copied) | FIR coefficients, from audio_pipe |
| `source/pdm/pdm_demo.py` (new) | `PdmDemo`: setup, chunked run (run-ahead or lockstep), ring drain, history, status |
| `source/pdm/tools/make_clips.py`, `tools/bench.py` (new) | clip generation/conversion; speed benchmark |
| `source/pdm/clips/*.wav`, `clips/SOURCES.md` (new) | booth clips and provenance |
| `ui/server.py` (mod) | `/pdm/clips`, ws actions `pdm_setup`/`pdm_run`/`pdm_stop`/`pdm_reset`/`pdm_history`, clock forcing |
| `ui/static/audio.js` (new), `ui/static/index.html`, `ui/static/workspace.js`, `ui/static/app.js` (mod) | Audio view, spectrogram, booth mode |
| `source/pdm/README.md`, `docs/electronica2026-demo-howto.md` (mod) | docs, Demo D |
| `tests/test_sd_pin_input.py`, `test_idle_skip.py`, `test_run_ahead.py`, `test_pdm_layout.py`, `test_pdm_gen.py`, `test_pdm_rx.py`, `test_pdm_e2e.py`, `test_pdm_server.py`, `ui_audio.test.js` (new) | tests |

Tests that need `source/pdm` modules do `sys.path.insert(0, str(REPO / "source"))` then `from pdm import …`, the way `tests/test_pif_eth.py` imports `pif_eth`. Create `source/pdm/__init__.py` (empty) in Task 4.

---

### Task 1: Pin-driven SD input (`PdmLink`, source modes, `CLK_SEL`/`CLK_INV`)

**Files:**
- Create: `pru_io/pdm_link.py`
- Modify: `pru_io/sd_filter.py` (`tick` at ~line 91, snapshot/restore/get_state), `pru_io/io_port.py` (`write_r30` ~line 45), `core/pru_core.py` (`step()` pre-tick next to the UART pre-tick ~line 268), `simulator.py` (new `attach_pdm_link`, `set_sd_source`)
- Test: `tests/test_sd_pin_input.py`

**Interfaces:**
- Produces:
  - `IOPort.gpo_listeners: list[Callable[[int, int], None]]` — called as `fn(old_gpo, new_gpo)` from `write_r30` only when the 20-bit GPO word changed.
  - `SigmaDeltaFilter.SOURCES = ("modulator", "pin", "off")`; `SigmaDeltaFilter.sources: list[str]` (default all `"modulator"`); `set_source(ch: int, mode: str) -> None` (ValueError on bad ch/mode); `pin_link: PdmLink | None`; `tick_n(n: int) -> None` (exactly equal to `n` calls of `tick()`; O(1) when no channel is in `"modulator"` mode); `clock_edge(net: int, rising: bool, data_level: Callable[[int], int]) -> None`.
  - `class PdmLinkError(RuntimeError)`.
  - `PdmLink(producer_core: PRUCore, consumer_core: PRUCore, time_of: Callable[[str, int], int], clock_pin: int, data_pins: dict[int, int], clock_net: int = 0)` — `data_pins` maps SD channel → producer GPO pin; `clock_net` is the SD clock input the producer clock drives (0 = SD0_CLK). Attributes `producer` and `consumer` hold the two cores.
  - `PdmLink.on_producer_gpo(old: int, new: int) -> None` (registered in `producer_core.io_port.gpo_listeners`).
  - `PdmLink.advance_consumer(consumer_cycles: int) -> None` — applies all queued edges with stamp ≤ `time_of(consumer, consumer_cycles)`.
  - `PdmLink.horizon() -> int | float` — `time_of(producer, producer.counters.cycles)`; `math.inf` once the producer is halted or past its program.
  - `PdmLink.next_valid_time(ch: int) -> int | None` — stamp of the queued edge that would set channel `ch` valid, without applying anything.
  - `PdmLink.edge_history: list[tuple[int, int]]` (stamp, gpo word) — bounded only in tests (`keep_history=True` kwarg, default False).
  - `PdmLink.snapshot()/restore()/reset()`.
  - `Simulator.set_sd_source(core: str, ch: int, mode: str) -> None`; `Simulator.attach_pdm_link(producer="pru1", consumer="pru0", clock_pin=0, data_pins={0: 1, 1: 1}) -> PdmLink`; `Simulator.detach_pdm_link() -> None`.

**Timing convention (decided here, used by Tasks 2/3/5/6):** an edge caused by a producer R30 write in an instruction that starts at producer cycle `c` is stamped `time_of(producer, c + 1)` (output valid when the instruction retires). A consumer instruction starting at consumer cycle `d` sees exactly the edges with stamp ≤ `time_of(consumer, d)`. `advance_consumer` raises `PdmLinkError("PDM link: consumer pru0 is ahead of producer pru1")` when `time_of(consumer, d) > horizon()`. `time_of` is `sim.iep.core_time_units`.

**Clock selection (verify against `references/SD_CLK_SEL_REG_part1.png`/`part2.png` before coding; audio_pipe's `pdm_rx.asm` comment is the starting point):** `CLK_SEL` = 0 → GPI R31[16] (not driven by the link: channel gets no clock), 1 → the channel's own SDn_CLK (net n), 2 → shared clock of its group (net 3·(n//3)). A pin-mode channel ticks on an edge of its selected net: rising if `CLK_INV` = 0, falling if `CLK_INV` = 1, sampling `data_pins[ch]` as it stands *after* all edges with the same or earlier stamp that precede this clock edge in queue order. Channels not in `"pin"` mode ignore the link; `"off"` channels never tick.

- [ ] **Step 1: Write failing tests** in `tests/test_sd_pin_input.py` (use `Simulator(config_path=nominal_config)`; drive the producer with a tiny PRU1 program that toggles GPO0 (clock) and sets GPO1 (data) from a fixed 1-bit pattern, e.g. `ldi32` a pattern word and `qbbs`/`set`/`clr` per bit):
  - `test_default_source_is_modulator` — fresh sim: `sd.sources == ["modulator"]*3`; existing `tests/test_sd_*.py` still pass (run them in Step 4).
  - `test_pin_mode_rising_edge_matches_cic` — ch0 `CLK_SEL=1, CLK_INV=0`, OSR 96, pin mode; after N producer clock periods ch0 `acc3` equals a Python triple integration of the data bits seen at each rising edge.
  - `test_shared_clock_inverted_channel_samples_falling_edge` — ch1 `CLK_SEL=2, CLK_INV=1` on the same data pin; ch1 accumulators equal the integration of the bits present at falling edges; ch0 of the rising-edge bits; both differ for a pattern that differs between halves.
  - `test_unselected_clock_gets_no_ticks` — ch1 `CLK_SEL=1` (own SD1_CLK, not wired): ch1 `acc1 == 0`.
  - `test_off_mode_never_ticks` — `set_source("pru0", 2, "off")`: ch2 accumulators stay 0 while PRU0 runs 10 000 instructions.
  - `test_tick_n_equals_repeated_tick` — modulator mode, sine, 3.072 MHz: two filters, one `tick()`×1000, the other `tick_n(1000)`: identical `snapshot()`.
  - `test_consumer_ahead_of_producer_raises` — step PRU0 alone past the producer's horizon → `PdmLinkError` with that message.
  - `test_next_valid_time_predicts_valid_rise` — the stamp returned equals the consumer time at which `channels[0].valid` first reads true when stepping one cycle at a time.
  - `test_snapshot_restore_roundtrip` — snapshot sd+link, run, restore, rerun: identical accumulators.

- [ ] **Step 2: Run them to verify they fail**

Run: `env -u PYTHONPATH python3 -m pytest tests/test_sd_pin_input.py -v`
Expected: FAIL (`ImportError: pdm_link` / `AttributeError: set_source`)

- [ ] **Step 3: Implement** `PdmLink`, the `SigmaDeltaFilter` changes, `IOPort.gpo_listeners`, the `PRUCore.step()` pre-tick (`if sd is not None and sd.pin_link is not None and sd.pin_link.consumer is self: sd.pin_link.advance_consumer(self.counters.cycles)` before dispatch), and the two `Simulator` methods. The edge queue is a `collections.deque` of `(stamp, gpo_word)`; `advance_consumer` pops in order, detects clock-net level changes against the last applied word, and calls `sd.clock_edge`. `next_valid_time` walks the queue without popping, counting the selected channel's sampling edges until `osr - _sample_count` remain.

- [ ] **Step 4: Run new and existing SD tests**

Run: `env -u PYTHONPATH python3 -m pytest tests/test_sd_pin_input.py tests/test_sd_channel.py tests/test_sd_filter.py tests/test_sd_e2e.py tests/test_sd_integration.py tests/test_sd_stepback.py tests/test_sd_memmap.py -v`
Expected: all PASS

- [ ] **Step 5: Commit** — `git add pru_io/pdm_link.py pru_io/sd_filter.py pru_io/io_port.py core/pru_core.py simulator.py tests/test_sd_pin_input.py && git commit -m "feat(sd): pin-driven SD input with CLK_SEL/CLK_INV (PdmLink)"`

---

### Task 2: Idle skip in `PRUCore` (run modes only)

**Files:**
- Modify: `core/pru_core.py` (new methods next to `_advance_idle_cycle` ~line 749), `simulator.py` (`idle_skip` attribute; `step_paced_many(..., idle_skip=False)`), `ui/server.py` (`run` and `run_multicore` paths pass the skip; `step`/`step_multicore` do not), `mcp_server/server.py` (`pru_run_until` uses `step_run`)
- Test: `tests/test_idle_skip.py`

**Interfaces:**
- Consumes: Task 1 `PdmLink.next_valid_time`, `PdmLink.horizon`, `SigmaDeltaFilter.tick_n`.
- Produces:
  - `PRUCore.step_run(max_cycles: int) -> int` — executes one normal `step()` or one skipped span of at most `max_cycles` cycles; returns instructions retired. With `max_cycles < 2` or no applicable pattern it is exactly `step()`.
  - `PRUCore.skipped_cycles: int` (cumulative, reset by `reset()`), for the UI load display.
  - `PRUCore._advance_bulk(cycles: int, instructions: int) -> None`.
  - `Simulator.idle_skip: bool = True`; `Simulator.step_paced_many(lead, followers, count=1, guard_ns=20.0, idle_skip=False)` — when true, lead and follower catch-up use `step_run` (follower budget = cycles to its target).

**Rules (all must hold, else fall back to `step()`):**
- Not when any `breakpoint` lies in the span (loop body, or the poll pc itself is skipped only for iterations *after* the first, never across a breakpoint at another pc — a spin's own pc with a breakpoint: no skip).
- Not when `io_port.uart_generator` is set, `io_port.perif.enabled`, `task_ring.busy`, or `io_port.i2c_device` is set.
- **Delay-loop collapse:** `self.loop_state` active, `self.pc == loop_state.start_address`, every instruction in `[start_address, end_address)` is `NOP`. Collapse `k = min(loop_state.count, max_cycles // body_len)` iterations (k ≥ 1): `_advance_bulk(k*body_len, k*body_len)`, `loop_state.count -= k`; if it reaches 0, `pc = end_address` and `loop_state = None` — identical to what `k*body_len` stepped NOPs leave behind.
- **Poll fast-forward:** instruction at `pc` is `QBBC`/`QBBS` with target == `pc`, testing `r31`; `io_port.read_r31` is in SD mode (`sd_filter.sd_en` and perif not enabled); the selected channel is in `"pin"` mode with `pin_link.consumer is self`. Let `u` = cycle units, `T0` = time of the current cycle, `te = next_valid_time(ch_sel)` (or `None`), `H = horizon()`. Skippable iterations `k` = number of iteration starts strictly before the first start ≥ `te`, capped by starts ≤ `H` and by `max_cycles`; skip only if `k ≥ 1`. Each skipped iteration is one taken branch: `_advance_bulk(k, k)`, pc unchanged, then `pin_link.advance_consumer(self.counters.cycles)`.
- `_advance_bulk` does, for the span, exactly what `step()`'s tail does per instruction, in bulk: timed accelerators `advance(cycles)`, `sd_filter.tick_n(instructions)`, `perif.advance_cycles(self.counters.cycles)` after the count update, `counters.cycles += cycles; counters.instruction_count += instructions`, device bus `advance_devices(cycles)` (or `_device_cycle += cycles`), `task_ring.tick(cycles)`, `cycle_observer(cycles)`, and MAC `begin_step()` called `min(instructions, 2)` times (R28/R29 do not change in a NOP or poll span, so two calls leave the latch exactly as n stepped instructions would). `skipped_cycles += cycles`.

- [ ] **Step 1: Write failing tests** in `tests/test_idle_skip.py`. Helper `run_both(source_pru0, source_pru1, cycles, skip)` builds a `Simulator(config_path=nominal_config)`, loads, runs (single core: loop `step_run(cycles_left)` vs `step()`; two cores: `step_paced_many("pru1", ["pru0"], ..., idle_skip=skip)`), and returns `(registers, pc, counters, memory DRAM0+shared, pin history)`.
  - `test_delay_loop_collapse_is_exact` — audio_pipe `pdm_clk.asm` (copy into `tests/asm/pdm_clk_ref.asm`) on PRU1, 200 000 cycles: skip on/off results identical, including the GPO edge times recorded via a `gpo_listeners` probe; `skipped_cycles > 0` with skip on.
  - `test_single_step_never_skips` — `Simulator.step("pru1", 1)` on the first NOP of a delay loop advances `instruction_count` by exactly 1.
  - `test_breakpoint_inside_delay_loop_still_hits` — breakpoint on the NOP of the delay loop, Run via `step_run`: stops with `pc` at the breakpoint and the same cycle count as a stepped run.
  - `test_modulator_mode_sd_unaffected` — `source/sdfm_sinc3_demo/sdfm_sinc3_demo.asm` (modulator mode, no pin link): skip on/off identical over 300 000 cycles (fast-forward must not engage; collapse may).
  - `test_perif_firmware_unaffected` — `source/perif_tx_pattern.asm` on PRU0 with perif enabled per its existing test setup: skip on/off identical.
  - `test_poll_fast_forward_is_exact` — PRU1: Task 1's pattern producer; PRU0: minimal SD consumer (configure ch0 pin mode, OSR 96, `qbbc self, r31, 28` spin, read shadow, clear valid, store to DRAM0, loop) — skip on/off identical, and with skip on PRU0 `skipped_cycles > 0.5 * cycles`.

- [ ] **Step 2: Run to verify failure** — `env -u PYTHONPATH python3 -m pytest tests/test_idle_skip.py -v` → FAIL (`AttributeError: step_run`).

- [ ] **Step 3: Implement** `step_run`, `_advance_bulk`, the two patterns, `Simulator.idle_skip` and the `step_paced_many` parameter; wire `ui/server.py` `run` (single core) and `run_multicore` (not `step_multicore`) to pass `idle_skip=sim.idle_skip`; `mcp_server/server.py` `pru_run_until` to use `step_run` with the remaining cycle budget. Add a server setting `{"action": "set_idle_skip", "enabled": bool}` that sets `sim.idle_skip` and echoes state. Signal-graph capture keeps its per-step `_capture_due` logic: a skipped span is one step, its pins are unchanged, so one sample with the post-span cycle stamp is correct — add `test_capture_over_skipped_span` to `tests/test_multicore_ws.py`-style coverage in `tests/test_idle_skip.py` (capture on, delay-loop firmware: every captured sample's pin word equals the stepped run's pin word at that cycle).

- [ ] **Step 4: Run new tests and the full suite**

Run: `env -u PYTHONPATH python3 -m pytest tests -q -x`
Expected: all PASS (same count as on `main` plus the new tests)

- [ ] **Step 5: Commit** — `git commit -m "feat(core): exact idle skip for run modes (NOP delay loops, R31 poll spins)"`

---

### Task 3: Run-ahead scheduler for one-way producer/consumer cores

**Files:**
- Modify: `simulator.py` (new `one_way_violations`, `run_ahead`, `class RunAheadRefused(RuntimeError)`)
- Test: `tests/test_run_ahead.py`

**Interfaces:**
- Consumes: Task 1 `PdmLink`, Task 2 `PRUCore.step_run`, `Simulator.idle_skip`.
- Produces:
  - `Simulator.one_way_violations(producer: str, consumer: str, producer_reads: list[tuple[int, int]], consumer_writes: list[tuple[int, int]]) -> list[str]` — human-readable reasons, empty = safe. Checks: any GPIO wire between the two cores (`list_gpio_wires()`); PERIF enabled on either core; an `XIN`/`XOUT`/`XCHG` to a scratchpad broadside ID (IDs from `xfr/scratchpad.py`) in both programs; producer program accesses the IEP (`c26` or an address in `0x2E000..0x2EFFF`); overlap of any `producer_reads` range with any `consumer_writes` range (ranges are `(addr, length)` in the shared map).
  - `Simulator.run_ahead(producer: str, consumer: str, cycles: int, *, producer_reads, consumer_writes, slice_cycles: int = 4096) -> dict` — raises `RunAheadRefused(reasons)` when `one_way_violations` is non-empty; otherwise repeats: run producer with `step_run` until its time ≥ consumer time + `slice_cycles` (or the consumer's end target), then run consumer with `step_run` while its instruction start time ≤ producer time, until the consumer has advanced `cycles`. Returns `{"producer_cycles": int, "consumer_cycles": int, "skipped": {core: int}, "breakpoint": str | None}`; stops early at a breakpoint on either core.

- [ ] **Step 1: Write failing tests** in `tests/test_run_ahead.py`:
  - `test_run_ahead_equals_lockstep` — Task 2's producer/consumer pair; run A: `run_ahead("pru1","pru0", 400_000, producer_reads=[], consumer_writes=[])`; run B: `step_paced_many("pru1", ["pru0"], …)` until PRU0 reaches the same cycle count; registers, counters, DRAM0, shared RAM, `edge_history` identical (skip on in both; and once more with `sim.idle_skip = False` in both).
  - `test_refuses_on_gpio_wire` — `add_gpio_wire("pru0", 5, "pru1", 5)` → `RunAheadRefused` whose reasons mention the wire.
  - `test_refuses_on_range_overlap` — `producer_reads=[(0x10100, 4)]`, `consumer_writes=[(0x10100, 0x400)]` → refused.
  - `test_stops_at_breakpoint` — breakpoint on the consumer's store instruction: returns `breakpoint == "pru0"` with consumer `pc` at it.

- [ ] **Step 2: Run to verify failure** — `env -u PYTHONPATH python3 -m pytest tests/test_run_ahead.py -v` → FAIL.
- [ ] **Step 3: Implement** `one_way_violations`, `RunAheadRefused`, `run_ahead`.
- [ ] **Step 4: Verify** — `env -u PYTHONPATH python3 -m pytest tests/test_run_ahead.py tests/test_idle_skip.py tests/test_sd_pin_input.py tests/test_multicore_ws.py -v` → all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(sim): run-ahead scheduling for one-way producer/consumer cores"`

---

### Task 4: Shared-RAM map, clip loader, clips

**Files:**
- Create: `source/pdm/__init__.py`, `source/pdm/pdm_shared.inc`, `source/pdm/pdm_layout.py`, `source/pdm/tools/make_clips.py`, `source/pdm/clips/chirp_lin_100_7000.wav`, `source/pdm/clips/chirp_log_100_7000.wav`, `source/pdm/clips/tone_1000.wav`, `source/pdm/clips/tone_3000.wav`, `source/pdm/clips/speech_librivox.wav`, `source/pdm/clips/SOURCES.md`
- Test: `tests/test_pdm_layout.py`

**Interfaces:**
- Produces (`pdm_layout.py`; `pdm_shared.inc` defines the same names as `.set` literals with a `PDM_` prefix):

| Name | Value | Meaning |
|---|---|---|
| `SHARED_BASE` | `0x10000` | shared RAM |
| `HDR_MAGIC` | `+0x00` u32 = `0x50444D31` | written by host |
| `HDR_N_CH` | `+0x04` u8 | host |
| `HDR_BLOCK` | `+0x06` u16 = 128 | host |
| `HDR_PCM_RATE` | `+0x08` u32 = 16000 | host |
| `HDR_CLIP_LEN` | `+0x0C` u32 frames | host |
| `HDR_GEN_POS` | `+0x10` u32 frames fetched | PRU1 |
| `HDR_GEN_DONE` | `+0x14` u32 (0/1) | PRU1 |
| `HDR_PCM_COUNT` | `+0x20` u32[8] | PRU0, per channel, written after the sample |
| `RING_BASE` | `+0x100`, stride `0x400` | ring k: 256 × int32 (24-bit signed), sample n at `RING_BASE + k*0x400 + 4*(n % 256)` |
| `CLIP_BASE` | `+0x2100` | interleaved L/R int16 frames |
| `CLIP_MAX_FRAMES` | 8192 | 0.512 s |

  - `pdm_layout.load_clip(path: Path) -> list[int]` — mono int16 at exactly 16 000 Hz, 16-bit, ≤ `CLIP_MAX_FRAMES`; stereo files use the first channel; normalised so `max(|x|) == 16384` (0.5 FS; silence stays all-zero); `ValueError(f"{path.name}: …")` naming the broken rule.
  - `pdm_layout.write_clips(memory, left: list[int], right: list[int]) -> int` — pads the shorter with zeros, writes `CLIP_BASE` frames and header fields (`MAGIC`, `N_CH=2`, `BLOCK`, `PCM_RATE`, `CLIP_LEN`, zeroes `GEN_*` and `PCM_COUNT`); returns frames. `memory` is `sim.memory` (`read(addr, n) -> (bytes, stalls)`, `write(addr, bytes)`).
  - `pdm_layout.read_header(memory) -> dict`, `pdm_layout.read_ring(memory, ch: int, start: int, count: int) -> list[int]`.
  - `pdm_layout.parse_inc(path) -> dict[str, int]` (used by the consistency test).
  - `make_clips.py` CLI: `python3 source/pdm/tools/make_clips.py chirps` writes the four generated clips (0.25 s each, linear chirp 100→7000 Hz, log chirp 100→7000 Hz, 1 kHz and 3 kHz tones, amplitude 0.5, stdlib `wave`/`math`); `python3 source/pdm/tools/make_clips.py speech <in-file> --start S --dur D` converts with `ffmpeg -ss S -t D -i in -ac 1 -ar 16000 -sample_fmt s16` to `speech_librivox.wav` and prints the command for SOURCES.md.

- [ ] **Step 1: Write failing tests** (`tests/test_pdm_layout.py`):
  - `test_inc_matches_python` — every `PDM_*` in `pdm_shared.inc` equals the Python constant; rings for 8 channels end ≤ `CLIP_BASE`; `CLIP_BASE + 4*CLIP_MAX_FRAMES <= 0x10000`.
  - `test_loader_normalises_to_half_scale` — a generated 1 kHz clip at 0.9 amplitude loads with `max(abs) == 16384`.
  - `test_loader_rejects_bad_clips` — 48 kHz, 8-bit, 9000 frames → `ValueError` whose message contains the file name and `"16000"` / `"16-bit"` / `"8192"` respectively.
  - `test_loader_accepts_silence` — all-zero clip loads as zeros.
  - `test_write_clips_header_and_padding` — write 100 + 50 frames into a `Simulator(nominal_config).memory`; header fields as in the table, frame 75 right sample == 0.
  - `test_shipped_clips_load` — every `clips/*.wav` loads; `SOURCES.md` mentions every shipped file.
- [ ] **Step 2: Run to verify failure** — `env -u PYTHONPATH python3 -m pytest tests/test_pdm_layout.py -v` → FAIL.
- [ ] **Step 3: Implement** `pdm_shared.inc`, `pdm_layout.py`, `make_clips.py`; run `make_clips.py chirps`.
- [ ] **Step 4: Speech clip.** Find a LibriVox recording (public domain; prefer a clear single reader, e.g. a short English-language section), download the MP3 to the session scratchpad, pick a 0.3–0.5 s phrase with clear voiced sounds, run `make_clips.py speech`. Write `clips/SOURCES.md`: per file the origin (generated by `make_clips.py chirps`, or LibriVox book/section/reader/URL/start/duration + the exact conversion command and the statement "LibriVox recordings are in the public domain").
- [ ] **Step 5: Verify** — `env -u PYTHONPATH python3 -m pytest tests/test_pdm_layout.py -v` → PASS.
- [ ] **Step 6: Commit** — `git add source/pdm tests/test_pdm_layout.py && git commit -m "feat(pdm): shared-RAM map, clip loader and booth clips"`

---

### Task 5: PRU1 generator `pdm_gen.asm` + references

**Files:**
- Create: `source/pdm/pdm_gen.asm`, `source/pdm/reference.py`
- Test: `tests/test_pdm_gen.py`

**Interfaces:**
- Consumes: Task 1 (`PdmLink.edge_history`), Task 4 (`pdm_shared.inc`, `write_clips`).
- Produces:
  - `pdm_gen.asm`: entry `PDM_GEN`, `.include "pdm_shared.inc"`. Build options (plain literals): `PDM_CLK_PIN .set 0`, `PDM_DAT_PIN .set 1`, `CFG_T_SH_CYC .set 65`, `CFG_FRAC_ACC .set 5`, `CFG_FRAC_MAX .set 48`, `PCM_PERIODS .set 192`, `SETUP_MIN_CYC .set <chosen ≥ 8>`.
  - `reference.sigma_delta2(pcm: list[int], periods_per_sample: int = 192) -> list[int]` — one bit per PDM period for one channel:
    ```python
    FS = 32768
    i1 = i2 = 0; fb = -FS  # previous bit 0
    for x in pcm:                       # each PCM sample held for periods_per_sample periods (ZOH)
        for _ in range(periods_per_sample):
            i1 += x - fb
            i2 += i1 - fb
            bit = 1 if i2 >= 0 else 0
            fb = FS if bit else -FS
            yield bit
    ```
    After the clip, `x = 0` forever (silence).
  - `reference.pdm_clock_edges(n_periods: int, start_cycle: int) -> list[tuple[int, bool]]` — the fractional-adder schedule (43 × 65-cycle and 5 × 66-cycle periods per 48, high 32/33, low 33; exactly the pattern of audio_pipe `pdm_clk.asm`).

**Firmware behaviour:** both channels' integrators in registers; one 32-bit `LBBO` from `CLIP_BASE + 4*frame` (L in `.w0`, R in `.w2`) every `PCM_PERIODS` periods while `frame < CLIP_LEN`, then x = 0, `HDR_GEN_DONE = 1`; `HDR_GEN_POS` updated per frame. Left bit (SD ch0, sampled at the rising edge) is driven during the low half, right bit (ch1, falling edge) during the high half, each at least `SETUP_MIN_CYC` cycles before its sampling edge. Every path through a half period has a fixed cycle count (branch-balanced) so edge times never depend on data. Delay padding uses `LOOP n` + single `NOP` bodies so the idle skip collapses them.

- [ ] **Step 1: Write failing tests** (`tests/test_pdm_gen.py`; PRU1 only, producer side observed through `PdmLink(keep_history=True)` attached to an idle PRU0 with all channels `"off"`, or a plain `gpo_listeners` probe):
  - `test_clock_edges_follow_fractional_schedule` — first 480 periods: every clock edge cycle equals `pdm_clock_edges` exactly (after the init offset, measured from the first rising edge).
  - `test_data_bits_match_reference` — clips: 1 kHz tone L, 3 kHz tone R (from `clips/`), 2 PCM frames' worth of periods + 10: the data level at each rising edge equals `sigma_delta2(left)` bits, at each falling edge `sigma_delta2(right)`.
  - `test_setup_margin` — for every sampling edge, cycles since the last data change ≥ `SETUP_MIN_CYC`.
  - `test_clip_end_sets_done_and_silence` — `CLIP_LEN = 3`: after 3×192 periods `HDR_GEN_DONE == 1`, `HDR_GEN_POS == 3`, following bits equal the reference with x = 0.
  - `test_idle_skip_collapses_padding` — 50 000 cycles with `step_run`: identical edge history to `step()`, PRU1 `skipped_cycles > 0`.
- [ ] **Step 2: Run to verify failure** — `env -u PYTHONPATH python3 -m pytest tests/test_pdm_gen.py -v` → FAIL.
- [ ] **Step 3: Implement** `reference.py` (`sigma_delta2`, `pdm_clock_edges`) and `pdm_gen.asm`. Document the per-half-period cycle budget and register map in the file header, as audio_pipe does.
- [ ] **Step 4: Verify** — `env -u PYTHONPATH python3 -m pytest tests/test_pdm_gen.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(pdm): PRU1 PDM generator (3.072 MHz clock, 2x 2nd-order sigma-delta)"`

---

### Task 6: PRU0 receiver `pdm_rx.asm` (N_CH, shared-RAM rings)

**Files:**
- Create: `source/pdm/pdm_rx.asm`; copy from `~/GitHub/audio_pdm/audio_pipe`: `decimation_fir.h` → `source/pdm/decimation_fir.h`, `tools/gen_fir_coefs.py` → `source/pdm/tools/gen_fir_coefs.py` (fix its paths), `firmware/fir_coefs.inc` → `source/pdm/fir_coefs.inc` (regenerate with the copied tool, must be byte-identical)
- Modify: `source/pdm/reference.py` (add `cic3`, `fir_decimate`, `dc_block`, `pcm_chain`)
- Test: `tests/test_pdm_rx.py`

**Interfaces:**
- Consumes: Task 1 (pin mode, `PdmLink`), Task 4 (`pdm_shared.inc`, `read_ring`, `read_header`), Task 5 (`sigma_delta2` for stimulus).
- Produces:
  - `pdm_rx.asm`: entry `PDM_RX`; build options `N_CH .set 2` (1..8, `.emsg` otherwise), `SD_OSR .set 96`, `SD_SAMPLE_SIZE .set 95`, `SD_HALF_SCALE .set 442368`, `DC_BLOCK .set 1`, `BLOCK_EVENT .set 0`, `WARMUP_SAMPLES .set 3`, plus per-channel literal tables (`SD_CLK_SEL_CHk`, `SD_CLK_INV_CHk`, `SD_CH_SEL_B3_CHk`) guarded by `.emsg` like audio_pipe. Defaults: ch0 `CLK_SEL=1, CLK_INV=0`; ch1 `CLK_SEL=2, CLK_INV=1`; for k ≥ 2 the same pattern as ch (k % 2) on group clocks (documented as the 8-mic hardware assumption).
  - DRAM0 layout: coefficients `0x800` (shared), per-channel state block `0x900 + k*0x220` (comb state 3 words, counters, delay index/phase, mirrored 128-word delay line, int64 DC estimate). Must fit 8 channels below `0x2000`.
  - Output: PCM sample to ring k, then `HDR_PCM_COUNT[k]` (sample first, then count — publish order from audio_pipe); R31 event at each 128-sample block end of ch `N_CH-1`.
  - `reference.pcm_chain(bits: list[int], osr=96, dc_block=True) -> list[int]` — CIC3 (comb mod 2^28, warm-up dropped) → FIR ↓2 (Q27 table parsed from `fir_coefs.inc`) → DC blocker → sat24; ported from audio_pipe `tests/test_pdm_rx.py` (`cic3_reference` and its FIR/DC helpers).

**Scheduling (decided):** main loop round-robins ch 0..N_CH−1 per SD window (select `ch_sel`, spin `qbbc self, r31, 28`, read shadow, clear valid, comb). FIR is staggered: even channels run their FIR on decimation phase 0, odd channels on phase 1, so a window runs at most ⌈N_CH/2⌉ FIRs. Budget: worst window ≤ 6250 cycles for N_CH = 8.

- [ ] **Step 1: Write failing tests** (`tests/test_pdm_rx.py`; stimulus: a tiny PRU1 "bit player" program is not needed — use Task 5's `pdm_gen.asm` as the producer with tone clips, running lockstep `step_paced_many("pru1", ["pru0"])` so this task does not depend on Task 3; record each channel's sampled bits from `PdmLink(keep_history=True)`):
  - `test_fir_coefs_regenerate_identically` — running the copied `gen_fir_coefs.py` reproduces `fir_coefs.inc` byte for byte.
  - `test_pcm_matches_reference_exactly` — 1 kHz L / 3 kHz R, ≥ 300 PCM samples per channel: rings (read via `read_ring` while running in chunks ≤ 128 samples) equal `pcm_chain(bits_ch)` sample for sample, both channels.
  - `test_pcm_count_and_block_event` — `HDR_PCM_COUNT[k]` equals samples written; one R31 event write per 128 samples (probe `io_port.write_r31` as audio_pipe does).
  - `test_worst_window_fits_budget` — record cycles from each spin exit of ch0 to the next spin entry of ch0 (a window); max ≤ 6250 for N_CH = 2; per-channel worst cost `w_comb`, `w_fir` measured; assert `8*w_comb + 4*w_fir + overhead ≤ 6250` (the N_CH = 8 budget check by extrapolation from measured per-channel paths; print the numbers).
  - `test_n_ch_build_options_guarded` — `N_CH 9` and an inconsistent `SD_CH_SEL_B3_CH1` fail to load with the `.emsg` text.
  - `test_n_ch_1_still_works` — N_CH = 1: ch0 PCM equals reference.
- [ ] **Step 2: Run to verify failure** — `env -u PYTHONPATH python3 -m pytest tests/test_pdm_rx.py -v` → FAIL.
- [ ] **Step 3: Implement** `pdm_rx.asm` (port audio_pipe's verified DSP blocks unchanged: comb mod 2^28, MAC multiply-only with signed correction and the one-NOP operand latency, Q32 DC blocker, sat24) and the reference functions. Shared-RAM stores use a base register loaded with `ldi32 rX, PDM_SHARED_BASE` and `SBBO` (no CTPPR dependency).
- [ ] **Step 4: Verify** — `env -u PYTHONPATH python3 -m pytest tests/test_pdm_rx.py tests/test_pdm_gen.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(pdm): PRU0 N-channel PDM receiver (SINC3, FIR /2, DC block) into shared RAM"`

---

### Task 7: `PdmDemo` driver, end-to-end checks, equivalence gates, speed bench

**Files:**
- Create: `source/pdm/pdm_demo.py`, `source/pdm/tools/bench.py`
- Test: `tests/test_pdm_e2e.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces:
  - `PdmDemo(sim: Simulator, *, run_ahead: bool = True, chunk_cycles: int = 400_000)`.
  - `PdmDemo.setup(clip_left: Path, clip_right: Path, *, swap: bool = False) -> list[str]` — returns notices. Raises `ValueError` if `sim` clocks are not 200/200 MHz (the server forces them first; tests use `nominal_config`). Loads both firmwares (`include_paths=[source/pdm]`), `gpcfg_write("pru0", 3)` (SD mux), sets ch0/ch1 `"pin"`, ch2 `"off"` on PRU0 and all channels `"off"` on PRU1's SD filter, `attach_pdm_link("pru1", "pru0", clock_pin=0, data_pins={0: 1, 1: 1})`, writes clips, clears history. If `one_way_violations` is non-empty, it sets `self.mode = "lockstep"` and adds the notice `"Run-ahead not possible (<reasons>); running lockstep"`.
  - `PdmDemo.run_chunk() -> dict` — advances ≤ `chunk_cycles` PRU0 cycles (`run_ahead` or `step_paced_many("pru1", ["pru0"], idle_skip=sim.idle_skip)`), then drains rings; returns `{"new": {ch: (start_index, samples)}, "status": status()}`. Never lets more than 128 samples accumulate per chunk (`chunk_cycles` is capped at 1 600 000).
  - `PdmDemo.history: dict[int, list[int]]`; `PdmDemo.done: bool` (both clips' PCM plus 64 samples of filter delay drained); `PdmDemo.status() -> dict` with keys `audio_ms`, `clip_ms`, `speed_ms_per_s` (audio per wall second over the last 10 chunks), `load` (`{"pru0": busy_fraction, "pru1": busy_fraction}` from `skipped_cycles`), `mode` (`"run-ahead"`/`"lockstep"`), `overrun` (`None` or message), `done`, `notices`.
  - Overrun: if `HDR_PCM_COUNT[k] - drained[k] > 256`, set `overrun = f"PCM ring {k} overrun: {lost} samples lost"`; `run_chunk` then raises `RuntimeError` with the same text after recording it in status.
  - `bench.py` CLI: `python3 source/pdm/tools/bench.py --audio-ms 20` prints a table: mode (lockstep/run-ahead) × skip (off/on) → wall s, simulated audio ms, ms audio per wall s, s audio per minute.

- [ ] **Step 1: Write failing tests** (`tests/test_pdm_e2e.py`):
  - `test_tones_end_to_end` — `tone_1000.wav` L, `tone_3000.wav` R, run to `done`: zero-crossing frequency of history[0] within ±1 % of 1000 Hz, history[1] of 3000 Hz; Goertzel at 3 kHz in ch0 and at 1 kHz in ch1 ≥ 60 dB below the own tone (crosstalk).
  - `test_history_equals_reference` — history[k] equals `pcm_chain(bits recorded by the link for ch k)` (full chain incl. the PRU1 modulator path).
  - `test_chirp_ridge_follows_law` — `chirp_lin_100_7000.wav` on L: per 256-sample Hann frame (hop 128) the peak bin frequency is within 2 bins of the chirp's instantaneous frequency at the frame centre (account for the measured pipeline delay).
  - `test_idle_skip_equivalence_full_pipeline` — **merge blocker**: 20 ms of audio with `sim.idle_skip` True vs False (lockstep both): identical history, registers, counters, `edge_history`.
  - `test_run_ahead_equivalence_full_pipeline` — **merge blocker**: 20 ms run-ahead vs lockstep (skip on both): identical as above.
  - `test_overrun_is_reported` — `chunk_cycles` forced (bypassing the cap via a private attribute) to 3 500 000 → `RuntimeError` containing `"overrun"`, and `status()["overrun"]` set.
  - `test_wrong_clock_rejected` — `Simulator` with PRU1 at 250 MHz → `setup` raises `ValueError` mentioning 200 MHz.
- [ ] **Step 2: Run to verify failure** — `env -u PYTHONPATH python3 -m pytest tests/test_pdm_e2e.py -v` → FAIL.
- [ ] **Step 3: Implement** `pdm_demo.py` and `bench.py`. `producer_reads = [(CLIP_BASE, 4*CLIP_MAX_FRAMES), (SHARED_BASE+HDR_CLIP_LEN, 4)]`, `consumer_writes = [(SHARED_BASE+HDR_PCM_COUNT, 32), (SHARED_BASE+RING_BASE, 8*0x400)]`.
- [ ] **Step 4: Verify** — `env -u PYTHONPATH python3 -m pytest tests/test_pdm_e2e.py -v` → PASS. (Mark the long tests with the repo's slow marker if one exists; otherwise keep audio ≤ 40 ms per test.)
- [ ] **Step 5: Measure speed** — `env -u PYTHONPATH python3 source/pdm/tools/bench.py --audio-ms 20`; paste the table into `source/pdm/README.md` (Task 10) and the PR. Target ≥ 0.1 s audio/min for run-ahead + skip; if missed, record the profile (`python3 -m cProfile -s cumtime source/pdm/tools/bench.py --audio-ms 5 | head -40`) in the PR — do not hide it.
- [ ] **Step 6: Commit** — `git commit -m "feat(pdm): PdmDemo driver, end-to-end and equivalence tests, speed bench"`

---

### Task 8: UI server: PDM endpoints, run loop integration, clock forcing, history

**Files:**
- Modify: `ui/server.py` (refactor the body of `put_clock_speed` into `_apply_clock_speed(mhz, group=None) -> None`; add `/pdm/clips`, ws actions)
- Test: `tests/test_pdm_server.py`

**Interfaces:**
- Consumes: Task 7 `PdmDemo`.
- Produces (websocket, client → server):
  - `{"action": "pdm_setup", "clip_left": str, "clip_right": str, "swap": bool}` — if clocks ≠ 200/200, call `_apply_clock_speed(200)` and send `{"type": "pdm_notice", "text": "PRU clocks set to 200 MHz for the PDM demo"}`; create a module-level `_pdm = PdmDemo(sim)` and `setup`; reply `pdm_status`.
  - `{"action": "pdm_run", "run_id": any}` — gated by the existing `_execution_owner` (mode `"pdm"`), one `run_chunk()`; replies `{"type": "pdm_pcm", "ch": k, "start": n, "samples": [int…]}` per channel with new samples, then `pdm_status` (with `run_id` echoed). Client re-sends `pdm_run` while running and not `done`.
  - `{"action": "pdm_stop"}`, `{"action": "pdm_reset"}` (re-setup with the same clips, history cleared), `{"action": "pdm_history"}` (all history as `pdm_pcm` messages from start 0, then `pdm_status`).
  - Server → client `{"type": "pdm_status", "audio_ms", "clip_ms", "speed_ms_per_s", "load", "mode", "overrun", "done", "notices", "clips": {"left": str, "right": str}, "running": bool}`.
  - `GET /pdm/clips` → `{"clips": [{"name": str, "ms": float}]}` from `source/pdm/clips/*.wav`.
  - Connection drop of the owner pauses (existing `_stop_execution`); `_pdm` and its history persist.

- [ ] **Step 1: Write failing tests** (`tests/test_pdm_server.py`, FastAPI `TestClient` as in `tests/test_multicore_ws.py`; config pinned via the existing server-config fixture pattern used in `tests/test_clock_speed_endpoint.py`):
  - `test_clip_list` — `/pdm/clips` lists the shipped clips with `ms ≈ 250`.
  - `test_setup_forces_200mhz_with_notice` — server started at 250 MHz: `pdm_setup` → a `pdm_notice` with that text; `/config/clock_speed` reports 200 for both groups.
  - `test_run_chunks_stream_pcm` — `pdm_setup` + three `pdm_run`: `pdm_pcm` starts are contiguous per channel, `pdm_status.audio_ms` increases.
  - `test_history_rebuild_after_reconnect` — run two chunks, close the socket, reconnect, `pdm_history`: concatenated samples equal what was streamed; one more `pdm_run` continues with `start` == previous total (no gap, no duplicate).
  - `test_second_client_cannot_run` — another socket's `pdm_run` while the first owns execution → the existing ownership error.
  - `test_bad_clip_name_rejected` — `clip_left: "../../memory.cfg"` → error message, no file read outside `clips/`.
- [ ] **Step 2: Run to verify failure** — `env -u PYTHONPATH python3 -m pytest tests/test_pdm_server.py -v` → FAIL.
- [ ] **Step 3: Implement** the refactor and handlers (reuse `_execution_owner`/`_stop_execution`/`_publish_updates`; do not add a server-side run loop).
- [ ] **Step 4: Verify** — `env -u PYTHONPATH python3 -m pytest tests/test_pdm_server.py tests/test_clock_speed_endpoint.py tests/test_multicore_ws.py tests/test_server.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(ui): PDM demo server API (setup, chunked run, history, clock forcing)"`

---

### Task 9: Audio view, spectrogram, booth mode

**Files:**
- Create: `ui/static/audio.js`
- Modify: `ui/static/index.html` (nav button `data-workspace-view="audio"` after I/O & Devices; `<section id="audio-view" class="workspace-view" hidden>`; styles), `ui/static/workspace.js` (accept the `audio` view), `ui/static/app.js` (route `pdm_*` messages to `audio.js`; `<script src="audio.js">`)
- Test: `tests/ui_audio.test.js`

**Interfaces:**
- Consumes: Task 8 messages and `/pdm/clips`.
- Produces (`audio.js`, attached to `window.PruAudio` for tests):
  - `fft(re: Float64Array, im: Float64Array): void` — in-place radix-2, length power of two.
  - `hann(n: number): Float64Array`.
  - `spectrumColumn(samples: ArrayLike<number>, fullScale = 8388607): Float64Array` — 256 samples → 129 values in dBFS (`20*log10(|X_k| / (fullScale * sum(hann)/2))`, floored at −140).
  - `colormap(db: number, min = -90, max = 0): [number, number, number]` — perceptual (magma-like) ramp, clamped.
  - `class Spectrogram { constructor(canvas, {fftSize=256, hop=128, minDb=-90, maxDb=0}); append(start: number, samples: number[]): void; reset(): void; columns: number; draw(): void }` — one column per hop, 0–8 kHz bottom→top, time left→right, a cursor at the build-up edge, colour bar and axis labels (font scales with canvas height for booth mode). `append` ignores samples already held (idempotent for history replays) and throws on a gap.
  - View controls: Gen L / Gen R `<select>` filled from `/pdm/clips`, read-only routing panel ("Gen L → PRU0 SD ch0 · rising edge · PRU1 GPO1 → SD0_D/SD1_D, clock GPO0 → SD0_CLK", L↔R swap toggle), progress bar (`audio_ms / clip_ms`), speed readout ("2.4 ms audio / s"), load chips (PRU0/PRU1 busy %), mode chip (run-ahead/lockstep), overrun and notice line, Run / Stop / Reset, a "Booth" button.
  - Booth mode: adds class `audio-booth` to `#audio-view` and calls `requestFullscreen()` when available; both spectrograms fill the viewport ("Left mic" / "Right mic"), one-line strip with the two clip pickers, Run and the speed counter, caption "PRU1 firmware synthesises 2 PDM microphones · PRU0 firmware decodes them · every cycle simulated". `Esc` / `fullscreenchange` leaves booth mode.
  - On view open: send `pdm_history` so a reload rebuilds the picture.

- [ ] **Step 1: Write failing tests** (`tests/ui_audio.test.js`, node `vm` with stub DOM/canvas like `tests/ui_workspace.test.js`):
  - `fft of a bin-centred sine peaks at that bin` — 256 samples of bin 32 → `spectrumColumn` max at index 32, ≥ 60 dB above bin 80.
  - `full-scale sine reads about 0 dBFS` — amplitude 8388607, bin 32 → value within ±1 dB of 0.
  - `colormap clamps and is monotonic in luminance` — −200 → same as −90; 0 → brightest; luminance increasing over 10 steps.
  - `append is idempotent and rejects gaps` — append(0, 256 samples) twice → `columns == 1` (256 samples, hop 128, fft 256 → 1 column); append(300, …) throws.
  - `booth button toggles class and Esc leaves` — click → `audio-booth` present; dispatch `keydown` Escape → removed.
  - `history replay after reload` — feeding `pdm_pcm` messages from start 0 into a fresh view yields the same `columns` as the live sequence.
- [ ] **Step 2: Run to verify failure** — `node --test --test-isolation=none tests/ui_audio.test.js` → FAIL.
- [ ] **Step 3: Implement** `audio.js` and the HTML/CSS/JS wiring, following the existing theme variables (light/dark), panel classes and `runtime-device-*` control styles.
- [ ] **Step 4: Verify** — `node --test --test-isolation=none tests/ui_*.test.js` → all PASS; `env -u PYTHONPATH python3 -m pytest tests/test_ui_design_tokens.py tests/test_dashboard_tabs.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(ui): Audio view with live spectrograms and booth mode"`

---

### Task 10: Docs, clpru check, real-browser check, PR

**Files:**
- Create: `source/pdm/README.md`
- Modify: `docs/electronica2026-demo-howto.md` (add "Demo D — PDM microphones → spectrogram" with a pointer to the README; that file lives on branch `docs/electronica2026-howto` — if it is not on `main` yet, put Demo D only in `source/pdm/README.md` and note the follow-up in the PR)
- Report: `docs/reports/2026-11-xx-pdm-audio-demo-report.md`

- [ ] **Step 1: README** — signal chain diagram, pins (PRU1 GPO0/1 → PRU0 SD0_CLK, SD0_D, SD1_D), shared-RAM map (from `pdm_layout.py`), PRU0 and PRU1 cycle budgets (measured numbers from Tasks 5–7, the N_CH = 8 extrapolation), simulator features used (pin-driven SD, idle skip, run-ahead), the speed table from Task 7, booth steps (start `python3 ui/server.py`, Audio tab, pick clips, Run, Booth), hardware porting notes (GPCFG SD mux, pinmux, IM73D122 edge check, CTBIR), clip provenance pointer.
- [ ] **Step 2: clpru assembly check** — assemble both firmwares with `~/ti/ti-cgt-pru_2.3.3/bin/clpru` (flags per the open-pru Linux build: `-v4` for ICSSG; include path `source/pdm`); expected: 0 errors. Fix syntax the simulator accepted but clpru rejects, re-run Tasks 5–7 tests. Record the exact command and output in the report.
- [ ] **Step 3: Real-browser check** (not CI) — start `python3 ui/server.py`, drive headless Chrome (claude-in-chrome skill): Audio view, pick `speech_librivox.wav` / `chirp_lin_100_7000.wav`, Run, columns appear, measured ms audio/s in the UI, Booth in/out, reload mid-run → picture rebuilds, Run continues. Screenshots + observations into the report.
- [ ] **Step 4: Full verification** — `env -u PYTHONPATH python3 -m pytest tests -q` and `node --test --test-isolation=none tests/ui_*.test.js` → all PASS; `git status` shows `memory.cfg` unstaged.
- [ ] **Step 5: Commit and PR** — `git add source/pdm/README.md docs/reports/… && git commit -m "docs(pdm): README, booth steps, verification report"`; `git push -u origin feat/pdm-audio-demo`; `gh pr create --base main` with summary, speed table, equivalence-test names, and the body ending `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Ask Thomas before pushing.
