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

HTML reports following the CSS-styled format used by other projects, containing:
- Header with project title and metadata
- Sections for Project Overview, Process, Design Choices, Files Created, Testing, Validation
- Statistics grid
- Footer with generation info

### 5. Skills (`docs/skills/`)

Skills stored here are not gitignored and are available to all users of the repository.

## Validation Checks

After creating or updating documentation, run these checks:

1. **Source READMEs exist**: `source/*/README.md` files present
2. **Source reports exist**: Major projects have PROJECT_REPORT.md  
3. **Plan/spec consistency**: Matching dates in plans/ and specs/ folders
4. **Report exists**: Corresponding HTML report in docs/reports/
5. **Links work**: Internal references point to correct locations
6. **Format matches**: Follows same structure as pif_eth/i2c examples

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