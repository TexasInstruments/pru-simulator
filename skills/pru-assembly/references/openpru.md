# Working with OpenPRU

This skill assumes an `open-pru` checkout (`github.com/TexasInstruments/open-pru`) is available. OpenPRU is the single source of truth for devices, project creation, building and coding rules. Don't restate its content here or in your replies; read it.

1. Read `AGENTS.md` in the checkout root, then follow its routing (`docs_ai/README.md` and the task runbooks).
2. For instruction syntax, operand limits, branch order and core revisions, read `docs/PRU Assembly Instruction Cheat Sheet.md`.
3. For coding rules, read `best_practices.md` before writing or reviewing PRU code.

## What this skill adds

The runbooks don't cover these. Merge them into the runbook's firmware and verification steps instead of running a second checklist:

- Lint the firmware with `scripts/lint_pru.py --openpru` (SKILL.md step 4).
- Simulate it with `scripts/simulate.py` (`references/simulator.md`).
- Write a register map and a cycle budget, and check each critical path against it (`references/timing.md`).
- For a project with a host side, build it with the project's own makefile as the runbook says. Flash or load only when the user asks.
