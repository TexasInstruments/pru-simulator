# CORES deck implementation plan

**Goal:** Replace duplicated multicore panels with one source/register pair, split into selected core panes, and two shared group clocks.
**Spec:** ../../../../../artifacts/ui/multicore-cores-deck-task.md (workspace root artifacts/ui).
**Architecture:** Pure core-selection state owns used/shown/focus/pin. The dashboard keeps execution membership separate from pane visibility, reuses existing instruction/register renderers and the single tiling layout, and patches the deck in place. Configuration remains authoritative for clocks.
**Stack:** Existing vanilla JavaScript/CSS, FastAPI and Python simulator; no new dependency.

## Constraints and rulings
- Preserve existing local issue fixes and user memory.cfg; no push or commits requested.
- Group 0 PRU0/RTU0 uses pru_clock_mhz; group 1 PRU1/RTU1 uses pru1_clock_mhz. Presets 200/225/250/300/333, custom configured rates survive load. IEP separate.
- Default used/shown PRU0. shown is nonempty subset of used; focus is shown. Selecting a hidden telemetry core shows it, so focus stays inspectable.
- Keep only the existing eight tiling panels in every mode. Core changes never switch the tile tree.
- Legacy layouts never persisted an enabled-multicore flag. Migrate an explicit saved core selection if present, not an abandoned MC tree merely because it exists.
- Clock changes follow current config reload semantics and state this consequence before applying. Reset Layout resets core selection but preserves clocks and dismissed coaching.
- Baseline: 30 Node checks pass; Python affected subset 52 pass/1 failure: existing test expects 250 MHz despite user's local memory.cfg at 200 MHz. Do not modify the user's config to satisfy it.

## Tasks
- [x] 1. Group-clock API: tests first in tests/test_clock_speed_endpoint.py. GET exposes both rates, members and choices; PUT accepts optional group (0/1), validates, preserves the other effective rate (materialize inherited group 1 when necessary), preserves IEP and atomic config replacement. Legacy no-group PUT still updates both. Include groups in state replies for UI refresh. Verify with pytest clock and IEP tests.
- [x] 2. Core-selection model: tests first in tests/ui_cores.test.js. Create ui/static/cores.js exporting PruCores.ORDER, normalize(saved, available), transition(state, action, available). State {used, shown, focus, pinned}; actions use/show/solo/focus/use-all/show-all/pin/reset. Normalize persisted invalid/target-specific state, migrate explicit legacy selection. Verify real transition sequences and immutability.
- [x] 3. Dashboard integration: replace old selectors and duplicated panel markup/layout registry with core deck and source/register pane containers. Implement patch-in-place deck, keyboard/touch/pin/hover bridge, persisted coaching, group-clock controls and explicit reload notice. Decouple execution used set from shown panes, focus correct targets for editing/breakpoints/editor/IO/telemetry, maintain SSI scenario state and run acknowledgement handling. Keep existing SPAD/constant table controls on the focused inspector. Add integration checks with browser and adapt existing unit harnesses to the new interface.
- [x] 4. Verify/review/docs: run Node suite and affected Python clock/server/scope/SSI tests, browser real firmware runs (1/2/4 shown, hidden used cores, no PRU0 lead, group clocks, migration, reset, keyboard and reduced motion), capture before/after screenshots, task review and final review. Update dashboard docs/handoff. Run graphify update at workspace root. Leave changes local on feat/pr5-simulator-ui.

## Progress
- Tasks 1–4 complete; implementation and review fixes remain local on `feat/pr5-simulator-ui`.
- Final evidence: 46 Node checks; 261 affected Python tests; real Firefox execution/SSI/responsive/motion/loopback checks. See `docs/agent-review-handoff.md`.
- User configuration preserved. No commits, push, motor-branch rebase or GitHub mutations performed.
