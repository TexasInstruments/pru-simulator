# SDD ledger — plan: docs/superpowers/plans/2026-11-04-spi-pru-link.md
Spec: docs/superpowers/specs/2026-11-04-spi-pru0-pru1-design.md
Branch: feat/spi-pru-link (in place, user declined worktree 2026-11-04; user's uncommitted memory.cfg = clock 250->200 + line endings, program.asm scratch, pif_eth_100/ untracked — never stage these)
Start: e033a87 (merge-base main 46efc4a)

## Pre-flight scan
| Rows | Produces / consumes | Finding |
|---|---|---|
| T1↔T3 | set_gpi_delay(core,cycles) → SpiLinkRuntime(hop_delay) | consistent |
| T2↔T3 | spi_link.py both; render_header(cfg, role) → controller load | consistent |
| T2↔T3/T4 | SpiBusMonitor on "pru0" endpoint must see PRU1-driven MISO via wire | unverified risk; T3/T4 tests exercise it |
| T3↔T4 | transfer(abort_after_bits), load_peripheral stub → real | consistent (abort moved into T3) |
| T4 test edge-polling | reload only PRU0 with new cfg; SpiConfig frozen | Ruling R2 |
| T4↔T5 | preload/read helpers, peripheral_status → run_test_sequence | consistent |
| T5↔T6 | load_supported → scenario default 25 MHz@200 | Ruling R3 |
| T6↔T7 | state-push key name ("spi_state" in T7 vs "wherever ssi_demo_state is" in T6) | Ruling R1 |
| T6 | apply_scenario validates SSI config only | Ruling R4 |
| T7 | browser check may be impossible for a subagent | Ruling R5 |
| T1 self | tests vs code | consistent |
| T2 self | measure_timing rows need two frames for tCSH | handled (None) |
| T3 self | tests vs firmware contract | consistent |
| T5 self | sweep cost in tests: subset only | consistent |
| T8 self | three models in one task → three dispatches | consistent |

Ruling R1: state push key is `io.spi_demo` (server: io_section["spi_demo"] = spi_demo_state(sim), next to ssi_demo at ui/server.py:1345); T7 reads msg.io?.spi_demo — follows existing SSI pattern — cost if wrong: rename in 2 places.
Ruling R2: T4 may add `SpiLinkRuntime.reload_controller(cfg: SpiConfig)` (reassemble PRU0 only, keep PRU1, wires, monitor) — needed for edge-polling proof — cost if wrong: small API addition.
Ruling R3: scenario default = mode 1 at 25 MHz if supported, else the fastest supported mode-1 rate ≤ 25 MHz — spec names 25 MHz/200 only as mockup example — cost if wrong: different default rate.
Ruling R4: apply_scenario dispatches config validation per scenario; SPI config = {mode, spi_mhz} — cost if wrong: refactor of one function.
Ruling R5: if the T7 implementer cannot drive a browser, it reports so and the controller performs the browser check with claude-in-chrome before marking T7 complete (verification only, no fixes) — cost if wrong: none beyond time.

## Tasks
Task 1: dispatched implementer (opus), BASE e033a87
Task 1: implementer DONE_WITH_CONCERNS 153e8a1. Baseline: 3 pre-existing failures also on clean HEAD (test_clock_speed_endpoint ×2, test_server am263x — rtu0 groups), not ours.
Ruling R6: hop delay = 5 cycles (not 4) — single-stepped sim gives 1 cycle native for wire→wbs→set at 333 MHz, so 18 ns silicon (6 cycles) needs +5; spec's binding intent is "calibrated to the user's measurement" and the 4 came from my 1–2 cycle estimate — cost if wrong: one cycle more conservative, possibly one fewer supported top rate. Spec/plan/global-constraints updated.
Task 1: ruling R6 applied by implementer c2c445c; review dispatched (opus) on e033a87..c2c445c
Task 1: review approved (opus). ⚠️ resolved: PRUCore.reset calls counters.reset() (l.232) before io_port.reset() (l.241) — ok; baseline failures verified by implementer on clean-HEAD worktree.
Task 1: minor (deferred): UI register panel shows undelayed R31 (ui/server.py:1331) — carry to Task 7
Task 1: minor (deferred): set_gpi_delay always resets history even if unchanged (io_port.py:497) — carry to Task 3 (load sets delay)
Task 1: minor (deferred): history masked to 20 bits but _gpi unmasked (io_port.py:470/448)
Task 1: minor (deferred): test docstring "1-2 cycles" ambiguity (test_gpi_delay.py:625)
Task 1: minor (deferred): _record_gpi linear scan (bounded ≤17)
Task 1: complete (commits e033a87..c2c445c, review clean)
Task 2: dispatched implementer (sonnet), BASE c2c445c
Task 2: implementer DONE_WITH_CONCERNS 586f8bd (15 tests; full suite 3299 pass, 3 known fails)
Ruling R7: tSU,MISO / tH,MISO are measured against PRU0's actual sample point (MISO launch edge of that bit + MISO_DELAY core cycles), not the nominal capture edge — with a sample delay the capture edge is not where PRU0 samples; setup = sample − MISO change for that bit, hold = next MISO change − sample — cost if wrong: one function's measurement reference.
Task 2: review (sonnet) Needs fixes — R7 not implemented, no MISO-row tests, tIDLE/tsk not measured.
Ruling R8: rows whose sim value is not measured from edges (tsk — sim models no pad skew; tIDLE — design value until Task 5 measures PRU1 fetch) carry "derived": True and never count as worst — cost if wrong: two rows' labelling.
Task 2: minor (deferred): unbounded edge/fault lists; capture starting with /CS already low ignored; nets mutable class attr
Task 2: fix round 1 dispatched (resume sonnet implementer), FIX_BASE 586f8bd
Task 2: fix round 1/5 (5 addressed, 0 open — R7, MISO-row tests, R8 derived rows, negative setup, minors; commits 586f8bd..6787cbf)
Task 2: complete (commits c2c445c..6787cbf, review clean)
Task 3: dispatched implementer (opus), BASE 6787cbf
Task 3: implementer DONE_WITH_CONCERNS 966929f (73 tests; suite 3377 pass + 3 known). Concerns: unrealisable miso_delay values (%2k in {0,k}, //2k>6) → controller_delay_ok(); asm unit lines from an uncommitted throwaway generator; /CS low before first instruction (R30=0).
Carry to Task 4: PRU1 must wait for /CS high (idle) before arming, so the start-up /CS-low glitch is not a frame.
Task 3: review (opus) Needs fixes — Q=4..6 MISO data untested (Important); minors: close() monitor reuse, /CS low at start, generator uncommitted, abort MOSI partial-byte check weak, comments/msg/dead halt, memory.write vs memory_read API mix.
Ruling R9: miso_delay holes (d % 2k in {0,k}) and SpiConfig default 0 stay; Task 5 picks the window value from controller_delay_ok values nearest the middle, not the arithmetic middle — cost if wrong: one helper in the sweep.
Task 3: minor (deferred): abort MOSI partial-byte bits unverified (diff 1403-1407); transfer mixes sim.memory.write / sim.memory_read
Task 3: fix round 1 dispatched (resume opus implementer), FIX_BASE 966929f
Task 3: fix round 1/5 (5 addressed, 0 open — Q4-6 data tests, close/monitor, /CS idle high, generator committed, text fixes; commits 966929f..2fd1a25)
Task 3: complete (commits 6787cbf..2fd1a25, review clean)
Task 4: dispatched implementer (opus), BASE 2fd1a25
User instruction (2026-11-04): on a usage limit, retry every 15 minutes until it clears (use a background sleep 900 then re-dispatch/resume).
Task 4: implementer stalled on 5-hour usage limit (HTTP 429, resets 01:40 Europe/Berlin); resumed after reset 2026-11-05T01:41:35+01:00
Task 4: implementer DONE_WITH_CONCERNS 7da951a (38 tests; suite 3430 pass + 3 known). PRU1 5 cyc/bit mode1, 4 mode0; idle fetch 12; commit 22/26; passes 50 MHz@300 k=3 both modes; 4×lbbo instead of lbco.
Task 4: review (opus) Needs fixes — edge-polling honest (stimulus: stretched/jittered/phase OK); Important: short /CS gap (2 tCLK) drops a whole frame silently at k≤6/7.
Task 4: minor (deferred): thin 50 MHz MISO window (mode1 [16,17]) — Task 5 sweep; reload_controller may half-load PRU0 on asm error; find_miso_window slow (~20 s/rate) and mutates PRU1 state
Task 4: fix round 1 dispatched (resume opus implementer), FIX_BASE 7da951a
Task 4: fix 81d5506 — commit 10 cycles, min /CS gap k3: 9 (m1)/5 (m0), k6: 1; 154 tests.
Task 4: known limitation (for final review + report): GPI changes injected between instructions (set_gpi_word) or by wires onto a stalled follower in step_paced_many are stamped at the end of the follower's current multi-cycle instruction; edges inside a 5-cycle sbco can merge. Tests use a true-cycle stimulus helper. Current PRU0↔PRU1 runs unaffected per implementer (gaps 150+, in-frame stalls ≤3).
Task 4: fix round 1/5 (5 addressed, 0 open — min /CS gap, deviation note, per-byte asserts, ZERO_BLK const, abort comment; commits 7da951a..81d5506)
Task 4: minor (deferred): status block written in 4 word stores (non-atomic for host readers) — carry to Task 6 (read after frame done); test helper _gpi_at reaches into private io._gpi_history; header says "no byte >48" but byte 18 not asserted
Task 4: complete (commits 2fd1a25..81d5506, review clean)
Task 5: dispatched implementer (opus), BASE 81d5506
Task 5: implementer DONE_WITH_CONCERNS d003357 — all 212 rows pass (2 modes × 53 (core,k) × hop 0/5), 134.6 s on 24 procs; fastest 50 MHz both modes (m1 window [16,17], 0 PRU1 slack; m0 [13,14,16,17]).
Ruling R10: reload_controller must leave one consistent time base for the monitor — reset both cores' cycle counters together (and GPI delay histories) and clear the monitor, so edges from PRU0 and PRU1 share a clock after reload — cost if wrong: a reload API detail.
Ruling R7a (amends R7): the pin-referred PRU0 sample point is launch + miso_delay − hop_delay (the pin state PRU0's R31 read actually sees); measure_timing takes hop_delay (default HOP_DELAY_CYCLES). Passing rows must not show negative MISO hold — cost if wrong: re-generate tables.
Ruling R11: the controller's real inter-frame /CS-high gap (144–231 cycles) is accepted; the strict 2 tCLK gap is proven by Task 4's direct-stimulus tests; doc/report state this — cost if wrong: demo can't show a minimum-gap burst.
Ruling R12: pru_io/spi_rates.json holds only the summary rows (no per-combination timing tables) because it is pushed to the UI; full tables live in docs/spi_link_timing.md (and may be recomputed on demand) — cost if wrong: file layout.
Task 5: pre-review fix round dispatched for R10, R7a, R12 (resume opus implementer)
Task 5: pre-review fix 1557844 — R10 (both counters + _device_cycle + histories reset), R7a (sample at launch+d−hop, change paired with latest launch ≥1 hop earlier), R11 doc, R12 JSON 58 KB. All 212 rows still pass; min worst margin +1.33 ns.
Ruling R9a: confirmed implementer's tie-break change — among accepted miso_delay values equidistant from the window middle, take the earlier one (pin-referred timing gives balanced 2+2 cycles vs 1-cycle hold) — 165 rows' miso_delay moved by 1–2, no pass/window changed — cost if wrong: a tie-break rule.
Task 5: review dispatched (opus) on 81d5506..1557844
Task 5: review (opus) Approved, 0 Critical/Important.
Ruling R11 correction: controller's real inter-frame /CS-high gap is 135–249 cycles (generated doc), not 144–231 as first quoted.
Task 5: ⚠️ resolved: "PRU1 min /CS gap 9 (m1)/5 (m0) at k=3" comes from Task 4 implementer's stimulus measurement (fix report), not a stepped assertion — deferred minor: assert it.
Task 5: minor (deferred): measure_timing hop default footgun (carry to Task 6: always pass rt.hop_delay); _supported_view duplicates load_supported; _work_trace/_bit_work duplicated in test_spi_peripheral; doc header says generated from JSON; p1_cycles_per_bit is min not max (6); tLEAD/tLAG ±2 in AM243 columns labelled PRDO1 could mislead; choose_miso_delay tie rule untested; run_test_sequence MOSI/RX/status failure paths untested; subset test compares early-stop window to itself; rt.close() outside try/finally; find_miso_window docstring
Task 5: complete (commits 81d5506..1557844, review clean)
Task 6: dispatched implementer (sonnet), BASE 1557844
Task 6: implementer DONE_WITH_CONCERNS 0d884d7 (14 tests; suite 3465 + 3 known; full suite not re-run after test-only fix). Did R31 panel fix; events() hex strings.
Ruling R13: leaving the SPI scenario (any path) restores the configured core clock if spi_setup changed it (fresh rebuild from memory.cfg values, still without writing memory.cfg) — Review Focus 1 "other demos behave exactly as before" — cost if wrong: one extra rebuild on leaving.
Ruling R14: `supported` (58 KB) is not sent on every state push; send it once with scenario_loaded / spi_setup responses (or a dedicated `spi_supported` message on request) and keep spi_demo light; monitor edges in the payload limited to the last frame (already) and the monitor's stored edges capped (keep last N frames) — cost if wrong: payload layout.
Task 6: review dispatched (sonnet) on 1557844..0d884d7
Task 6: review (sonnet) Needs fixes — R13 missing, R14a supported on every push, R14b uncapped edges.
Task 6: minor (deferred): spi_setup validates row twice; _load_spi_scenario non-dict config path; spi_send/sequence block event loop (documented)
Task 6: fix round 1 dispatched (resume sonnet implementer), FIX_BASE 0d884d7
Task 6: fix 7a4c66f (R13 rebuild on leave, R14a spi_supported message, R14b keep last 8 frames)
Task 6: fix round 1/5 (4 addressed, 0 open — R13, R14a, R14b, stall doc; commits 0d884d7..7a4c66f); test_clock_speed_endpoint.py re-run by re-reviewer: only the 2 known failures.
Task 6: minor (deferred): _publish_updates not called after leave-time rebuild
Task 1 minor (R31 panel undelayed) — fixed in Task 6.
Task 6: complete (commits 1557844..7a4c66f, review clean)
Task 7: dispatched implementer (sonnet), BASE 7a4c66f
Task 7: implementer DONE 510622e (ui_spi_view 7/7; headless-Chrome check done, 4 screenshots). Node 22 rejects --test-isolation=none. Pre-existing UI test failures reported (capture_integrity 2, chrome 1, shared_selection 6) same with changes stashed — to verify.
Task 7: controller look at spi-view-fastest.png: layout matches mockup A+v3; frame-strip MISO row shows monitor decode (3F BF FD …) while scope boxes / PRU0 RX show FE FD FB … — inconsistent; cursor labels overlap near frame start.
Task 7: review (sonnet) Needs fixes — strip MISO uses monitor capture-edge decode (inconsistent); diff breaks ui_chrome io-tab list.
Controller check (temp worktree at 7a4c66f): ui_capture_integrity 2 fail, ui_shared_selection 6 fail, ui_chrome 1 fail already on base — pre-existing.
Ruling R15: the SPI view decodes MISO at PRU0's sample point everywhere (strip + boxes) with a visible note; backend monitor frame.miso (capture-edge decode) is not shown — cost if wrong: one display choice; a "logic-analyzer view" could be added later.
Task 7: fix round 1 dispatched (resume sonnet implementer), FIX_BASE 510622e
Task 7: fix b34174c
Task 7: fix round 1/5 (3 addressed, 0 open — strip MISO R15, ui_chrome list, labels; commits 510622e..b34174c)
Task 7: complete (commits 7a4c66f..b34174c, review clean). Controller viewed spi-view-fastest.png after fix: strip/boxes/RX consistent.
Task 8: step 1 (docs, haiku) dispatched, BASE b34174c
Task 8 step 1: docs 3620e11 (haiku) — Python example verified by implementer; pin pairs derived from §7+§5.3 nets (controller checked: correct).
Task 8 step 2: final whole-branch review dispatched (opus) on 46efc4a..3620e11
Final review (opus): With fixes — 1 Critical (scenario tests depend on user's uncommitted 200 MHz memory.cfg), 7 Important, minors.
Ruling R16: editor-visible firmware must assemble standalone — each .asm carries a default config block (.set lines between markers, defaults = scenario default config) that render_header REPLACES at load instead of prepending (the simulator assembler evaluates $isdefed as false, so .if-guards cannot be used) — cost if wrong: header mechanism.
Ruling R17: MISO sample delay becomes selectable in the UI (spec §5.1): spi_setup accepts optional miso_delay (validated with controller_delay_ok; default = row's miso_delay), control lists realisable values with the passing window marked — amends R4 — cost if wrong: one control.
Ruling R18: tLEAD/tLAG/tH-tL rows get no AM243 min/max; margin = measured − design minimum − 2 ns PRDO1 skew, source "design (min …) − PRDO1 skew"; only cells sourced from Table 6-11x are tinted as datasheet data; texts saying "AM243 columns are datasheet data" adjusted — spec anti-goal — cost if wrong: labelling.
Ruling R19: the "PRU1 needs 9 (mode 1) / 5 (mode 0) cycles at k=3" claim gets stimulus tests (pass at 9/5, lost frame at 8/4) or is removed — no untested cycle claims.
Ruling R20: spec §5.2's "sample from the previous edge" is an accepted deviation — the user's hint was optional ("you can also"), the two-poll design still reaches 50 MHz and is more robust; documented in spi_link.md and the report and surfaced to the user — cost if wrong: an unused optimisation.
Ruling R21: Reset / HW Reset inside the SPI scenario re-apply the SPI scenario at the current session config (demo survives, fresh time base), matching SSI; only program loads and other scenarios leave it — cost if wrong: reset semantics.
Ruling R22: changing the execution-core selection so that PRU0 or PRU1 is dropped leaves the SPI scenario (Review Focus 1); tested.
Ruling R23: p1_cycles_per_bit keeps the minimum; add p1_bit_cycles_max (limiting value, 6) to rows/JSON and use the max wherever "limiting" is stated.
Final review minors to fix in the wave: tIDLE measured fetch in UI; UI note "worst row limits the next faster rate"; timing-doc "two cycles per edge" contradiction; spi.js clientX 0; put_clock_speed must not persist a session clock; runnable Python example via tools/spi_sweep.make_config. Controller fixes spec §2 row 12 (own doc).
Final review: deferred (can wait): greyed failing-rate path dead (no rate fails); _publish_updates after leave rebuild; others per triage table.
Final fix wave dispatched (opus, fresh implementer), FIX_BASE 3620e11
Final fix wave: BASE 89cb9ad (controller doc commit on top of 3620e11); implementer opus dispatched
Final fix wave: implementer stalled on 5-hour usage limit (HTTP 429, resets 06:40 Europe/Berlin) at 2026-11-05T03:45:33+01:00; retrying every 15 min per user instruction
Final fix wave: resumed after limit reset 2026-11-05T06:41:53+01:00
Final fix wave: implementer DONE_WITH_CONCERNS 94cc42f..3aa80bb — all 15 items; clean worktree 3490 pass + 3 known; UI only known failures.
Ruling R18a (amends R18): tLEAD/tLAG/tH-tL are exact by construction → margin None, source "exact by construction (design); up to 2 ns PRDO1 skew on silicon", excluded from worst (like derived rows); worst returns to tSU,MISO — R18 literal reading made a self-imposed minimum the worst row everywhere — cost if wrong: labelling of three rows.
Final fix wave: R18a b3bb622 + screenshots 7ebca7c; scoped re-review dispatched (opus) on 89cb9ad..7ebca7c
Final fix wave re-review (opus): all 15 + R18a ADDRESSED; clean-worktree 250 MHz scenario+as-shown tests 34 passed. Minor open: duplicated sentence docs/spi_link.md:28 → fixed in report step.
Final: parked — hop-0 sweep rows show tH,MISO −2 ns limiting text (pre-existing, hop 0 is informational) — Ruling: stands; supported rates are hop 5 — cost if wrong: doc wording.
Final: parked — put_clock_speed drops a live SpiLinkRuntime without close (pre-existing; new sim discards it) — Ruling: harmless, can wait.
Task 8 step 3: report dispatched (sonnet)
Task 8: complete (report 5a27131). Branch pushed to origin/feat/spi-pru-link 2026-11-05T07:25:16+01:00. Report-writer dispatch (sonnet, 109379 tokens, 636857 ms) not in the committed CSV.
