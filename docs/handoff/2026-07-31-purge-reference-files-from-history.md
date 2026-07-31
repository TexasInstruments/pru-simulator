# Handoff — purging reference RTL and internal specs from history (2026-07-31)

Cross-machine snapshot (agent memory is per-PC; this file travels with the
repo so any PC sees it after `git pull`). Continues from
[`2026-07-30-dec-lut-header-and-asm-style.md`](2026-07-30-dec-lut-header-and-asm-style.md).

## Status

**Complete and pushed** — `main` at `332657c`. No source or test changes this
session; nothing to run. Version tooltip unchanged at **v0.2.6**.

## ⚠️ History was rewritten twice — do this on every other PC

Every commit SHA in the repo changed. A clone that still holds the old
history will push the purged files straight back on its next `git push`.
On each other machine, before doing anything else:

```bash
git fetch origin && git reset --hard origin/main
git reflog expire --expire=now --all && git gc --prune=now
```

The `reset --hard` will **not** delete your local reference files — they are
untracked and gitignored now, so they simply stop being tracked. The `gc`
matters: without it the old objects linger locally and can be re-pushed.

If a machine has unpushed local work, rebase it onto the new `main` rather
than merging — a merge would drag the old history back in.

## What landed

| Commit | What |
|---|---|
| `b31bf69` | `chore`: untrack Verilog references (10 × `.v` + `endat.gtar`) |
| `6a8f4a2` | `chore`: untrack internal reference docs (3 files) |
| `332657c` | `docs(handoff)`: repoint commit SHAs after the rewrite |

Plus two `git filter-repo` runs that rewrote all history — those are not
commits, they replaced every existing one.

### What was removed from GitHub

Removed from the current tree **and from every commit in history**, while
staying on disk locally:

- `references/icss_m_core.v`, `references/icss_g_scu_sd.v`, and the eight
  `references/endat/icss_m_scu_ed*.v` files
- `references/endat.gtar` — an *uncompressed tar containing copies of all
  eight EnDat `.v` files*. Untracking the `.v` files alone would have left
  the same RTL published inside this archive; that is the kind of thing to
  check for before declaring such a removal done.
- `references/pdsp_v2p4p8.docx`
- `references/icss_g_scu_sd_functional_spec.md`
- `references/endat/ENDAT_INTERFACE_SPEC.md`

All 14 are still in `references/` on this PC, now covered by three new
`.gitignore` blocks so they cannot be re-added by accident.

### Why a rewrite and not just `git rm --cached`

Untracking only removes a file from the tip. Every prior commit still carries
the blob, and on GitHub anyone with repo access can fetch it. Since the point
was that this material is not published, the tip-only removal was not enough.

## How the rewrite was done — reuse this

`git filter-repo` (not in the distro; installed into a throwaway venv, since
this box's Python is PEP-668 externally-managed):

```bash
python3 -m venv fr-venv && ./fr-venv/bin/pip install git-filter-repo
PATH="$PWD/fr-venv/bin:$PATH" git filter-repo --force --invert-paths \
    --path-glob '*.v' --path references/endat.gtar
```

The procedure worth repeating, in order:

1. **`git bundle create <file> --all`**, then `git bundle verify` it. Both
   backups are at `~/pru-simulator-backup-{cb532bb,836b38c}.bundle` on this
   PC. They contain the purged material — delete them once you are satisfied,
   and do not copy them anywhere shared.
2. **Dry run in a throwaway clone first** (`git clone --no-local`), and check
   there: commit count unchanged, `HEAD^{tree}` hash *identical* to the real
   repo, author/date/subject list identical, and no target path anywhere in
   `git log --all --name-only`. For the RTL, also grepped every blob in the
   rewritten repo for `endmodule` — zero hits, which is what proved the tar
   copies were gone too.
3. **Push with a pinned lease**, not a bare force:
   `git push --force-with-lease=refs/heads/main:<expected-sha> origin main`.
   Aborts instead of clobbering if the remote moved.
4. **Re-add `origin` and re-set upstream.** filter-repo deletes the remote on
   purpose, and `git remote add` alone does not restore the branch's upstream
   — `git branch --set-upstream-to=origin/main main` is a separate step.
5. **Verify against GitHub, not just locally.** Walked all 73 published trees
   via `gh api repos/.../git/trees/<sha>?recursive=1` and confirmed none of
   the six target paths appears in any of them.

## Repointing the handoff SHAs

The rewrite invalidated the 13 commit SHAs cited across five handoff notes.
filter-repo's `.git/filter-repo/commit-map` was no help — the second run
overwrote the first, so it only maps rewrite-1 output to rewrite-2 output,
not original to final.

Instead the original history was cloned back out of the backup bundle and
commits matched on **author name + email + author timestamp + subject**,
all of which filter-repo preserves. 72/72 matched with no key collisions,
each cited SHA resolving to exactly one successor. 16 occurrences updated.

Replacements were scoped to backticked tokens under `docs/handoff/` only —
a loose hex pattern over `docs/` would have hit binary literals like
`0b10110000` and CSS colors like `#f9e2af22`.

## Open items

- **GitHub still holds the old objects.** After a force-push the unreachable
  commits stay fetchable by direct SHA until GitHub garbage-collects. Both
  pre-rewrite tips (`cb532bb`, `836b38c`) and everything under them are in
  that state. Closing this needs a **GitHub Support ticket** for
  TexasInstruments/pru-simulator asking them to purge unreachable objects;
  **not yet filed**. Mitigating factors: the repo is private, 0 forks, 0 open
  PRs at the time of the rewrite.
- **Other PCs not yet re-synced** — see the warning above. Until they are,
  the purge is one careless `git push` away from being undone.
- **Nothing enforces the ignore rules.** `.gitignore` stops accidental
  `git add`, but a `git add -f` or a new copy under a different name would
  sail through. No pre-commit hook checks for `.v`/spec material.
- **`references/` still holds other material that was not part of the ask**
  and is still tracked. Some is clearly publishable (`spruhv6b.pdf`,
  `spruij2.pdf` are public TI literature numbers); some is less obvious —
  `BS_MAC.{md,pdf}`, `PRU Instruction Set v01.pptx`, and the ten register
  screenshots (`GPCFG0_REG_*`, `SD_*`). Worth a deliberate pass now rather
  than a third rewrite later, since each one costs another force-push and
  another round of re-syncing every clone.
- Unchanged from earlier handoffs: version not bumped (v0.2.6),
  `dec_lut.h` has no in-tree consumer, `gen_dec_lut.py --check` not in CI,
  no encode-side header, `memory.cfg` CRLF churn with no `.gitattributes`,
  perif examples never viewed in a browser, `PATTERN` being assemble-time,
  channel 0 only, `PerifRegisters.get_busy()`, SD mode plotting GPO/GPI,
  single-shot UI affordance, pif_eth Options 2/3, the `PROJECT_REPORT.md`
  RX section, broadside CRC.
