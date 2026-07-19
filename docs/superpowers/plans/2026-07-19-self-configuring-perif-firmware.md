# Self-Configuring Perif Demo Firmware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The perif drift-demo firmwares configure their own GPCFG mux and CFG registers at startup, so the UI demo needs only: load two files, enable loopback, Run.

**Architecture:** Pure firmware + test change. Backend support is already verified (memory-bus stores to `PerifRegisterRegion`/`GpcfgRegion` fire the existing callbacks; the IO window already follows `io.mode` in state pushes). Task 1 adds the TX prologue, Task 2 the RX prologue, Task 3 the no-host-setup integration test, the ws mode-switch regression test, and doc updates.

**Tech Stack:** PRU assembly, pytest, `fastapi.testclient`.

**Spec:** `docs/superpowers/specs/2026-07-19-self-configuring-perif-firmware-design.md`

## Global Constraints

- Register recipe: GPCFG0 `0x26008` / GPCFG1 `0x2600C` mux_sel=1 is bits [29:26] → word `0x04000000`; `TXCFG 0x260E4 = 0x00070010`; `CH0CFG0 0x260E8 = 0` (full 32-bit store — `tx_frame_size` is bits [15:11]); `RXCFG 0x26100 = 0x0007001F`.
- All existing tests must keep passing unchanged (their host register writes become redundant, not wrong).
- Run tests with `python3 -m pytest` from the repo root.

---

### Task 1: TX firmware config prologue

**Files:**
- Modify: `source/perif_tx_pattern.asm`
- Test: `tests/test_perif_drift_experiment.py` (append)

**Interfaces:**
- Consumes: memory-mapped registers listed in Global Constraints; r0/r1 are free at program entry (r0 is re-initialized before TX-go, r1 unused by this firmware).
- Produces: firmware that self-configures GPCFG0 + TXCFG + CH0CFG0; Task 3's integration test relies on it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_perif_drift_experiment.py`:

```python
def test_tx_firmware_self_configures():
    """No host setup at all: the TX firmware writes GPCFG0/TXCFG/CH0CFG0."""
    sim = Simulator()
    assert sim.load("pru0", (_SRC / "perif_tx_pattern.asm").read_text()) == []
    sim.step("pru0", 40)
    assert sim.gpcfg_state("pru0")["mux_sel"] == 1
    regs = sim._perif["pru0"].registers
    assert regs.get_shared_config()["txcfg"] == 0x00070010
    assert regs.get_tx_frame_size(0) == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_perif_drift_experiment.py -k tx_firmware_self -v`
Expected: FAIL at `mux_sel == 1` (firmware doesn't write GPCFG yet).

- [ ] **Step 3: Add the prologue**

In `source/perif_tx_pattern.asm`, replace the header's host-prerequisites block:

```
; Host prerequisites (set before running):
;   GPCFG mux = 1 (perif mode) on pru0
;   TXCFG (0x260E4) = 0x00070010   ; clk_sel=core, div=7 -> 25 MHz bit clock
;   CH0CFG0 (0x260E8) = 0          ; tx_frame_size=0 -> continuous mode
```

with:

```
; Self-configuring: the prologue writes GPCFG0 mux=1 (perif mode),
; TXCFG (0x260E4) = 0x00070010 (clk_sel=core, div=7 -> 25 MHz bit clock)
; and CH0CFG0 (0x260E8) = 0 (tx_frame_size=0 -> continuous mode).
; Host prerequisite: enable loopback ch0 (harness, not memory-mapped).
```

and insert the prologue between `start:` and the `ldi r30.b2` line:

```
start:
        ldi  r0, 0x0000         ; GPCFG0: mux_sel=1 (bits 29:26)
        ldi  r0.w2, 0x0400
        ldi  r1, 0x6008
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4
        ldi  r0, 0x0010         ; TXCFG = 0x00070010
        ldi  r0.w2, 0x0007
        ldi  r1, 0x60E4
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4
        ldi  r0, 0              ; CH0CFG0 = 0 (continuous mode)
        ldi  r0.w2, 0
        sbbo r0, r1, 4, 4       ; 0x260E8, full 32-bit clear

        ldi  r30.b2, 0x00       ; select ch0 (byte2 strobe; clk_mode 0)
```

(Also update the header's register-map comment: r0 doubles as prologue scratch; r1 is prologue address scratch.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_perif_drift_experiment.py -k tx_firmware_self -v`
Expected: PASS

- [ ] **Step 5: Run the drift-experiment file**

Run: `python3 -m pytest tests/test_perif_drift_experiment.py -q`
Expected: all pass (prologue must not disturb the existing roundtrip/drift tests).

- [ ] **Step 6: Commit**

```bash
git add source/perif_tx_pattern.asm tests/test_perif_drift_experiment.py
git commit -m "feat: perif TX firmware self-configures GPCFG0/TXCFG/CH0CFG0"
```

---

### Task 2: RX firmware config prologue

**Files:**
- Modify: `source/perif_rx_capture.asm`
- Test: `tests/test_perif_drift_experiment.py` (append)

**Interfaces:**
- Consumes: registers per Global Constraints; r0/r1 free at entry (r1 is re-initialized to the buffer pointer immediately after the prologue).
- Produces: firmware that self-configures GPCFG1 + RXCFG; Task 3 relies on it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_perif_drift_experiment.py`:

```python
def test_rx_firmware_self_configures():
    """No host setup at all: the RX firmware writes GPCFG1/RXCFG."""
    sim = Simulator()
    assert sim.load("pru1", (_SRC / "perif_rx_capture.asm").read_text()) == []
    sim.step("pru1", 40)
    assert sim.gpcfg_state("pru1")["mux_sel"] == 1
    assert sim._perif["pru1"].registers.get_shared_config()["rxcfg"] == 0x0007001F
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_perif_drift_experiment.py -k rx_firmware_self -v`
Expected: FAIL at `mux_sel == 1`.

- [ ] **Step 3: Add the prologue**

In `source/perif_rx_capture.asm`, replace the header lines:

```
; Host prerequisites: GPCFG mux=1 on pru1;
;   RXCFG (0x26100) = 0x0007001F  ; sample_size=7, sb_pol=1, core clk, div=7
```

with:

```
; Self-configuring: the prologue writes GPCFG1 mux=1 (perif mode) and
; RXCFG (0x26100) = 0x0007001F (sample_size=7, sb_pol=1, core clk, div=7).
; Host prerequisite: enable loopback ch0 (harness, not memory-mapped).
```

and insert between `start:` and `ldi r1, 0x2000`:

```
start:
        ldi  r0, 0x0000         ; GPCFG1: mux_sel=1 (bits 29:26)
        ldi  r0.w2, 0x0400
        ldi  r1, 0x600C
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4
        ldi  r0, 0x001F         ; RXCFG = 0x0007001F
        ldi  r0.w2, 0x0007
        ldi  r1, 0x6100
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4

        ldi  r1, 0x2000         ; capture buffer
```

(r0 is not used again by this firmware; r1 is re-initialized here.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_perif_drift_experiment.py -k rx_firmware_self -v`
Expected: PASS

- [ ] **Step 5: Run the drift-experiment file**

Run: `python3 -m pytest tests/test_perif_drift_experiment.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add source/perif_rx_capture.asm tests/test_perif_drift_experiment.py
git commit -m "feat: perif RX firmware self-configures GPCFG1/RXCFG"
```

---

### Task 3: No-host-setup integration + ws mode-switch tests, docs

**Files:**
- Test: `tests/test_perif_drift_experiment.py` (append)
- Test: `tests/test_perif_server.py` (append)
- Modify: `readme.md` only if it documents the demo's host register steps (check with `grep -n "260E4\|26100\|GPCFG" readme.md`; skip if no hits).

**Interfaces:**
- Consumes: self-configuring firmwares from Tasks 1–2; `sim.perif_loopback(0, True)`; `step_paced` from the paced-run feature.
- Produces: regression coverage; no new code interfaces.

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_perif_drift_experiment.py` (fails only if Tasks 1–2 are incomplete; with them done it must pass immediately):

```python
def test_roundtrip_with_no_host_register_setup():
    """Only loopback is enabled by the host; firmware does the rest."""
    sim = Simulator()
    sim.perif_loopback(0, True)
    _load_demo_firmware(sim)
    for _ in range(60):
        sim.step_paced("pru0", "pru1", 1000)
    data = list(sim.memory_read(0x2000, 32))
    assert data == [i & 0xFF for i in range(32)]
```

- [ ] **Step 2: Write the ws mode-switch test**

Append to `tests/test_perif_server.py`:

```python
def test_io_window_mode_follows_firmware_gpcfg_write():
    """Firmware writing GPCFG must flip io.mode in the next state push."""
    prog = (
        "start:\n"
        "        ldi  r0, 0x0000\n"
        "        ldi  r0.w2, 0x0400\n"
        "        ldi  r1, 0x6008\n"
        "        ldi  r1.w2, 0x0002\n"
        "        sbbo r0, r1, 0, 4\n"
        "        halt\n"
    )
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "load", "core": "pru0", "source": prog})
        st = ws.receive_json()
        assert st["io"]["mode"] == "gpio"
        ws.send_json({"action": "run", "core": "pru0", "max_steps": 20})
        st = ws.receive_json()
        assert st["io"]["mode"] == "perif"
        assert st["io"]["mux_sel"] == 1
        # Restore GP mode + reset so shared server state doesn't leak.
        ws.send_json({"action": "gpcfg_write", "core": "pru0", "mux_sel": 0})
        ws.receive_json()
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
```

- [ ] **Step 3: Run both new tests**

Run: `python3 -m pytest tests/test_perif_drift_experiment.py -k no_host tests/test_perif_server.py -k firmware_gpcfg -v`
Expected: 2 PASSED (they are regression locks; Tasks 1–2 already provide the behavior).

- [ ] **Step 4: Check readme for stale host-setup docs**

Run: `grep -n "260E4\|26100\|GPCFG" readme.md`
If hits describe the demo's manual register setup, update them to say the firmwares self-configure and only loopback is a host step. If no hits, skip.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_perif_drift_experiment.py tests/test_perif_server.py readme.md
git commit -m "test: no-host-setup perif roundtrip + firmware-driven IO mode switch"
```
