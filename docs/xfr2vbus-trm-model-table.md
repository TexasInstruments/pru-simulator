# XFR2VBUS: TRM-vs-model field table

Primary source: **AM64x/AM243x TRM, document SPRUIM2J, section 6.4.6.3.1
"PRU_ICSSG XFR2VBUS Hardware Accelerator"** (Tables 6-106/6-107, §6.4.6.3.1.1
through §6.4.6.3.1.6 "XFR2VBUS Programming Model", ending where §6.4.6.3.2
XFRDMA — an unrelated peripheral — begins). Cite the TRM this way (document +
section + table) when working from a checkout of this repo.

The line numbers used throughout this doc (e.g. "line 7543") are pointers
into an archived plain-text extract of that TRM chapter, kept at
`research/refs/icssg-chapter-6.4-functional.txt` **relative to the workspace
root that contains this repo as a subdirectory — not relative to this repo
itself.** A checkout of this repo alone does not include that file (this repo
does not vendor TRM text; see "Do NOT copy the reference text into the repo"
policy). If you have this repo checked out standalone, use the
document/section/table citation above instead of the line numbers, or locate
your own copy of SPRUIM2J and consult the section/table cited. Global IDs
from Table 6-60 (line 3463-3466 in that same extract).

This table is the authority the implementation in
`xfr/xfr2vbus_accelerator.py` was built against. Where the model diverges
from a literal TRM reading, the divergence and its justification are called
out explicitly below — do not treat silence as agreement with a prior
paraphrase (the NCP-127 issue text under-specified RD_SIZE and is wrong on
that field; see "Corrections" below).

## Global IDs (Table 6-60, line 3463)

| ID | Name | Direction | Modelled |
|---|---|---|---|
| 0x60 | RD_ID0 | XIN (data/status) / XOUT (config/addr) | yes, `XFR2VBUSReadAccelerator` |
| 0x61 | RD_ID1 | same as RD_ID0, independent state | yes |
| 0x62 | WR_ID0 | XOUT (data/addr) / XIN (status) | yes, `XFR2VBUSWriteAccelerator` |
| 0x63 | WR_ID1 | same as WR_ID0, independent state | yes |
| 0x64 | TX_PRU RD | — | not modelled — TX_PRU core is not simulated by this repo (`simulator.py` only instantiates pru0/rtu0/pru1) |

RD_ID0/RD_ID1 and WR_ID0/WR_ID1 are TRM's "2 x XFR2VBUS RX threads" / "2 x
XFR2VBUS TX threads" (line 7321-7322): independent hardware channels, not
aliases. Each gets its own accelerator instance with private state, the same
pattern `BSwapAccelerator` uses for its three device IDs.

## Write commands (Table 6-106, lines 7386-7448)

| PRU register | Access | Field | TRM notes | Model |
|---|---|---|---|---|
| R17-R2 (64B mode) | XOUT | WR_DATA | data; atomic combined XOUT spans R19-R2, and the R19-R18 WR_ADDR note (line 7413) says a combined XOUT "needs to be the full 64 bytes" | combined-flow size validated against `{32,64}` (`_VALID_COMBINED_SIZES`) |
| R19-R18 | XOUT | WR_ADDR | 48-bit; R19=upper 16, R18=lower 32 (matches the read table's explicit `R20:R19` high:low ordering) | `_addr_hi`/`_addr_lo`, partial-byte-preserving |
| R9-R2 (32B mode) | XOUT | WR_DATA | "Size restrictions" list (lines 7421-7426): 1B, 4B aligned, 32B aligned, no holes; the R11-R10 WR_ADDR note (line 7438) separately says a combined XOUT "needs to be the full 32 bytes" | split-flow size validated against `{1,4,8,32,64}`, combined-flow against `{32,64}` — see **"Split vs. combined write sizes"** below for why this table's own list and §6.4.6.3.1.6's list are each right for a different flow shape |
| R11-R10 | XOUT | WR_ADDR | 48-bit; R11=upper16, R10=lower32 | same latch as R19/R18 — see "Corrections" |
| R20[0] (ALL modes) | XIN | WR_BUSY | 1 = (WR_CMD_FIFO≠0) or (WR_DATA_FIFO≠0) | always 0 post-write — see "Simplifications" |

### Split vs. combined write sizes (Table 6-106 vs. §6.4.6.3.1.6)

Two TRM passages both discuss legal WR_DATA sizes and, read carelessly,
appear to conflict:

* **Table 6-106's "Size restrictions"** (lines 7421-7426, printed under the
  **32-Byte Mode** R9-R2 WR_DATA row — not the 64-byte-mode row) lists "1
  byte / 4 bytes aligned ... / 32 bytes aligned ...". It does not mention 8
  bytes anywhere.
* **§6.4.6.3.1.6 "XFR2VBUS Programming Model"** (line 7543) lists the
  address-then-data **split** write flow as "XOUT (6 Byte address or 4 Byte
  address) then XOUT 32 Byte/ 8 Byte/ 4 Byte/ 1 Byte data" — four sizes,
  explicitly including 8.

These are not actually the same rule applied twice with a contradiction; they
govern two different XOUT shapes:

1. **Combined** — one XOUT sets address and data together. Both WR_ADDR
   notes are explicit that this shape carries the *whole* data window and
   nothing less: "the one XOUT can set the address and data at the same
   time, but in this case data needs to be the full 32 bytes" (R11-R10 note,
   line 7438) / "...the full 64 bytes" (R19-R18 note, line 7413). Legal
   combined sizes are therefore exactly `{32, 64}` — 8 is never legal here.
2. **Split** — a separate WR_ADDR XOUT followed by a separate WR_DATA-only
   XOUT. §6.4.6.3.1.6's Programming Model is the later, more specific
   statement of exactly what this flow allows, and it names 8 bytes as one
   of its four legal data sizes. Table 6-106's "Size restrictions" list,
   read under the combined-size notes it sits beside, is best understood as
   an incomplete restatement of the split-flow rule (it also omits 8 — the
   same gap the Programming Model list fills) rather than a second,
   competing enumeration; it does not anywhere say "8 bytes is illegal."

The model (`xfr/xfr2vbus_accelerator.py`, `_validate_size`) treats the two
passages as governing different flows rather than picking one and silently
discarding the other: `_VALID_SPLIT_SIZES = (1, 4, 8, 32, 64)` for the split
flow, `_VALID_COMBINED_SIZES = (32, 64)` for the combined flow, selected by
whether a given XOUT call actually touches a WR_ADDR register in addition to
WR_DATA. In this model the two shapes are also geometrically distinct: a
payload-bearing XOUT must start at `&R2.b0`, and the WR_ADDR registers sit
immediately past the full 32-/64-byte WR_DATA window, so an XOUT cannot reach
WR_ADDR without having already supplied the entire preceding WR_DATA window —
an "8-byte combined" XOUT is not constructible through this call shape at
all. `_validate_size` is still written as an explicit combined/split check
(rather than relying on that geometry implicitly) so the rule survives a
future refactor and is independently unit-tested
(`tests/test_xfr2vbus_accelerator.py::TestWriteChannel::test_8_byte_combined_write_is_rejected`
calls it directly, since the shape it guards against cannot be reached
through `xout()`).

## Read commands (Table 6-107, lines 7451-7527)

| PRU register | Access | Field | TRM notes | Model |
|---|---|---|---|---|
| R17-R2 | XIN | RD_DATA | fully pops regardless of XIN length (line 7360, 7376) | `xin()` slices `_buffer[:length]`, always clears `_buffer` |
| R18[0] | XOUT | RD_AUTO | 0→1 must write RD_ADDR; 4-byte mode unsupported in auto (line 7482) | `_auto`; rejects `auto and size_code==0` |
| R18[2:1] | XOUT | RD_SIZE | 0h=4B, 1h=Reserved, 2h=32B, 3h=64B (lines 7483-7488) | `_SIZE_BYTES = {0:4, 2:32, 3:64}`; `1` raises |
| R18[0] | XIN | RD_BUSY | (RD_CMD_FIFO≠0) or (RD_DATA_FIFO≠0) | `bool(_buffer is not None)` |
| R18[1] | XIN | RD_CMD_FL | 0=Empty, 1=Occupied, pops only once data has arrived | always 0 — see "Simplifications" |
| R18[2] | XIN | RD_DATA_FL | 0=Empty, 1=Occupied | same value as RD_BUSY here |
| R18[3] | XIN | RD_MST_REQ | 1 = last data still in flight | always 0 — see "Simplifications" |
| R20:R19 | XOUT | RD_ADDR | 48-bit; R20=upper16, R19=lower32; writing R19 submits the command | writing R19 triggers `_issue_read()`; writing R20 alone only updates the latch |

## Hardware behaviour (§6.4.6.3.1.1-.4, lines 7350-7382)

* One command in the command FIFO, one 64-byte buffer, per direction, per
  channel (line 7325, 7330, 7355, 7358, 7378, 7381). Modelled: one `_addr`
  latch and (read side) one `_buffer` slot per accelerator instance — never a
  queue.
* "XIN of the read data will fully pop the data, independent of XIN size"
  (line 7360, 7376). Modelled exactly: `xin()` always discards `_buffer`
  after slicing, even if `length < len(_buffer)`.
* Read-side back-pressure: the programming model requires "Wait RD_BUSY = 0h"
  before submitting a new address (line 7545). Modelled: `_issue_read()`
  raises `ValueError` if `_buffer` is still occupied — this is the "existing
  core hold mechanism" instruction from the brief read narrowly: `step()` is
  untouched, so the accelerator cannot itself stall the PC; a firmware image
  that violates the documented wait is a real protocol violation and fails
  loud instead of being silently absorbed into a fictitious FIFO.
* Auto mode (§6.4.6.3.1.3, lines 7361-7376): each `XIN RD_DATA` pop re-issues
  the next read at `addr + 0x20`/`+0x40` before returning, matching "1 XIN of
  read data will cause a new command to be issued".

## Corrections to the NCP-127 issue paraphrase

* **RD_SIZE encoding.** The issue text says "0=4B, 1=32B, 2=64B". The TRM
  (Table 6-107, R18[2:1], lines 7483-7488) says **0h=4B, 1h=Reserved,
  2h=32B, 3h=64B**. The model uses the TRM encoding; `1` raises
  `ValueError`. This is exactly the kind of paraphrase drift the brief warned
  about ("the issue text is not the authority") — verified against the
  primary table, not against the prior summary.
* **"R17.b3 enable" write-mode selection** (line 7388: "64 Byte Mode
  (Implied if R17.b3 enable is true)"). The archived TRM extract does not
  define which specific bit within R17 byte 3 is the "enable" flag, and R17
  only has architectural meaning as the top word of 64-byte WR_DATA — it is
  otherwise unused in 32-byte-mode traffic. Rather than guess an undocumented
  bit position, the model derives 32B-vs-64B mode from the highest register a
  given XOUT actually reaches: R12..R17 are only ever valid in 64B mode, so
  touching them means R10/R11 (which fall *inside* the 64B WR_DATA window,
  not just adjacent to it) are WR_DATA for that call, not WR_ADDR; otherwise
  R10/R11 are the 32B-mode WR_ADDR pair. TRM never documents a single XOUT
  mixing the two windows, so a call is always unambiguously one mode or the
  other under this rule. Flagged here explicitly for review rather than
  silently implemented — this was caught by a failing test
  (`test_combined_atomic_xout_sets_address_and_data_together`) during
  development: an earlier version classified R10/R11 as WR_ADDR
  unconditionally and silently dropped 8 bytes of a 64-byte combined write.

## Simplifications (labelled simulator-only, not TRM claims)

* **WR_BUSY reads 0 immediately after every write.** The simulator retires a
  VBUSM write synchronously inside `xout()`, in a single Python call with no
  modelled bus latency — there is no elapsed-cycle window in which a "command
  in flight" state could ever be observed. This is consistent with the TRM's
  own note that "external bandwidth is very high... all egress commands and
  data should get sent without head of line blocking" (line 7351-7352); the
  only thing not modelled is "based on arbitration some delay is possible"
  (line 7353), which this simulator has no VBUSM contention model for at all.
* **RD_CMD_FL and RD_MST_REQ always read 0.** Both describe the interval
  between issuing a read command and its data arriving. The simulator fetches
  read data synchronously inside `_issue_read()` (called from the `xout()`
  that submits RD_ADDR), so by the time software can poll R18 the data has
  already landed — "last data has been latched" (RD_MST_REQ=0) and "it only
  pops the read command FIFO after the read data has arrived" (RD_CMD_FL=0)
  are both trivially true at every observable point.
* **"Wait WR_BUSY=0h OR RD_DATA_FL=1h" (line 7547) between issuing RD_ADDR and
  XIN'ing RD_DATA** is unconditionally satisfied by construction (RD_DATA_FL
  is already 1 by the time `xout()` returns), so no explicit wait state is
  modelled.
* No VBUSM/CBASS0 fullness or arbitration state is modelled at all (line
  7351: "The only blocking condition is caused when the VBUSM command/data
  FIFO is full"); this repo has no cross-core external-bus contention model
  to hook into.

## Backing memory

Per line 3355-3356, XFR2VBUS reaches SoC CBASS0/MSMC memory — a materially
different address space from the ICSSG-local `PRUCore.memory` MemoryBus used
by LBBO/SBBO (DRAM0/DRAM1/shared RAM, per the `_map_data_addr` comment in
`core/pru_core.py`). The accelerators read/write through a new, separate,
public `PRUCore.vbus_memory: MemoryBus` (constructor parameter, defaults to a
fresh empty bus so an unconfigured access fails loud like every other
unmapped-region access in this codebase). `Simulator` now owns one shared
`vbus_memory` bus across pru0/rtu0/pru1, mirroring how it already shares
`self.memory`; no default region is registered in it, since inventing a
default SoC memory map/size for this repo is not something the archived TRM
extract or `memory.cfg` supports as evidence.
