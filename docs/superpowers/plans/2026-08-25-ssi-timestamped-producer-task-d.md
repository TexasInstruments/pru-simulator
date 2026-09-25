# SSI timestamped producer / PRU0 estimator — Task D

## Initial implementation request

Integrate the accepted timestamped producer with the simulator `SSIRuntime`,
implement the actual PRU0 ring consumer and request-time estimator, initialize
and read the IEP consistently in the paired PRU programs, and add end-to-end
tests. Keep the PRU path minimal, deterministic, cycle-bounded, and compatible
with the existing default static 12-bit/4 MHz sequence. Do not extend this
task into the UI, MCP, or parent CCS/hardware projects.

## Role and timing contract

- PRU0 is the reactive encoder emulator: clock input `R31.8`, data output
  `R30.0`.
- PRU1 is the SSI reader/master: clock output `R30.0`, data input `R31.16`.
- PRU1 starts first, holds clock high, enables `IEPCLK.OCP_EN` through `c4`,
  and enables IEP0 through `c26` with `0x11` (300 MHz, increment 1).
- PRU0 performs ring reads, validation, period/age/horizon arithmetic, and
  frame preparation during idle `tm`. The request falling edge only captures
  COUNT_LO followed by latched COUNT_HI and enters the existing bit loop.
- The producer may publish every 288 IEP ticks (960 ns), while the default
  SSI request is approximately one frame per 16 us.

## Implementation checklist

- [x] Keep static mode as the default and preserve the prepacked 1..64-bit
  frame path.
- [x] Connect `SSIPositionProducer` to `SSIRuntime` without implicitly
  starting timestamped publication on `Apply`.
- [x] Read the coherent producer head and only the newest and predecessor
  ring slots; do not scan 256 entries.
- [x] Add bounded period measurement, sample age/horizon checks, generation
  checks, seqlock retries, and ring-progress overrun detection.
- [x] Use signed Q31.32 samples and bounded shift/add plus restoring-divide
  arithmetic for the safe integer-aligned subset.
- [x] Reject fractional payloads and unsupported layouts with explicit status
  bits instead of truncating or corrupting a frame.
- [x] Hold the last prepared frame on no-pair, stale, horizon, generation,
  coherence, overflow, missed-preparation, or overrun failure.
- [x] Keep producer-owned head fields live; PRU0 diagnostics must not rewrite
  the input seqlock.
- [x] Add diagnostics for accepted samples, retries, stale samples, ring
  overruns, request timestamp, estimate, status, generation, and observed
  head.
- [x] Add paired tests for static compatibility, constant motion, positive and
  negative motion, 960 ns publication, stop/stale fallback, generation
  switching, ring wrap, fractional rejection, IEP rollover, and no mixed
  generation frame.
- [x] Add parser/execution coverage and measure the timestamped edge-to-first
  data transition and 4 MHz clock phase widths.
- [x] Update the source READMEs, project reports, and this plan.
- [x] Add UI/MCP controls in the integration task.
- [x] Port the accepted simulator implementation to the parent CCS/hardware
  projects, including the R5 publication API and synthetic bring-up source.

## Safe arithmetic and dynamic packing subset

The PRU estimator accepts binary or reflected Gray encoding, integer-aligned
Q31.32 positions up to 31 bits, and 1..64-bit frames. Position placement may
be explicit or right-aligned, and zero padding is preserved. The frame is
prepared in two 32-bit words during `tm`; the SSI edge/bit loop is unchanged.

The 32-byte sample ABI carries no per-sample status value or Gray-excess
window metadata. Nonzero status/error fields, Gray-excess, Tannenbaum semantic
formation, fractional payloads, and out-of-range values are rejected with an
explicit diagnostic and the last prepared frame is held. Static prepacked
frames continue to support the full 1..64-bit generic layout.

## Verification record

The Task D contract suite is `tests/test_ssi_task_d.py`. It uses the AM243x
300 MHz simulator memory map, paired GPIO wires, the generated ABI, and the
same IEP c4/c26 mapping used by the assembly. Focused results and measured
cycle values belong in the corresponding session report under
`docs/reports/`.
