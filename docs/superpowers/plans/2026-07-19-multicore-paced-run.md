# Time-Paced Multicore Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the UI's multicore Run keep both cores' peripheral clocks aligned (via a new `Simulator.step_paced` primitive and `run_multicore` websocket action) so the perif drift demo produces a clean capture in the UI.

**Architecture:** Task 1 adds `Simulator.step_paced(lead, follow, ...)` — the guard-band pacing loop from `tools/perif_drift_report.py:run_drift` lifted into the simulator, with a 1:1 fallback when a core has no perif block. Task 2 adds the `run_multicore` websocket action wrapping it with the existing `run` action's breakpoint/halt semantics. Task 3 switches the frontend's multicore Run to the new action (Step stays 1:1 per user decision).

**Tech Stack:** Python 3 / FastAPI (existing), vanilla JS, pytest + `fastapi.testclient`.

**Spec:** `docs/superpowers/specs/2026-07-19-multicore-paced-run-design.md`

## Global Constraints

- Pacing invariant: `follow`'s perif `_now_ns` must never exceed `lead`'s; it trails by up to `guard_ns` (default 20.0) plus one step's advance.
- Fallback: if `lead` or `follow` is missing from `sim._perif` (e.g. rtu0), pace 1:1 by instructions — identical to current behavior.
- Multicore **Step** button behavior must NOT change (user decision).
- Run tests with `python3 -m pytest` from the repo root.
- Demo register recipe (used in tests): GPCFG mux 1 on both cores; PRU0 `TXCFG 0x260E4 = 0x00070010`; PRU1 `RXCFG 0x26100 = 0x0007001F`; loopback ch0 enabled; firmware `source/perif_tx_pattern.asm` (PRU0) and `source/perif_rx_capture.asm` (PRU1); capture at DRAM1 `0x2000`, count u32 at `0x3FF8`.

---

### Task 1: `Simulator.step_paced`

**Files:**
- Modify: `simulator.py` (add method directly after `step`, which ends at line 260)
- Test: `tests/test_perif_drift_experiment.py` (append)

**Interfaces:**
- Consumes: `self.cores[name]` (`PRUCore` with `.step()`, `.halted`, `.pc`, `.instructions`), `self._perif[name]._now_ns` (perif blocks exist for pru0/pru1 only).
- Produces: `step_paced(lead: str, follow: str, count: int = 1, guard_ns: float = 20.0) -> None`. Task 2's server handler calls it with `count=1` per iteration.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_perif_drift_experiment.py`:

```python
def _load_demo_firmware(sim):
    assert sim.load("pru0", (_SRC / "perif_tx_pattern.asm").read_text()) == []
    assert sim.load("pru1", (_SRC / "perif_rx_capture.asm").read_text()) == []


def test_step_paced_produces_clean_capture():
    """Regression: instruction-lockstep corrupted the capture within bytes
    (observed 00 01 81 01 82 ...); step_paced must yield the clean pattern."""
    sim = Simulator()
    _setup_perif(sim)
    _load_demo_firmware(sim)
    for _ in range(60):
        sim.step_paced("pru0", "pru1", 1000)
    count = int.from_bytes(bytes(sim.memory_read(0x3FF8, 4)), "little")
    assert count >= 64
    data = list(sim.memory_read(0x2000, 64))
    assert data == [i & 0xFF for i in range(64)]


def test_step_paced_follow_never_leads():
    sim = Simulator()
    _setup_perif(sim)
    _load_demo_firmware(sim)
    for _ in range(50):
        sim.step_paced("pru0", "pru1", 100)
        t0 = sim._perif["pru0"]._now_ns
        t1 = sim._perif["pru1"]._now_ns
        assert t1 <= t0, f"follow leads: pru1 {t1} > pru0 {t0}"


def test_step_paced_rtu0_fallback_is_one_to_one():
    """No perif on rtu0: both cores advance exactly count instructions."""
    sim = Simulator()
    prog = "start:\n        add r2, r2, 1\n        jmp start\n"
    assert sim.load("pru0", prog) == []
    assert sim.load("rtu0", prog) == []
    sim.step_paced("pru0", "rtu0", 250)
    assert sim.cores["pru0"].counters.cycles == 250
    assert sim.cores["rtu0"].counters.cycles == 250
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_perif_drift_experiment.py -k step_paced -v`
Expected: 3 FAILED with `AttributeError: 'Simulator' object has no attribute 'step_paced'`

- [ ] **Step 3: Implement `step_paced`**

In `simulator.py`, insert directly after the `step` method (after line 260):

```python
    def step_paced(self, lead: str, follow: str, count: int = 1,
                   guard_ns: float = 20.0) -> None:
        """Step *lead* by *count* instructions, pacing *follow* by perif time.

        After each lead instruction, *follow* is stepped until its perif
        clock trails lead's by at most *guard_ns* — follow never leads, so
        an RX on follow only samples line history a TX on lead has already
        recorded. Falls back to 1:1 instruction interleave when either
        core has no perif block (e.g. rtu0).
        """
        lead_pru = self._get_core(lead)
        follow_pru = self._get_core(follow)
        lead_perif = self._perif.get(lead)
        follow_perif = self._perif.get(follow)
        paced = lead_perif is not None and follow_perif is not None
        for _ in range(count):
            if not lead_pru.halted and lead_pru.pc < len(lead_pru.instructions):
                lead_pru.step()
            if not paced:
                if not follow_pru.halted and follow_pru.pc < len(follow_pru.instructions):
                    follow_pru.step()
                continue
            target = lead_perif._now_ns - guard_ns
            safety = 1000
            while (follow_perif._now_ns < target and safety > 0
                   and not follow_pru.halted
                   and follow_pru.pc < len(follow_pru.instructions)):
                follow_pru.step()
                safety -= 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_perif_drift_experiment.py -k step_paced -v`
Expected: 3 PASSED

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass (baseline 1132 passed, 2 xfailed, plus the 3 new tests).

- [ ] **Step 6: Commit**

```bash
git add simulator.py tests/test_perif_drift_experiment.py
git commit -m "feat: Simulator.step_paced — perif-time-paced multicore stepping"
```

---

### Task 2: `run_multicore` websocket action

**Files:**
- Modify: `ui/server.py` (add an `elif` branch after the `run` handler, which ends at line 408)
- Test: `tests/test_perif_server.py` (append)

**Interfaces:**
- Consumes: `sim.step_paced(lead, follow, 1)` from Task 1; existing `_send_state(websocket, core, at_breakpoint=...)`; message fields `core` (lead, default `"pru0"`), `partner` (default `"pru1"`), `max_steps` (default 1000).
- Produces: websocket action `run_multicore`; on completion sends two state messages (lead first, then partner), each with its own `at_breakpoint` flag.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_perif_server.py`:

```python
def _receive_states(ws, cores):
    """Receive one state message per core; return {core: state}."""
    out = {}
    for _ in cores:
        st = ws.receive_json()
        out[st["core"]] = st
    assert set(out) == set(cores)
    return out


def test_run_multicore_paces_perif_demo():
    """Regression: the drift demo must capture the clean counter pattern
    when driven through the multicore run action."""
    from pathlib import Path
    src = Path(__file__).parent.parent / "source"
    with client.websocket_connect("/ws") as ws:
        for core in ("pru0", "pru1"):
            ws.send_json({"action": "reset", "core": core})
            ws.receive_json()
            ws.send_json({"action": "gpcfg_write", "core": core, "mux_sel": 1})
            ws.receive_json()
        ws.send_json({"action": "write_perif_register", "core": "pru0",
                      "addr": 0x260E4, "value": 0x00070010})
        ws.receive_json()
        ws.send_json({"action": "write_perif_register", "core": "pru1",
                      "addr": 0x26100, "value": 0x0007001F})
        ws.receive_json()
        ws.send_json({"action": "perif_loopback", "core": "pru0",
                      "channel": 0, "enabled": True})
        ws.receive_json(); ws.receive_json()
        ws.send_json({"action": "load", "core": "pru0",
                      "source": (src / "perif_tx_pattern.asm").read_text()})
        ws.receive_json()
        ws.send_json({"action": "load", "core": "pru1",
                      "source": (src / "perif_rx_capture.asm").read_text()})
        ws.receive_json()

        for _ in range(60):
            ws.send_json({"action": "run_multicore", "core": "pru0",
                          "partner": "pru1", "max_steps": 1000})
            _receive_states(ws, ("pru0", "pru1"))

        ws.send_json({"action": "read_memory", "addr": 0x2000, "length": 32,
                      "tag": "mem1"})
        mem = ws.receive_json()
        assert mem["data"] == [i & 0xFF for i in range(32)]

        # Restore shared server state.
        ws.send_json({"action": "perif_loopback", "core": "pru0",
                      "channel": 0, "enabled": False})
        ws.receive_json(); ws.receive_json()
        for core in ("pru0", "pru1"):
            ws.send_json({"action": "write_perif_register", "core": core,
                          "addr": 0x260E4 if core == "pru0" else 0x26100,
                          "value": 0})
            ws.receive_json()
            ws.send_json({"action": "gpcfg_write", "core": core, "mux_sel": 0})
            ws.receive_json()
            ws.send_json({"action": "reset", "core": core})
            ws.receive_json()


def test_run_multicore_stops_at_lead_breakpoint():
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "reset", "core": "pru0"})
        ws.receive_json()
        ws.send_json({"action": "reset", "core": "pru1"})
        ws.receive_json()
        prog = "start:\n        add r2, r2, 1\n        jmp start\n"
        for core in ("pru0", "pru1"):
            ws.send_json({"action": "load", "core": core, "source": prog})
            ws.receive_json()
        ws.send_json({"action": "toggle_breakpoint", "core": "pru0", "addr": 1})
        ws.receive_json()
        ws.send_json({"action": "run_multicore", "core": "pru0",
                      "partner": "pru1", "max_steps": 1000})
        states = _receive_states(ws, ("pru0", "pru1"))
        assert states["pru0"]["at_breakpoint"] is True
        assert states["pru0"]["pc"] == 1
        # Cleanup: clear breakpoint (toggle again) + reset both cores.
        ws.send_json({"action": "toggle_breakpoint", "core": "pru0", "addr": 1})
        ws.receive_json()
        for core in ("pru0", "pru1"):
            ws.send_json({"action": "reset", "core": core})
            ws.receive_json()
```

(State messages carry `type`, `core`, `pc`, `halted`, `registers` (hex strings), and the `at_breakpoint` flag; breakpoints are set/cleared with the existing `toggle_breakpoint` action's `addr` field — both verified against `ui/server.py`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_perif_server.py -k run_multicore -v`
Expected: both FAIL (the `run_multicore` action is unknown, so the server never replies and `receive_json` times out or errors; a timeout counts as the expected failure here).

- [ ] **Step 3: Implement the action**

In `ui/server.py`, after the `run` handler's `await _send_state(...)` (line 408), add:

```python
            elif action == "run_multicore":
                max_steps = int(msg.get("max_steps", 1000))
                partner = msg.get("partner", "pru1")
                lead_pru = sim.cores[core]
                partner_pru = sim.cores[partner]
                lead_bp = partner_bp = False
                try:
                    steps = 0
                    while (steps < max_steps and not lead_pru.halted
                           and lead_pru.pc < len(lead_pru.instructions)):
                        sim.step_paced(core, partner, 1)
                        steps += 1
                        if lead_pru.pc in lead_pru.breakpoints:
                            lead_bp = True
                            break
                        if partner_pru.pc in partner_pru.breakpoints:
                            partner_bp = True
                            break
                except ValueError as ve:
                    await websocket.send_json({"type": "error", "errors": [str(ve)]})
                await _send_state(websocket, core, at_breakpoint=lead_bp)
                await _send_state(websocket, partner, at_breakpoint=partner_bp)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_perif_server.py -k run_multicore -v`
Expected: 2 PASSED

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add ui/server.py tests/test_perif_server.py
git commit -m "feat(ui): run_multicore action — perif-time-paced multicore run"
```

---

### Task 3: Frontend uses `run_multicore`

**Files:**
- Modify: `ui/static/app.js` (the multicore branch inside `startRun()`, lines 1987–1989)

**Interfaces:**
- Consumes: `run_multicore` action from Task 2; existing globals `mcPartner`, `signalGraph.recording`.
- Produces: UI-only; Step button (lines 2016–2020) is NOT touched.

- [ ] **Step 1: Replace the multicore Run sends**

In `ui/static/app.js` `startRun()`, change:

```js
    if (multiCoreMode) {
      sendAction({ action: "run", core: "pru0", max_steps });
      sendAction({ action: "run", core: mcPartner, max_steps });
    } else {
```

to:

```js
    if (multiCoreMode) {
      sendAction({ action: "run_multicore", core: "pru0",
                   partner: mcPartner, max_steps });
    } else {
```

- [ ] **Step 2: Syntax check**

Run: `node --check ui/static/app.js`
Expected: exit 0, no output.

- [ ] **Step 3: Verify end-to-end against a live server**

Browser if available; otherwise headless: start `python3 -c "from ui.server import start_dashboard; start_dashboard(port=8081)"` in the background, then confirm (a) `curl -s http://127.0.0.1:8081/static/app.js | grep -c run_multicore` prints ≥1, and (b) the websocket demo flow from Task 2's first test passes against the served app (it uses the same `app` object via TestClient, already covered — the curl check confirms the served JS is the edited file). If a browser is available instead: run the full drift-demo walkthrough and confirm the memory window at `0x2000` shows `00 01 02 03 …` while Running.

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ui/static/app.js
git commit -m "feat(ui): multicore Run uses time-paced run_multicore action"
```
