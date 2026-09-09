# Capability inventory

This is the present-tense, code-derived inventory for the simulator.  A row
marked **supported** names both the implementation and an exercising test.  A
row marked **partial** names the implemented boundary and the missing path.  A
row marked **not supported** names the dispatch or registration path that makes
the feature unavailable.  This document describes the current `main` tree,
not unmerged work.

<!-- inventory:xfr-supported=0,10,11,12,15 -->
<!-- inventory:device-models=AM243x,AM263x -->
<!-- inventory:cores=pru0,pru1,rtu0 -->

The metadata above is deliberately machine-readable.  `tests/test_capability_inventory.py`
derives each set from the running code/configurations and fails if this document
claims a different set.

## Supported

| Area | What exists | Implementation | Exercising test |
| --- | --- | --- | --- |
| PRU core and ISA | The parser and execution engine implement ALU, branch/bit, load/store, control, XFR, and MVI instruction families. | `core/parser.py`, `core/pru_core.py`, `core/alu.py`, `core/branch.py` | `tests/test_alu.py`, `tests/test_branches.py`, `tests/test_isa_execution.py`, `tests/test_mvi.py`, `tests/test_pru_core.py` |
| Cores | The simulator instantiates `pru0`, `rtu0`, and `pru1`; `pru1` uses swapped local DRAM mapping. | `simulator.py` (`Simulator.__init__`); `core/pru_core.py` (`_map_data_addr`) | `tests/test_pru1_core.py`, `tests/test_dram_mapping.py`, `tests/test_capability_inventory.py` |
| Configured device models | The supplied configuration models are `AM243x` and `AM263x`.  Their memory regions load from their `[device]` configuration files. | `config/memory_am243x.cfg`, `config/memory_am263x.cfg`; `simulator.py` (`_load_memory`, `_get_device_config`) | `tests/test_integration.py::TestSimulatorBasic::test_load_from_real_config`, `tests/test_capability_inventory.py` |
| Memory and constants | Configured memory regions, latency/jitter accounting, constant-table addressing, and PRU local-DRAM mapping exist. | `mem/memory_bus.py`, `mem/regions.py`, `mem/constant_table.py`, `core/pru_core.py` | `tests/test_memory.py`, `tests/test_dram_mapping.py`, `tests/test_pru_core.py` |
| ELF loading | TI PRU ELF32 loading feeds text, data, and symbols to a core. | `core/elf_loader.py`, `simulator.py` (`load_elf`) | `tests/test_elf_loader.py`, `tests/test_mcp_elf_load.py` |
| XFR scratchpads | XIN/XOUT/XCHG operate on SPAD banks `10`, `11`, `12` and IPC scratchpad `15`; the XFR shift mode exists for the SPAD banks. | `xfr/xfr_bus.py`, `core/pru_core.py` | `tests/test_xfr.py`, `tests/test_pru_core.py` |
| XFR MAC | Broadside MPY/MAC accelerator device ID `0` exists. | `xfr/mac_accelerator.py`; registration in `core/pru_core.py` | `tests/test_mac_accelerator.py`, `tests/test_integration.py::TestMACIntegration` |
| GPIO and opt-in I2C model | R30/R31 20-bit GPIO, grouped loopback, and an opt-in TCA9538 model on pins 0/1 exist. | `pru_io/io_port.py`, `pru_io/tca9538.py`, `simulator.py` (`i2c_attach`) | `tests/test_io_port.py`, `tests/test_io_loopback.py`, `tests/test_tca9538.py`, `tests/test_i2c_tca9538_firmware.py` |
| Sigma-delta filter | Three SD channels, pattern generators, SD registers, fast detect, and the R30/R31 interface exist. | `pru_io/sd_filter.py`, `pru_io/sd_channel.py`, `pru_io/sd_modulator.py`, `pru_io/sd_registers.py` | `tests/test_sd_filter.py`, `tests/test_sd_fast_detect.py`, `tests/test_sd_registers.py`, `tests/test_sd_e2e.py` |
| Peripheral Interface | Two three-channel Peripheral Interface blocks (PRU0 and PRU1), their registers, TX/RX FIFOs, timed loopback, and GPCFG mux gating exist. | `perif/peripheral_interface.py`, `perif/perif_channel.py`, `perif/perif_registers.py`, `perif/gpcfg.py`, `perif/loopback.py`, `simulator.py` | `tests/test_perif.py`, `tests/test_perif_loopback.py`, `tests/test_perif_integration.py`, `tests/test_pru1_core.py` |
| IEP counter and compare | A shared IEP0 has a 64-bit enabled counter, DEFAULT_INC, 16 64-bit compares, CMP status, and optional CMP0 counter reset. | `perif/iep.py` (`IepTimer`); registration in `simulator.py` (`IepRegisterRegion`) | `tests/test_iep_timer.py::test_counter_does_not_run_until_cnt_enable`, `tests/test_iep_timer.py::test_higher_compares_set_their_own_status_bit`, `tests/test_iep_timer.py::TestIepThroughFirmware` |
| IEP capture API | Ten capture slots latch the current counter through `IepTimer.capture_event(n)`, with enable, first/last, valid, and write-one-to-clear behavior. | `perif/iep.py` (`capture_event`, `CAP_*`) | `tests/test_iep_timer.py::test_capture_latches_the_counter_at_the_event`, `tests/test_iep_timer.py::test_first_mode_keeps_the_first_event`, `tests/test_iep_timer.py::test_slots_are_independent` |
| Interfaces | The MCP facade and deterministic headless runner expose load, execution, inspection, and I/O operations. | `mcp_server/server.py`, `tools/headless_runner.py` | `tests/test_mcp_server.py`, `tests/test_headless_runner.py` |

## Partial

| Area | What works | Boundary that does not exist | Evidence |
| --- | --- | --- | --- |
| IEP timer | Counter, compare, and software/harness-invoked capture work. | Shadow mode, slow compensation, sync/EHRPWM reset, interrupt routing, and mapping a pin/peripheral event to `capture_event(n)` do not exist.  Capture therefore requires another model or the harness to call the method directly. | Implemented: `perif/iep.py` (`tick`, `capture_event`); tests listed above. Missing path: `perif/iep.py` documents and implements no routing beyond `capture_event`; `core/pru_core.py` only calls `iep.tick()`. |
| Peripheral Interface RX auto-arm | Firmware arms RX through R30 bits `[26:24]`; the normal RX capture path works after that write. | `CHnCFG1.RX_EN_COUNTER` / `get_rx_en_count_delay()` is decoded but never starts an arm countdown or calls `arm_rx`. | Implemented: `perif/peripheral_interface.py::process_r30`, `perif/perif_channel.py::arm_rx`; tests `tests/test_perif_integration.py`, `tests/test_perif_loopback.py`. Missing path: `perif/perif_registers.py::get_rx_en_count_delay` has no reference in `perif/perif_channel.py` or `perif/peripheral_interface.py`. |

## Not supported

| Area | What does not exist | Unreachable/fallback path |
| --- | --- | --- |
| INTC | An interrupt controller, event mapping, host interrupts, and IEP-to-INTC routing do not exist. | `perif/iep.py` has no interrupt dispatch and `core/pru_core.py` only ticks IEP; no INTC instance is registered in `simulator.py`. |
| XFR2VBUS | Device IDs `0x60`–`0x64` do not implement XFR2VBUS. | They are absent from `XFRBus._pads` in `xfr/xfr_bus.py` and from `PRUCore.accelerators` in `core/pru_core.py`; XFR falls through to `XFRBus.xin/xout/xchg`, whose unknown-ID branches return zeros or discard data.  `tests/test_xfr.py::TestXFRBus::test_unknown_device_xin_returns_zeros` and its XOUT/XCHG warning tests exercise that fallback. |
| XFRDMA / PSI-L | No XFRDMA device or PSI-L bridge exists. | No accelerator or pad registers the DMA IDs; the same unknown-ID fallback in `xfr/xfr_bus.py` is the only reachable XFR path. |
| XFR2TR | Task-ring IDs `0x70`–`0x72` do not implement XFR2TR. | IDs `0x70`–`0x72` are absent from `xfr/xfr_bus.py` and `core/pru_core.py` registrations and reach the unknown-ID fallback in `xfr/xfr_bus.py`. |
| BSWAP XFR | Device IDs `0xA0`–`0xA2` do not implement BSWAP semantics. | They are absent from the XFR pad and accelerator registrations and reach the unknown-ID fallback in `xfr/xfr_bus.py`. |
| Unsupported XFR diagnostics | Unsupported XFR IDs do not fail loudly. | `xfr/xfr_bus.py` logs a warning, returns zero bytes for XIN/XCHG, and discards XOUT; `tests/test_xfr.py::TestXFRBus::test_unknown_device_xin_returns_zeros` and the warning tests cover this behavior. |
| Per-processor feature filtering | The selected `[device] target` and `core_version` do not control which simulated processors or peripherals exist.  In particular, RTU_PRU appears even for configurations whose target does not provide it. | `simulator.py::Simulator.__init__` unconditionally builds `pru0`, `rtu0`, and `pru1` before it reads clock fields from `_get_device_config`; no branch consumes `target` or `core_version`. `tests/test_capability_inventory.py` checks the fixed core set. |

## Roadmap (intentionally empty)

TI owns this section; it is intentionally empty pending TI product decisions.
