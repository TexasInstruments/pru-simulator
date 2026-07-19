# Perif CFG Register Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add editable raw-hex RXCFG/TXCFG register fields to the UI perif panel, wired to the existing `write_perif_register` websocket action.

**Architecture:** The backend already supports register writes (`Simulator.write_perif_register`) and the perif state payload already flows to the client via `PeripheralInterface` → `get_shared_config()`. Task 1 adds the raw u32 register values and block base address to that dict (pure data addition, no new endpoints). Task 2 renders an editor row in the perif panel that prefills from state and sends the existing action on Apply.

**Tech Stack:** Python 3 (no framework beyond FastAPI already in use), vanilla JS (`ui/static/app.js`), pytest + `fastapi.testclient` for websocket tests.

**Spec:** `docs/superpowers/specs/2026-07-19-perif-cfg-register-editor-design.md`

## Global Constraints

- Register layout (from `perif/perif_registers.py`): RXCFG = `block+0x00`, TXCFG = `block+0x04`; block base 0x260E0 (PRU0), 0x26100 (PRU1).
- New shared-config keys are exactly: `rxcfg`, `txcfg`, `base_addr`.
- UI must not clobber an input the user is editing (follow the loopback section's `document.activeElement` pattern in `updatePerifPanel`).
- Run tests with `python3 -m pytest` from the repo root.

---

### Task 1: Raw RXCFG/TXCFG values in the shared-config payload

**Files:**
- Modify: `perif/perif_registers.py` (the `get_shared_config` method, lines 187–199)
- Test: `tests/test_perif.py` (add to the existing register test class near `test_field_roundtrip`, line 44)
- Test: `tests/test_perif_server.py` (extend `test_write_perif_register_via_ws`, line 69)

**Interfaces:**
- Consumes: `PerifRegisters(base_addr)` with `write(addr, data)` and `get_shared_config()` (existing).
- Produces: `get_shared_config()` returns three additional keys — `"rxcfg": int` (raw u32 of block+0x00), `"txcfg": int` (raw u32 of block+0x04), `"base_addr": int` (absolute block base). Task 2's JS reads exactly these keys from `state.io.perif.shared`.

- [ ] **Step 1: Write the failing unit test**

In `tests/test_perif.py`, inside the class containing `test_field_roundtrip` (it uses the `regs` fixture returning `PerifRegisters(_BASE)`), add:

```python
    def test_shared_config_carries_raw_registers_and_base(self, regs):
        regs.write(_BASE + 0x00, (0x0007001F).to_bytes(4, "little"))  # RXCFG
        regs.write(_BASE + 0x04, (0x00070010).to_bytes(4, "little"))  # TXCFG
        sh = regs.get_shared_config()
        assert sh["rxcfg"] == 0x0007001F
        assert sh["txcfg"] == 0x00070010
        assert sh["base_addr"] == _BASE
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_perif.py -k raw_registers -v`
Expected: FAIL with `KeyError: 'rxcfg'`

- [ ] **Step 3: Implement**

In `perif/perif_registers.py`, extend `get_shared_config` (keep existing keys unchanged):

```python
    def get_shared_config(self) -> dict:
        """Shared RX/TX clock config for the UI."""
        return {
            "rx_sample_size": self.get_rx_sample_size(),
            "rx_sb_pol": self.get_rx_sb_pol(),
            "rx_clk_sel": self.get_rx_clk_sel(),
            "rx_div_factor": self.get_rx_div_factor(),
            "rx_div_factor_frac": self.get_rx_div_factor_frac(),
            "tx_clk_sel": self.get_tx_clk_sel(),
            "tx_div_factor": self.get_tx_div_factor(),
            "tx_div_factor_frac": self.get_tx_div_factor_frac(),
            "share_en": self.get_share_en(),
            "rxcfg": self._read_u32(_RXCFG),
            "txcfg": self._read_u32(_TXCFG),
            "base_addr": self._base,
        }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_perif.py -k raw_registers -v`
Expected: PASS

- [ ] **Step 5: Write the failing server-integration assertion**

In `tests/test_perif_server.py`, extend `test_write_perif_register_via_ws`: after the existing `cfg["tx_frame_size"] == 8` assertion and before the mux restore, add a TXCFG write and payload check:

```python
        # TXCFG @ 0x260E4 raw value must round-trip into the shared payload
        ws.send_json({"action": "write_perif_register", "core": "pru0",
                      "addr": 0x260E4, "value": 0x00070010})
        state = ws.receive_json()
        sh = state["io"]["perif"]["shared"]
        assert sh["txcfg"] == 0x00070010
        assert sh["base_addr"] == 0x260E0
        # Restore TXCFG so shared server state doesn't leak.
        ws.send_json({"action": "write_perif_register", "core": "pru0",
                      "addr": 0x260E4, "value": 0})
        ws.receive_json()
```

Note: this file's tests restore any state they mutate (see the comments in `test_gpcfg_switches_mode_and_exposes_perif_state`) — the restore write above follows that convention. The pre-existing `0x260E8` write in this test is not restored; that predates this plan — leave it as-is, do not "fix" it in this task.

- [ ] **Step 6: Run the server test**

Run: `python3 -m pytest tests/test_perif_server.py::test_write_perif_register_via_ws -v`
Expected: PASS if Step 3 is done (the payload flows automatically). If it fails with `KeyError`, Step 3 was incomplete.

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass (1107+ passed, 2 xfail as of 2026-07-18 baseline; new tests add to the count).

- [ ] **Step 8: Commit**

```bash
git add perif/perif_registers.py tests/test_perif.py tests/test_perif_server.py
git commit -m "feat(perif): expose raw RXCFG/TXCFG + base_addr in shared config payload"
```

---

### Task 2: CFG register editor row in the perif panel

**Files:**
- Modify: `ui/static/index.html` (perif panel markup ~line 1463, CSS block near `.perif-r30-decode` ~line 1222)
- Modify: `ui/static/app.js` (`updatePerifPanel`, insert after the "Shared clock / decode row" block that ends at line 791)

**Interfaces:**
- Consumes: `state.io.perif.shared.rxcfg` / `.txcfg` / `.base_addr` (from Task 1); existing JS helpers `sendAction(obj)` (`app.js:391`), `parseHexOrDec(s)` (`app.js:2197`, hoisted function declaration — callable from `updatePerifPanel`); existing server action `write_perif_register` (`ui/server.py:424`) which re-sends state after the write.
- Produces: UI-only; no new programmatic interfaces.

- [ ] **Step 1: Add the container to the panel markup**

In `ui/static/index.html`, insert one line between the mode bar and the decode row (currently lines 1462–1463):

```html
            <div class="perif-cfg-regs" id="perif-cfg-regs"></div>
            <div class="perif-r30-decode" id="perif-r30-decode"></div>
```

(The first line is new; the second is the existing decode div shown for placement.)

- [ ] **Step 2: Add the CSS**

In the same file's style block, directly after the `.perif-r30-field` rule (line 1223), add:

```css
    .perif-cfg-regs { background: #1a1a2e; padding: 6px 8px; border-radius: 4px; margin-bottom: 8px; font-size: 10px; }
    .perif-cfgreg-row { display: flex; align-items: center; gap: 6px; margin: 2px 0; }
    .perif-cfgreg-name { color: #4fc3f7; font-weight: bold; width: 44px; }
    .perif-cfgreg-addr { color: #888; }
    .perif-cfgreg-row input { background: #0d1117; color: #e0e0e0; border: 1px solid #333; border-radius: 3px; font-family: monospace; font-size: 10px; padding: 2px 4px; width: 90px; }
    .perif-cfgreg-btn { background: #2d2d44; color: #e0e0e0; border: 1px solid #444; border-radius: 3px; font-size: 10px; cursor: pointer; padding: 1px 8px; }
    .perif-cfgreg-btn:hover { background: #3a3a55; }
    .perif-cfgreg-err { color: #ef5350; }
```

- [ ] **Step 3: Render the editor in `updatePerifPanel`**

In `ui/static/app.js`, insert the following block immediately after the "Shared clock / decode row" block (after line 791's closing `}`) and before the "Channel cards" comment:

```js
  // CFG register editor (skip rebuild while the user edits an input)
  const cfgRegs = document.getElementById('perif-cfg-regs');
  if (cfgRegs) {
    const sh = p.shared || {};
    const cfgFocus = document.activeElement;
    const cfgEditing = cfgRegs.contains(cfgFocus) && cfgFocus.tagName === 'INPUT';
    if (sh.base_addr != null && !cfgEditing) {
      const hex8 = v => '0x' + (v >>> 0).toString(16).toUpperCase().padStart(8, '0');
      cfgRegs.innerHTML = '';
      [['RXCFG', 0x0, sh.rxcfg], ['TXCFG', 0x4, sh.txcfg]].forEach(([name, off, val]) => {
        const addr = sh.base_addr + off;
        const row = document.createElement('div');
        row.className = 'perif-cfgreg-row';
        row.innerHTML = `
          <span class="perif-cfgreg-name">${name}</span>
          <span class="perif-cfgreg-addr">@0x${addr.toString(16).toUpperCase()}</span>
          <input type="text" spellcheck="false" value="${hex8(val ?? 0)}">
          <button class="perif-cfgreg-btn" data-addr="${addr}">Apply</button>
          <span class="perif-cfgreg-err"></span>`;
        cfgRegs.appendChild(row);
      });
      cfgRegs.querySelectorAll('.perif-cfgreg-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const row = btn.closest('.perif-cfgreg-row');
          const err = row.querySelector('.perif-cfgreg-err');
          const v = parseHexOrDec(row.querySelector('input').value.trim());
          if (isNaN(v) || v < 0 || v > 0xFFFFFFFF) {
            err.textContent = 'invalid value';
            return;
          }
          err.textContent = '';
          sendAction({ action: 'write_perif_register', core: currentCore,
                       addr: parseInt(btn.dataset.addr, 10), value: v });
        });
      });
    }
  }
```

Notes for the implementer:
- `p` is already in scope (`const p = io.perif;` at the top of the function, guarded by `if (!p) return;`).
- The `!cfgEditing` guard mirrors the loopback section's pattern (lines 822–827) so live state pushes don't clobber typing.
- After Apply, the server re-sends state; the input (no longer necessarily focused, but if still focused, untouched) and the decode bar refresh from the authoritative register value.

- [ ] **Step 4: Static sanity check**

Run: `node --check ui/static/app.js`
Expected: no output (exit 0). If `node` is unavailable, run `python3 -m pytest tests/test_server.py -q` to at least confirm the server still serves (JS syntax then gets checked in Step 5).

- [ ] **Step 5: Verify in the running app**

1. Start: `python3 ui/server.py` (serves http://localhost:8080).
2. Open the app, core **PRU0**, set GP-Mux to **1 — Peripheral Interface**. The perif panel must now show `RXCFG @0x260E0` and `TXCFG @0x260E4` rows with value `0x00000000`.
3. Enter `0x00070010` into TXCFG, click Apply. Expected: decode bar updates to `tx_clk_sel=1 tx_div=7`; the input re-renders as `0x00070010`.
4. Switch core to **PRU1** (mux 1 there too): rows must read `@0x26100` / `@0x26104`.
5. Enter `xyz` into a field, Apply. Expected: red `invalid value` next to the row, no state change.
6. Cross-check the write landed in memory: set the memory window to address `0x260E0`, length `32`, and confirm bytes `10 00 07 00` at offset +4 (TXCFG, little-endian).
7. Stop the server (Ctrl-C).

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass, same counts as Task 1 Step 7.

- [ ] **Step 7: Commit**

```bash
git add ui/static/index.html ui/static/app.js
git commit -m "feat(ui): editable RXCFG/TXCFG register fields in perif panel"
```
