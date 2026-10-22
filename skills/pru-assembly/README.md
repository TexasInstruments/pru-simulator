# pru-assembly (Claude Code skill)

Write, review, debug, simulate and build assembly for TI PRU cores (PRU-ICSS, PRU_ICSSG, PRUSS)
on AM243x, AM64x, AM62x, AM261x, AM263x, AM263Px and legacy AM335x.

Open `skill-map.html` in a browser for the overview: dependency map, workflow, scripts and examples.

## Contents

| Path | Purpose |
| --- | --- |
| `SKILL.md` | Entry point: 8-step workflow, templates, conventions, pitfalls |
| `references/` | Six on-demand reference files (ISA, timing, C interop, simulator, OpenPRU, AM335x) |
| `scripts/lint_pru.py` | Linter, Python 3 standard library only |
| `scripts/simulate.py` | Wrapper around TI pru-simulator (`run`, `edges`) |
| `scripts/build.sh` | clpru build wrapper for AM335x and standalone files |
| `skill-map.html` | Visual overview of the skill |

## Install

Copy this folder to `~/.claude/skills/pru-assembly/` (user-wide) or `<project>/.claude/skills/pru-assembly/`
(one project). Restart Claude Code, then ask a PRU question or type `/pru-assembly`.

## Requirements

- Python 3.9+ for the linter and simulator wrapper.
- Optional: TI PRU Code Generation Tools (`clpru`, env `PRU_CGT`) for `build.sh`.
- Optional: [pru-simulator](https://github.com/TexasInstruments/pru-simulator) (env `PRU_SIM_DIR`) for `simulate.py` and the MCP tools.

## Quick check

    python scripts/lint_pru.py --help
