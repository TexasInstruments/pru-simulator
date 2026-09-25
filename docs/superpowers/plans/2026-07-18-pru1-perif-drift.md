# PRU1 + Perif Loopback Clock-Drift Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `pru1` core with its own configurable clock, move the second Peripheral Interface block to it, and build TX/RX firmware + a pytest experiment that measures the pattern length at which clock drift breaks reception.

**Architecture:** PRU1 gets its own `pru1_clock_mhz` (defaults to `pru_clock_mhz`); its `PeripheralInterface`/`SigmaDeltaFilter` convert cycles→ns with that clock, so its timeline physically diverges from PRU0's. The existing `Loopback` maps absolute ns between cores, so drift emerges without touching it. TX firmware streams a start-bit-prefixed counter pattern in perif continuous mode (`tx_frame_size == 0`); RX firmware drains the RX FIFO to DRAM1. Spec: `docs/superpowers/specs/2026-07-18-pru1-perif-drift-design.md`.

**Tech Stack:** Python 3.12, pytest, FastAPI/WebSocket TestClient, PRU assembly (this repo's parser dialect), vanilla JS UI.

## Global Constraints

- New config key: `[device] pru1_clock_mhz`, optional, defaults to `pru_clock_mhz`.
- GPCFG mux mapping: `{"pru0": 0, "pru1": 1}`; for `rtu0` `gpcfg_write` is a no-op and `gpcfg_state` returns `{"mux_sel": 0}`.
- `rtu0` keeps its SD filter + shared SD registers; it loses its perif (`io_port.perif = None`).
- Perif bases: pru0 `0x260E0`, pru1 `0x26100`. Loopback: PRU0 TX ch-N → PRU1 RX ch-N.
- Bit clock for the experiment: core clock, div=7 → 200/8 = 25 MHz → 40 ns/bit, 320 ns/byte (64 PRU cycles/byte — firmware refill loops fit easily).
- RX framing: `sb_pol=1`, `sample_size=7`; the single start bit is consumed by the RX, so the TX stream is `[1, p0(8b), p1(8b), …]` and the firmware pre-shifts bytes so received bytes equal the counter pattern `0x00, 0x01, … 0xFF, 0x00 …` exactly.
- Run the full suite (`python3 -m pytest tests/ -q`) before every commit; all tests green (2 pre-existing xfail allowed).
- Keep commit messages focused on the change and its verification results.

---

### Task 0: Commit the pending SD-mode UI work

The working tree already contains finished, verified work (SD mux-mode IO-window switching: `simulator.py`, `ui/server.py`, `ui/static/app.js`, `ui/static/index.html`, `tests/test_perif_server.py`). It must be committed before this plan's changes so the diffs stay separable.

- [ ] **Step 1: Verify the pending diff is only the SD-mode change**

Run: `git status --short && git diff --stat`
Expected: exactly the 5 files above modified, nothing else.

- [ ] **Step 2: Run the suite and commit**

```bash
python3 -m pytest tests/ -q   # expect: 1108 passed, 2 xfailed
git add simulator.py ui/server.py ui/static/app.js ui/static/index.html tests/test_perif_server.py
git commit -m "feat(ui): switch IO window to SD view on GPCFG mux select"
```

---

### Task 1: PRU1 core, per-core clock config, perif rewire

**Files:**
- Modify: `simulator.py` (constructor ~lines 81-141; `gpcfg_write`/`gpcfg_state` ~lines 302-310)
- Modify: `tests/test_perif_loopback.py` (rtu0 → pru1)
- Modify: `ui/server.py:50,175-176` (`_history` gains `"pru1"`)
- Test: `tests/test_pru1_core.py` (new)

**Interfaces:**
- Produces: `sim.cores["pru1"]` (a `PRUCore`), `sim._perif["pru1"]` (base 0x26100), `sim._loopback` targeting pru1, `Simulator._pru1_clock_mhz: float`, `gpcfg_write("pru1", n)` / `gpcfg_state("pru1")`, rtu0 gpcfg no-op semantics.
- Consumes: existing `PRUCore`, `PeripheralInterface`, `Loopback`, `GpcfgRegisters`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pru1_core.py`:

```python
# tests/test_pru1_core.py
"""PRU1 core instance: own clock, owns the second perif block (TRM: GPCFG1/0x26100)."""
import configparser
import os

import simulator
from simulator import Simulator


def test_pru1_core_exists_with_perif():
    s = Simulator()
    assert "pru1" in s.cores
    assert s.cores["pru1"].io_port.perif is not None
    assert s._perif["pru1"].registers._base == 0x26100
    # rtu0 lost the perif block (it never had one on real hardware)
    assert s.cores["rtu0"].io_port.perif is None


def test_loopback_targets_pru1():
    s = Simulator()
    assert s._loopback.target is s._perif["pru1"]
    assert s._loopback.source is s._perif["pru0"]


def test_gpcfg_mapping():
    s = Simulator()
    s.gpcfg_write("pru1", 1)
    assert s._perif["pru1"].enabled is True
    assert s.gpcfg_state("pru1") == {"mux_sel": 1}
    # rtu0 has no GPCFG mux: write is a no-op, state reads 0
    s.gpcfg_write("rtu0", 1)
    assert s.gpcfg_state("rtu0") == {"mux_sel": 0}
    assert s.gpcfg_state("pru1") == {"mux_sel": 1}   # unaffected


def test_pru1_clock_defaults_to_pru_clock():
    s = Simulator()
    assert s._pru1_clock_mhz == s._pru_clock_mhz


def test_pru1_clock_from_config(tmp_path):
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(os.path.dirname(simulator.__file__), "memory.cfg"))
    cfg["device"]["pru1_clock_mhz"] = "200.1"
    path = tmp_path / "memory.cfg"
    with open(path, "w") as f:
        cfg.write(f)
    s = Simulator(str(path))
    assert s._pru1_clock_mhz == 200.1
    # PRU1's perif ns-timeline runs off its own clock
    assert abs(s._perif["pru1"]._period_ns - 1000.0 / 200.1) < 1e-9
    assert abs(s._perif["pru0"]._period_ns - 1000.0 / 200.0) < 1e-9
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_pru1_core.py -v`
Expected: FAIL/ERROR (`KeyError: 'pru1'`).

- [ ] **Step 3: Implement in `simulator.py`**

In `__init__`, add the third core and per-core clocks (replacing the current core/SD/perif wiring block, currently lines ~85-141):

```python
        io_pru0 = IOPort()
        io_rtu0 = IOPort()
        io_pru1 = IOPort()
        self.cores: dict[str, PRUCore] = {
            "pru0": PRUCore("PRU0", self.memory, self.xfr, io_pru0, self.constant_table),
            "rtu0": PRUCore("RTU0", self.memory, self.xfr, io_rtu0, self.constant_table),
            "pru1": PRUCore("PRU1", self.memory, self.xfr, io_pru1, self.constant_table),
        }

        # Wire SD filters to each core's IOPort (PRU1 runs on its own clock)
        dev = self._get_device_config(config_path)
        pru_clock_mhz = float(dev.get("pru_clock_mhz", "200"))
        pru1_clock_mhz = float(dev.get("pru1_clock_mhz", str(pru_clock_mhz)))
        self._pru_clock_mhz = pru_clock_mhz
        self._pru1_clock_mhz = pru1_clock_mhz
        core_clocks = {"pru0": pru_clock_mhz, "rtu0": pru_clock_mhz,
                       "pru1": pru1_clock_mhz}
        for name, core in self.cores.items():
            core.io_port.sd_filter = SigmaDeltaFilter(pru_clock_mhz=core_clocks[name])
```

Keep the SD register wiring block (pru0 owns regs, rtu0 shares) exactly as it is — pru1's SD `registers` stays `None`. Then the perif block becomes:

```python
        # ---- Peripheral Interface (3-channel SCU): PRU0 + PRU1 --------------
        # TRM: GPCFG1_REG (0x2600C) and block 0x26100 belong to PRU1, not RTU0.
        uart_clock_mhz = float(dev.get("uart_clock_mhz", "192"))
        core_order = ["pru0", "pru1"]
        perif_bases = {"pru0": 0x260E0, "pru1": 0x26100}
        self._perif = {}
        for name in core_order:
            core = self.cores[name]
            perif = PeripheralInterface(pru_clock_mhz=core_clocks[name],
                                        uart_clock_mhz=uart_clock_mhz)
            perif.registers = PerifRegisters(perif_bases[name])
            perif.build_channels()
            core.io_port.perif = perif
            self.memory.add_region(PerifRegisterRegion(perif.registers, perif_bases[name]))
            self._perif[name] = perif
```

(The `_on_mux_change` callback and `GpcfgRegion` wiring stay as-is; `core_order[1]` now resolves to `pru1`.) The loopback line becomes:

```python
        # Loopback: PRU0 TX channel-N -> PRU1 RX channel-N.
        self._loopback = Loopback(self._perif["pru0"], self._perif["pru1"])
```

Replace `gpcfg_write`/`gpcfg_state` (add the mapping as a module-level constant near the imports):

```python
_GPCFG_INDEX = {"pru0": 0, "pru1": 1}   # rtu0 has no GPCFG GP-mux (TRM)
```

```python
    def gpcfg_write(self, core: str, mux_sel: int) -> None:
        """Set the GPCFG PRU_GP_MUX_SEL for *core* (0=GP, 1=Perif, 3=SD)."""
        idx = _GPCFG_INDEX.get(core)
        if idx is None:
            return          # rtu0: no GPCFG mux — ignore (keeps WS server robust)
        self._gpcfg.set_mux_sel(idx, mux_sel)

    def gpcfg_state(self, core: str) -> dict:
        """Return the current GPCFG PRU_GP_MUX_SEL for *core*."""
        idx = _GPCFG_INDEX.get(core)
        return {"mux_sel": self._gpcfg.get_mux_sel(idx) if idx is not None else 0}
```

Also delete the now-duplicate `uart_clock_mhz`/`pru_clock_mhz` reads if the old lines remain (the constructor must read `_get_device_config` once into `dev`).

- [ ] **Step 4: Update `tests/test_perif_loopback.py`**

Replace every `rtu0` with `pru1` in that file (variable names and core strings — lines 40, 42, 46, 47, 49, 53, 63-68, 72, 80-83, 92: `s.gpcfg_write("pru1", 1)`, `pru1 = s._perif["pru1"]`, etc.).

- [ ] **Step 5: Update `ui/server.py` step-back history**

Line 50: `_history = {"pru0": [], "rtu0": [], "pru1": []}`.
Lines 175-176 (inside the hard-reset branch): add `_history["pru1"].clear()`.

- [ ] **Step 6: Run the new tests, then the full suite**

Run: `python3 -m pytest tests/test_pru1_core.py tests/test_perif_loopback.py -v`
Expected: all PASS.
Run: `python3 -m pytest tests/ -q`
Expected: all pass (2 xfail). If `test_perif_server.py` fails on state payloads, inspect — `perif_state("rtu0")` now returns `None`, which `_send_state` already handles.

- [ ] **Step 7: Commit**

```bash
git add simulator.py tests/test_pru1_core.py tests/test_perif_loopback.py ui/server.py
git commit -m "feat: add PRU1 core with own clock; move perif block + loopback target to PRU1"
```

---

### Task 2: TX pattern firmware (`source/perif_tx_pattern.asm`)

**Files:**
- Create: `source/perif_tx_pattern.asm`
- Test: `tests/test_perif_drift_experiment.py` (new — first test only)

**Interfaces:**
- Produces: `source/perif_tx_pattern.asm` — streams `[start=1, 0x00, 0x01, …]` continuously on perif ch0; consumed by Task 3's experiment.
- Consumes: Task 1 (`pru1` core, loopback → pru1). Perif register recipe (host side): PRU0 `TXCFG @ 0x260E4 = 0x00070010` (clk_sel=core, div=7 → 25 MHz); `CH0CFG0` stays 0 (tx_frame_size=0 → continuous). PRU1 `RXCFG @ 0x26100 = 0x0007001F` (sample_size=7, sb_pol=1, clk_sel=core, div=7).

- [ ] **Step 1: Write the firmware**

Create `source/perif_tx_pattern.asm`:

```asm
; =============================================================
; Peripheral Interface — continuous TX of a counter pattern (ch0)
; =============================================================
; Streams the byte pattern 0x00,0x01,...,0xFF,0x00,... prefixed by a
; single start bit, in perif CONTINUOUS mode (tx_frame_size == 0).
;
; The RX consumes the first '1' as its start bit, so each pushed byte
; is the pattern pre-shifted right by one:  push_k = carry | (p_k >> 1),
; carry_next = (p_k & 1) << 7, with carry_0 = 0x80 (the start bit).
; The receiver then captures exactly p_0, p_1, ... byte-aligned.
;
; Host prerequisites (set before running):
;   GPCFG mux = 1 (perif mode) on pru0
;   TXCFG (0x260E4) = 0x00070010   ; clk_sel=core, div=7 -> 25 MHz bit clock
;   CH0CFG0 (0x260E8) = 0         ; tx_frame_size=0 -> continuous mode
;
; Register map: r2=pattern byte p, r3=carry bits, r4=byte to push,
;               r5=R31 status scratch, r6=prefill counter, r0=TX-go word
; =============================================================

start:
        ldi  r30.b2, 0x00       ; select ch0 (byte2 strobe; clk_mode 0)
        ldi  r2, 0              ; p = 0
        ldi  r3, 0x80           ; carry = start bit
        ldi  r6, 0

prefill:                        ; fill the 4-deep FIFO before go
        lsr  r4, r2, 1
        or   r4, r4, r3
        and  r3, r2, 1
        lsl  r3, r3, 7
        add  r2, r2, 1
        and  r2, r2, 0xFF
        mov  r30.b0, r4         ; push (byte0 strobe)
        add  r6, r6, 1
        qbne prefill, r6, 4

        ldi  r0, 0              ; TX go: R31 bit 18
        ldi  r0.w2, 0x0004
        mov  r31, r0

loop:                           ; generate next byte, wait for room, push
        lsr  r4, r2, 1
        or   r4, r4, r3
        and  r3, r2, 1
        lsl  r3, r3, 7
        add  r2, r2, 1
        and  r2, r2, 0xFF
wait_room:
        and  r5, r31, 0x1C      ; ch0 tx_fifo count, bits [4:2]
        qbeq wait_room, r5, 0x10 ; spin while FIFO full (count == 4)
        mov  r30.b0, r4
        jmp  loop
```

- [ ] **Step 2: Write the failing round-trip test**

Create `tests/test_perif_drift_experiment.py` with a test that runs the TX firmware and receives via a Python-armed PRU1 RX channel (no RX firmware yet):

```python
# tests/test_perif_drift_experiment.py
"""TX/RX firmware over the PRU0->PRU1 perif loopback + clock-drift experiment."""
import os
from pathlib import Path

from simulator import Simulator

_SRC = Path(__file__).parent.parent / "source"

TXCFG_PRU0 = 0x260E4
RXCFG_PRU1 = 0x26100
TXCFG_VAL = 0x00070010     # clk_sel=core, div=7 -> 25 MHz bit clock
RXCFG_VAL = 0x0007001F     # sample_size=7, sb_pol=1, clk_sel=core, div=7


def _setup_perif(sim):
    sim.gpcfg_write("pru0", 1)
    sim.gpcfg_write("pru1", 1)
    sim.write_perif_register("pru0", TXCFG_PRU0, TXCFG_VAL)
    sim.write_perif_register("pru1", RXCFG_PRU1, RXCFG_VAL)
    sim.perif_loopback(0, True)


def test_tx_pattern_roundtrip_python_rx():
    """TX firmware streams the counter pattern; a Python-armed RX decodes it."""
    sim = Simulator()
    _setup_perif(sim)
    errors = sim.load("pru0", (_SRC / "perif_tx_pattern.asm").read_text())
    assert errors == []

    ch = sim._perif["pru1"].channels[0]
    ch.arm_rx(True)

    received = []
    for _ in range(200):                      # chunked lockstep
        sim.step("pru0", 100)
        sim.cores["pru1"].io_port.perif.advance_cycles(
            sim.cores["pru0"].counters.cycles)
        while ch.rx_valid:
            received.append(ch.rx_head())
            ch.clr_val()
        if len(received) >= 32:
            break

    assert len(received) >= 32
    assert received[:32] == [i & 0xFF for i in range(32)]
```

- [ ] **Step 3: Run to verify it fails**

Run: `python3 -m pytest tests/test_perif_drift_experiment.py -v`
Expected: FAIL. First run may fail on assembly errors (fix mnemonic/operand syntax against `core/parser.py` — e.g. if `mov r30.b0, r4` doesn't strobe byte 0, use `and r30.b0, r4, 0xFF`) or on framing (received bytes shifted → check the carry logic). Iterate on the asm until the pattern round-trips; that is the test's purpose.

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add source/perif_tx_pattern.asm tests/test_perif_drift_experiment.py
git commit -m "feat: continuous perif TX counter-pattern firmware + roundtrip test"
```

---

### Task 3: RX firmware + drift experiment + report tool

**Files:**
- Create: `source/perif_rx_capture.asm`
- Create: `tools/__init__.py` (empty), `tools/perif_drift_report.py`
- Modify: `tests/test_perif_drift_experiment.py` (add experiment tests)

**Interfaces:**
- Produces: `run_drift(ppm, max_bytes, tmp_dir) -> tuple[int, int | None]` in `tools/perif_drift_report.py` — returns `(received_count, first_bad_index)`; RX capture buffer at DRAM1 `0x2000`, received-byte counter (u32 LE) at `0x3FF8`.
- Consumes: Task 2's TX firmware and register recipe.

- [ ] **Step 1: Write the RX firmware**

Create `source/perif_rx_capture.asm`:

```asm
; =============================================================
; Peripheral Interface — RX capture to DRAM1 (ch0), for PRU1
; =============================================================
; Arms RX ch0, then forever: wait for rx_valid (R31 bit 24), store the
; FIFO head byte (R31 byte 0) to DRAM1, pop the FIFO (write R31 bit 24),
; and maintain a received-byte counter.
;
; Memory map (DRAM1): capture buffer 0x2000.. (grows up, max ~8 KB),
;                     received-byte count (u32) at 0x3FF8.
;
; Host prerequisites: GPCFG mux=1 on pru1;
;   RXCFG (0x26100) = 0x0007001F  ; sample_size=7, sb_pol=1, core clk, div=7
;
; Register map: r1=buffer pointer, r2=byte count, r3=count address,
;               r4=received byte, r5=FIFO-pop command word
; =============================================================

start:
        ldi  r1, 0x2000         ; capture buffer
        ldi  r2, 0              ; byte count
        ldi  r3, 0x3FF8         ; count address
        ldi  r5, 0
        ldi  r5.w2, 0x0100      ; R31 bit 24 = clr_val ch0 (FIFO pop)
        ldi  r30.b3, 0x01       ; arm RX ch0 (byte3 strobe, bit 24)

poll:
        qbbc poll, r31, 24      ; wait for ch0 rx_valid
        and  r4, r31, 0xFF      ; byte 0 = ch0 RX FIFO head
        sbbo r4, r1, 0, 1       ; store to buffer
        add  r1, r1, 1
        add  r2, r2, 1
        sbbo r2, r3, 0, 4       ; publish count
        mov  r31, r5            ; pop FIFO
        jmp  poll
```

- [ ] **Step 2: Write the experiment helper + report tool**

Create empty `tools/__init__.py`. Create `tools/perif_drift_report.py`:

```python
#!/usr/bin/env python3
"""Clock-drift experiment: at what pattern length does perif RX break?

Streams the counter pattern PRU0 -> PRU1 over the ch0 loopback with
PRU1's clock offset by N ppm, and reports the first corrupted byte index.
Usage: python3 tools/perif_drift_report.py
"""
import configparser
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator import Simulator  # noqa: E402

_ROOT = Path(__file__).parent.parent
TXCFG_PRU0 = 0x260E4
RXCFG_PRU1 = 0x26100
TXCFG_VAL = 0x00070010     # clk_sel=core, div=7 -> 25 MHz bit clock
RXCFG_VAL = 0x0007001F     # sample_size=7, sb_pol=1, clk_sel=core, div=7
COUNT_ADDR = 0x3FF8
BUF_ADDR = 0x2000
CYCLES_PER_BYTE = 64       # 8 bits x 8 core cycles/bit at div=7


def make_config(ppm: float, out_dir: str) -> str:
    """Copy memory.cfg with pru1_clock_mhz offset by *ppm*."""
    cfg = configparser.ConfigParser()
    cfg.read(_ROOT / "memory.cfg")
    base = float(cfg["device"].get("pru_clock_mhz", "200"))
    cfg["device"]["pru1_clock_mhz"] = repr(base * (1.0 + ppm / 1e6))
    path = os.path.join(out_dir, f"memory_{int(ppm)}ppm.cfg")
    with open(path, "w") as f:
        cfg.write(f)
    return path


def run_drift(ppm: float, max_bytes: int, tmp_dir: str):
    """Run the experiment; return (received_count, first_bad_index or None)."""
    sim = Simulator(make_config(ppm, tmp_dir))
    sim.gpcfg_write("pru0", 1)
    sim.gpcfg_write("pru1", 1)
    sim.write_perif_register("pru0", TXCFG_PRU0, TXCFG_VAL)
    sim.write_perif_register("pru1", RXCFG_PRU1, RXCFG_VAL)
    sim.perif_loopback(0, True)
    assert sim.load("pru0", (_ROOT / "source" / "perif_tx_pattern.asm").read_text()) == []
    assert sim.load("pru1", (_ROOT / "source" / "perif_rx_capture.asm").read_text()) == []

    budget = max_bytes * CYCLES_PER_BYTE + 20_000
    stepped = 0
    while stepped < budget:
        sim.step("pru0", 200)      # TX first so the line history leads RX
        sim.step("pru1", 200)
        stepped += 200
        count = int.from_bytes(bytes(sim.memory_read(COUNT_ADDR, 4)), "little")
        if count >= max_bytes:
            break

    count = min(count, max_bytes)
    data = list(sim.memory_read(BUF_ADDR, count)) if count else []
    first_bad = next((i for i, b in enumerate(data) if b != (i & 0xFF)), None)
    return count, first_bad


def main():
    print(f"{'ppm':>8} | {'received':>8} | {'first bad byte':>14} | {'bits':>8}")
    print("-" * 48)
    with tempfile.TemporaryDirectory() as td:
        for ppm in (0, 50, 100, 200, 500, 1000, 2000):
            max_bytes = 2000 if ppm < 200 else 1000
            count, first_bad = run_drift(ppm, max_bytes, td)
            bad = "-" if first_bad is None else str(first_bad)
            bits = "-" if first_bad is None else str(first_bad * 8)
            print(f"{ppm:>8} | {count:>8} | {bad:>14} | {bits:>8}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Add the failing experiment tests**

Append to `tests/test_perif_drift_experiment.py`:

```python
import pytest

from tools.perif_drift_report import run_drift


def test_rx_firmware_captures_pattern(tmp_path):
    """Zero drift: RX firmware reconstructs a long pattern intact."""
    count, first_bad = run_drift(0.0, 1500, str(tmp_path))
    assert count == 1500
    assert first_bad is None


@pytest.mark.parametrize("ppm", [200.0, 500.0, 1000.0])
def test_drift_breaks_reception_at_expected_length(ppm, tmp_path):
    """Failure length scales ~1/ppm (physics band: slip of ~a bit period)."""
    count, first_bad = run_drift(ppm, 1000, str(tmp_path))
    assert first_bad is not None, f"no corruption at {ppm} ppm within {count} bytes"
    bits = first_bad * 8
    # Slip-to-failure ~ initial_phase_margin / ppm; margin in (0,1) bit.
    lo = 0.05 / (ppm * 1e-6)
    hi = 2.0 / (ppm * 1e-6)
    assert lo <= bits <= hi, f"{ppm} ppm failed at {bits} bits, outside [{lo:.0f},{hi:.0f}]"


def test_drift_failure_length_monotonic(tmp_path):
    """More drift -> earlier failure."""
    results = {}
    for ppm in (200.0, 1000.0):
        _, first_bad = run_drift(ppm, 1000, str(tmp_path))
        assert first_bad is not None
        results[ppm] = first_bad
    assert results[1000.0] < results[200.0]
```

- [ ] **Step 4: Run to verify failure, then iterate**

Run: `python3 -m pytest tests/test_perif_drift_experiment.py -v`
Expected: new tests FAIL first (missing asm/tool), then pass once implemented. Debug order: (1) RX asm syntax, (2) zero-drift roundtrip via firmware, (3) drift cases. If a drift case falls outside the band, print actuals with `python3 tools/perif_drift_report.py` and check the physics (bit period 40 ns, slip rate ppm×1e-6 per bit) before touching the band.

- [ ] **Step 5: Run the report and record results**

Run: `python3 tools/perif_drift_report.py`
Expected: a table where first-bad-bits shrinks roughly ∝ 1/ppm and the 0-ppm row shows no corruption. Paste the table into the commit message body.

- [ ] **Step 6: Full suite, then commit**

```bash
python3 -m pytest tests/ -q    # all pass
git add source/perif_rx_capture.asm tools/ tests/test_perif_drift_experiment.py
git commit -m "feat: perif RX capture firmware + clock-drift pattern-length experiment"
```

---

### Task 4: UI — PRU1 core select + multicore partner choice

**Files:**
- Modify: `ui/static/index.html` (core-select ~line 1249; controls bar ~line 1254; loopback label ~line 1458)
- Modify: `ui/static/app.js` (mc dicts lines 23-28; MC-mode `"rtu0"` call sites; `updatePerifPanel`)
- Test: `tests/test_perif_server.py` (add one WS test)

**Interfaces:**
- Consumes: Task 1 (`pru1` in `sim.cores`, gpcfg rtu0 no-op).
- Produces: `mcPartner` JS variable + `#mc-partner-select` dropdown; PRU1 reachable from the UI.

- [ ] **Step 1: Add the failing server test**

Append to `tests/test_perif_server.py`:

```python
def test_pru1_state_has_perif_rtu0_does_not():
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "get_state", "core": "pru1"})
        st = ws.receive_json()
        assert st["core"] == "pru1"
        assert "perif" in st["io"]
        ws.send_json({"action": "get_state", "core": "rtu0"})
        st = ws.receive_json()
        assert "perif" not in st["io"]
```

Run: `python3 -m pytest tests/test_perif_server.py -v` — the new test should PASS already (backend done in Task 1); if it fails, fix the backend before touching JS.

- [ ] **Step 2: index.html changes**

Core select (line ~1249): add `<option value="pru1">PRU1</option>` after the RTU0 option.
After the Multi-core button (line ~1254), add:

```html
    <select id="mc-partner-select" title="Second core in multi-core view" style="display:none;">
      <option value="rtu0">RTU0</option>
      <option value="pru1">PRU1</option>
    </select>
```

Loopback section title (line ~1458): change `Loopback → core-1 RX (jitter / latency / drift)` to `Loopback → PRU1 RX (jitter / latency / drift)`.

- [ ] **Step 3: app.js changes**

1. Top-level state (lines 23-28): add `pru1` keys everywhere `pru0`/`rtu0` keys exist (`mcPrevRegs` init at lines 23-24, `mcLastSourceKey`, `mcBreakpoints`, `mcHaltedState`, `mcBreakState`) and add:

```js
let mcPartner = "rtu0";
```

2. Replace hardcoded `"rtu0"` with `mcPartner` in every multi-core code path. Find them with `grep -n '"rtu0"' ui/static/app.js`; as of this plan they are lines 328, 1438, 1458, 1460, 1471, 1473, 1945, 1974, 2872, 2879, 3012, 3147, 3181, 3190 — each is either `sendAction({... core: "rtu0" ...})` inside a `multiCoreMode` branch, `buildMCRegTable("rtu0")`, the `core === "rtu0"` state-routing check (line 3012), or the `mcPrevRegs` re-init object literals (init fresh objects with all three core keys instead). Example, the step handler:

```js
  if (multiCoreMode) {
    sendAction({ action: "step", core: "pru0", count: 1 });
    sendAction({ action: "step", core: mcPartner, count: 1 });
  }
```

3. Partner dropdown wiring (near `btnMulticore.addEventListener`, line ~2843):

```js
const mcPartnerSelect = document.getElementById("mc-partner-select");
mcPartnerSelect.addEventListener("change", () => {
  mcPartner = mcPartnerSelect.value;
  if (multiCoreMode) {
    buildMCRegTable(mcPartner);
    sendAction({ action: "get_state", core: "pru0" });
    sendAction({ action: "get_state", core: mcPartner });
  }
});
```

In `toggleMultiCore` show/hide it: `mcPartnerSelect.style.display = multiCoreMode ? "" : "none";`.

4. `updatePerifPanel` (line ~750): label text `'core-1'` → `'PRU1'` (line ~770), and hide the GP-Mux row for RTU0 — add at the top of the function:

```js
  const muxRow = document.getElementById('io-mux-row');
  if (muxRow) muxRow.style.display = (currentCore === 'rtu0') ? 'none' : '';
```

- [ ] **Step 4: Manual UI verification**

Run: `python3 ui/server.py &` then open `http://localhost:8080`.
Check: PRU1 appears in the core select and shows the perif panel when its GP-Mux is set to 1; RTU0 shows no GP-Mux row; Multi-core toggle reveals the partner dropdown; selecting PRU1 as partner and pressing Step steps PRU0+PRU1 (register views update). Kill the server afterwards.

- [ ] **Step 5: Full suite + commit**

```bash
python3 -m pytest tests/ -q    # all pass
git add ui/static/index.html ui/static/app.js tests/test_perif_server.py
git commit -m "feat(ui): PRU1 core select + selectable multicore partner"
```
