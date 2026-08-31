# Project Documentation Skill for PRU Simulator

This skill ensures consistent documentation format across all simulator projects in the pru-simulator repository, following the same structure as well-documented projects like pif_eth and i2c_tca9538_running_led.

## When to Use

- Creating documentation for new simulator projects
- Updating documentation for existing simulator projects
- Ensuring consistency in documentation format across the repository
- Validating that all documentation follows the established structure

## Standard Documentation Structure

### 1. Source Project Documentation (`source/*/README.md`)

Each source directory should contain a README.md following this format:

```markdown
# project_name — Brief Description

[One or two paragraph overview of what the firmware/program does, including:]
* Key specifications (clock rates, protocol details, memory layout)
* PIN assignments and descriptions
* DRAM memory map table
* Files table listing purpose of each file
* How to run in simulator (multi-core, single-core, MCP server, automated tests)
```

See: `source/pif_eth/README.md` or `source/ssi_reader_4mhz_12bit/README.md` for examples.

### 2. Source Project Report (`source/*/PROJECT_REPORT.md`)

Each major source project should have a PROJECT_REPORT.md following this format:

```markdown
# project_name — Project Report

[Overview sentence]

## 1. Initial prompt
[Verbatim request that initiated the work]

## 2. Design choices and decisions
### 2.1 [Category]
[Design decision explanations]

## 3. Files generated
[Tables listing source files, documentation files, test/support files]

## 4. Testing
[Description of verification strategy at unit, integration, automated, and manual levels]

## 5. Notable observations during implementation
[Key insights or lessons learned]

## 6. Running with the UI simulator
[Step-by-step instructions for different validation methods]
```

### 3. Superpowers Documentation (`docs/superpowers/`)

#### Plans (`docs/superpowers/plans/YYYY-MM-DD-description.md`)
Implementation plans recording what was built, using checkbox syntax for task tracking.

#### Specs (`docs/superpowers/specs/YYYY-MM-DD-description-design.md`)
Technical design specifications with:
- Problem statement
- Scope decisions
- Detailed design of each component
- Testing strategy
- Files table

### 4. Session Reports (`docs/reports/YYYY-MM-DD-project-session-report.html`)

**Every session report MUST reuse the exact CSS and markup patterns established in
`docs/reports/2026-08-12-i2c-tca9538-session-report.html`.** That file is the canonical
template — not just an example to loosely draw inspiration from. Do not invent a new
color palette, font stack, or section markup for a new report. When starting a new
report, copy that file's `<style>` block verbatim (including the
`@media (prefers-color-scheme: dark)` block and the `:root[data-theme="dark"]` override —
every report must support dark mode) and reuse its class names as-is.

Reports that drift from this template (different CSS variable names, missing dark mode,
ad-hoc card grids, `<pre>` instead of `.prompt-block`, etc.) must be fixed before being
considered done. If you're unsure whether a new section's content fits an existing class,
prefer adapting the content to an existing class over inventing a new one.

#### Required CSS variables (`:root`)

`--bg`, `--surface`, `--surface-2`, `--border`, `--text`, `--text-muted`, `--text-faint`,
`--accent`, `--accent-strong`, `--good`, `--warn`, `--mono`, `--serif` — plus the dark-mode
overrides for all of the above in both the `prefers-color-scheme: dark` media query and
`:root[data-theme="dark"]`.

#### Required page shell

```html
<div class="page">
  <div class="titleblock">
    <p class="eyebrow">Session Report · pru-simulator</p>
    <h1>Project Title</h1>
    <div class="meta-row">
      <span>Branch <b>...</b> → <b>...</b></span>
      <span>N commits</span>
      <span>N tests passing</span>
      <span class="status-chip">Merged &amp; pushed</span>
    </div>
  </div>

  <section id="...">
    <h2><span class="num">1</span> Section Title</h2>
    ...
  </section>

  <div class="statgrid">
    <div class="stat"><div class="v">...</div><div class="k">...</div></div>
  </div>

  <footer>pru-simulator · ... · generated from session transcript</footer>
</div>
```

Every `<h2>` is numbered sequentially with `<span class="num">N</span>`. Only put real,
already-known facts in the `.meta-row` and `.statgrid` — never fabricate a branch name,
commit count, or stat to fill a slot; omit the slot instead.

#### Standard section order (use what applies, in this order — skip a section rather than fabricate its content)

1. **Prompt / Initial Request** (`.prompt-block`, `white-space: pre-wrap`) — the verbatim
   request that started the session. Only include this if you have the actual original
   wording; if it's a summary rather than verbatim text, say so in a `.prompt-source` note
   instead of claiming it's verbatim.
2. **Process** — a `.steps` row of `.step` blocks (compact mono badge `.n` + bold `.label`,
   NOT big circular numbers), one per phase actually followed.
3. **Design Choices** — a decision log using `.decision-group-label` to separate groups
   (e.g. "Decided up front" vs. "Found and corrected during implementation"), each decision
   as a `.decision` block (`.idx`, `.title`, `.why`). Add the `.fixed` class to any
   `.decision` that documents a real bug caught and corrected during implementation or
   review — the CSS automatically renders a "bug found in review" tag on it.
4. **Context Usage** (when applicable — implementation sessions with dispatched
   subagents): an `.arch-note` explaining what the numbers mean, followed by a
   `.table-wrap` table with per-task implementer/reviewer/fix-round/total-token columns
   (`.bar-cell`/`.bar-track`/`.bar-fill` for the visual bar, `.task-name` for the row
   label, a `tfoot` totals row). For documentation-only work, or work with no subagent
   token accounting, replace this with a plain `.table-wrap` table describing what was
   enhanced (component / location / enhancement) — do not force fake token numbers into
   the implementation-style table.
5. **Files Created** — `.table-wrap` table of file → purpose.
6. **Testing and Validation** — prose + `.table-wrap` tables/`.prompt-block` command
   snippets, not `<pre>`. Numbered `<h3>` subsections are fine for multiple validation
   methods (automated, manual UI, MCP, etc.).
7. **Review Findings** (when applicable) — `.table-wrap` table of task / finding /
   severity (`.tag`, `.tag.critical`, `.tag.important`, `.tag.minor`) / outcome.
8. **Timeline** (when applicable — real wall-clock data from git history is available) —
   `.table-wrap` table of task / landed timestamp / elapsed, with a `tfoot` wall-clock
   span row. Never invent timestamps; omit this section if the data isn't available.

Close with a `.statgrid` of the session's real headline numbers and a one-line `<footer>`.

### 5. Skills (`docs/skills/`)

Skills stored here are not gitignored and are available to all users of the repository.

## Validation Checks

After creating or updating documentation, run these checks:

1. **Source READMEs exist**: `source/*/README.md` files present
2. **Source reports exist**: Major projects have PROJECT_REPORT.md  
3. **Plan/spec consistency**: Matching dates in plans/ and specs/ folders
4. **Report exists**: Corresponding HTML report in docs/reports/
5. **Links work**: Internal references point to correct locations
6. **Format matches**: The report's `<style>` block matches
   `docs/reports/2026-08-12-i2c-tca9538-session-report.html` verbatim (same CSS variables,
   dark-mode blocks, and class names), source docs follow the same structure as
   pif_eth/i2c examples

## Example Usage

When documenting a new simulator project:

```bash
# 1. Add source README
cp docs/templates/README.md source/my_project/README.md
# Edit to match actual content

# 2. Add source report
cp docs/templates/PROJECT_REPORT.md source/my_project/PROJECT_REPORT.md
# Edit with actual project details

# 3. Create superpowers docs
# Plan: docs/superpowers/plans/2026-MM-DD-feature-name.md
# Spec: docs/superpowers/specs/2026-MM-DD-feature-name-design.md

# 4. Generate session report
# docs/reports/2026-MM-DD-project-session-report.html

# 5. Validate
python -m pytest tests/test_my_feature.py -q  # Verify tests still pass
```