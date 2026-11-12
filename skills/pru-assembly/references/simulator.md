# TI pru-simulator

`github.com/TexasInstruments/pru-simulator` is a cycle-counting PRU simulator with a browser dashboard, an MCP server and a headless JSON runner. The simulator changes quickly. Its `readme.md` (supported-features tables) and `docs/device_capabilities.md` are the source of truth for what is modelled; check them in the user's checkout instead of relying on this file.

## Contents

1. What it can and can't tell you
2. Install
3. Fastest path: scripts/simulate.py
4. MCP server
5. Headless runner
6. A verification pattern

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

## 4. MCP server

Set up and use the MCP server from the simulator's own `docs/mcp_server.md`; this skill doesn't copy it. Its tool table can lag the implementation (tracked in simulator issue #101), so list the tools the server actually exposes instead of trusting a fixed list. The server reads `memory.cfg` relative to its working directory, so start it from the simulator checkout.

For a one-shot run or a pin-timing check, `scripts/simulate.py` is enough. Use the MCP tools when you need to stop, look at registers or memory, and continue.

## 5. Headless runner

For CI or scripted checks, from the simulator repo:

```bash
python -m tools.headless_runner --json --assembly file.asm --core pru0 --max-steps 10000
python -m tools.headless_runner --json --elf build/fw.out --until 'reg:r0==0x55' --max-cycles 5000
```

It prints exactly one JSON object. Exit codes: 2 load error, 3 condition unmet or budget exhausted, 4 internal error. Its condition syntax differs slightly from the MCP tool: `cycles>=N` and `mem32:ADDR==V`. Use `--config` to choose a target and `--include` for include directories.

## 6. A verification pattern

1. Lint, then load. Fix every load error before looking at behaviour.
2. Run to `halt` (or to a pin or register condition for forever-loops) with a budget slightly above the expected cycle count. Exhausting the budget means the code is slower than planned or stuck.
3. Check the end state against expectations written down before the run: registers, memory, pins.
4. For pin timing, use `simulate.py edges` and compare each gap against the budget.
5. Record "simulated on <target>" in the cycle-budget table. Change it to "measured" only after a hardware check.
