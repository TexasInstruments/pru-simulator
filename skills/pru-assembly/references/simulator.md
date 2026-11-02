# TI pru-simulator

`github.com/TexasInstruments/pru-simulator` is a cycle-counting PRU simulator with a browser dashboard, an MCP server and a headless JSON runner. Details here were checked at commit a17fd62 (v0.2.6, 2026-10-01).

## Contents

1. What it can and can't tell you
2. Install
3. Fastest path: scripts/simulate.py
4. MCP server setup for Claude Code
5. MCP tools
6. Headless runner
7. A verification pattern

## 1. What it can and can't tell you

- **Targets:** AM243x (PRU_ICSSG, V4) and AM263x (PRU-ICSS, V3), selected by `config/memory_am243x.cfg` or `config/memory_am263x.cfg`. The default `memory.cfg` is AM243x.
- **Modelled:** the full ISA (ALU, branches, `loop`, `lbco`/`sbco`/`lbbo`/`sbbo`, `xin`/`xout`/`xchg`, `mvi*`), MAC, scratchpad, BSWAP (ICSSG), IEP, sigma-delta filter, the Peripheral Interface, direct GPI/GPO, multi-core runs, and GPO→GPI loopback.
- **Not modelled:** INTC, UART, eCAP, CRC, broadside RAM, task manager, MII/MDIO, PWM, shift-in/shift-out I/O modes. Code that depends on these will load but not behave like hardware.
- **Timing model:** one cycle per instruction, plus per-region memory latencies (and optional jitter) set in the `.cfg` file, for example `read_latency = 2`. Those latencies are configuration values, not measurements. Treat simulated cycle counts as exact for register-and-pin code and as estimates for code with memory accesses.
- **Hardware validation:** TI checked the disassembler against clpru 2.3.3 output and ran a 97-test ISA firmware on real hardware and in the simulator with matching DRAM results.
- **AM335x and other devices:** core logic that only uses registers and R30/R31 can run with `--target am263x` (also a V3 core). Addresses, constants-table entries and peripherals will not match.

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
python3 scripts/simulate.py edges firmware.asm --pin 5 --edges 4           # cycles between pin changes
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
