# IEP 32-bit shadow compares and PWM efficiency status clear

Technical note for `perif/iep.py` and the ICSS CFG word `ICSSG_SA_MX_REG`
(upstream issue #73). It states what the IEP model does, which source supports
each statement, and what is deliberately left open.

## Sources

* AM64x/AM243x TRM, SPRUIM2J (April 2026):
  * 6.4.13.2.2 "PRU_ICSSG IEP Timer", PDF p. 731: the shadow-mode and
    CMP_STATUS self-clear features list.
  * 6.4.13.2.3 "32-Bit Shadow Mode", PDF pp. 731-732: rules of operation and
    sequence of operation.
  * IEP_CMP_STATUS_REG, Table 14-10994, PDF p. 6937.
  * ICSSG_SA_MX_REG (ICSS CFG offset `0x40`), Tables 14-10186 and 14-10188,
    PDF pp. 6547-6548. The TRM spells the register `ICSSG_SA_MX_REG`; no
    register called `PIN_MX` appears in it.
* AM261x TRM SPRUJB6 documents the same IEP `SHADOW_EN` sequence, so shadow
  mode is available on every target. It has no `ICSSG_SA_MX_REG`/`PWM_EFC_EN`.
* Upstream issue #73 silicon observations (cited as reported): a compare value
  of 0 did not fire, a compare equal to CMP0 did fire, the measured half-period
  was CMP0+1 and not CMP0+2, and enabling shadow mode preserved the existing
  active compare values.
* Register-level measurements on LP-AM243 (ICSSG0 IEP0, PRU at 250 MHz, IEP
  clock 200 MHz) and LP-AM261 (ICSSM0 IEP0, 1.000 IEP count per PRU cycle),
  taken with PRU firmware that reads and writes the IEP registers and samples
  `IEP_COUNT_REG0` with PRU cycle time stamps. Each scenario was run three
  times with identical register values. Below, "measured" means these runs.
  LP-AM261 repeated the period and the entry/copy scenarios and behaved like
  LP-AM243.

## Supported semantics

### Shadow mode (`IEP_CMP_CFG_REG[17] SHADOW_EN`)

* The controller counter is 32 bits. It wraps at 2^32 without carrying into
  `IEP_COUNT_REG1`. Entering shadow mode keeps only the low 32 bits of the
  counter.
* `IEP_CMPj_REG0` reads the **active** compare (measured on LP-AM243 ICSSG0,
  register level).
* `IEP_CMPj_REG1` reads and writes the **shadow** copy. This is not a write
  buffer on REG0: a REG1 write does not change REG0 while the counter runs, and
  it never changes `CMP_STATUS`.
* The shadow values of all sixteen compares are copied into the active
  registers when the counter is reset by a CMP0 hit
  (`CMP0_RST_CNT_EN`) and when the 32-bit counter rolls over.
* **Stopped counter at `COUNT == 0`** (measured on LP-AM243 ICSSG0 and
  LP-AM261 ICSSM0, register level): the copy also runs between resets. A REG1
  write shows in REG0 at once, before the counter is enabled, and setting
  `SHADOW_EN` loads REG1 into REG0 at once. With the counter stopped at
  `COUNT != 0` there is no copy: REG0 keeps its value after a REG1 write and at
  counter enable, and changes at the first reset or rollover. The model
  implements exactly this, for a counter that is stopped (`CNT_ENABLE = 0`).
  A running counter that happens to be at 0 is not a stopped counter and takes
  no copy.
  Entering that state (COUNT written to 0 while stopped, or the counter
  stopped at 0) copies at once in the model; this follows from the measured
  continuous copy and was not measured separately.
* Without a new shadow write, every later reset reloads the same shadow values.
* A CMP0 write only decides the *next* reset period. The period that is already
  running ends at the previously loaded CMP0, so changing CMP0 cannot select a
  wrong reset period.
* **Entering shadow mode** (measured on LP-AM243 ICSSG0 and LP-AM261 ICSSM0,
  register level): the active low words are kept and REG1 is **not** seeded from
  them. REG1 keeps its previous contents, the old upper word of the 64-bit
  compare (normally 0). A counter that is running with reset on CMP0 therefore
  fires at the old CMP0 once more, the first reset loads REG1 (0), and CMP0
  stops firing at that value unless firmware writes REG1 first. With the counter
  stopped at `COUNT == 0` the old upper word reaches REG0 at once (REG1 5 in
  64-bit mode reads REG1 5 and REG0 5 after `SHADOW_EN`). Firmware should write
  every REG1 it relies on after setting `SHADOW_EN`.
* **A write to `IEP_CMPj_REG0` in shadow mode** (measured on LP-AM243 ICSSG0,
  register level; the access is not ignored): the written data is discarded
  (it never reads back), REG1 is copied into REG0, and `CMP_STATUS` bit j is
  cleared. The running period is unchanged (period 13 before and after a write
  of 77 in the measurement). Partial-word writes to REG0 do the same.
* Leaving shadow mode keeps the active values and makes REG1 the upper half of
  the 64-bit compare again (REG1 then reads 0 in the model; not measured).
  64-bit mode behaviour is unchanged by this note except as listed under
  "Observed on silicon, not modelled here".

### Reset timing

In shadow mode the compare hit and the reset are on different IEP clocks. For
`DEFAULT_INC = 1` and `CMP0 = N`: the counter equals N on clock N and sets
`CMP_STATUS[0]`; clock N+1 resets the counter to 0 and loads the shadow copies;
that reset clock is not itself compared (a compare of 0 does not fire in the
model). The period is therefore N+1 clocks. Measured on LP-AM243 ICSSG0 and
LP-AM261 ICSSM0 (register level, N = 5, 13, 100, three repetitions each): the
largest counter value sampled is N and N+1 distinct values 0..N are seen, so
the period is N+1. This matches the CMP0+1 half-period in #73 and the TRM
sentence that `CMP0_RST_CNT_EN` "will reset the controller counter on the next
ICSSG_IEP_CLK/ICSSG_ICLK cycle".

Other hits on the same clock as the CMP0 hit are reported on that clock, so
simultaneous CMP0 and CMPn events set both status bits together.

### PWM efficiency (`ICSSG_SA_MX_REG[16] PWM_EFC_EN`)

* The register is mapped at ICSS CFG `+0x40` (`0x00026040` in the simulator
  memory map) and is available on AM243x/AM64x only. Reads and writes use the
  normal memory bus, so firmware `SBCO`/`LBCO` reach it. Only the TRM's R/W
  fields are kept (mask `0x00010FFF`). It is cleared by
  `IepTimer.hardware_reset()`, not by the IEP block `reset()`, like `IEPCLK`.
* With `PWM_EFC_EN` set the model clears every `CMP_STATUS` bit at the start of
  the next IEP clock. This is the documented "auto clear the bit on next clock
  cycle". A hit on that same clock sets its bit again after the clear.
  Measured on LP-AM243 ICSSG0 (register level, CMP0 = 40, CMP1 = 20, 300
  jittered `CMP_STATUS` reads per run): with `PWM_EFC_EN = 1` the status was
  non-zero in only 3 to 5 % of the reads and was 0 at the end without a
  write-1-to-clear; with `PWM_EFC_EN = 0` it stayed set until the write. The
  clear therefore happens within about one IEP count; the exact delay is not
  resolved better than that.
* `IepTimer.add_compare_listener(callback)` delivers a `CompareEvent(index,
  count, tick)` for every compare hit, independent of `CMP_STATUS`. This is the
  seam for a PWM state machine (#72): the status flag may be gone, the event is
  not. `tick` counts IEP clocks processed with the counter enabled since the
  last hardware reset.

## Observed on silicon, not modelled here

These were measured on LP-AM243 ICSSG0 and LP-AM261 ICSSM0 at register level
and differ from the model. They are left for separate changes.

* **64-bit mode period is also N+1.** With `SHADOW_EN = 0`, CMP0 = N and reset
  on CMP0, the largest sampled counter value is N and N+1 distinct values are
  seen, the same as in shadow mode. The model's 64-bit mode still resets on the
  hit clock (period N) and its tests are unchanged.
* **Compare is greater-or-equal, not equality.** With the counter stopped at
  COUNT = 50, enabling CMP1 = 49 or 50 sets `CMP_STATUS` bit 1 and CMP1 = 51
  does not. With the counter running, writing CMP1 = 10 when COUNT is about 819
  sets the bit at once, and after a write-1-to-clear the level does not fire
  again. Only these cases were measured (not rollover, not the CMP0 reset
  interaction). The model fires on equality only. One visible consequence: after
  a running counter loads a CMP0 of 0 (see "Entering shadow mode"), silicon
  holds `COUNT` at 0, whereas the model lets it count upwards, because a
  compare of 0 never matches an incrementing count under equality.
* **A write to `CMPj_REG0` in 64-bit mode clears `CMP_STATUS` bit j** (for
  example 7 to 5). The model does not.
* **`IEP_COUNT_REG0` written with `0xFFFFFFFF` reads back `0xFFFFFFFF`**;
  the probe's counter clear therefore uses 0.
* **`GLOBAL_CFG` reset value on AM261x is `0x551`** (CNT_ENABLE set), on AM243x
  `0x550`. The model uses `0x550` on both.

## Limitations and open questions

* **Setup window.** The TRM asks for a new compare to be programmed 4 counts
  before the next rollover or reset. Measured on LP-AM243 ICSSG0 (register
  level; CMP0 = 100, new shadow 140, 400 trials in each of 3 runs, one value
  pair): every shadow store that reached the IEP up to about one count before
  the reset clock was loaded at that reset, and none after it, with no overlap
  in 1200 trials. There is no window of dropped stores 2 to 4 counts before the
  reset (uncertainty of about one count from the store-latency calibration).
  The model has no setup window either: a shadow write that precedes the reset
  clock is loaded at that reset, a later one at the next.
* **Status clear delay is model granularity.** The model clears the status on
  the next IEP clock; silicon was seen to clear within about one IEP count (see
  above) and the exact delay is not resolved. Firmware that polls `CMP_STATUS`
  at a core cycle may therefore never observe a flag when efficiency mode is on;
  use the compare event seam for model consumers.
* **COUNT_REG1 in shadow mode.** The counter is 32-bit; writes to COUNT_REG1
  are ignored and it reads 0 (not measured).
* **Rollover evaluation order.** On a 32-bit rollover the model loads the shadow
  copies first and compares the wrapped count against the new active values.
  Not measured.
* **Compare event seam.** `CompareEvent.tick` counts IEP clocks since hardware
  reset; it carries no shared-time stamp. Listeners are called synchronously
  while the IEP advances, so a listener must not rewrite IEP registers
  (`DEFAULT_INC`, `CNT_ENABLE`, `SHADOW_EN`) from the callback, and a snapshot
  restore re-emits events to the same listeners.
* Register offsets now follow main's #90 fix: CMP0..CMP7 use `0x78..0xB4`,
  RXIPG0/1 use `0xB8`/`0xBC`, and CMP8..CMP15 use `0xC0..0xFC` in both
  shadow and 64-bit modes. The randomized oracle uses an explicit offset
  table rather than the old contiguous formula. This is simulator-tested;
  only CMP0..CMP2 were touched in the shadow-mode silicon measurements.
* `IEP_COUNT_RESET_VAL_REG` (counter reset value), slow compensation, sync
  outputs and interrupt routing remain unmodelled.

## Not measured

No pad, waveform or PWM output was observed; everything above is register
level. Also not measured: pad timing and dead time, the IEP clock taken from
the OCP clock (`IEPCLK = 1`), `DEFAULT_INC` above 1, slow compensation,
compare behaviour during a rollover or together with the CMP0 reset, the
CMP8..CMP15 offsets, and AM263x silicon itself (LP-AM261 stands in for it). The
setup-window run used a single CMP0 value pair, and the status auto-clear run
used a single period and only CMP0/CMP1 with no PWM state machine attached.

## Tests

`tests/test_iep_shadow_compare.py` has hand-computed clock numbers, a naive
per-clock reference that does not call the model (random programs with bulk and
single-step advance, with and without the stopped-counter copy), a
legacy-vs-event-engine equivalence check for 64-bit mode, and firmware
`SBCO`/`LBCO` tests through `Simulator`. The tests for REG1 on entry, the
stopped-counter copy and the REG0 write quote the measured register words
(board and log line) in their docstrings; the remaining tests are hand
computation, not silicon evidence.
