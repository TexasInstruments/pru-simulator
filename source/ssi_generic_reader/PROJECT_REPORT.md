# ssi_generic_reader - Project Report

Runtime-configurable SSI master/reader for **PRU1**. It generates the SSI
clock, samples the PRU0 emulator data line, extracts structural fields, and
publishes coherent mailbox and trace records.

## 1. Initial prompt

Implement a generic PRU1 reader with configurable 1..64-bit frames, 300 MHz
cycle timing, structural position/status extraction, atomic generation
switching, seqlock mailbox publication, and optional trace capture.

## 2. Design choices

### 2.1 Active clock master

PRU1 drives `clock_high_cycles` and `clock_low_cycles` directly as LOOP-body
counts. The balanced bit path adds a fixed 11-cycle high overhead and 4-cycle
low overhead, so the effective bit period is `high + low + 15` cycles. The
clock stays high between frames for `tp_pause_outer_iters *
SSI_PAUSE_INNER_ITERS`. No debounce is needed because the reader owns the
request timing.

### 2.2 Deterministic sampling

Each bit is sampled after the configured `sample_delay_cycles`; host-side
validation enforces `tv_cycles < sample_delay_cycles < clock_high_cycles`.
The hot loop only shifts the two 32-bit accumulators and samples the input.

### 2.3 Structural extraction only

The PRU extracts `position_value` and `status_bits` by offset and width. It
does not spend cycles decoding Gray, Gray-excess, or Tannenbaum. The host
runtime performs semantic decode after reading the mailbox.

### 2.4 64-bit frames and publication

The low/high accumulators support fields that cross the 32-bit boundary. A
mailbox write uses odd sequence -> all sample fields -> matching even
sequence, so R5 or simulator consumers retry rather than accepting torn data.
Trace capture appends 24-byte records to the 1024-entry ring and increments
the monotonic overrun counter after wrap.

### 2.5 Atomic generation changes

PRU1 checks `requested_generation` at its idle boundary. It waits for PRU0's
acknowledgement in loopback mode, applies the complete new configuration,
then acknowledges its own generation. Reader-only mode skips the PRU0 wait.
This keeps a frame on one generation and avoids mixed timing/layout fields.

## 3. Signal and memory contract

| Signal | PRU1 role | Connection |
|---|---|---|
| SSI clock | `R30.0` (GPO0), output | PRU1 `R30.0` -> PRU0 `R31.8` |
| SSI data | `R31.16` (GPI16), input | PRU0 `R30.0` -> PRU1 `R31.16` |

The program maps `c28` to the shared ABI. It reads configuration at `0x0000`
and frame-independent results are published at mailbox `0x0200` and trace
buffer `0x0400` relative to the ABI base.

## 4. Dashboard and files

The dashboard loads this program on PRU1, the generic emulator on PRU0, and
installs the two virtual wires. The Generic SSI Runtime panel stages a
configuration, semantic positions or raw frames, and capture settings before
one validated configuration/frame Apply transaction. Generic loading replaces
stale GPIO wires; the pair-aware toolbar and register view keep PRU1 as the
reader/master instead of defaulting to RTU0.

Important files:

| File | Purpose |
|---|---|
| `ssi_generic_reader.asm` | PRU1 deterministic SSI master/reader |
| `README.md` | Build and simulator usage |
| `tests/test_ssi_generic_reader.py` | Sampling, extraction, mailbox, and trace tests |
| `pru_io/ssi_runtime.py` | Host validation and semantic decode |
| `ui/server.py` / `ui/static/*` | Dashboard workflow |

## 5. Verification

The focused SSI/runtime/UI suite covers:

- binary, Gray, Gray-excess, and Tannenbaum host-side packing/decode;
- 1..64-bit frame extraction, including fields crossing bit 32;
- alignment, padding, status bits, sentinel/fault responses, and overflow
  rejection;
- deterministic configured high/low/sample timing, the fixed 15-cycle bit
  overhead, `tm`, `Tp`, and synchronous
  formation timing;
- generation switching, reader-only operation, mailbox seqlock coherence,
  trace wrap/overrun, MCP parity, and dashboard parity.

Run the focused tests with:

```text
python -m pytest -q tests/test_ssi_config_abi_generated.py tests/test_ssi_runtime.py tests/test_ssi_generic_emulator.py tests/test_ssi_generic_reader.py tests/test_ssi_runtime_ui.py tests/test_ssi_runtime_trace.py tests/test_mcp_server.py
```

The current focused result is 96 passing tests. One unrelated peripheral
drift experiment remains a pre-existing failure in the complete repository
suite.

## 6. Hardware adaptation

The matching CCS/R5 project is in
`encoder-workspace/firmware/ccs-tests/ssi_test`: PRU1 is the reader/master
and PRU0 is the emulator, both targeted at 300 MHz. The ABI snapshot and R5
staging API are present there. TI compiler/SDK build and physical LaunchPad
acceptance remain to be run in CCS.
