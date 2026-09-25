# Generic runtime-configurable SSI handoff

This handoff records the simulator and parent hardware slices for the
generic SSI interface. The current core assignment is fixed throughout:

- **PRU0** is the reactive encoder emulator. It receives clock on `R31.8`
  (LaunchPad BP.51) and drives data on `R30.0` (BP.33).
- **PRU1** is the SSI master/reader. It drives clock on `R30.0` (BP.11) and
  samples data on `R31.16` (BP.57).
- Loopback wiring is BP.11 -> BP.51 for clock and BP.33 -> BP.57 for data.

## Implemented simulator behavior

The generated ABI is shared by both PRU programs and the host runtime:

- configuration/handshake: `0x0000-0x00FF`;
- 16 prepacked frame slots: `0x0100-0x01FF`;
- seqlock latest-sample mailbox: `0x0200-0x023F`;
- capture counters: `0x0240-0x027F`;
- 1024 records of 24 bytes at `0x0400`.

`c28` maps the ICSS shared RAM for both programs. The PRU loops cache the
configuration per generation; generation changes are acknowledged and
applied only at idle frame boundaries.

The Python `SSIRuntime` layer is the simulator equivalent of the R5 control
plane. It provides named profiles, staged validation, explicit Apply,
natural-position packing, semantic readback, raw-frame access, mailbox
seqlock reads, and trace-ring reads. The UI and MCP surfaces use this same
runtime rather than duplicating the protocol.

The default profile is the legacy 12-bit binary loopback with the sequence
`ABC, AAA, BCA, 12A, CC2`. Named profiles cover the documented frame
families. Gray, Gray-excess, alignment, padding, status fields, and overflow
checks are host-side so the PRU edge loops remain short and deterministic.
Tannenbaum currently uses profile-supplied field layout and pass-through
position packing; it is not inferred inside the PRU loop.

Synchronous formation is modeled at the emulator's idle frame boundary. A
configured formation pause is completed before the next slot is loaded, and
the reader validates that `Tp` leaves enough high-idle time. Fault modes,
finite fault repeats, frame/time holds, 64-bit frames, mailbox seqlocks, and
trace overwrite/overrun accounting are implemented.

## UI procedure

1. Start `python ui/server.py` and open `http://localhost:8080`.
2. In **Generic SSI Runtime**, click **Load PRU0 emulator + PRU1 reader**.
3. Select a profile and set frame/timing/formation fields.
4. Enter natural positions, optionally set Gray-excess count/offset, and
   click **Pack positions**. The default positions are already filled in.
5. Choose the capture mode. **Stage** only stages a proposal for inspection;
   **Apply atomically** sends the complete layout and raw-frame sequence in
   one transaction. The server validates the whole request before changing
   the active generation. A value that exceeds the selected resolution is
   rejected rather than truncated. After Stage, the profile selector and
   numeric fields show the staged proposal; the active generation and PRU
   acknowledgements remain unchanged until Apply succeeds.
6. Click **Multi-core** if you want both register panels. Loading the generic
   pair selects **PRU1** as the second panel automatically, so the pair view
   shows the reader and emulator rather than the unrelated RTU0 core.
7. Enable Signal Graph recording when waveforms are needed, click **Run pair**
   or the toolbar **Run**, and click **Refresh** to inspect mailbox and trace
   counters. The toolbar Run/Step/SIM/Reset actions use the PRU1-reader /
   PRU0-emulator pair while the generic pair is loaded.
8. The two memory panels use absolute/global addresses: PRU0 DRAM is
   `0x00000000`, PRU1 DRAM is `0x00002000`, and shared RAM is `0x00010000`.
   Reads carry a panel request ID, so switching regions cannot be overwritten
   by a late response from the previous region.

Loading also removes any stale manually-created GPIO wires before installing
the exact default pair:

```text
PRU1:GPO0 -> PRU0:GPI8    SSI clock
PRU0:GPO0 -> PRU1:GPI16   SSI data
```

The Generic SSI Runtime panel displays the active wire list. If a hard reset
is needed, its button reloads the generic pair afterward; a normal Reset
resets both PRUs without discarding the runtime configuration.

## MCP/API procedure

The equivalent operations are available through the simulator's MCP server:
list profiles, stage a profile and overrides, set semantic positions, apply,
read the latest mailbox, and read the trace ring. The `ssi_runtime_positions`
UI action and the MCP position operation both use `SSIRuntime.set_positions`.

## Parent hardware slice

The matching CCS project is in
`encoder-workspace/firmware/ccs-tests/ssi_test`:

- `pru0_ssi_emulator` contains the PRU0 image;
- `pru1_ssi_reader` contains the PRU1 image and owns the 300 MHz clock setup;
- `include/ssi_config_abi.h` and `.inc` are checked-in ABI snapshots;
- `ssi_runtime.c` provides R5 validation, packing, generation apply, mailbox
  readback, and UART command parsing;
- the R5 load order is PRU1 first, then PRU0, followed by an acknowledged
  Apply.

The TI PRU/ARM compiler and SDK are not available in this repository's
current validation environment. CCS must still build both assembly projects,
run their post-build header generation, build the R5 project, and validate
the physical BP.11/BP.51 and BP.33/BP.57 wiring with a logic analyzer.

## Verification snapshot

The focused simulator/runtime/UI suite currently passes 96 tests, including
25 dashboard/server regressions. JavaScript syntax validation also passes with
`node --check ui/static/app.js`. The full simulator suite has one unrelated
pre-existing failure in
`tests/test_perif_drift_experiment.py::test_roundtrip_with_no_host_register_setup`;
the SSI-focused tests pass.

The parent static CCS contract suite passes 13 tests. No repository commit or
push is part of this handoff.
