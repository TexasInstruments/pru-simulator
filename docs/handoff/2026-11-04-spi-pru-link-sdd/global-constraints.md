(Copied verbatim from plan "Global Constraints")
## Global Constraints

- Pins (R30/R31 bit numbers): PRU0 CLK out 16, MOSI out 13, /CS out 17, MISO in 14. PRU1 CLK in 1, MOSI in 2, /CS in 6, MISO out 11.
- Frame: /CS low, 19 bytes (cmd, addr, idle, 16 data), MSB first, /CS high. Commands `0x01` WRITE, `0x02` READ, `0x03` READ/WRITE. Valid addr 0x00–0x7F, byte offset = addr × 16.
- PRU1 drives MISO 0x00 during cmd/addr/idle, and during data for WRITE, unknown commands and bad addresses.
- Modes 0 and 1 only (CPOL = 0). Mode 1 = set on rising, capture on falling. Mode 0 = capture on rising, set on falling, first bit valid before the first rising edge.
- Core clocks {200, 225, 250, 300, 333} MHz; both PRUs always the same clock. SPI CLK = core / (2·k), 10–50 MHz, never 50 MHz at 200 MHz.
- "Supported" = zero byte errors over the full test sequence with **GPI delay 5 cycles** (Ruling R6) on both cores.
- PRU1 must stay an edge-polling peripheral: it never counts cycles from /CS to find bit positions. It may time a sample from the previous edge it observed.
- No cycle claim from hand counting: every cycle budget quoted in code comments, docs or the report is confirmed by stepping in the simulator.
- AM243 numbers only from SPRSP65J §6.11.5.16.1: SR<sub>I</sub> 1–3 V/ns, C<sub>L</sub> 2–30 pF (Table 6-112); t<sub>sk(GPO-GPO)</sub> max 2 ns (Table 6-113, PRDO1). There is no direct-input-mode table; input setup/hold rows are labelled "design budget".
- Pins/board from SPRUJ12F (Tables 4-11…4-14, B-2), as in spec §7.
- Firmware timing tests pin the core clock with the `sim_config` fixture (`tests/conftest.py`), never the repo's `memory.cfg`.
- SPI-view clock changes are for the session only; `memory.cfg` is never written by SPI code.
- Default GPI delay is 0; no existing test or demo may change behaviour.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Record per dispatch: task, role (implementer/reviewer/fix), model, subagent_tokens, duration_ms → append to `docs/reports/spi-pru-link-dispatch-log.csv` (controller does this, not subagents).

