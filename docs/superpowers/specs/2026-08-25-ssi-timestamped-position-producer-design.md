# Timestamped SSI position producer design

## Problem

Application positions can be formed every 960 ns, while a 12-bit/4 MHz SSI
request completes roughly every 16 us. PRU0 cannot and should not transmit all
intermediate values. It needs a coherent time series from which it can prepare
the position corresponding to the next SSI request boundary.

## Scope

- PRU0 remains the encoder emulator; PRU1 remains the SSI clock master/reader.
- IEP0 is a shared 300 MHz timestamp domain.
- The producer is independent of the PWM algorithm and accepts signed Q31.32
  unwrapped positions.
- The PRU estimator supports a deliberately bounded deterministic subset:
  binary or reflected Gray, integer Q31.32 values up to 31 position bits,
  1..64-bit frames, explicit/right-aligned placement, and zero padding.
- The sample ABI has no per-sample status or Gray-excess window metadata;
  those dynamic layouts are rejected explicitly. Static prepacked frames
  retain the complete generic layout support.
- Static prepacked 1..64-bit operation is unchanged.

## Design

The producer publishes one 32-byte seqlocked entry, then publishes a 16-byte
head seqlock. PRU0 reads only the head, newest slot, and predecessor. It checks
coherence, adjacency, sample generation, age, request horizon, arithmetic
range, and wire range. During `tm`, it predicts the next request timestamp from
the two previous request edges and linearly estimates the corresponding
position. At the next edge it captures the actual IEP timestamp and starts the
existing SSI bit loop.

Failure never fabricates an all-ones sample. PRU0 keeps the last prepared frame
and publishes a status bit plus counters. The explicit SSI fault injector is
the only path that intentionally emits configured invalid wire responses.

The simulator host provides constant, linear, and triangle trajectories. The
dashboard and MCP API express controls in encoder counts and counts/second and
convert them to Q31.32 per IEP tick. The CCS host exposes a publication API
that accepts either Q31.32 or integer counts with an explicit IEP timestamp.

## Testing

- IEP register mapping, enable/disable, rollover, reset, and multicore pacing.
- Sample/head seqlock ordering, wrap, catch-up, and exact overwrite accounting.
- Oracle arithmetic for signed motion, rounding, age/horizon, and range.
- Paired PRU constant/linear motion, stale fallback, generation switch,
  overrun, rollover, 960 ns cadence, 4 MHz phase widths, and edge latency.
- Dashboard WebSocket and MCP configure/start/stop/step/read parity.
- Parent CCS source-contract tests for c26/c28, IEP initialization, generated
  ABI, publication fences, UART commands, and portable include paths.
