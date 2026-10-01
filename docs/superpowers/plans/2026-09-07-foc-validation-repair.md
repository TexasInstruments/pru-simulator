# Open-Loop FOC Validation Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the open-loop FOC simulator so firmware, simulated time, PMSM integration, lifecycle controls, observability, UI, and MCP all share one honest 100 kHz simulation clock and the validation criteria in the user-provided repair plan.

**Architecture:** Version the schema-generated shared ABI with an explicit control-period setting, true IEP timestamps, ramped-command telemetry, and status flags. Pace the PRU firmware with absolute IEP deadlines; integrate the plant from elapsed IEP time using the last coherent PWM publication; expose cached coherent state and timestamped sample batches through the runtime/server; and keep UI/MCP controls on the same lifecycle boundary.

**Tech Stack:** Python 3, pytest, FastAPI WebSocket, vanilla JavaScript/Canvas, PRU assembly interpreted by the local simulator, schema-generated Python/assembly/C ABI snapshots.

**Spec:** `docs/superpowers/specs/2026-09-04-foc-open-loop-design.md` plus the user-provided `Open-loop FOC validation and repair plan`.

## Global Constraints

- Preserve open-loop control; do not add closed-loop current or speed regulation.
- Pace assembly at 100 kHz using absolute IEP deadlines and the configured IEP frequency.
- Integrate the plant from elapsed IEP time and apply the previously held duties until a new coherent PWM publication.
- Use conventional inverse Clarke/SVPWM and matching voltage/current transforms.
- Reject non-finite references, invalid acceleration, and voltage-vector magnitude above `1/sqrt(3)` pu.
- Keep the existing motor parameters fixed during implementation repairs.
- Disabled output is neutral `0.5/0.5/0.5` and holds the commanded angle.
- Preserve legacy `IdRef`/`IqRef` API aliases while documenting their voltage meaning.
- Preserve the user’s pre-existing `pru-simulator/memory.cfg` 200 MHz edit.

---

### Task 1: Version and regenerate the FOC ABI

**Files:**
- Modify: `schema/foc_abi.json`
- Modify: `tools/gen_foc_abi.py`
- Regenerate: `source/foc_abi.inc`, `pru_io/foc_abi.py`, `include/foc_abi.h`
- Test: `tests/test_foc_abi_generated.py`, new ABI contract tests in `tests/test_foc_validation_repair.py`

**Interfaces:**
- Control block gains `control_period_iep_ticks` and canonical voltage-reference semantics while retaining legacy aliases at the Python runtime boundary.
- PWM output gains true timestamp/status fields and telemetry fields for ramped command speed and electrical angle error.
- Generated helpers provide status constants, compatible packing/unpacking, and bounded coherent reads.

- [ ] **Step 1: Write failing ABI contract tests** for ABI version, control-period offset, timestamp/status fields, voltage-reference aliases, and generated snapshot idempotence.
- [ ] **Step 2: Run the focused ABI tests** and confirm they fail against the current version-1 layout.
- [ ] **Step 3: Update the schema and generator** with contiguous fields, status constants, canonical voltage names, compatibility aliases, and generated C definitions for every block.
- [ ] **Step 4: Regenerate all snapshots** with `python tools/gen_foc_abi.py`.
- [ ] **Step 5: Run focused ABI tests** and then the existing ABI drift test.

### Task 2: Repair PRU timing, enable handling, transforms, and saturation

**Files:**
- Modify: `source/foc_open_loop/foc_open_loop.asm`
- Modify: `tests/test_foc_firmware.py`
- Add/modify: `tests/test_foc_validation_repair.py`

**Interfaces:**
- Firmware consumes the versioned control period and reads the IEP count through `c26`.
- Firmware publishes neutral output/status when disabled, true IEP publication timestamps, ramped speed telemetry, and saturation/deadline status.

- [ ] **Step 1: Add failing firmware tests** for generation-zero latching, independent enable changes, neutral disabled output, timestamp cadence at 200/250/300 MHz, conventional cardinal-angle transforms, valid-duty clamping, and deadline fault publication.
- [ ] **Step 2: Run those tests** and record the expected failures (free-running loop, stale/non-neutral output, swapped transforms, and absent status/timestamps).
- [ ] **Step 3: Implement one absolute-deadline wait loop** with modular 64-bit deadline comparison, fixed control-period advancement, and no loop-count time fallback.
- [ ] **Step 4: Implement independent enable handling** and coherent neutral publication while holding the command angle.
- [ ] **Step 5: Replace the swapped SVGEN phase mapping** with conventional inverse Clarke/SVPWM and add final duty clamping/status bits.
- [ ] **Step 6: Publish real IEP timestamps and ramped-command/status data** under the existing PWM seqlock.
- [ ] **Step 7: Run the firmware-focused tests** at each configured clock and the existing firmware suite.

### Task 3: Make the PMSM model and runtime time-correct and reset-safe

**Files:**
- Modify: `pru_io/foc_motor_model.py`
- Modify: `pru_io/foc_runtime.py`
- Modify: `simulator.py`
- Modify: `core/pru_core.py` for explicit execution-fault state
- Add/modify: `tests/test_foc_validation_repair.py`, `tests/test_foc_open_loop.py`

**Interfaces:**
- `FocMotorModel.advance_to(timestamp)` uses only elapsed IEP ticks, held coherent PWM, and fixed motor parameters.
- `FocRuntime.state()` returns cached coherent `pwm`/`fb`, clock metadata, status, session/reset id, and timestamped samples.
- Runtime lifecycle methods reset owned memory, plant state, pending execution, and history together; step-back is rejected while FOC is active.

- [ ] **Step 1: Add failing timing/lifecycle tests** for held-duty integration, no loop-counter fallback, incomplete-publication retention, neutral load state, stop-at-boundary, reload/reset clearing, IEP rollover/rebase, and disabled step-back.
- [ ] **Step 2: Run the focused tests** and verify current loop-count scaling, stale PWM, and `None` snapshots reproduce the failures.
- [ ] **Step 3: Refactor the model** to integrate old duties before adopting a new coherent PWM, publish feedback on a bounded telemetry cadence, and retain the last coherent block on retries.
- [ ] **Step 4: Refactor runtime initialization/lifecycle** to clear all FOC blocks, validate references, convert RPM/s acceleration to per-loop Q24 slew, preserve legacy aliases, and freeze measured speed on stop.
- [ ] **Step 5: Add clock/session metadata and sample batching** with a 100 µs simulation-time sample cadence and 100 ms history window.
- [ ] **Step 6: Add explicit PRU execution-fault capture** and make reset/rebase paths restore IEP/model state together.
- [ ] **Step 7: Run model/runtime tests at 200/250/300 MHz** and confirm matching trajectories at matching simulated times.

### Task 4: Repair WebSocket controls, telemetry, charts, and needles

**Files:**
- Modify: `ui/server.py`
- Modify: `ui/static/app.js`
- Modify: `ui/static/index.html`
- Add/modify: `tests/test_foc_ui.py`, `tests/test_foc_validation_repair.py`

**Interfaces:**
- `foc_start` applies visible references and starts one bounded async execution task for PRU0 plus the plant.
- `foc_stop` disables at a coherent boundary and pauses without zeroing measured speed.
- `foc_state` includes clock metadata, status, ramped command, angle error, and timestamped sample batches.

- [ ] **Step 1: Add failing WebSocket/browser-contract tests** for voltage labels/defaults, RPM/s acceleration, start/stop execution, stale-sample retention, 100 ms timestamped windows, reverse/stationary samples, angle convention, reset/session metadata, and step-back rejection.
- [ ] **Step 2: Run focused UI tests** and confirm the current UI is index/theta sampled, uses current-reference labels, independently animates the dial, and requires manual generic Run execution.
- [ ] **Step 3: Implement the bounded FOC execution task** with cooperative yielding, approximately 30 Hz publication, immediate control responses, and fast-path disabled for instruction stepping/breakpoints/tracing.
- [ ] **Step 4: Update markup and rendering** for `Vd/Vq reference`, `RPM/s`, requested/ramped/measured speed, angle error, simulated time, loop frequency, simulation/wall ratio, status/saturation, and shared angle convention.
- [ ] **Step 5: Plot timestamped batches** on a 100 ms simulation-time window sampled every 100 µs, including zero speed and reverse rotation.
- [ ] **Step 6: Run the focused WebSocket/UI tests** and browser-facing static contract checks.

### Task 5: Fix MCP loading/fault reporting and documentation

**Files:**
- Modify: `mcp_server/server.py`
- Modify: `source/foc_open_loop/README.md`, `source/foc_open_loop/PROJECT_REPORT.md`
- Modify: `docs/superpowers/specs/2026-09-04-foc-open-loop-design.md` only where the repaired ABI/operating procedure differs
- Add/modify: `tests/test_mcp_server.py`, `tests/test_foc_validation_repair.py`

**Interfaces:**
- `pru_foc_inject` loads with the FOC include path, validates load and execution faults, and returns status/error truthfully with clock/session/status metadata.

- [ ] **Step 1: Add failing MCP tests** for actual assembly loading, missing include-path handling, memory-fault status, and returned timing/status metadata.
- [ ] **Step 2: Run the focused MCP tests** and reproduce the current false-success/memory-fault behavior.
- [ ] **Step 3: Pass the source include directory**, detect `PRUCore` execution faults, and return an error status without discarding diagnostic state.
- [ ] **Step 4: Update operating documentation** with the single-clock procedure, voltage reference names, reset/stop semantics, and acceptance commands.
- [ ] **Step 5: Run MCP and documentation/static checks**.

### Task 6: Full acceptance and regression verification

**Files:**
- Test: all FOC and simulator test modules
- Modify: `docs/reports/2026-09-04-foc-open-loop-session-report.html` if the report needs updated measured results

- [ ] **Step 1: Run focused FOC firmware/model/runtime/UI/MCP tests.**
- [ ] **Step 2: Run the full `python -m pytest -q` suite.**
- [ ] **Step 3: Run simulated-time acceptance scenarios** for nominal, reference change, reverse, higher-speed valid point, insufficient voltage, no excitation, stop/restart/reset, invalid inputs, clock consistency, and accelerated/instruction equivalence.
- [ ] **Step 4: Record measured simulated time, wall time, loop cadence, and simulation-time/wall-time ratio** in the session report.
- [ ] **Step 5: Review the final diff**, explicitly confirm the pre-existing `memory.cfg` edit remains intact, and report any acceptance gap with evidence.

## Verification record

Final verification on 2026-09-07:

- Focused command `python -m pytest tests/test_foc_validation_repair.py
  tests/test_foc_open_loop.py tests/test_foc_ui.py tests/test_foc_firmware.py
  tests/test_mcp_server.py -q`: **76 passed in 52.16 s**.
- Explicit real-assembly clock check at 200, 250, and 300 MHz: **6 passed**; control periods
  were 2000, 2500, and 3000 IEP ticks and the measured control cadence remained approximately
  100 kHz.
- `node --check ui/static/app.js`, Python compilation checks, and `git diff --check` passed
  (Git emitted only its normal LF/CRLF conversion warnings).
- Full command `python -m pytest -q`: **1521 passed, 2 xfailed in 159.24 s**.
- The FOC WebSocket source/disassembly regression confirmed that the live PRU PC and instruction
  counters are now published alongside FOC telemetry; before the fix, the firmware executed but
  the source panel received no generic core-state packets.
- Physical command `python tools/run_foc_acceptance.py --clock 200 --case nominal_400
  --case reference_change --case reverse_reset --case high_valid_1000
  --case insufficient_voltage --case zero_voltage_rest` used the actual assembly and ran for
  1.001779 s, 1.001779 s, 1.001779 s, 2.003351 s, 1.001365 s, and 0.201846 s of simulated
  time respectively. The nominal, reference-change, reverse, and 1000 RPM cases settled within
  the requested speed/ripple limits without slips. The insufficient-voltage case honestly lost
  synchronism; zero voltage kept the rotor stationary while the command angle moved.
- Active measured throughput ranged from **0.005651x to 0.006449x real time** (5.651 to 6.449
  simulated ms per wall second). Paused wall time was excluded from this metric.
- Browser verification was unavailable because the in-app browser list was empty. FastAPI
  WebSocket tests and Node/static UI checks were run instead.
- The user’s pre-existing `memory.cfg` 200 MHz edit remains intact. No commit or push was made.
