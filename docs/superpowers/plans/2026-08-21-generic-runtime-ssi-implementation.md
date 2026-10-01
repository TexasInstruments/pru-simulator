# Generic runtime SSI implementation - verification plan

## Objective

Deliver a generic, runtime-configurable SSI loopback in the simulator and
keep the parent AM243x CCS project aligned with the same ABI and reversed
roles: PRU0 is the emulator and PRU1 is the reader/master.

## Performance constraints

- Keep all position encoding and validation in the host/control plane.
- Cache configuration at generation boundaries; do not read shared control
  memory in the per-bit loops.
- Use fixed loop bounds and cycle-derived timing in PRU assembly.
- Publish mailbox/trace data after acquisition without delaying the next
  clock request beyond the configured protocol timing.
- Reject overflow rather than masking or silently truncating values.

## Work status

- [x] Reverse simulator roles and update the two virtual wires.
- [x] Keep the generated ABI and `c28` shared-memory mapping synchronized.
- [x] Implement frame widths through 64 bits, sequence holds, faults, and
  idle-boundary generation changes.
- [x] Add host-side profiles, binary/Gray/Gray-excess/Tannenbaum packing,
  alignment, padding, status fields, and range validation.
- [x] Add synchronous formation gating and `Tp` validation.
- [x] Add seqlock mailbox, trace ring, MCP operations, and dashboard controls.
- [x] Make dashboard Apply a single validated configuration/frame transaction.
- [x] Replace stale GPIO topology on generic load and display the actual
  PRU1-clock/PRU0-data bindings.
- [x] Make toolbar Run/Step/SIM/Reset pair-aware for the loaded generic SSI
  runtime and select PRU1 in the second register panel.
- [x] Add request IDs and view-baseline resets to both memory panels so late
  responses cannot replace a newer absolute-address window.
- [x] Add parent CCS PRU0/PRU1 projects and the R5 staged-apply/UART layer.
- [x] Keep staged edits separate from active mailbox/trace decoding until both
  PRU acknowledgements complete; validate `tv < sample < high` in both hosts.
- [x] Synchronize source READMEs, project reports, handoff, and session
  report.
- [ ] Build the TI assembly and R5 projects in CCS.
- [ ] Run LaunchPad logic-analyzer and UART acceptance tests.

## Verification commands

```text
python -m pytest -q tests/test_ssi_config_abi_generated.py tests/test_ssi_runtime.py tests/test_ssi_generic_emulator.py tests/test_ssi_generic_reader.py tests/test_ssi_runtime_ui.py tests/test_ssi_runtime_trace.py tests/test_mcp_server.py
node --check ui/static/app.js
python -m pytest -q encoder-workspace/firmware/ccs-tests/test_ssi_project.py
```

The focused simulator suite and parent static contract suite must pass before
the hardware handoff. A complete simulator run is also required; unrelated
pre-existing failures must be recorded separately instead of being hidden.

Latest repository validation: 96 SSI-focused simulator tests pass, 1,342
tests pass in the complete simulator suite, and the only failure is the
pre-existing peripheral drift experiment documented in the session report.

## Hardware handoff

CCS must generate `pru0_ssi_emulator_load_bin.h` and
`pru1_ssi_reader_load_bin.h`, build the R5 host, load PRU1 before PRU0, and
confirm both acknowledgements. Physical wiring is BP.11 -> BP.51 for clock
and BP.33 -> BP.57 for data. Runtime changes should be applied through the
staged UART control path and checked against raw logic-analyzer frames.
