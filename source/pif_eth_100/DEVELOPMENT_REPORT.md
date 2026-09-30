# pif_eth_100 — Development Report

Date: 2026-09-30 (skeleton, generated mid-session; the final numbers are regenerated in the final report task)

All figures are simulator measurements, not silicon claims.

## 1. Summary

_Completed in the final report task._

## 2. The request

The user's original prompt, verbatim (typos as written):

> the pif_eth project  in the source folder goes up to 125 Mbit with n=2. I need another variant with 8b/10b line coder CRC32 ussing the hardware widget and 200 bytes frame with 80 Mbit net data rate and 100 Mbit line rate. PRU frequency should be 300 MHz to make N=3. Use superpower planing with opus 5.5 and implementation with Sonnet 5.5. Put the project in a separate folder e.g. pif_eth_100. Document whole development in a report including porompt, interaction, tokesn, models, time spent. Also generate a users guide on how to run the receive and transmitter in the UI.

## 3. Decisions and interaction log

### 3.1 Decisions

Source: design spec §1 and the §17 addendum (`docs/superpowers/specs/2026-09-30-pif-eth-100-design.md`).

| Topic | Decision |
|---|---|
| "100 Mbit line rate" | 100 Mbaud: TXCFG n = 3 at a 300 MHz core, 10 ns per line bit. |
| "80 Mbit net data rate" | Interpreted as the 8b/10b coding rate: 100 Mbaud x 8/10 = 80 Mbit/s of octets while a burst is on the wire (F2). Commas, FCS and the inter-frame gap lower the frame-averaged goodput (F3); that is measured and reported next to it, not hidden. |
| Folder | Separate, self-contained `source/pif_eth_100/`; `source/pif_eth/` is not modified. |
| TX base | `pif_eth_tx_n2.asm` (all FIFO-full checks kept), copied; only the TXCFG word changes to `0x00020010` (n = 3). FCS on the hardware CRC widget (`pif_eth_crc32_hw.inc`, copied). |
| Frame | BERT frame, 200 B xorshift32 payload + 4 B FCS = 204 octets before 8b/10b, bracketed by K28.5 commas. |
| RX | RX Option 1 (realtime raw capture, decode after the frame) on PRU1, over the existing zero-drift perif loopback channel 0. RX divider 1.5 (div_factor 0, FRAC = 1, RXCFG `0x0000801F`) gives exactly 2x oversampling (5 ns samples). |
| Core clock | 300 MHz on both cores (`config/memory_pif_eth_100.cfg`); the root `memory.cfg` is never used by the tests and only changed transiently by the UI run. |
| Loopback latency rule (spec 17.2) | Default 0 ns; all three sample-to-edge residues are tested by shifting the latency by 0, 10/3 and 20/3 ns. A fixed 5/6 ns latency is only the fallback if the 0 ns setting fails on the edge-tie signature (second gate in plan Task 5). The user chose "keep the plan: try 0 ns, then fall back". **Outcome:** the second gate in Task 5 triggered. At 0 ns the edge-phase test at the 10/3 ns shift failed (symbol_errors = 71, crc_ok = 0); with 5/6 ns all three phases were clean, so `LOOPBACK_LATENCY_NS` = 5/6 ns. |
| RX optimisation (spec 17.1) | A separate, post-baseline deliverable, `pif_eth_100_rx_fast.asm` (fast aligned decode with a 2:1 decimation LUT). The baseline `pif_eth_100_rx.asm` stays untouched; if the fast RX cannot pass, the baseline remains the delivered result. |
| Existing files (spec 17.3) | Only a changelog entry under "Unreleased" in the top-level `readme.md` and a new `docs/handoff/` note; no other existing file changes. |
| UI validation (spec 17.4) | A scripted walkthrough over the server's HTTP/WS endpoints is the validation of record; a real-browser run is added only if the Chrome extension answers. |
| Branch and commits (spec 17.5) | Branch `feat/pif-eth-100` off `feat/sd-sweep`; one commit per task; explicit-path staging only; no push, no PR; commit trailer naming Sonnet 5.5. |

### 3.2 Interaction log

Times are UTC (session start 2026-09-30T13:19:49+02:00 = 11:19:49Z). This log is a sanitised summary: system reminders, memory contents and tool output are deliberately not reproduced.

**This log is hand-supplemented.** `session_stats.py` deliberately drops `tool_result` records, and the answers to the coordinator's multiple-choice questions arrive in the transcript as tool results, so the tool's own "Interaction log" output would show only the plain-text user messages. The main session has 4 multiple-choice question rounds (with answers) and 5 plain-text user messages (the original prompt, "yes, go ahead", "approve, Sonnet 5.5 subagent-driven", and two "continue with the plan" resumes). The other user-role records with text are relayed subagent messages and task notifications, and are not listed. The rows below combine both sources; question rounds are marked (question).

| Time (UTC) | Who | What |
|---|---|---|
| 11:19:44 | User | The original prompt (section 2). Classified as an architectural task (new firmware variant plus documentation), so: brainstorming, spec, plan, execution. |
| 11:20:44 | User (question 1, "Frame size") | Chose "200 B payload + 4 B FCS (Recommended)", that is 204 octets before 8b/10b, with 80 Mbit/s read as the 8b/10b coding rate. Effect: the frame is a 200 B PRNG payload plus FCS; the goodput is measured, not assumed. |
| 11:23:17 | User (question 2, "RX sampling") | Chose "2x oversample via fractional divider 1.5 (Recommended)"; the alternatives offered were 3x at n_rx = 1 and 1x at n_rx = 3. Effect: the old rule n_rx = n_tx / 2 cannot work at n_tx = 3, and the fractional divider gives exactly 2x. |
| 11:24:46 | User | "yes, go ahead" (approve the design; start the spec). |
| 11:25:13 - 11:37:51 | Planning subagent (Opus 5.5) | Wrote the design spec (12.6 min). |
| 12:03:29 | User (question 3, "Spec review", "Goodput", "Extra files", "Commits") | Spec: "Approve as written". Goodput (end-to-end about 9 Mbit/s because RX decodes after the frame): "Also add an RX-optimisation task". Extra files: "Handoff note + readme entry (Recommended)". Commits: "Work on a new branch, commit per task (Recommended)". Effect: the spec addendum (§17) adds a separate optimised RX (`pif_eth_100_rx_fast.asm`), a readme changelog entry and handoff note, and branch `feat/pif-eth-100` with one commit per task. |
| 12:03:56 - 12:57:37 | Planning subagent (Opus 5.5) | Spec addendum §17 and the implementation plan (53.7 min elapsed, including a ~10 min gap inside the run). |
| 13:22:53 | User (question 4, "Plan / exec", "Latency") | Not an approval at first: the user asked to be sure that the transmission on the wire is 100 Mbit, and said that the throughput table in the plan was not clear. Latency question: "Keep the plan: try 0 ns, then fall back (Recommended)". The coordinator then explained that the wire stays 100 Mbit/s (80 Mbit/s of data inside a burst), and that the table's goodput is 1600 payload bits divided by the whole frame period (burst 21.04 us + TX prep about 9 us + RX post-frame decode about 148 us at baseline), which is why the averages are lower. |
| 13:24:58 | User | "approve, Sonnet 5.5 subagent-driven": one Sonnet 5.5 subagent per task, each followed by a Sonnet 5.5 task reviewer; Opus 5.5 for the final whole-branch review. |
| 13:25 onward | Coordinator and Sonnet 5.5 subagents | Implementation tasks 2 to 10, each with an implementer and a reviewer (see the per-agent table in section 5). At Task 5 the loopback-latency second gate triggered (section 3.1); the fallback of 5/6 ns was used. The Chrome extension was not connected (two attempts in Task 8), so the UI guide was validated by script only. The coordinator ran Task 1 (branch and commit) itself. |
| 13:56:51 | User | "continue with the plan": execution resumed after a transient outage of the harness safety classifier (Bash and Agent calls returned "no verdict"); the coordinator had stopped after two retry rounds and told the user. No work was lost. |
| 13:59:38 | User | "continue with the plan" (second resume, same cause). |

## 4. Process and phases

1. Brainstorming (main session, Sonnet 5.5): interpret the request, agree the terms in the spec §1 table.
2. Design spec (Opus 5.5 planning subagent): 12.6 min; reported by the coordinator as 209,015 subagent tokens and 43 tool uses.
3. User review of the spec, with the decisions recorded as the §17 addendum.
4. Spec addendum and implementation plan (Opus 5.5, the same planning agent): 53.7 min; cumulative 461,515 subagent tokens and 69 tool uses across both planning runs.
5. Implementation: one Sonnet 5.5 subagent per plan task, each followed by a spec and code-quality review subagent.
6. Final verification (pending at this snapshot).

Plan tasks and their commits (`git log --oneline feat/sd-sweep..HEAD` at this snapshot):

| Task | Content | Commit |
|---|---|---|
| 1 | Branch, design spec and plan | `a8a6af1`, `83a4753` (plan uses the session scratchpad instead of /tmp) |
| 2 (T0) | 300 MHz config, golden-model copies, fractional-divider spike | `1151b86` |
| 3 (T1) | `frames.py` BERT-200 frame builder, golden-model tests | `fe0cf2c` |
| 4 (T2) | PRU0 TX at n = 3 and the `run_100` driver core | `ee0cf1d` |
| 5 (T3) | PRU1 RX at 2x via divider 1.5, traced loopback run | `b5b9603` |
| 6 (T4) | Measured F1/F2/F3 throughput and seed-sweep CLI | `c791be1` |
| 7 (T5) | `seed_ui_100.py` seed/arm/status for the browser UI | `808d97b` |
| 8 (T6) | Scripted UI walkthrough and the user's guide | `e5f5444` |
| 9 (T7) | README with clock plan, maps and measured throughput | `295ee3f` |
| 10 (T8) | `session_stats.py` and this report skeleton | this commit |
| later | RX optimisation, readme changelog and handoff note, final report and PDFs (Task 13), final verification | pending |

## 5. Models and tokens

Source: `python3 source/pif_eth_100/session_stats.py`, a snapshot at the cut-off shown below. Counting method: every assistant record with `message.usage` is de-duplicated by `message.id` (a streamed reply is written as several records that repeat the usage; per id the maximum of each counter is kept), then input, cache-write, cache-read and output tokens are summed per model. Cache reads are listed separately because they dominate the totals: every API call re-reads the whole conversation prefix from the cache, so the "Total" column grows with the number of calls times the context size, not with the amount of new work.

Model IDs below are taken from the `model` field of the transcript records.

Session start 2026-09-30T13:19:49+02:00; transcript cut-off 2026-09-30T13:52:56.108Z (UTC).

#### Tokens per model

| Model | Input | Cache write | Cache read | Output | Total |
|---|---:|---:|---:|---:|---:|
| claude-opus-5-5 | 190 | 1,147,557 | 24,893,289 | 30,731 | 26,071,767 |
| claude-sonnet-5-5 | 482 | 942,297 | 17,484,634 | 74,294 | 18,501,707 |

#### Per agent (wall-clock = first to last record)

| Agent | Model(s) | Start (UTC) | Wall-clock | Total tokens | Task (first prompt, 90 chars) |
|---|---|---|---:|---:|---|
| main | claude-sonnet-5-5 | 2026-09-30T11:13:46.019Z | 155.6 min | 10,926,171 | the pif_eth project  in the source folder goes up to 125 Mbit with n=2. I need another var |
| afef5f3cea2c8b370 | claude-opus-5-5 | 2026-09-30T11:25:13.375Z | 92.4 min | 26,071,767 | You are the PLANNING architect (Opus 5.5) for a new firmware variant in the PRU simulator  |
| a9eff8cf3c249a707 | claude-sonnet-5-5 | 2026-09-30T13:25:56.218Z | 1.1 min | 374,680 | You are implementing Task 2 (T0: config, golden-model copies, fractional-divider spike — t |
| ab26866df04b4299e | claude-sonnet-5-5 | 2026-09-30T13:27:19.974Z | 0.6 min | 154,375 | You are reviewing one task's implementation: first whether it matches its requirements, th |
| aaf1c15487d56c01f | claude-sonnet-5-5 | 2026-09-30T13:28:10.366Z | 0.6 min | 222,336 | You are implementing Task 3 (T1: `frames.py` BERT-200 frame builder + pure-Python tests) o |
| abf0616162471a3c1 | claude-sonnet-5-5 | 2026-09-30T13:29:03.309Z | 0.4 min | 67,516 | Review Task 3 (frames.py BERT-200 builder + pure-Python tests) of the pif_eth_100 plan. Fi |
| a807a88e6106c1f8c | claude-sonnet-5-5 | 2026-09-30T13:29:47.454Z | 1.2 min | 371,301 | You are implementing Task 4 (T2: TX firmware `pif_eth_100_tx.asm` + `run_100.py` core + TX |
| ad8b188c10aa5609c | claude-sonnet-5-5 | 2026-09-30T13:31:09.060Z | 0.5 min | 253,432 | Review Task 4 (TX firmware pif_eth_100_tx.asm + run_100.py core + TX-only tests) of the pi |
| a53ed3425580afb81 | claude-sonnet-5-5 | 2026-09-30T13:31:51.660Z | 1.3 min | 436,422 | You are implementing Task 5 (T3: baseline RX firmware `pif_eth_100_rx.asm` + loopback run  |
| ab61557445a453093 | claude-sonnet-5-5 | 2026-09-30T13:33:23.823Z | 0.9 min | 179,395 | Review Task 5 (baseline RX firmware pif_eth_100_rx.asm + run_loopback + cycle-budget tests |
| a0c720c095cbfba77 | claude-sonnet-5-5 | 2026-09-30T13:34:32.741Z | 1.4 min | 334,998 | You are implementing Task 6 (T4: throughput figures F1/F2/F2p/F3 + seed-sweep CLI in run_1 |
| a3c1113f677fe8585 | claude-sonnet-5-5 | 2026-09-30T13:36:05.723Z | 0.9 min | 208,002 | Review Task 6 (throughput figures F1/F2/F2p/F3 + seed-sweep CLI in run_100.py) of the pif_ |
| a4e793ab2d015dc48 | claude-sonnet-5-5 | 2026-09-30T13:37:10.836Z | 1.4 min | 379,835 | You are implementing Task 7 (T5: `seed_ui_100.py` seed/arm/status for the browser UI + uni |
| acd4da7d0b0b3eb90 | claude-sonnet-5-5 | 2026-09-30T13:38:44.038Z | 0.8 min | 304,573 | Review Task 7 (seed_ui_100.py: seed/arm/status for the browser UI, plus 4 unit tests inclu |
| af6fae04e443e69f5 | claude-sonnet-5-5 | 2026-09-30T13:39:54.164Z | 3.2 min | 1,510,114 | You are implementing Task 8 (T6: scripted UI walkthrough `ui_walkthrough_100.py`, a real b |
| a2f1645e52054199b | claude-sonnet-5-5 | 2026-09-30T13:43:21.346Z | 1.2 min | 405,781 | Review Task 8 (ui_walkthrough_100.py scripted UI walkthrough + USERS_GUIDE.md) of the pif_ |
| aa7053c0722b5afe4 | claude-sonnet-5-5 | 2026-09-30T13:44:49.413Z | 2.8 min | 878,386 | You are implementing Task 9 (T7: `source/pif_eth_100/README.md` filled with measured outpu |
| a4f74912584bafe7d | claude-sonnet-5-5 | 2026-09-30T13:47:49.019Z | 1.2 min | 419,975 | Review Task 9 (source/pif_eth_100/README.md with measured output) of the pif_eth_100 plan. |
| a0693ee6db1b999ae | claude-sonnet-5-5 | 2026-09-30T13:49:23.119Z | 3.5 min | 1,074,415 | You are implementing Task 10 (T8: `session_stats.py` transcript→tokens/models/time/interac |

Two different measures of subagent size appear in this report and must not be compared:

- **Transcript totals (this section).** For the Opus 5.5 planning subagent the table above gives 26,071,767 tokens in total, of which 24,893,289 are cache reads and only 1,178,478 are input, cache-write and output tokens (94 distinct assistant messages).
- **Coordinator-reported subagent tokens.** The coordinator reported 209,015 tokens for the spec run and 461,515 cumulative across the spec and plan runs (43 and 69 tool uses). That is the completion-summary figure of the subagent tool; it is a different, much smaller accounting and it cannot be recomputed from the transcripts.

## 6. Time spent

Session start 2026-09-30T13:19:49+02:00 (11:19:49Z). The transcript cut-off for this snapshot is given at the top of section 5. Wall-clock per agent is first to last transcript record, so the waiting between two runs of the same agent is included.

| Phase | Start (UTC) | End (UTC) | Wall-clock |
|---|---|---|---:|
| Brainstorming (main session) | 11:19:44 | 11:25:13 | about 5.5 min |
| Design spec (Opus 5.5) | 11:25:13 | 11:37:51 | 12.6 min |
| Spec review with the user | 11:37:51 | 12:03:56 | about 26 min |
| Spec addendum and plan (Opus 5.5) | 12:03:56 | 12:57:37 | 53.7 min |
| Plan finished until approval | 12:57:37 | 13:24:58 | about 27 min |
| Implementation (Tasks 2 - 10) | 13:25:56 | snapshot | see below |

At this snapshot the Sonnet 5.5 subagents used 16.4 min of summed wall-clock in 9 implementer runs (including the one running this task) and 6.5 min in 8 reviewer runs; the per-agent table in section 5 has each run. The main session's figure in the per-agent table (155.6 min at generation time) runs from the main transcript's first record to its last record; the first records (attachments and session setup, from 11:13:46Z) precede the first prompt (11:19:44Z) and the session-start timestamp, so it slightly overstates the working time.

## 7. Results

_Completed in the final report task._

## 8. RX optimisation

_Completed in the final report task._

## 9. Deviations from the spec

- **Loopback latency fallback (taken).** Spec 17.2 anticipated it. At Task 5, 0 ns failed in the `run_100` harness at the 10/3 ns phase (the float edge-tie signature described in spec 17.2); the fallback of 5/6 ns was used, documented as a modelled wire propagation delay, not drift.
- **UI validation by script only.** The Chrome extension was not connected, so the user's guide was validated through the scripted walkthrough over the server's HTTP/WS endpoints (spec 17.4 fallback), not in a real browser.

## 10. Lessons learned

- A spike harness can pass a check that the production harness fails: the 0 ns loopback latency passed in the spike but not in `run_100`, because the exact-tie phase lands at a different shift. Cover every residue by construction, not by the single base case.
- Loading a program keeps the core's old PC and registers, so a Reset is needed after re-loading (spec 17.4).
- Token totals from transcripts need de-duplication by message id and a clear separation of cache reads; otherwise totals are inflated several times over and are not comparable with a coordinator's reported subagent tokens.
- More lessons are added in the final report task.

## 11. Caveats

- All figures are simulator results. The fractional RX divider (1.5) has not been verified against silicon; on silicon the sample edges may be spaced unevenly (spec risk R6).
- The token, model and time tables are a snapshot of a session that was still running; they are regenerated in the final report task.
- Token totals include cache reads (see section 5). Per-agent wall-clock is first to last record.
- The interaction log is a sanitised summary, not a transcript.
