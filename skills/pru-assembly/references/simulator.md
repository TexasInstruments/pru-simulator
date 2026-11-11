# TI pru-simulator

`github.com/TexasInstruments/pru-simulator` is a cycle-counting PRU simulator with a browser dashboard, an MCP server and a headless JSON runner. The simulator changes quickly. Its `readme.md` (supported-features tables) and `docs/device_capabilities.md` are the source of truth for what is modelled; check them in the user's checkout instead of relying on this file.

## Contents

1. What it can and can't tell you
2. Install
3. Fastest path: scripts/simulate.py
4. MCP server setup for Claude Code
5. MCP tools
6. Headless runner
7. A verification pattern

## 1. What it can and can't tell you

- **Targets:** selected by the `config/memory_<target>.cfg` files (`ls config/`). `simulate.py --target` takes the target name. The default `memory.cfg` is AM243x.
- **What is modelled** differs per target and grows with each release. Read the "PRU features" table in the simulator's `readme.md` and `docs/device_capabilities.md`. Don't assume a peripheral or accelerator is present or absent from memory.
- **Unmodelled accelerator transfers do not fail.** An `xin`/`xout`/`xchg` to a device ID the simulator doesn't model (or that the target doesn't have) logs a warning, reads return zeros, writes are discarded, and the run continues. A PASS therefore proves nothing about code that uses such an ID. Look for these warnings in the run output; if any appear, say the accelerator was not simulated. In the simulator, `core.strict_unsupported_xfr = True` makes such a transfer raise instead.
- **Timing model:** one cycle per instruction, plus per-region memory latencies (and optional jitter) set in the `.cfg` file. Some presets are calibrated on silicon (for example LP-AM261 local RAM and MSRAM, AM243x MSRAM); the `.cfg` header comments say which. Uncalibrated figures are configuration values, not measurements. Known gaps: shared-RAM contention between cores, and anything outside the ICSS. Treat simulated cycle counts as exact for register-and-pin code and as estimates for code with memory accesses.
- **Hardware validation:** TI checked the disassembler against clpru 2.3.3 output and ran a 97-test ISA firmware on real hardware and in the simulator with matching DRAM results.
- **Other devices:** core logic that only uses registers and R30/R31 can run on the nearest modelled target. Addresses, constants-table entries and peripherals will not match.

## 2. Install

```bash
git clone https://github.com/TexasInstruments/pru-simulator.git
pip install -r pru-simulator/requirements.txt        # Python 3.9+
export PRU_SIM_DIR=$PWD/pru-simulator
```

The dashboard is optional: `python ui/server.py` from the repo, then open `http://localhost:8080`.

## 3. Fastest path: scripts/simulate.py

This skill's wrapper runs the simulator the same way every time and prints a short summary. It finds the simulator via `PRU_SIM_DIR`, `./pru-simulator` or `~/pru-simulator`.

```bash
python3 scripts/simulate.py run   firmware.asm --target am243x               # run to halt
python3 scripts/simulate.py run   firmware.out --until 'reg:r0==0x55' --max-cycles 5000
python3 scripts/simulate.py edges firmware.asm --pin 5 --edges 4           # cycles and ns between pin changes
python3 scripts/simulate.py edges firmware.asm --pin 5 --clock-mhz 250       # override the config's PRU clock
```

Exit codes: 0 pass, 2 load error, 3 condition not met or budget exhausted, 4 simulator error, 5 not installed.

The simulator's assembler reports only the first error it meets. Run `scripts/lint_pru.py` first, which reports all of them.

## 4. MCP server setup for Claude Code

The server reads `memory.cfg` relative to its working directory, so start it from the repo:

```bash
claude mcp add --transport stdio --scope user pru-simulator -- \
  bash -c 'cd /path/to/pru-simulator && exec python mcp_server/server.py'
claude mcp list        # should show pru-simulator as connected
```

The repo's own `docs/mcp_server.md` shows an equivalent JSON entry with a `cwd` field. Its tool table predates several tools listed below.

## 5. MCP tools

| Tool | Key parameters | Use |
| --- | --- | --- |
| `pru_load` | `source`, `core`, `include_paths` | Assemble and load source text |
| `pru_elf_load` | `core`, `path` or `b64` | Load a clpru-built `.out` (TI PRU ELF32) |
| `pru_step` | `core`, `count` | Execute N instructions |
| `pru_step_multicore` | `lead`, `follow`, `count`, `guard_ns` | Step two cores in lockstep |
| `pru_run_until` | `core`, `condition`, `max_steps`, `max_cycles` | Run until a condition or budget |
| `pru_registers` | `core` | All 32 registers plus carry |
| `pru_memory` | `addr`, `length` | Read memory (hex and ASCII); there is no write tool |
| `pru_io` | `core` | GPO and GPI pin states |
| `pru_set_input` | `core`, `pin`, `value` | Drive a GPI pin |
| `pru_vcd_export` | `path`, `core`, `max_steps`, `pins`, `include_gpi` | Run and write a VCD waveform |
| `pru_uart_inject` | `source`, `payload`, `baudrate`, … | UART RX test harness (expects the frame counter in r20 and an error flag at DRAM0 0x0FFE, as in `source/uart_rx_11frame.asm`) |
| `pru_i2c_attach` | `core`, `enabled`, `address` | Attach a TCA9538 I2C expander model on bits 0/1 |
| `pru_reset`, `pru_breakpoint`, `pru_status` | | Reset a core, add a breakpoint, snapshot all cores |

`pru_run_until` conditions: `halt`, `cycles>N`, `reg:rN==V`, `mem:ADDR!=V` (32-bit, inequality only), `gpi:N==V` and `gpo:N==V` for pins 0–19 (`pin:` is an alias for `gpi:`). Cycle counts in results are cumulative since the last reset, so subtract two results to time a stretch of code.

## 6. Headless runner

For CI or scripted checks, from the simulator repo:

```bash
python -m tools.headless_runner --json --assembly file.asm --core pru0 --max-steps 10000
python -m tools.headless_runner --json --elf build/fw.out --until 'reg:r0==0x55' --max-cycles 5000
```

It prints exactly one JSON object. Exit codes: 2 load error, 3 condition unmet or budget exhausted, 4 internal error. Its condition syntax differs slightly from the MCP tool: `cycles>=N` and `mem32:ADDR==V`. Use `--config` to choose a target and `--include` for include directories.

## 7. A verification pattern

1. Lint, then load. Fix every load error before looking at behaviour.
2. Run to `halt` (or to a pin or register condition for forever-loops) with a budget slightly above the expected cycle count. Exhausting the budget means the code is slower than planned or stuck.
3. Check the end state against expectations written down before the run: registers, memory, pins.
4. For pin timing, use `simulate.py edges` and compare each gap against the budget.
5. Record "simulated on <target>" in the cycle-budget table. Change it to "measured" only after a hardware check.
