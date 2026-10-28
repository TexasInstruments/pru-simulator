# ICSSG PWM module

Technical note for `perif/pwm.py` (upstream issue #72). It states what the model
does, what each statement rests on, and what is left open.

Two kinds of statement are made, and they are kept apart:

* **Measured on LP-AM243 ICSSG0 (register level).** The trip registers
  `ICSSG_PWM0..3` and the state words `ICSSG_PWMm_k` were read back from
  silicon after scripted writes, using the two software trip sources only
  (bit 20 = source 0 through POS_ERR, bit 19 = source 1 through OVER_ERR). The
  words are replayed in `tests/test_icssg_pwm.py` and kept in
  `tests/icssg_pwm_silicon_words.py`.
* **Assumption / simulator-tested only.** Everything about pads (levels, edge
  times, dead time), the glitch filter, the pin sources, trip sources 2..8,
  `TRIP_CMP0_EN` returning pads to the initial state and the legacy
  `PWM_EFC_EN` first-compare behaviour. No pad, waveform or PWM output was
  observed on silicon; these parts are a reading of the TRM and have not been
  independently verified.

## Sources

* AM64x/AM243x TRM, SPRUIM2J (April 2026):
  * 6.4.10 "PRU_ICSSG PWM Module", PDF pp. 680-683: features, states
    overview, Table 6-123 (entry events), Table 6-124 (state transitions),
    Table 6-125 (PWM to IEP CMP mapping), 6.4.10.3 trip state logic, 6.4.10.4
    glitch filter.
  * Registers `ICSSG_PWM0..3` (CFG `0x130..0x13C`, Tables 14-10357 to 14-10368)
    and `ICSSG_PWMm_k` state configuration (CFG `0x140..0x16C`, Tables
    14-10369 onward).
* `docs/iep_shadow_compare.md` for the IEP compare-event seam.
* Issue #72 silicon observations, and two ICSSG_PWM0 words read on an
  LP-AM243 after trips (`0x40402210`, `0x40420200`), cited as reported.
* Register-level probe on an LP-AM243 (ICSSG0) and an LP-AM261 (ICSSM0): the
  trip-register words and the device-gating reads described below.

## Availability

AM243x/AM64x (ICSSG) only. On AM261x/AM263x (ICSSM) there is no `Simulator.pwm`,
`io()["pwm_pins"]` is empty, and `set_pwm_trip_input` and
`export_pin_waveform(..., include_pwm=True)` raise `ValueError`.

**Simulator and silicon differ here, by design.** On LP-AM261 ICSSM0 silicon
the CFG words `0x130..0x16C` and `CFG+0x40` read as zero, with no bus error and
no hang (measured through the PRU and through JTAG; writes were not measured).
The simulator does not mimic that: a bus access to `0x130..0x16F` of an ICSSM
target raises `ValueError`. The fault is deliberate and conservative: it flags
firmware that uses an ICSSG-only peripheral, which would otherwise read zeros
and carry on. Do not read the fault as a silicon behaviour.

## Registers (CFG offset, firmware `SBCO`/`LBCO` through the memory bus)

`ICSSG_PWMn`, n = 0..3 at `0x130 + 4n`. Bits 20:0 are stored (writing
`0xFFFFFFFF` reads `0x001FFFFF`; measured on LP-AM243 ICSSG0). The same
behaviour applies to all four sets:

| bits | field | model |
|------|-------|-------|
| 7:0 | DEBOUNCE_VALUE | stored, 8 bits wide (measured); glitch filter timing is an assumption |
| 16:8 | TRIP_MASK | one bit per TRIP_VEC source 0..8; a **set bit enables** the source (measured on LP-AM243 ICSSG0 (register level) for sources 0 and 1) |
| 17 | TRIP_CMP0_EN | stored (measured); leaves TRIP_S/TRIP_VEC unchanged across IEP0 CMP0 events (measured); returning the pads to initial at CMP0 is an assumption |
| 18 | TRIP_RESET | **level**: reads back what was written, does not self-clear (measured). Clears TRIP_VEC while 1; does **not** clear TRIP_S |
| 19 | OVER_ERR_TRIP | software level for source 1 |
| 20 | POS_ERR_TRIP | software level for source 0 |
| 29:21 | TRIP_VEC | read-only, **one-hot**, one bit per asserted enabled source (measured) |
| 30 | TRIP_S | read-only latched status (direct writes ignored, measured); cleared by the bit 23 write below |

TRIP_VEC / TRIP_S behaviour, all measured on LP-AM243 ICSSG0 (register level):

* TRIP_VEC tracks **every asserted enabled source** while any is asserted
  (`0x003` when both software sources are asserted in one write) and **freezes
  at its last value** when all of them deassert, until TRIP_RESET. There is no
  priority and no first-cause latch.
* TRIP_S sets when TRIP_VEC becomes non-zero. It is **not** cleared by
  TRIP_RESET (issue #72's observation). While TRIP_RESET is 1 no trip is
  latched and TRIP_VEC reads 0, even with a source asserted; releasing it with
  the source still asserted trips again at once.
* A write with **bit 23 = 1** clears TRIP_S, but only while TRIP_VEC is 0 or
  bit 18 is 1 in that write; it is ignored while TRIP_VEC is latched. Bit 23
  clears TRIP_S together with any other field in the same word. The other
  TRIP_VEC bits (21, 22, 24..29) and bit 30 have no effect when written.
  The sequence that clears a latched TRIP_S, source low, is
  `bit18=1` -> `bit18=0` -> `bit23=1` -> `0`. (Bit 23 together with bit 18 and a
  *latched* TRIP_VEC in one write was not measured; the model clears.)
* PWM1, PWM2 and PWM3 behave exactly like PWM0 for all of the above.

`ICSSG_PWMm_k` (set m, phase k) at `0x140 + 4(3m+k)`: only bits 11:0 are stored
(`0xFFFFFFFF` reads `0x00000FFF`; measured). INIT POS `[1:0]` NEG `[3:2]` (0 Z,
1 L, 2 H); TRIP POS `[5:4]` NEG `[7:6]` (same encoding); ACTIVE POS `[9:8]` NEG
`[11:10]` (0 toggle, 1 L, 2 H). A field written with 3 reads back 3 (measured);
what encoding 3 drives is an assumption (below).

## Supported semantics

* **State machine** per set, shared by its six pads (TRM 6.4.10.2): hardware
  reset, trip reset and an enabled IEP0 CMP0 return to *initial*; the first
  CMPx event of the set enters *active*; a trip enters *trip*. Compare events
  are consumed through `IepTimer.add_compare_listener`, so
  `ICSSG_SA_MX_REG.PWM_EFC_EN` clearing `CMP_STATUS` has no effect on the PWM.
* **Compare to pad** (Table 6-125): IEP0 CMP1..CMP6 drive PWM0 pads 0..5
  (`CMP1` = PWM0_0_POS, `CMP2` = PWM0_0_NEG, ...), CMP7..CMP12 drive PWM1.
  Pad index is `set*6 + phase*2 + (1 if NEG)`. A compare acts only on its own
  pad. In the trip state compare events are ignored. CMP13 and CMP14 drive no
  PWM0/1 pad (the pad side of PWM2/3 is not modelled).
* **Dead time.** The TRM defines no dead-time field or counter. A dead time is
  the difference between the two IEP compare values that firmware programs for
  a complementary pair, and edges land on the IEP clock of the compare hit. The
  model adds no delay of its own.
* **Trip sources** (TRIP_VEC sources): 0 position error (software bit 20 or
  the external pin), 1 overcurrent (software bit 19 or pin), 2..4 SD short per
  phase, 5 `PWMn_TZ_IN` (trip_e2), 6..8 per-phase debounced trip (trip_e1_x).
  External levels are driven with `Simulator.set_pwm_trip_input(set, source,
  level)`; this is a pad stimulus and never appears in R31. It is a Python API
  only: there is no MCP tool or web-UI control for it.
* **Trip latching** (registers measured, see above). Sources are level
  sensitive and only enabled sources (mask bit set) can trip. A source that is
  still asserted when TRIP_RESET is released trips the set again at once. The
  pad state machine enters *trip* when TRIP_VEC first becomes non-zero.
* **Trip recovery.** A write with TRIP_RESET = 1 returns the pad state machine
  to *initial* and applies the initial pad levels (pad side: assumption). An
  enabled CMP0 (from trip or active) returns the pad state machine to *initial*
  and applies the initial pad levels; it leaves TRIP_S/TRIP_VEC alone (the
  register part is measured; whether the real pad state machine leaves the trip
  state at CMP0 was not measured). TRIP_S itself is cleared only by the bit 23
  sequence above.
* **Glitch filter.** Applies to sources 5..8. A rising level counts once it has
  stayed high for DEBOUNCE_VALUE core clocks; a pulse of exactly that width
  passes and a shorter one is rejected. The core clock is converted to IEP
  clocks with the current IEP rate, rounded up (5 core clocks at a 100 MHz IEP
  and 200 MHz core is 3 IEP clocks). A value of 0 bypasses the filter. The
  falling edge is not delayed. Expired deadlines are applied whenever the
  module is looked at: any PWM register access, compare event, `Simulator.io()`,
  `pwm_pins`, the VCD sampler, the edge log or the public `IcssgPwm.sync()`.
* **Configuration writes** to a state register move the affected pads at once
  when the set is in initial or trip; in the active state the new action is
  used at the pad's next compare.
* **Pads.** `Simulator.io(core)["pwm_pins"]` has 24 entries (`None` = Z, 0, 1);
  the web UI I/O panel shows them as a text row, and
  `export_pin_waveform(..., include_pwm=True)` adds `pwm_<n>` signals (`z` for
  Z). VCD samples after each retired instruction, so an edge is placed at the
  core cycle that follows it. `IcssgPwm.edges` logs `(IEP clock, pad, level)`;
  it keeps the **last 4096 edges** (`EDGE_LOG_DEPTH`, or the `edge_log_depth`
  constructor argument) and `IcssgPwm.edges_dropped` counts the older ones.
  R31 is never changed by pad levels (only the 12 live pads of PWM0/1 can be
  driven; PWM2/3 pads stay Z: the pad state machine of those sets needs IEP1,
  which is not modelled, so their pad side is unmodelled).
* **Reset and snapshots.** `IepTimer.hardware_reset()` (and so
  `Simulator.hard_reset()`) resets all PWM registers, timers, state and pads.
  Externally held trip pad levels survive the reset (a pad stays where it is);
  held filtered pads restart their filter and trip again once enabled.
  `IcssgPwm.snapshot()/restore()` are used by the UI snapshot and include held
  pads and pending debounce timers.
* **Cost.** The IEP compare listener is registered on the first non-zero write
  to a PWM register, the first trip input, or the first time `IEP_CMP_CFG`
  enables any of CMP1..CMP12 (also when restoring a snapshot), not at
  construction, because a registered listener makes the IEP step clock by clock
  (about 8 us per compare hit in the benchmark of the review; a 200 M clock
  second is on the order of 15 s). Programs that never touch the PWM and never
  enable CMP1..CMP12 keep the fast path. Firmware that leaves the reset-default
  state words (Z, toggle) in place and only enables CMP1..CMP12 therefore still
  sees its pads toggle. Compare hits before the listener is registered are not
  seen by the module. For programs that configure the PWM before the IEP runs,
  behaviour is identical to an eagerly registered listener (25 seeded
  differential tests).

## Where the TRM is silent or ambiguous (assumptions)

Items the TRM leaves open and the register probe settled are listed first, with
the result; the rest remain assumptions.

* **TRIP_VEC encoding: one-hot.** Measured on LP-AM243 ICSSG0 (register level):
  source 0 reads `0x001`, source 1 reads `0x002` (an index would read 0 and 1),
  and two sources read `0x003`.
* **TRIP_MASK polarity: a set bit enables.** Measured on LP-AM243 ICSSG0
  (register level) for sources 0 and 1: mask 0 plus OVER_ERR does not trip,
  mask bit 1 plus OVER_ERR trips, the crossed combinations do not. Sources 2..8
  are assumed to follow the same mask.
* **TRIP_S** is R/W in the TRM. Measured on LP-AM243 ICSSG0 (register level):
  direct writes have no effect; TRIP_RESET does not clear it; a bit 23 write
  does (see Registers).
* **Priority** among simultaneous sources: none exists on silicon (TRIP_VEC
  holds all asserted enabled sources).
* **Reserved state encoding 3** is stored (measured). What it drives is an
  assumption: Z in the initial and trip states, hold in the active state. The
  TRM defines only 0/1/2.
* **TRIP_CMP0_EN not changing TRIP_S/TRIP_VEC across CMP0 events**: measured on
  LP-AM243 ICSSG0 (register level) with hundreds of CMP0 events and the source
  held or dropped. Whether CMP0 returns the pads to the initial state was **not
  measured** (no pad was observed); the model does so as an assumption.
* **"All I/Os of that PWMm set go into an active state"** is read as: the set
  state changes on the first compare and each pad takes its active action at
  its own compare. Whether the other pads also act on the first event is not
  stated. The alternative reading (the first event applies the active action to
  all six pads) would change every other pad of the set at the first compare and
  remove the dead time between CMP1 and CMP2 of a complementary pair.
* **PWM_EFC_EN.** ICSSG_SA_MX_REG (Table 14-10188) says: "1 Efficiency mode:
  PWM SM will go from IDLE to ACTIVE and at the same time PWM output will get
  updated during this SM state transition and IEP CMP flags will get auto HW
  cleared". Legacy mode (reset value 0) therefore may differ at the
  initial-to-active transition; that first-compare pad behaviour is
  **unmodelled** and not measured. `IcssgPwm.efficiency_mode` exposes the bit and
  is a documented no-op for the pads (edge lists are identical for both values
  in the tests). The CMP_STATUS auto-clear itself is an IEP matter.
* **Toggle of a Z pad** is undefined; the model drives H.
* **CMP0** is applied to sets that are not in the initial state.
* Order inside one IEP clock: CMP0 is delivered before CMP1..CMP12, and a
  glitch-filter deadline on the same clock is applied before the compare.

## Silicon observations in #72 and what the model does with them

* **TRIP_S not cleared by TRIP_RESET; bit 23 write clears it.** Modelled, as
  measured on LP-AM243 ICSSG0 (register level) for PWM0..PWM3 with the software
  sources: TRIP_RESET clears TRIP_VEC only, and a bit 23 write clears TRIP_S
  under the conditions in the Registers section. Sources 2..8 were not
  exercised; the model applies the same rule to them.
* **TRIP_CMP0_EN not clearing TRIP_S/TRIP_VEC at CMP0.** Modelled as measured
  (status bits unchanged across CMP0 events); the pad side is an assumption.
* **TZ_IN needs RXACTIVE=1 and a floating pad reads high.** Pad configuration
  is not modelled; `set_pwm_trip_input` drives the already-valid internal
  signal.
* **Pad not delivered to R31.** Followed: pad levels are not looped into R31.

## Limitations

* **PWM2/PWM3 have no pad side.** Their trip registers behave like PWM0's
  (measured), but the pad state machine of those sets is driven by IEP1, which
  this simulator does not model: no pads, no state, no trip effect on pads.
* Sigma-delta fast detect, SD overcurrent and position-feedback trips have no
  hardware source in the simulator; only the pin hook and software bits exist.
* The debounce timer runs on IEP clocks, so it does not advance while the IEP
  counter is disabled.
* Compare events for CMP8..CMP15 are delivered by index. Firmware access to the
  CMP8..CMP15 registers still uses the pre-existing offsets noted in
  `docs/iep_shadow_compare.md`; that bug is unchanged.
* Trip inputs can only be driven from Python (`set_pwm_trip_input`); the MCP
  server and the web UI have no control for them.
* ICSSM targets fault on the PWM CFG words where silicon reads zero (see
  Availability).

## Not measured on silicon

Any pad, waveform or PWM output state; pad edge time against compare count;
dead time at the pad; toggle parity after an IEP halt; glitch-filter width and
timing at the pad; the trip-output latency; the TZ and pin trip sources and
trip sources 2..8; `TRIP_CMP0_EN` returning the pads to the initial state; the
legacy-mode (`PWM_EFC_EN` = 0) first-compare pad behaviour; concurrent IEP1
operation; true power-on reset values of the PWM registers (JTAG reset does not
reset the PWM block; the first reads of the probe session were all zero);
writes to the ICSSM CFG words. The model places pad edges on the IEP clock of
the compare hit and applies trips at the register write or filter expiry; that
part is simulator-tested only.

## Tests

`tests/test_icssg_pwm.py`: firmware `SBCO`/`LBCO` tests with hand-computed IEP
clock edges and dead time (simulator-tested only); a word-for-word replay of
the measured register sequences (`tests/icssg_pwm_silicon_words.py`, three
contiguous silicon runs: mask polarity, one-hot VEC, both sources, level-type
TRIP_RESET, sticky TRIP_S, the bit 23 clear and its conditions, CMP0_EN, direct
TRIP_S/VEC writes, the state words, PWM1..PWM3); each trip behaviour in its own
test; every source 0..8 against its own mask bit; CMP0 and TRIP_S; debounce
boundaries, 8-bit width and rounding for sources 5..8; deadlines seen without
PWM register access (io, edge log, VCD); simultaneous compares; CMP1..CMP14
mapping; PWM2/3 registers; ICSSM faulting by design; reset with a held pad;
snapshot replay; the R31 check on PRU0 and PRU1 with all live pads high; lazy
listener differential tests; UI state push.
